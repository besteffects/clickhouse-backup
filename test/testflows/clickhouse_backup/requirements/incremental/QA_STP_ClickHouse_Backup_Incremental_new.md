# QA-STP ClickHouse Backup Incremental Backups
# Software Test Plan

(c) 2026 Altinity Inc. All Rights Reserved.

**Author:** vsviderskyi

**Date:** July 16, 2026


## Introduction

This test plan describes how to test **incremental backups** in `clickhouse-backup`.

An incremental backup stores only the data that was changed since a previous backup, instead of copying everything again. This makes backups faster and smaller. The previous backup that an incremental backup is built on
the top of is called its **base backup**.

The goal of this plan is to confirm that:

* An incremental backup stores only the new or changed data, not the whole table again.
* Incremental backup restores exactly the same data as the original.
* A part that was changed and has the same name is still saved correctly (also there should be no mistakes if the data was not changed).
* Backups that other backups depend on are not accidentally deleted.
* Incremental backups keep working after interruptions, during background activity in ClickHouse, and on larger amounts of data.


## How Incremental Backups Work in clickhouse-backup

ClickHouse stores table data in immutable files on disk called **data parts**. A part is written once and is
never modified in place. When data changes, ClickHouse writes new parts and eventually merges old ones into
new ones.

`clickhouse-backup` utility uses this to make incremental backups: when it creates an incremental backup, it compares
the current parts against the parts already saved in the base backup. Parts that already exist in the base
backup are **reused** (their data is not uploaded again). Only genuinely new parts are uploaded.

Because of this, an incremental backup on its own is not complete — it points to its base backup (and the base
may point to an earlier one, forming a **chain**). When restoring, `clickhouse-backup` collects the reused
parts from the earlier backups in the chain automatically.

The chain is **linear** — each backup records exactly one base backup, so every
new incremental backup adds one more link. There is **no fixed limit** on chain depth in `clickhouse-backup` curerntly.
It follows the chain of base backups recursively until it reaches the full backup at the start. In practice, the
depth is controlled by how the backups are managed by users, not by the tool:

Basic commands:
* The `watch` command (and scheduled backups) periodically makes a new *full* backup, which starts a fresh
  chain, so the depth resets each full cycle.
* The `rebase` command collapses a chain by turning an incremental backup into a self-contained one.
* Retention (`backups_to_keep_remote`) only removes backups that no remaining chain still needs, so it never
  breaks a chain that is still in use.

**Chain compaction (`rebase`).** The `rebase` command turns an incremental backup into a complete, standalone
backup. It works by walking the whole chain (from the chosen backup back to its full backup), finding every
part that was "reused" from an earlier backup, and copying those parts into the chosen backup. The copy happens
directly on the remote storage (a server-side copy), so data is not downloaded and re-uploaded. Afterwards the
backup no longer records a base backup — it becomes a full backup on its own, and all of its former earlier
backups can be deleted without affecting it. This works for regular remote backups uploaded with
`upload_by_part: true`; it is not available for embedded backups.

**Chain Depth**

There is no exact depth at which a chain becomes "too long" — `clickhouse-backup` does not enforce a limit. Only *cost* can grow, and `rebase` is the tool to control it.

* **Restore/download cost.** To download or restore an incremental backup, every ancestor up to the full backup
  must be reachable, and reused parts are resolved by walking the chain one level at a time. A longer chain
  means more remote metadata reads and more places a part may have to be fetched from, so restore takes longer.
* **Retention cost.** Automatic retention (`backups_to_keep_remote`) will not delete a backup that a later
  backup still needs, so a long chain pins many old backups in remote storage. A manual `delete` can still
  remove them, but that breaks every dependent restore.
* **Fragility.** If any single backup in the chain is lost or corrupted, every backup that depends on it becomes
  unrestorable.

So the practical rule is: compact with `rebase` when you want to shorten restore time, or when you need to delete
old backups but a later increment still depends on them. The scheduled `watch` workflow does this automatically
with `full_type=rebase`, and retention can trigger it via `rebase_before_remove_old_remote: true`.

This matches how `rebase` is described in the `clickhouse-backup` source code:

> Rebase - copy required parts from the required backups chain into backupName on remote storage
> and remove the required_backup dependency, so backupName becomes a full backup

The official description of this behavior is in the clickhouse-backup documentation (see
[References](#references)).

### Which Part Types Are Supported
<a id="which-part-types-are-supported"></a>

`clickhouse-backup` copies a part by simply taking the files that make up that part (using
`ALTER TABLE ... FREEZE`). It decides whether to reuse a part or upload it again based only on the part's
**name** and a fingerprint of its files — it never looks at how the part is organized inside. Because of that, the way ClickHouse stores a part does not affect the backup: all storage variations are handled the same way.

To keep things clear, it helps to separate three different ideas that are easy to mix up: the **part type**, the **on-disk storage format**, and the **kind/state** of a part.

**1. Part type: Wide or Compact.** ClickHouse has exactly two part types, which only describe how the columns are laid out inside a part (see the
[ClickHouse documentation on part types](https://clickhouse.com/docs/resources/support-center/knowledge-base/data-management/understanding-part-types-and-storage-formats)):

| Part type | What it means | Supported? |
| --------- | ------------- | ---------- |
| **Wide** | Each column is stored in its own file | Yes — fully supported |
| **Compact** | All columns are stored together in one file | Yes — fully supported |

Both are backed up and reused identically. 

Older ClickHouse versions also had an experimental `InMemory` part type that was never written to disk; it has been removed and is not relevant here.

**2. On-disk storage format: Full or Packed.** This is a separate ClickHouse concept that only changes how a part's files are laid out on disk — either as individual files (**Full**) or bundled into a single archive
(**Packed**). Since `clickhouse-backup` copies whatever files a part consists of, both formats are captured the same way. Packed storage is a newer option, so it has to be tested. But it does not change how incremental reuse works.

**3. Kind and state of a part.** Regardless of type or storage format, a part can come from different
operations or be in different states. This is where support is different:

| Kind / state of part | Backed up and reused? | Explanation |
| -------------------- | --------------------- | ----------- |
| **Regular data part** (from a normal `INSERT`) | Yes | The normal case — this is what incremental backup is designed around. |
| **Part left by a classic mutation** (`ALTER TABLE ... UPDATE`/`DELETE`) | Yes (as new data) | A finished mutation writes brand-new parts with new names; the backup uploads them as new. In-progress mutations are recorded separately (`backup_mutations`). |
| **Projection parts** (a projection stored inside a part) | Yes, together with their parent part | They live inside the part's directory, so they are copied with it. Specific projections can be excluded with a skip-projections option. |
| **Patch part** (from a lightweight `UPDATE`) | Not reliably — see note below | Needs to be materialized first; explained in the note. |
| **Detached part** | No | Not part of the table's live data, so ClickHouse does not include it when freezing. |
| **Temporary / in-progress part** (`tmp_...`) | No | Represents an insert or merge that is still running, so it is not backed up. |
| **Broken part** | No (fails or skipped) | Default `max_broken_part_ratio: 0` aborts the backup. If set `> 0`, broken parts are skipped and listed in `broken_parts`, not reused. |

**One prerequisite: the table must store its data as parts.** Only **MergeTree-family** tables (plain,
`Replicated*`, and object-disk-backed) have parts, so only they can be backed up incrementally. Tables using
engines like `Memory`, `Log`, `TinyLog`, or `Set` have no parts, so there is nothing to reuse (their data is
still backed up, just not incrementally). Tables whose data lives in an external system (`MySQL`, `PostgreSQL`,
`MaterializedPostgreSQL`, `DataLakeCatalog` database engines) are skipped from freezing entirely.

> [!NOTE]
> **Patch parts and lightweight updates.** A lightweight `UPDATE` (ClickHouse 25.8+; also a lightweight
> `DELETE` when `lightweight_delete_mode` is `lightweight_update` or `lightweight_update_force`) does not
> rewrite data. Instead it writes a small **patch part** that is applied on read, and materialized into the
> real data later — during background merges/mutations or `ALTER TABLE ... APPLY PATCHES`.
> `clickhouse-backup` has no special handling for patch parts, and ClickHouse stores them in a **separate
> partition** named `patch-<hash>-<original_partition_id>`. This causes
> two problems for backups:
> 1. A whole-table backup may pick the patch part up as if it were a normal part, but a partition-scoped backup
>    (`--partitions=<original_partition>`) will **miss it**, because the patch part is in a different partition —
>    so the update would be lost on restore.
> 2. Even in a whole-table backup, it is **not confirmed** that a restored patch part re-applies the update
>    correctly.
>
> **Recommendation:** apply pending updates before backing up — run `ALTER TABLE ... APPLY PATCHES` (or let
> background merges finish) so the changes are written into normal Wide/Compact parts, which are fully
> supported.