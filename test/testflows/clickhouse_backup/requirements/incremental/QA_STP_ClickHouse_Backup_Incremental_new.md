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

### Which ClickHouse Versions Are Supported

**Short answer:** on every ClickHouse version in the Testflows matrix tests
(`22.3` … `26.3 (latest currently)`), **regular part-level incremental backups are supported** (`--diff-from` /
`--diff-from-remote`). There is no Testflows version where regular incremental is “not supported”.

`clickhouse-backup` itself supports ClickHouse **above 1.1.54394**
([ReadMe — Limitations](https://github.com/Altinity/clickhouse-backup/blob/master/ReadMe.md#limitations)).
Regular incremental works on that whole range for MergeTree-family tables. What changes by version is only
*how* freeze/fingerprint/cleanup work, and whether the **embedded** incremental path is available.

**Is incremental supported?**

| Backup mode | Incremental supported? | From ClickHouse version | Notes |
| ----------- | ---------------------- | ----------------------- | ----- |
| **Regular** (`--diff-from`, `--diff-from-remote`) | **Yes** | above 1.1.54394 (same as the tool) | Default path for this plan; MergeTree-family only |
| **Embedded** (`use_embedded_backup_restore: true` + `base_backup`) | **Yes** | **22.7+** documented; **22.8+** in Testflows | Needs `clickhouse-backup` 2.5.3+; skip on Testflows `22.3` |
| Below tool minimum (≤ 1.1.54394) | **No** | — | Outside `clickhouse-backup` support |

> [!NOTE]
For Testflows, run embedded incremental on **22.8+** only.

**Related version notes.** For Testflows (`22.3`+), the old
implementation floors below are already satisfied on every cell, so they do **not** need their own table rows for deciding what to run:

* `hash_of_all_files` fingerprint — 19.11+ (`pkg/backup/create.go`; older CH used CRC64 of `checksums.txt`)
* hard-linked freeze out of `shadow` — 21.4+ (`pkg/filesystemhelper`)
* `ALTER TABLE ... UNFREEZE` after backup — above 21.4 (`pkg/backup/create.go`)

What *does* still gate scenarios in this plan:

* **Patch parts** (lightweight `UPDATE`, 25.8+) — incremental still works, but pending patches are
  unreliable; materialize with `APPLY PATCHES` first.
* **Object disks** — S3 21.8+, GCS from S3 22.6+, Azure Blob 23.3+.

In embedded mode ClickHouse computes the file-level diff itself and the base backup is passed at create time, so
`--diff-from` (local base) is regular-mode only.

**What to run for this test plan (Testflows).** Automated coverage is Testflows from **22.3**, specifically versions:

`22.3`, `22.8`, `23.3`, `23.8`, `24.3`, `24.8`, `25.3`, `25.8`, `26.3`

On **all** of these: regular incremental are **supported**.

For this plan, use the Testflows matrix and the tables above.

Constraints within the Testflows matrix:

* Regular incremental scenarios: run on **all** matrix versions (`22.3`+).
* Embedded incremental: **skip** `22.3`; run on `22.8`+.
* Object disks: S3 from `22.3`; GCS/Azure needs later versions (22.8+ / 23.3+).
* Patch parts: `25.8`+.

Default image when `CLICKHOUSE_VERSION` is unset: **26.3** (specified in `test/testflows/run.sh`).

### Backward Compatibility Across ClickHouse Versions

A common real-world case is a chain that runs for a long time (for example a year) while the ClickHouse server is
upgraded underneath it: the **full/base backup was created on an older version X**, and later **incremental
backups are created on a newer version Y**. Two questions follow from that, and both are answered by how
`clickhouse-backup` decides to reuse a part.

**How reuse is decided by clickhouse-backup** 

When building an increment, `clickhouse-backup` matches each
current part against the base backup by the part's **name** and a **content fingerprint** of its files —
`hash_of_all_files` when available, otherwise a CRC64 checksum of `checksums.txt` (see `pkg/backup/upload.go`
`markDuplicatedParts`, `pkg/filesystemhelper/filesystemhelper.go` `addRequiredPartIfNotExists`). It **never
compares the ClickHouse version** that created a backup. Each backup does record the server version that
made it (the `clickhouse_version` field in `backup_name/metadata.json`), but that value is informational only —
nothing in the create, upload, download, or restore path reads it back to gate reuse or restore.

**Q1 — Base created on version X, increments created on version Y (X older, Y newer): is that supported?**

**Yes**, from `clickhouse-backup`'s side. Because reuse is decided purely by part name + fingerprint:

* A data part that ClickHouse has **not** rewritten since the upgrade keeps the same name and the same file
  contents, so its fingerprint still matches the base and it is **reused** (not uploaded again).
* A part that ClickHouse **did** rewrite after the upgrade — through a background merge, a mutation, or because
  the on-disk part was rebuilt — gets a **new name** (or a changed fingerprint) and is therefore **uploaded as
  new data**, which is the correct behavior. This is the same rule that applies within a single version.
* Restore/download walks the chain and reassembles parts regardless of which version created each one.

This relies on one ClickHouse property that this plan treats as an assumption to verify rather than a
guarantee. ClickHouse data parts are **immutable**, so a version upgrade does not rewrite existing parts in
place — they change only when a merge or mutation runs.

> Caveat (not relevant to modern upgrades): reuse only deduplicates when both sides expose the **same kind of
> fingerprint**. A backup made on ClickHouse **< 19.11** stores only the legacy CRC64 checksum, while **≥ 19.11**
> stores `hash_of_all_files` and does not compute the CRC64. If a chain straddles the 19.11 boundary, a
> name-matched part fails the fingerprint check and is re-uploaded — the restore is still correct, it just
> stops saving space. Any realistic multi-year upgrade (for example 24.x → 25.x) stays above 19.11, so
> both sides use `hash_of_all_files` and dedup works normally.

**Q2 — In one chain, are all parts created by the same ClickHouse version, or can they differ?**
They can differ. There is no requirement that every part in a chain comes from the same ClickHouse version.
Each part is stored or reused independently by name + fingerprint, and each backup in the chain records its own
`clickhouse_version`. A chain that spans an upgrade will legitimately contain parts created by version X (the
untouched parts carried forward from the base) alongside parts created by version Y (the new/rewritten parts in
later increments). The chain itself is only a linear list of `required_backup` links. It carries no
single ClickHouse-version constraint.