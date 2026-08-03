# QA-STP ClickHouse Backup Incremental Backups
# Software Test Plan

(c) 2026 Altinity Inc. All Rights Reserved.

**Author:** vsviderskyi

**Date:** July 20, 2026

## Table of Contents

* 1 [Introduction](#introduction)
* 2 [How Incremental Backups Work in clickhouse-backup](#how-incremental-backups-work-in-clickhouse-backup)
    * 2.1 [Which Part Types Are Supported](#which-part-types-are-supported)
    * 2.2 [Which ClickHouse Versions Are Supported](#which-clickhouse-versions-are-supported)
    * 2.3 [Backward Compatibility Across ClickHouse Versions](#backward-compatibility-across-clickhouse-versions)
    * 2.4 [Restoring Into Empty vs. Non-Empty Tables](#restoring-into-empty-vs-non-empty-tables)
    * 2.5 [Incremental Restore in a Multi-Replica Setup](#incremental-restore-in-a-multi-replica-setup)
    * 2.6 [Concurrent INSERT / ALTER During Backup and Restore](#concurrent-insert-alter-during-backup-and-restore)
    * 2.7 [Backup Storage Location and Chain Portability](#backup-storage-location-and-chain-portability)
* 3 [Testing Approach](#testing-approach)
    * 3.1 [Measuring Data Equivalence](#measuring-data-equivalence)
* 4 [Scope and Relationship to Existing Tests](#scope-and-relationship-to-existing-tests)
* 5 [Timeline](#timeline)
* 6 [Commands and Options Used](#commands-and-options-used)
* 7 [Test Environment](#test-environment)
* 8 [References](#references)
* 9 [Human Resources And Assignments](#human-resources-and-assignments)
* 10 [Release Notes](#release-notes)
* 11 [Test Scenarios](#test-scenarios)
    * 11.1 [Scenario 1: Create and restore a single incremental backup](#scenario-1-create-and-restore-a-single-incremental-backup)
    * 11.2 [Scenario 2: Incremental backup after adding new data](#scenario-2-incremental-backup-after-adding-new-data)
    * 11.3 [Scenario 3: Incremental backup when nothing changed](#scenario-3-incremental-backup-when-nothing-changed)
    * 11.4 [Scenario 4: A changed part with the same name is uploaded again](#scenario-4-a-changed-part-with-the-same-name-is-uploaded-again)
    * 11.5 [Scenario 5: Incremental backup after deleting data](#scenario-5-incremental-backup-after-deleting-data)
    * 11.6 [Scenario 6: Restore from a long chain of incremental backups](#scenario-6-restore-from-a-long-chain-of-incremental-backups)
    * 11.7 [Scenario 7: Restore an earlier backup in the chain](#scenario-7-restore-an-earlier-backup-in-the-chain)
    * 11.8 [Scenario 8: Restore reused parts from a local backup or by download](#scenario-8-restore-reused-parts-from-a-local-backup-or-by-download)
    * 11.9 [Scenario 9: Keep backups that other backups depend on](#scenario-9-keep-backups-that-other-backups-depend-on)
    * 11.10 [Scenario 10: Restore an incremental backup when its base is missing](#scenario-10-restore-an-incremental-backup-when-its-base-is-missing)
    * 11.11 [Scenario 11: Make an incremental backup self-contained (rebase)](#scenario-11-make-an-incremental-backup-self-contained-rebase)
    * 11.12 [Scenario 12: Resume an interrupted incremental upload](#scenario-12-resume-an-interrupted-incremental-upload)
    * 11.13 [Scenario 13: Incremental backups while ClickHouse merges parts](#scenario-13-incremental-backups-while-clickhouse-merges-parts)
    * 11.14 [Scenario 14: Compare local-base and remote-base incremental backups](#scenario-14-compare-local-base-and-remote-base-incremental-backups)
    * 11.15 [Scenario 15: Incremental backup of tables on S3 object disks](#scenario-15-incremental-backup-of-tables-on-s3-object-disks)
    * 11.16 [Scenario 16: Incremental backup using the embedded BACKUP engine](#scenario-16-incremental-backup-using-the-embedded-backup-engine)
    * 11.17 [Scenario 17: Incremental backup limited to selected partitions](#scenario-17-incremental-backup-limited-to-selected-partitions)
    * 11.18 [Scenario 18: Incremental backups on a two-node sharded cluster](#scenario-18-incremental-backups-on-a-two-node-sharded-cluster)
    * 11.19 [Scenario 19: Incremental backup with a large amount of data](#scenario-19-incremental-backup-with-a-large-amount-of-data)
    * 11.20 [Scenario 20: Automatic incremental backup chains with `watch`](#scenario-20-automatic-incremental-backup-chains-with-watch)
    * 11.21 [Scenario 21: How insert size and merges affect incremental backup size](#scenario-21-how-insert-size-and-merges-affect-incremental-backup-size)
    * 11.22 [Scenario 22: Incremental backup of Wide/Compact parts and Full/Packed storage](#scenario-22-incremental-backup-of-widecompact-parts-and-fullpacked-storage)
    * 11.23 [Scenario 23: Patch parts (lightweight UPDATE) must be materialized before backup](#scenario-23-patch-parts-lightweight-update-must-be-materialized-before-backup)
    * 11.24 [Scenario 24: Incremental backup across a ClickHouse version upgrade](#scenario-24-incremental-backup-across-a-clickhouse-version-upgrade)
    * 11.25 [Scenario 25: Restore an incremental backup into a table that already has data](#scenario-25-restore-an-incremental-backup-into-a-table-that-already-has-data)
    * 11.26 [Scenario 26: Restore an incremental backup on a multi-replica cluster](#scenario-26-restore-an-incremental-backup-on-a-multi-replica-cluster)
    * 11.27 [Scenario 27: Parallel INSERT / ALTER during incremental backup and restore](#scenario-27-parallel-insert-alter-during-incremental-backup-and-restore)
    * 11.28 [Scenario 28: Incremental chain across a changed backup storage or folder](#scenario-28-incremental-chain-across-a-changed-backup-storage-or-folder)

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
compares the ClickHouse version** that created a backup. Separately, each backup’s top-level
`backup_name/metadata.json` stores one server-wide field `clickhouse_version` — the
`VERSION_DESCRIBE` of the ClickHouse that ran that create (`BackupMetadata.ClickHouseVersion` in
`pkg/metadata/backup_metadata.go`, set by `pkg/backup/create.go`). It is **not** recorded per part. The
field is informational only: nothing in create, upload, download, or restore reads it back to gate reuse or
restore.

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
Parts are not tagged with a version; reuse is only by name + fingerprint. What *is* recorded is one
`clickhouse_version` **per backup** (in that backup’s `metadata.json`), so after an upgrade the base may show
X and a later increment Y. A chain that spans an upgrade will still contain parts that first appeared under X
(reused from the base) alongside parts first uploaded under Y. The chain itself is only a linear list of
`required_backup` links and carries no single-version constraint.

### Restoring Into Empty vs. Non-Empty Tables

Restoring an incremental backup is not limited to an empty table, and the target table/database may already
exist. The important question — *does it delete the existing table first or attach on top of it?* — has a
different answer depending on **which restore mode** is used, because the schema and data steps are controlled independently.

**It is identical when restoring full or incremental backup.** An incremental backup is only "incremental" in its *data*. 
Every backup, full or incremental, stores the complete
table schema (the `CREATE` statement) in its own per-table metadata (`Query` in
`backup_name/metadata/<db>/<table>.json`, refer to `pkg/metadata/table_metadata.go`). When you restore an increment,
`clickhouse-backup` reads **that increment's own metadata**, so the schema is self-contained and the base
backup is not needed to (re)create the table. The only part of restore that walks the chain is the *data* step,
which resolves the parts marked `required` from the base backups (`pkg/backup/download.go` recursive
`Download` of `RequiredBackup`; `prepareRequiredPartsForRestore`). Concretely, restoring an increment does the
same schema drop/recreate as restoring a full backup, then attaches its own new parts plus the `required` parts
pulled from the chain.

**Default restore parameters.** The three flags that decide the behavior all default to **false**, and this is
identical for full and incremental backups (`cmd/clickhouse-backup/main.go`, `restore` command — *"Create
schema and restore data from backup"*):

| Flag | Default | Meaning at the default |
| ---- | ------- | ---------------------- |
| `-s`, `--schema` (schema-only) | `false` | not schema-only → the **data** step also runs |
| `-d`, `--data` (data-only) | `false` | not data-only → the **schema** step also runs |
| `--rm`, `--drop` (force drop) | `false` | drop is not *forced by this flag*… |

Because both `--schema` and `--data` default to false, a bare `clickhouse-backup restore <backup>` runs
**schema *and* data**. Also because the schema step runs, `RestoreSchema` calls `dropExistsTables`
**unconditionally** — so by default  the table is **dropped and recreated**. You do **not** need `--rm` to
get a clean, non-duplicating restore; `--rm`/`--drop` only matters when you also pass `--data` (it forces a drop
on the otherwise-additive data-only path). The only way to *skip* the drop is to explicitly ask for data-only
with `--data` parameter. All of this applies **unchanged** to an incremental backup. The flags mean exactly what they mean
for a full backup.

**How schema restore treats an existing table.** 

Whenever `clickhouse-backup` restores *schema* (for either a
full or an incremental backup), it first **drops the existing object** and recreates it: `RestoreSchema` calls
`dropExistsTables` unconditionally, which issues `DROP TABLE IF EXISTS` (or `DETACH` when
`--restore-schema-as-attach` is used) before the `CREATE` (`pkg/backup/restore.go` `RestoreSchema` →
`dropExistsTables`; `pkg/clickhouse/clickhouse.go` `DropOrDetachTable`). So schema restore is destructive by
design — it deletes and re-creates. It does not merge into the existing definition.

**How data restore treats existing data.** 

Data restore copies the backup's parts (including the `required`
parts collected from the rest of the chain) into the table's `detached/` directory and runs
`ALTER TABLE ... ATTACH PART` for each one; this step **never** truncates and is purely **additive**.
Incremental restore uses the **same** attach step as a full restore.

Putting the two together gives the actual behavior per restore mode (this is the answer for non-empty targets):

| Restore mode | Schema step runs? | Existing table deleted first? | Data outcome on a populated target |
| ------------ | ----------------- | ----------------------------- | ---------------------------------- |
| `restore` (default: schema **and** data) | Yes | **Yes — `DROP TABLE IF EXISTS` then recreate** | Table is emptied by the drop, then parts attached → faithful copy, **no duplication** |
| `restore --schema` | Yes | **Yes** | Schema replaced; no data restored |
| `restore --data` (data-only) | **No** | **No** | Parts attached **on top of** existing rows → overlapping data becomes **duplicated rows** |
| `restore --data --partitions=...` | No | Per-partition: `DROP PARTITION` for the targeted partitions | Targeted partitions **replaced** cleanly; other partitions left untouched (`dropExistPartitions`, [#756](https://github.com/Altinity/clickhouse-backup/issues/756)) |
| `restore --rm` / `--drop` | Yes | **Yes (explicit)** | Same as default; also forces the drop when combined with `--data` |
| Embedded (`use_embedded_backup_restore: true`) | depends on flags as above | schema step drops; data step uses native `RESTORE ... SETTINGS allow_non_empty_tables=1` for `--data` | `allow_non_empty_tables=1` lets ClickHouse restore into a non-empty table; the additive caveat applies to this command |

So the short answer: a **default full restore deletes the existing table first and recreates it** (no
duplication); a **data-only (`--data`) restore does not delete anything and attaches on top**, which is the one
mode where a non-empty target can produce wrong data. A safety check can additionally abort a schema-dropping
restore and require `--rm`/`--drop` when the target tables already contain rows and `restore_schema_on_cluster`
is configured.

**The three situations spelled out.** To remove any ambiguity, here is exactly what happens to the target in
each situation, for the two restore modes that matter (a plain `restore`, which restores schema **and** data,
versus a data-only `restore --data`):

* **1. Target table does not exist, or exists but is empty.** Both modes give a faithful copy. A plain `restore`
  drops (a no-op when the table is absent or empty) and recreates from the backup schema, then attaches the
  parts; `restore --data` simply attaches into the empty table. There are no pre-existing rows, so nothing can
  be duplicated.
* **2. Target table already contains data.**
  * *Plain `restore` (schema + data), `--schema`, or `--rm`/`--drop`:* the table is **dropped**
    (`DROP TABLE IF EXISTS`) and recreated, so the pre-existing rows are **destroyed** and replaced by the
    backup's data — a faithful copy with no duplication. Be aware this silently discards whatever the target
    held before.
  * *`restore --data` (data-only):* the table is **not** dropped; the backup's parts are attached on top, so the
    pre-existing rows stay and any overlapping data becomes **duplicated rows**.
  * *`restore --data --partitions=X`:* only partition `X` is dropped and replaced; other partitions are left
    intact.
* **3. Target table has a *different schema* than the backup.**
  * *Plain `restore` / `--schema` / `--rm`:* the existing table is dropped **by name, regardless of its current
    structure**, and recreated from the backup's `CREATE` statement. The target's different schema **and** its
    data are gone; you end up with the backup's schema and data. `clickhouse-backup` does **not** compare or
    merge the two definitions — it replaces, so a differently-defined target table is silently destroyed.
  * *`restore --data` (data-only):* the schema is **not** touched, so the table keeps its different structure.
    ClickHouse then validates each backup part against that structure during `ALTER TABLE ... ATTACH PART`. If
    the structures are **incompatible** (differing columns, types, sorting/partition key), ClickHouse rejects
    the attach and the **restore fails with an error**; if they happen to be **compatible**, the parts attach
    (subject to the duplication caveat above). `clickhouse-backup` does not reconcile the schema difference in
    this mode — it relies on ClickHouse's part-vs-table validation.
  * *A `PARTITION BY` mismatch is a specific, important case of the above.* Every data part carries a
    `partition_id` that ClickHouse **computes from the table's partition expression** at the time the part was
    written, and it is stored with the part (the part directory is named `<partition_id>_<min>_<max>_<level>`).
    On `ATTACH PART`, ClickHouse re-derives the `partition_id` from the **target table's** current `PARTITION BY`
    and requires it to match the part being attached. So a data-only restore of parts produced under the
    backup's `PARTITION BY` into a table defined with a **different** `PARTITION BY` is rejected — the attach
    fails and the restore errors out; `clickhouse-backup` does not (and cannot safely) re-partition the data. In
    every other restore mode this cannot happen, because the schema step recreates the table from the backup's
    own `CREATE`, so the partition expression always matches.

**Where this can cause incorrect data (but not corruption).** The risk exists **only on the `--data`
(data-only) path**, and it is always *logical* — extra or double-counted rows — never damaged part files:

* **Duplicated rows** on plain `MergeTree` when `--data` attaches parts over overlapping existing rows.
  ClickHouse assigns fresh block numbers to attached detached parts, so nothing is overwritten and no error is
  raised; `clickhouse-backup` does not detect or warn about the resulting duplicates.
* **Misleading results on deduplicating/aggregating engines.** `ReplacingMergeTree`, `SummingMergeTree`, and
  `AggregatingMergeTree` only collapse or sum duplicates during background merges. Right after an additive
  `--data` restore the table can show doubled counts or sums until a merge (or a `FINAL` query) runs, so a
  row-count check taken too early can both hide a real problem or flag a non-problem.
* **Partial-partition surprises.** Restoring an increment scoped with `--partitions` onto a table that has
  other partitions replaces only the named partitions; the rest of the table is intentionally left as-is. That
  is correct behavior but must be accounted for when comparing against a full-table expectation.

The safe rules of thumb, which the scenarios below encode: a plain `restore` (or `--rm`) gives a faithful copy
because it drops and recreates the table; use `--data --partitions=...` to **replace** specific partitions; and
only use a bare `--data` restore onto a populated table when you deliberately want to merge datasets and can
tolerate (or later deduplicate) overlaps.


### Incremental Restore in a Multi-Replica Setup

`clickhouse-backup` is a **per-node** tool: each invocation talks to one ClickHouse server and only reads/writes
that node's local data. It does **not** do a restore out to every replica by itself. In a cluster of
`Replicated*` tables the missing piece is filled by ClickHouse's own replication: parts attached on one
replica are propagated to the other replicas of the same shard through Keeper/ZooKeeper. This is also true for full
backups and, in exactly the same way, for **incremental** backups — the incremental nature only affects which
parts get attached on the one node that runs the data restore, not how they spread to the sibling nodes.

**Do we restore on one replica or all of them? Split the answer into schema and data.** The recommended and
tested pattern (see the Kubernetes restore `Job` and the sharded-cluster recipe in `Examples.md`) restores
**schema on every replica** but **data on only the first replica of each shard**:

| Restore step | Where to run it | Why |
| ------------ | --------------- | --- |
| **Schema** (`restore_remote --schema --rm`) | **Every replica** in each shard (or once with `restore_schema_on_cluster` / `ON CLUSTER`) | Each replica must have its own local table definition and must register its own replica entry in Keeper before it can receive data |
| **Data** (`restore_remote --data`) | **Only the first replica** of each shard | The attached parts replicate automatically to the other replicas; running `--data` on more than one replica of the same shard attaches the same parts twice and **duplicates rows** |

**Important: the "data on only the first replica" rule applies *only* to the `Replicated*` engines family.**
It is a direct consequence of ClickHouse replication that copies parts between replicas. Plain (non-replicated) `MergeTree`-family tables has no such mechanism. The distinction is fundamental to how
data restore must be run across nodes:

| Engine family | Is data auto-propagated between nodes? | Where to run the `--data` step | Duplication risk |
| ------------- | -------------------------------------- | ------------------------------ | ---------------- |
| **`Replicated*MergeTree`** (`ReplicatedMergeTree`, `ReplicatedReplacingMergeTree`, …) | **Yes** — via Keeper/ZooKeeper | **Only the first replica** of each shard; siblings receive the data through replication | Running `--data` on more than one replica of a shard **duplicates rows** |
| **Plain `MergeTree` family** (`MergeTree`, `ReplacingMergeTree`, `SummingMergeTree`, `AggregatingMergeTree` and so on, *without* the `Replicated` prefix) | **No** — each node is an independent local table | **Every node** that must hold the data (there are no "replicas" to propagate to) | No cross-node duplication from replication (nothing propagates).|

So for a plain `MergeTree` table there is no such thing as "restore on the first replica and let it spread". It won't be copied. If two nodes each carry a plain `MergeTree` table of the same name, they are **independent**
tables; restoring data on node A leaves node B empty. To end up with the same data on several nodes you must run
the **data** restore on **each** of them (and each node needs the backup. For an increment, the full
chain is available locally). In practice a plain `MergeTree` in a multi-node cluster is normally used **per
shard behind a `Distributed` table** (each node holds a *different* slice of the data). So you back up and
restore each node independently rather than relying on any propagation. The schema step is the same in both
cases — it always runs on every node.

**What is incremental-specific here.** When you run the `--data` step on the first replica, `clickhouse-backup`
attaches that increment's **own** parts **plus** the `required` parts it pulls from the base backups in the
chain (`prepareRequiredPartsForRestore`; recursive `Download` of `RequiredBackup`). ClickHouse replication then
copies the **resulting final parts**. Base-chain parts and increment parts are alike to the sibling replicas.
The consequence worth testing: **only the one replica that runs `--data` needs access to the full backup chain**
(and needs the base backups downloaded locally). The other replicas reconstruct the same data purely through
native replication and never reads the increment or the base backups at all. So an incremental restore in a
multi-replica cluster is still a **single-node data restore per shard** followed by replication.

**Safety knobs that matter for a multi-replica incremental restore:**

* `check_replicas_before_attach: true` (default) makes a node wait/skip when a sibling is already attaching the
  same parts, guarding against the accidental double-`--data` duplication described above.
* `sync_replicated_tables: true` (default) issues `SYSTEM SYNC REPLICA` so a node has caught up before it
  participates, which matters when the base and the increment were taken at different times.
* `default_replica_path` / `default_replica_name` and the opt-in `rebind_replica_path_if_exists` resolve
  Keeper replica-path conflicts when a table with the same path already exists. **`rebind_replica_path_if_exists`
  must stay `false` during a concurrent multi-replica restore**, otherwise rebinding a path held by a live
  sibling causes incorrect restore (see, [#1428](https://github.com/Altinity/clickhouse-backup/issues/1428)).
* `--replicated-copy-to-detached` copies parts into `detached/` but **skips** the `ATTACH PART`, which is used
  when you want replication (or a later manual attach) to bring the data in rather than attaching directly.

**Backup creation on replicated clusters.** The same "first replica per shard" rule applies when *creating* the
incremental backup: run `create` / `create_remote --diff-from-remote=...` on one replica per shard, so the
increment is computed once per shard against that shard's base backup. Backing up every replica would produce
redundant copies of the same replicated data.


### Concurrent INSERT / ALTER During Backup and Restore

Another question is illustrated by the `partA + partB → partC` merge in the request - is what an incremental
backup captures when the table keeps changing **while** the backup (or restore) is running. `clickhouse-backup`
does **not** stop merges or makes a table lock. It relies on ClickHouse's
part-level atomicity instead. The behavior is the same for full and incremental backups. 

But it is called out here because the *deduplication* outcome (reuse vs. re-upload) is what makes it visible on an increment.

**During backup creation.** `clickhouse-backup` runs `ALTER TABLE ... FREEZE`, which tells ClickHouse to
hard-link a copy of every **active** part (live data that is already finished — not `tmp_...` in-progress
parts, not `detached/`). That FREEZE moment is the cutoff: only those parts enter the backup. Concurrent
work is judged only by whether it finished **before** or **after** that moment:

* **Parallel INSERT.** A part is either *active* at freeze time (fully included) or it is not (fully excluded) —
  parts are atomic, so there are never half-written parts in a backup. Rows inserted **before** freeze are in
  the backup; rows inserted **after** are not. For an increment this simply means the just-inserted new parts
  are uploaded as new data. Unchanged older parts are still reused from the base.
* **Parallel merge (the `partC` case).** A merge that combines `partA + partB` into `partC` does not lose or
  change rows — but it produces a **new part with a new name and a new `hash_of_all_files`**. What the increment
  stores depends on whether that merge finished before the increment's freeze:
  * *Merge completed before freeze:* the active set is `{partC}`. `partC` is **not** present in the base (the
    base had `partA`, `partB`), so the increment cannot deduplicate it and **uploads `partC` as new data** —
    re-storing rows that the base already held inside `partA + partB`. The restore is still correct (`partC`
    contains all the rows). But the increment is larger than a "pure delta".
  * *Merge not yet completed at freeze:* the active set is still `{partA, partB}`, both are found in the base by
    name + fingerprint, so the increment **reuses** them and uploads only changed data.
  * Either way there is **no data loss or corruption** — only a difference in **how much the increment re-uploads**.
* **Parallel ALTER.**
  * *Metadata-only `ALTER`* (TTL, comment, index add) is fast and rewrites no parts. The schema captured for the
    backup is whatever `SHOW CREATE TABLE` returned when the table list was built.
  * *Data-rewriting `ALTER` / mutation* (`ALTER ... UPDATE`/`DELETE`, `MODIFY COLUMN` that rewrites) produces
    **new mutation parts** with new names. They then become the active parts the freeze captures — so the
    increment re-uploads them as new data. In-progress mutations are recorded once per backup
    (`GetInProgressMutationsBatch` over `system.mutations`, gated by `backup_mutations`) so a restore can carry
    pending mutations forward (restore side needs `restore_as_attach`).
  * *Column-type races are actively guarded.* If a concurrent `ALTER ... MODIFY COLUMN` leaves the active parts
    with **inconsistent column types** (some parts old type, some new) mid-backup, the default
    `check_parts_columns: true`  **aborts** backup with *"inconsistent data types for active data part"*
    (`CheckSystemPartsColumnsForTables`, `pkg/clickhouse/clickhouse.go`) rather than produces a corrupt backup.
    It is true unless you explicitly pass `--skip-check-parts-columns`.
  * *CREATE/DROP races* are tolerated: `ignore_not_exists_error_during_freeze: true` (default) ignores
    ClickHouse error codes 60/81 so frequent CREATE/DROP of tables during a backup does not fail the whole run.
* **No cross-table instant.** FREEZE is atomic **per table**, so a multi-table backup is a set of per-table
  snapshots taken at slightly different moments, not one cluster-wide instant. For a single table the snapshot
  is consistent; across tables.

**During restore.** Restore is **not** designed to run against a table that is being written concurrently. The
intended pattern is to restore into a inactive target. A default `restore` gives that by dropping and recreating
the table first.

* *Default `restore` (schema + data)* drops and recreates the table, then attaches parts. A client that writes
  to the table during the drop/recreate window can see *"table doesn't exist"* or lose those writes when the
  table is replaced. This is expected and is why restores should run during a maintenance window.
* *`restore --data`* is additive, so a concurrent `INSERT` blended with `ATTACH PART` produces **duplicated
  / overlapping rows** (never corruption — block numbers keep parts valid). The same additive caveat as a
  non-empty target.
* *Replicated tables* rely on `check_replicas_before_attach: true` to avoid two nodes attaching the same parts
  at once, and `restore_as_attach: true` to restore tables that have in-progress mutations or an inconsistent
  part structure.

The practical rule the scenarios encode: incremental **backup** creation is safe to run against a live,
writing table (FREEZE gives a consistent per-table snapshot; concurrent merges only affect dedup efficiency,
and a racing column-type ALTER aborts the backup rather than corrupting it), but **restore** should target a
locked table.


### Backup Storage Location and Chain Portability

An incremental backup depends on its base, so a question is *where* that dependency is resolved. Should
the whole chain live in one place, and what breaks if you change the storage type or the folder? The design is simple.

**How the dependency is recorded — by name only.** When an increment is uploaded with `--diff-from-remote=<base>`,
`clickhouse-backup` stores the base **name** in the increment's `metadata.json` as `required_backup: <base>`
(`pkg/backup/upload.go`; `backupMetadata.RequiredBackup = diffFromRemote`). It stores **no** endpoint, no
storage type, no bucket, and no path — just the name. On restore/download the tool resolves that name
**recursively in the currently configured remote storage** (`pkg/backup/download.go` recursive `Download` of
`remoteBackup.RequiredBackup` → `ReadBackupMetadataRemote`). If the name is not found there it aborts with
`'<name>' is not found on remote storage`.

**Consequence: the entire chain must live in one remote storage, under one path.** Because resolution is
name-based against the single configured remote, the base and **every** increment in the chain must be
co-located in the **same** `remote_storage` (same type), the **same** bucket/host, and the **same** `path`
prefix. You **cannot** split a chain. For example keep the base in one bucket and push increments to another is not a good approach
because both the increment *upload* (which reads the base via `getTablesDiffFromRemote`) and the later
*download* read only from the one configured remote.

**What happens when you change the storage type or folder:**

| Change you make | Effect on the chain | What to do |
| --------------- | ------------------- | ---------- |
| Point config at a **different / empty** storage, or a different `remote_storage` **type** | Increments can't find their base by name → download/restore fails with `'<base>' is not found on remote storage` | Migrate the whole chain first (below), or start a new full backup there |
| Change **`path`** (folder prefix) within the same bucket/host | Old backups still live under the **old** prefix and are invisible under the new one → same "not found" | Move/copy the whole chain to the new prefix, or start a fresh full backup under it |
| Change bucket/host/type **and copy the entire chain** preserving names and layout | **Works** — resolution is by name + directory layout, not by endpoint | Copy **all** backups of the chain (same names, same structure, **including `object_disk_path` data** for object-disk backups) |
| Keep base in storage A, write increments to storage B | **Not supported** — one remote per operation | Use **one storage per chain** |

**Object-disk backups need their data moved too.** For tables on object disks, the part data is stored under
the storage's `object_disk_path` (separate from the metadata `path`). Migrating a chain of object-disk backups
means copying **both** the backup `path` tree **and** the referenced `object_disk_path` data, and a restore into
a different account/bucket may require object-disk key rewriting — so a storage move is heavier for object-disk
chains than for regular ones.

**Safer ways to change storage.** Two tool-supported patterns avoid a fragile chain move:

* **Start a new full backup in the new location** and begin a fresh chain there. The old chain stays intact in
  the old storage for as long as you keep it for restore.
* **Rebase first** `clickhouse-backup` `rebase`, to make a chosen backup
  **self-contained** — it then carries all its parts and no longer has a `required_backup`.So that single
  backup can be copied to the new storage and restored on its own.

  ## Testing Approach

Each test follows the same simple idea:

1. Put a table into a known state and make a full backup (call it the **base state**).
2. Change the data in a controlled way and make an incremental backup (**state 1**, **state 2**, and so on for
   longer chains).
3. Restore the latest backup onto an empty table (or a clean node) and check the result.

Unless a scenario says otherwise, restore is done onto an empty target (or a clean node) so the restored data
can be compared directly against the source. Note that a plain `restore` also **drops and recreates** an
existing table before restoring, so it is faithful even against a populated target. Restoring into a table that
already has data becomes a concern only with a data-only (`--data`) restore.

For every test we confirm two things:

* **The data is correct** — after restore, the table contains exactly the same rows as the original. "Exactly
  the same" is defined precisely below in [Measuring Data Equivalence](#measuring-data-equivalence). Wherever a
  scenario says "compare rows / counts against the source", it means that check.
* **Only the changes were stored** — the incremental backup is much smaller than a full backup would be. This
  is visible from the reported backup size (for example in `clickhouse-backup list`). This matters because a
  broken incremental backup that secretly copied everything would still restore correctly, so a data check
  alone is not enough to prove the feature works.

### Measuring Data Equivalence

Restore correctness is measured using two checks that must both
match between the source table and the restored table:

1. **Row count** — `SELECT count() FROM t`.
2. **Order-independent hash/count over all columns** —
   `SELECT sum(cityHash64(*)) AS h, count() AS c FROM t`.
   `cityHash64(*)` hashes **all columns** of each row.
   
    `sum(...)` is commutative, so the result does not depend
   on row or part order. Both `h` and `c` must be equal on source and target.

**Exact (deterministic) alternative for small tables.** When an ordered, row-by-row comparison is wanted,
we can select every column ordered by a full key and compare position by position:
`SELECT * FROM t ORDER BY <all columns that make the row unique>`. This is what the project's integration tests do — `checkData` in `test/integration/utils.go` runs `SELECT * ... ORDER BY <orderBy>` and
asserts every column of every row plus the total row count. It is precise but memory-heavy, so it is best for
small fixtures; the aggregate fingerprint above is the scalable default for large tables.

**Conditions to control before comparing:**

* **Schema/column order must match.** `cityHash64(*)` depends on column order and types. A default `restore`
  recreates the table from the backup's `CREATE`, so this is guaranteed; if you compare against an
  independently-defined table, align the column list explicitly.
* **Deduplicating / aggregating engines.** On `ReplacingMergeTree`, `SummingMergeTree`, and
  `AggregatingMergeTree` the visible rows depend on background merges, so take the fingerprint with `FINAL`
  (`SELECT sum(cityHash64(*)) FROM t FINAL`) or after `OPTIMIZE TABLE t FINAL`, on **both** sides.
* **`AggregateFunction` state columns** are compared via `finalizeAggregation(col)` (or after `FINAL`) rather
  than hashing the raw state bytes, which are not guaranteed to be byte-identical.
* **Floats / `DateTime` / `Nullable` / `LowCardinality`** hash by value and compare correctly. No special
  handling is needed beyond the engine and schema limitations above.

**A note on background merges.** ClickHouse merges parts in the background, which renames and rewrites them.
This can change which parts count as "new" between two backups and is the main cause of unstable test results.
Tests that need parts to stay stable stop merges (`SYSTEM STOP MERGES`) or insert into separate partitions so
the changed parts are predictable. One test has to be done to deliberately run *with* merges to check behavior under that
condition.

## Scope and Relationship to Existing Tests

The internal mechanics of incremental backups (how parts are marked as reused, checksums, object-disk copying,
rebase internals) are already exercised by the Go integration tests under `test/integration/`. Integration tests run
against a single ClickHouse node and inspect backup metadata directly.

This plan focuses on **end-to-end, user-visible behavior** and on **multi-node scenarios** that the
single-node integration tests cannot cover. If a scenario overlaps with existing integration coverage, this should be noted so the same thing is not tested twice for no reason.

## Timeline

The testing of `clickhouse-backup` incremental backups SHALL be started on July 20, 2026.

## Commands and Options Used

Incremental backups are selected with one of two options that name the base backup:

| Option | Where the base backup lives | Commands that accept it |
| ------ | --------------------------- | ----------------------- |
| `--diff-from-remote=<name>` | On remote storage (S3, FTP, etc.) | `create`, `create_remote`, `upload`, and the `watch` command |
| `--diff-from=<name>` | In local backups only | `create_remote`, `upload` (not plain `create`) |

Other relevant options and settings:

| Name | Purpose |
| ---- | ------- |
| `upload_by_part` (config) | Must be `true` to use `--diff-from-remote`; it is `true` by default |
| `backups_to_keep_remote` (config) | How many remote backups to keep; must not delete backups still needed by a chain |
| `--partitions` | Limit a backup or restore to selected partitions |
| `rebase` (command) | Turn an incremental backup into a self-contained one |
| `use_embedded_backup_restore` (config) | Use ClickHouse's built-in BACKUP engine instead of the file-level approach |

## Test Environment

The tests use the standard TestFlows environment for `clickhouse-backup`, which starts:

* Two ClickHouse nodes, `clickhouse1` and `clickhouse2`, and a ZooKeeper node for coordination.
* A `clickhouse_backup` container running the `clickhouse-backup` binary under test.
* Remote storage backends available in the environment (for example MinIO for S3, plus FTP and SFTP servers).
* Extra nodes may be added if decided so later.

**ClickHouse versions under test.** Run the plan against the versions listed in
[Which ClickHouse Versions Are Supported](#which-clickhouse-versions-are-supported). The Testflows CI matrix is
`22.3`, `22.8`, `23.3`, `23.8`, `24.3`, `24.8`, `25.3`, `25.8`, `26.3`, selected with `CLICKHOUSE_VERSION`.
Version-specific scenarios are gated as described there (embedded mode 22.7+, the object-disk type, and patch parts 25.7+). The default image when `CLICKHOUSE_VERSION` is unset is `26.3`.

Most single-node scenarios run on `clickhouse1`. Scenarios that need more than one node (for example the
sharded-cluster scenario) use both `clickhouse1` and `clickhouse2` via the predefined `sharded_cluster`
configuration. To confirm a restore is complete, tests drop the table (or use a clean node) and restore into an empty target.

## References

The following stable sources describe the behavior tested here:

* clickhouse-backup project and documentation: <https://github.com/Altinity/clickhouse-backup>
* How incremental backups work with remote storage:
  <https://github.com/Altinity/clickhouse-backup/blob/master/Examples.md#how-incremental-backups-work-with-remote-storage>
* Supported ClickHouse versions (Limitations) and embedded mode (22.7+):
  <https://github.com/Altinity/clickhouse-backup/blob/master/ReadMe.md#limitations>
* Commands and options (`create`, `create_remote`, `upload`, `restore`, `rebase`, `watch`, `--diff-from`,
  `--diff-from-remote`, `--partitions`): <https://github.com/Altinity/clickhouse-backup/blob/master/Manual.md>
* Changelog (embedded BACKUP/RESTORE 22.7+, hard-linked freeze 21.3+):
  <https://github.com/Altinity/clickhouse-backup/blob/master/ChangeLog.md>
* ClickHouse version matrix used by CI: `.github/workflows/build.yaml` (Testflows and integration jobs)

## Human Resources And Assignments

The following team members SHALL be dedicated to this effort:

* Vitalii Sviderskyi (regression tests)
* Vitaliy Zakaznikov (manager, regression tests)
* Eugene Klimov (clickhouse-backup development)

## Release Notes

* https://github.com/Altinity/clickhouse-backup/blob/master/ChangeLog.md
* https://github.com/Altinity/clickhouse-backup/blob/master/Examples.md

---

## Test Scenarios

### Scenario 1: Create and restore a single incremental backup

**Goal:** Confirm the basic flow works: a full backup, then one incremental backup, then a correct restore.

**Steps:**

1. Create a partitioned `MergeTree` table on a single node cluster and insert a first batch of data.
2. Make a full remote backup: `create_remote base_backup`.
3. Insert a second batch of data into a new partition.
4. Make an incremental backup: `create_remote --diff-from-remote=base_backup inc_backup`.
5. Drop the table and restore the incremental backup: `restore_remote inc_backup`.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Data after restore | The table contains both batches of data, identical to before the drop |
| Backup size | `inc_backup` is much smaller than `base_backup` (only the new data was stored) |


### Scenario 2: Incremental backup after adding new data

**Goal:** Confirm that when only new data is added, the unchanged data is reused (deduplicated) and not stored
again.

**Steps:**

1. Full backup of a table with data in partition A.
2. Stop merges, then insert new data only into a new partition B.
3. `create_remote --diff-from-remote=<full> inc_backup`.
4. Restore `inc_backup` onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Data after restore | Both partitions A and B are present and correct |
| Backup size | The incremental backup size corresponds roughly to partition B only |

### Scenario 3: Incremental backup when nothing changed

**Goal:** Confirm that an incremental backup taken with no data changes stores almost nothing but still
restores the full data.

**Steps:**

1. Full backup of a table; stop merges so parts stay identical.
2. Without changing any data, `create_remote --diff-from-remote=<full> inc_backup`.
3. Restore `inc_backup` onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Backup size | The incremental backup is nearly empty (only bookkeeping, no data) |
| Data after restore | The table matches the original data exactly |

### Scenario 4: A changed part with the same name is uploaded again

**Goal:** Confirm the most important safety rule — if a part changed but happens to have the same name as one
in the base backup, its new content is uploaded, not skipped. (clickhouse-backup detects this by comparing the
part's file checksums, not just its name.)

**Steps:**

1. Full backup of a table with data in one partition.
2. Force that data to be rewritten while keeping the same partition, for example `OPTIMIZE TABLE ... FINAL` or a
   data mutation.
3. `create_remote --diff-from-remote=<full> inc_backup`.
4. Restore `inc_backup` onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Data after restore | The restored data reflects the rewritten content, not the old content |
| Backup content | The changed data was actually stored in the incremental backup (it was not skipped as "unchanged") |

### Scenario 5: Incremental backup after deleting data

**Goal:** Confirm that deleting data (dropping a partition) before an incremental backup is reflected on
restore.

**Steps:**

1. Full backup of a table with partitions A and B.
2. `ALTER TABLE ... DROP PARTITION B`.
3. `create_remote --diff-from-remote=<full> inc_backup`.
4. Restore `inc_backup` onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Data after restore | Only partition A is present; partition B is gone |

### Scenario 6: Restore from a long chain of incremental backups

**Goal:** Confirm that a chain of several incremental backups can be restored correctly, collecting reused
parts from all earlier backups in the chain.

**Steps:**

1. Full backup (`base_backup`).
2. Add data and make `inc1` based on `base_backup`.
3. Add more data and make `inc2` based on `inc1`.
4. Add more data and make `inc3` based on `inc2`.
5. Restore the newest backup `inc3` onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Data after restore | The table contains all data added across the full backup and every increment |
| Chain used | The restore automatically pulls the reused parts from the earlier backups in the chain |

### Scenario 7: Restore an earlier backup in the chain

**Goal:** Confirm that restoring a backup from the middle of a chain gives the data as it was at that point in
time, not the latest data.

**Steps:**

1. Build the chain `base_backup` → `inc1` → `inc2` → `inc3` as in Scenario 6.
2. Restore `inc1` (not the newest) onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Data after restore | The table matches the data as it was when `inc1` was taken (not `inc2` or `inc3`) |

### Scenario 8: Restore reused parts from a local backup or by download

**Goal:** Confirm that restore works whether the base backup's data is already present locally or has to be
downloaded from remote storage.

**Steps:**

1. Build `base_backup` → `inc1` on remote storage.
2. Case A: keep `base_backup` present locally, then restore `inc1` (reused parts come from the local base).
3. Case B: with no local backups present, restore `inc1` (reused parts are downloaded from remote).

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Case A | Restore succeeds; data matches the original |
| Case B | Restore succeeds; data matches the original |
| Both cases | Produce identical restored data |

### Scenario 9: Keep backups that other backups depend on

**Goal:** Verify retention never deletes a backup that a later incremental backup still depends on.

**Steps:**

1. Build a chain of 5 backups using `--diff-from-remote`.
2. Set `backups_to_keep_remote = 3` and run the remote cleanup.
3. Download and restore the latest backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Backups kept | Older backups that the kept chain still needs are NOT deleted, even though the limit is 3 |
| Data after restore | The latest backup restores correctly using its full chain |

### Scenario 10: Restore an incremental backup when its base is missing

**Goal:** Confirm that if the base backup is missing, restoring the incremental backup fails clearly instead of
producing wrong or partial data.

**Steps:**

1. Build `base_backup` → `inc1`.
2. Delete `base_backup` from remote storage so the chain is broken.
3. Attempt to restore `inc1`.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Result | The restore fails with a clear error that names the missing base backup |
| No bad data | No half-restored or corrupted table is left behind; the tool does not crash |

### Scenario 11: Make an incremental backup self-contained (rebase)

**Goal:** Confirm the `rebase` command turns an incremental backup into a complete, standalone backup so it no
longer needs its earlier backups. ("Rebase" here means copying the reused parts into the incremental backup
itself.)

**Steps:**

1. Build `base_backup` → `inc1` → `inc2`.
2. Run `rebase inc2`.
3. Delete the earlier backups `base_backup` and `inc1`.
4. Restore `inc2` onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| After rebase | `inc2` no longer depends on any earlier backup |
| Data after restore | `inc2` restores correctly even though its earlier backups were deleted |

### Scenario 12: Resume an interrupted incremental upload

**Goal:** Confirm that an incremental backup upload that is interrupted can resume and finish correctly without
re-uploading data it already handled.

**Steps:**

1. Full backup, then add new data.
2. Start uploading the incremental backup and interrupt it partway (stop/restart the process).
3. Run the same upload again to let it resume.
4. Restore the finished incremental backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Resume | The second run completes the backup without errors |
| No repeated work | Data already uploaded (and reused parts) are not uploaded again |
| Data after restore | The restored data matches the original |

### Scenario 13: Incremental backups while ClickHouse merges parts

**Goal:** Confirm the chain still restores correctly when ClickHouse merges parts in the background between
backups (merges rename and rewrite parts).

**Steps:**

1. Full backup with merges enabled.
2. Allow or trigger background merges, add some new data, then make an incremental backup.
3. Restore the incremental backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Data after restore | The restored data matches the original despite the merges |

### Scenario 14: Compare local-base and remote-base incremental backups

**Goal:** Confirm that both ways of choosing a base backup — a local base (`--diff-from`) and a remote base
(`--diff-from-remote`) — produce the same result for the same data changes.

**Steps:**

1. Make the same data change on top of the same full backup twice, once for each option:
   * with `--diff-from-remote=<full>`;
   * with `--diff-from=<full>` (keeping the full backup available locally).
2. Restore each incremental backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Both options | Store only the changed data and restore correctly |
| Comparison | Both restored tables contain identical data |

### Scenario 15: Incremental backup of tables on S3 object disks

**Goal:** Confirm incremental backups work for tables whose data is stored on an S3 object disk inside
ClickHouse, reusing unchanged data instead of copying it again.

> Applies to ClickHouse versions that support the object-disk type under test (S3 21.8+, GCS over S3 22.6+,
> Azure Blob 23.3+). See
> [Which ClickHouse Versions Are Supported](#which-clickhouse-versions-are-supported).

> Note: This overlaps with existing Go integration coverage of object-disk incremental backups; it is included
> here for the end-to-end / multi-node environment.

**Steps:**

1. Create a table that uses an S3 object-disk storage policy; make a full remote backup.
2. Add new data, then make an incremental backup with `--diff-from-remote`.
3. Restore the incremental backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Reuse | Unchanged object-disk data is reused, not copied again (incremental backup stays small) |
| Data after restore | The restored data matches the original |

### Scenario 16: Incremental backup using the embedded BACKUP engine

**Goal:** Confirm incremental backups work when `clickhouse-backup` uses ClickHouse's built-in `BACKUP`
command (the "embedded" mode, enabled by `use_embedded_backup_restore: true`). In this mode ClickHouse itself
computes the difference against the base backup.

> Applies to ClickHouse 22.7+ (`use_embedded_backup_restore: true` and native
> `BACKUP ... SETTINGS base_backup=...`), so skip it on older versions in the matrix such as `22.3`. See
> [Which ClickHouse Versions Are Supported](#which-clickhouse-versions-are-supported).

**Steps:**

1. With `use_embedded_backup_restore: true`, make a full remote backup.
2. Add data, then make an incremental backup with `--diff-from-remote`.
3. Restore the incremental backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Reuse | The incremental backup stores only the changes relative to the base |
| Data after restore | The restored data matches the original |

> Note: Embedded mode does not support sharded-operation mode, so this scenario is single-node only.

### Scenario 17: Incremental backup limited to selected partitions

**Goal:** Confirm that `--partitions` can limit an incremental backup to specific partitions and still restore
those partitions correctly.

**Steps:**

1. Create a table with partitions A, B, and C; make a full backup.
2. Add data to B and C, then make an incremental backup with `--diff-from-remote` and `--partitions=B,C`.
3. Restore the selected partitions onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Scope | Only partitions B and C are included in the incremental backup |
| Data after restore | The restored data for B and C matches the original |

### Scenario 18: Incremental backups on a two-node sharded cluster

**Goal:** Confirm per-node incremental backups work on the two-node sharded cluster and restore correctly.
(The environment provides a 2-shard cluster: `clickhouse1` and `clickhouse2`, one shard each.)

**Steps:**

1. Using the `sharded_cluster` configuration, create a table on both nodes and insert different data on each
   shard.
2. On each node, make a full backup, then add data and make an incremental backup with `--diff-from-remote`,
   using a per-node backup name.
3. On a clean cluster, restore the schema on both nodes, then restore the data on each node from its own
   incremental backup.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Per-node chains | Each node has its own full → incremental chain |
| Data after restore | The combined data across both shards matches the original cluster data |

### Scenario 19: Incremental backup with a large amount of data

**Goal:** Confirm incremental backups stay correct and efficient when the table holds a large amount of data,
so that only the changed portion is transferred.

**Steps:**

1. Launch the test environment and load a large but practical dataset into a table (for example tens of
   millions of rows across many partitions — large enough to be meaningful, small enough to finish in CI).
   Record the table size from `system.parts`.
2. Make a full remote backup and record how long it takes and how large it is.
3. Change only a small, known part of the data (add a few new partitions and/or rewrite a couple of existing
   ones), then make an incremental backup and record its time and size.
4. Restore the latest incremental backup onto a clean node and compare the data.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Small delta | The incremental backup is far smaller and faster than the full backup, in proportion to the small change |
| Reuse | Most of the data is reused from the full backup, not copied again |
| Data after restore | Row counts and row contents match the original |
| Stability | The run completes without running out of memory or disk space |

### Scenario 20: Automatic incremental backup chains with `watch`

**Goal:** Confirm the `watch` command automatically creates a repeating pattern of full and incremental
backups and cleans up old ones correctly.

**Steps:**

1. Start `watch` with short intervals (a full backup every so often, incremental backups in between) and a
   remote-retention limit.
2. Add data between intervals so each cycle produces a new incremental backup based on the previous one.
3. After several cycles, restore the latest backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Chain built | Incremental backups are created based on the previous backup; full backups appear on schedule |
| Cleanup | Old backups that are no longer needed are removed, but backups still needed by a chain are kept |
| Data after restore | The latest backup restores correctly |

### Scenario 21: How insert size and merges affect incremental backup size

**Goal:** Confirm and document that the size of an incremental backup depends not only on how much data was
added, but also on how it was inserted and on background merges — inserting the same data in a few large
inserts (with merges quiet) produces a smaller incremental backup than many tiny inserts or heavy merges.

**Steps:**

1. Make a full backup of a baseline table.
2. Case A: add a fixed amount of new data using a few large inserts, with merges quiet.
3. Case B: add the same amount of data using many small inserts and/or by triggering merges.
4. Make an incremental backup for each case and compare their sizes.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Size difference | Case A produces a smaller incremental backup than Case B for the same amount of new data |
| Data after restore | Both cases restore to the same correct data |

### Scenario 22: Incremental backup of Wide/Compact parts and Full/Packed storage

**Goal:** Confirm incremental backup reuses and restores data correctly across all the ways ClickHouse can
physically store a part. This covers two independent settings (see
[Which Part Types Are Supported](#which-part-types-are-supported)):

* **Part type** — `Wide` (each column in its own file) or `Compact` (all columns in one file).
* **Storage format** — `Full` (each file stored individually) or `Packed` (all of a part's files bundled into a
  single archive).

`clickhouse-backup` copies a part's files regardless of these settings, so every combination should behave
identically.

**Steps:**

1. Create `MergeTree` tables that force each combination, and confirm from `system.parts` (columns `part_type`
   and `part_storage_type`):
   * **Wide**: `SETTINGS min_bytes_for_wide_part = 0, min_rows_for_wide_part = 0`.
   * **Compact**: `SETTINGS min_bytes_for_wide_part = '1G', min_rows_for_wide_part = 100000000`.
   * **Packed** storage: raise one of `min_bytes_for_full_part_storage` / `min_rows_for_full_part_storage` /
     `min_level_for_full_part_storage` above the part being written (defaults of `0` give `Full` storage), and
     confirm `part_storage_type = 'Packed'`.
   * At minimum, cover: Wide+Full, Compact+Full, and one Packed case (for example Compact+Packed).
2. Insert data into each table and make a full backup.
3. For each table: add data to a new partition, and rewrite one existing partition (for example with
   `OPTIMIZE TABLE ... FINAL`), then make an incremental backup with `--diff-from-remote`.
4. Restore each incremental backup onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Unchanged parts reused | For every combination, the unchanged parts are reused (not stored again) |
| Changed part stored again | For every combination, the rewritten partition's data is stored in the incremental backup |
| Data after restore | Every table restores to exactly the original data |
| Format independence | Behavior is the same across `Wide`/`Compact` and `Full`/`Packed`; no combination fails or is skipped |

### Scenario 23: Patch parts (lightweight UPDATE) must be materialized before backup

**Goal:** Document and verify the limitation that pending **patch parts** created by lightweight `UPDATE` are not
reliably backed up by `clickhouse-backup`, and confirm the safe workaround (`APPLY PATCHES` before backup). This
is a negative scenario: it shows the failure modes so users know to materialize patches first.

> Applies to ClickHouse versions where lightweight `UPDATE` / patch parts exist (25.7+, on by default in 25.8+).
> A patch part lives in a separate partition named `patch-<hash>-<original_partition_id>`, and `clickhouse-backup`
> has no special handling for it (see [Which Part Types Are Supported](#which-part-types-are-supported)).

**Steps:**

*Case A — partition-scoped backup misses the patch part (negative):*

1. Create a `MergeTree` table with data in partition `p1`; make a full backup.
2. Run a lightweight `UPDATE` that changes rows in `p1` (do NOT apply patches). Confirm a patch part appears in
   `system.parts` in a `patch-...` partition.
3. Make an incremental backup limited to the original partition:
   `create_remote --diff-from-remote=<full> --partitions=p1 <inc>`.
4. Restore `<inc>` onto an empty table.

*Case B — the safe workaround (positive control):*

5. On the original table (with the pending update), run `ALTER TABLE ... APPLY PATCHES` (or wait for merges) so
   the update is written into ordinary parts. Confirm no `patch-...` partition remains.
6. Make an incremental backup and restore it onto an empty table.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Case A — update lost | The partition-scoped backup does not include the patch part, so the restored `p1` data does **not** reflect the lightweight update (this demonstrates the limitation) |
| Case B — update preserved | After `APPLY PATCHES`, the update is in an ordinary part; the incremental backup includes it and the restored data reflects the update correctly |
| Guidance confirmed | The scenario confirms the recommendation: materialize patch parts with `APPLY PATCHES` before backing up |

> [!NOTE]
> If a future version of `clickhouse-backup` adds explicit patch-part support, Case A's expected result should be
> revisited (the update should then be preserved without `APPLY PATCHES`).

### Scenario 24: Incremental backup across a ClickHouse version upgrade

**Goal:** Confirm a chain that starts on an older ClickHouse version X and continues on a newer version Y works
correctly — unchanged parts carried from the base (created by X) are reused, parts written by Y are uploaded as
new, and the final restore is correct. This verifies the compatibility statements in
[Backward Compatibility Across ClickHouse Versions](#backward-compatibility-across-clickhouse-versions).

> `clickhouse-backup` decides part reuse by name + content fingerprint and never compares the ClickHouse
> version, so this scenario really tests the assumption that ClickHouse keeps immutable parts stable across an
> upgrade. Pick X and Y from the [supported versions](#which-clickhouse-versions-are-supported) (for example a
> recent LTS as X and a newer release as Y); both MUST be ≥ 19.11 so the fingerprint stays `hash_of_all_files`
> on both sides.

**Steps:**

1. Start ClickHouse on the older version **X**. Create a `MergeTree` table with several partitions, run
   `SYSTEM STOP MERGES`, insert data, and record each part's name and `hash_of_all_files` from `system.parts`.
2. Make a full remote backup `base_backup` (created by X). Confirm its `metadata.json` `clickhouse_version`
   reports X.
3. Stop ClickHouse and **upgrade the server to the newer version Y**, keeping the same data directory. Restart
   and confirm the existing parts keep the same names and `hash_of_all_files` (they were not rewritten by the
   upgrade).
4. On version Y, add data to a new partition (and optionally rewrite one existing partition with
   `OPTIMIZE TABLE ... FINAL` to force a Y-created part), then make an incremental backup
   `create_remote --diff-from-remote=base_backup inc_backup` (created by Y). Confirm its `clickhouse_version`
   reports Y.
5. On a clean target, restore the schema and then restore `inc_backup` onto an empty table.

**Expected result:**

| ChWhat is checkedeck | Expected |
| ----- | -------- |
| Base parts reused | Parts unchanged since the upgrade are marked `required` in `inc_backup` (reused from the X-created base, not uploaded again) |
| New parts uploaded | Data added or rewritten on Y is stored in `inc_backup` as new parts |
| Mixed-version chain | The chain contains parts created by both X and Y; each backup's `clickhouse_version` reflects the version that made it |
| No version guard | The increment and the restore succeed even though base and increment report different `clickhouse_version` values |
| Data after restore | The restored table contains exactly the original data plus the changes made on Y |

> [!NOTE]
> If step 3 shows that the upgrade rewrote existing parts (new names / changed fingerprints), that is a
> ClickHouse-side behavior: those parts will simply be re-uploaded in `inc_backup`. The restore must still be
> correct; only the space savings are reduced. Record this if observed, as it affects increment size after an
> upgrade.

### Scenario 25: Restore an incremental backup into a table that already has data

**Goal:** Confirm and document how restoring an incremental backup behaves when the **target table/database
already exists and holds data**. This verifies the statements in
[Restoring Into Empty vs. Non-Empty Tables](#restoring-into-empty-vs-non-empty-tables): a default full restore
(and `--rm`) **drops and recreates** the table first (so no duplication), whereas a data-only (`--data`)
restore **attaches on top** of existing data and can duplicate rows. It also confirms that `--partitions`
replaces only the named partitions, that no case damages part files, and how restore behaves when the target
has a **different schema** (Case F) or a **different `PARTITION BY`** (Case G) than the backup.

> The deciding factor is whether the **schema step** runs. Schema restore drops the existing object
> (`DROP TABLE IF EXISTS` via `dropExistsTables`) and recreates it; the data step (`ALTER TABLE ... ATTACH
> PART`) is purely additive. Default `restore` runs both steps; `--data` runs only the additive data step.

**Steps:**

*Case A — default full restore (schema + data) onto a populated table (drops first):*

1. Build `base_backup` → `inc_backup` on a plain `MergeTree` table with rows in partitions `p1` and `p2`;
   record the exact rows and the total row count `N`.
2. On a target that already contains **different/extra** rows in the same table, run a plain `restore inc_backup`
   (no `--data`, no `--schema`, no `--rm`).
3. Compare the row count and rows against the source.

*Case B — data-only restore onto overlapping data (demonstrates duplication):*

4. Reset the target so it again contains the **same** data as the source. Restore `inc_backup` with `--data`
   and **no** `--partitions` and **no** `--rm`.
5. Compare the row count and rows against the source.

*Case C — partition-scoped data restore replaces cleanly:*

6. On a target that already contains `p1` and `p2`, restore `inc_backup` with `--data --partitions=p1` (only
   `p1` is targeted).
7. Check `p1` and `p2` contents and counts.

*Case D — `--rm` gives a faithful whole-table copy:*

8. On a populated target, restore `inc_backup` with `--rm` (drop and recreate).
9. Compare against the source.

*Case E — deduplicating engine hides/exposes duplicates only after merge (optional but recommended):*

10. Repeat Case B on a `ReplacingMergeTree` table. Check the count immediately after restore, then after
    `OPTIMIZE TABLE ... FINAL`.

*Case F — target table has a different schema than the backup:*

11. Create the target table with a **different structure** than the one in `inc_backup` (for example an extra
    column, a changed column type, or a different `ORDER BY`) and put some rows in it.
12. Run a plain `restore inc_backup` (schema + data) and check the resulting structure and data.
13. Reset the target to the different structure again, then run `restore inc_backup --data` (data-only) and
    record whether the `ATTACH PART` step succeeds or fails.

*Case G — target table has a different `PARTITION BY` than the backup (data-only):*

14. Build `inc_backup` on a table partitioned one way (for example `PARTITION BY toYYYYMM(dt)`).
15. Manually pre-create the destination table with the **same columns and `ORDER BY`** but a **different
    `PARTITION BY`** (for example `PARTITION BY toYYYYMMDD(dt)`, or `PARTITION BY tuple()` for a single
    partition). Do **not** restore its schema from the backup.
16. Run `restore inc_backup --data` (data-only) and record the outcome of the `ATTACH PART` step and the error
    message, if any.
17. For contrast, run a plain `restore inc_backup --rm` on the same pre-created table and confirm the partition
    layout afterwards.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Case A — drop then recreate | The default restore drops the existing table (`DROP TABLE IF EXISTS`) and recreates it before attaching, so the pre-existing extra rows are gone and the table matches the source exactly (count `N`); no duplication |
| Case B — no error | The data-only restore succeeds; the target is not required to be empty |
| Case B — duplication | The target now holds roughly `2N` rows: parts are attached on top of the existing data (overlapping rows are duplicated, not merged or overwritten) |
| Case B — no corruption | Every attached part is valid and queryable; the issue is duplicated rows only, never damaged files |
| Case C — targeted replace | `p1` is dropped and re-attached from the backup, so `p1` matches the source with no duplication; `p2` is left exactly as it was on the target (untouched) |
| Case D — clean copy | After `--rm` the table exactly matches the source (same rows, same count `N`), with no leftover pre-existing data |
| Case E — engine behavior | Immediately after restore the `ReplacingMergeTree` may report doubled counts; after `OPTIMIZE ... FINAL` duplicates collapse. Confirms that row-count checks on deduplicating engines must account for merge timing |
| Case F — plain restore replaces schema | The default restore drops the differently-structured target by name and recreates it from the backup's `CREATE` statement; the final table has the **backup's schema and data**, and the target's old structure and rows are gone (no merge of the two schemas) |
| Case F — data-only restore keeps schema | The data-only restore leaves the target's different structure in place; ClickHouse validates each part during `ATTACH PART` — an **incompatible** structure makes the restore **fail with an error**, while a **compatible** one attaches the parts (with the Case B duplication caveat). `clickhouse-backup` never reconciles the schema difference in this mode |
| Case G — `PARTITION BY` mismatch fails on `--data` | The data-only restore **fails**: `ATTACH PART` is rejected because the target's `PARTITION BY` produces a different `partition_id` than the part carries (the part directory name encodes the backup's `partition_id`). No data is attached; no silent re-partitioning happens |
| Case G — `--rm` succeeds | The `--rm` (drop + recreate) run recreates the table from the backup's `CREATE`, so its `PARTITION BY` matches the backup and all parts attach cleanly (count `N`). Confirms the mismatch is only reachable on the data-only path |

> [!NOTE]
> Case B is a *documented behavior* check, not a bug: a data-only restore onto a populated table is additive by
> design because it skips the schema (drop) step. For a faithful restore use a plain `restore` or `--rm` (both
> drop and recreate); to replace specific partitions use `--data --partitions=...`. In embedded mode the same
> behavior applies and non-empty data restore is enabled explicitly via `allow_non_empty_tables=1`.
> If `restore_schema_on_cluster` is configured, a schema-dropping restore may abort and require `--rm`/`--drop`
> when the target already has rows (ChangeLog [#1325]).

### Scenario 26: Restore an incremental backup on a multi-replica cluster

**Goal:** Confirm the multi-replica restore workflow for an **incremental** backup: schema is restored on every
replica, data is restored on only the first replica of each shard, and ClickHouse replication then propagates
the increment's own parts **and** the `required` base-chain parts to the sibling replicas — so the siblings end
up identical without ever reading the backup chain. This verifies
[Incremental Restore in a Multi-Replica Setup](#incremental-restore-in-a-multi-replica-setup).

> Use a shard with at least two replicas (`ReplicatedMergeTree` with a `{replica}` macro). This scenario is
> about the *restore* fan-out, so keep it single-shard/two-replica to stay focused; the sharded case is covered
> by [Scenario 18](#scenario-18). Keep `check_replicas_before_attach: true` (default).

**Steps:**

1. On replica A, build a chain: full `base_backup`, then `inc_backup` after adding new rows, on a
   `ReplicatedMergeTree` table. Upload both to remote. Record the source rows and total count `N`.
2. Drop the table on **both** replicas (start from a clean cluster).
3. **Schema step — every replica:** run `restore_remote --schema --rm inc_backup` on replica A **and** replica B
   (or use `restore_schema_on_cluster`). Confirm the table now exists on both and each has its own replica entry
   in Keeper.
4. **Data step — first replica only:** run `restore_remote --data inc_backup` on replica A only. Do **not** run
   `--data` on replica B.
5. Wait for replication to converge (`SYSTEM SYNC REPLICA` on B), then compare row counts and rows on **both**
   replica A and replica B against the source.
6. Confirm on replica B that the backup chain was never downloaded locally (no local `base_backup`/`inc_backup`
   directory was needed for B to get the data).
7. **Negative check (duplication):** on a fresh clean cluster repeat steps 3–4 but run `restore_remote --data`
   on **both** replicas, then compare the count.
8. **Contrast — plain `MergeTree` (no replication):** repeat the whole flow with the table declared as a plain
   `MergeTree` (no `Replicated` prefix) on both nodes. Restore schema on both nodes, then `--data` on node A
   only, and compare node A vs node B.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Schema on all replicas | After step 3 the table exists on both replica A and B with matching structure; each replica is registered in Keeper |
| Data on first replica | After step 4 replica A holds the full dataset (base + increment), count `N` |
| Replication to siblings | After step 5 replica B holds the **same** count `N` and the same rows as A, produced by native replication — the base-chain and increment parts both arrive without B reading any backup |
| Chain locality | Replica B never needed the backup chain locally; only replica A resolved and read `base_backup` + `inc_backup` |
| Negative — double `--data` | Running `--data` on both replicas attaches the same parts twice → duplicated rows (≈ `2N`); confirms why data restore must run on only one replica per shard (and that `check_replicas_before_attach` is the guard) |
| Contrast — plain `MergeTree` | With a non-replicated engine, node A holds count `N` but node B stays **empty** — nothing propagates. To populate node B you must also run `--data` on B (each node needs the chain locally). Confirms the "first replica only" rule is specific to `Replicated*MergeTree` |

> [!NOTE]
> This is the incremental analogue of the standard sharded/replicated restore recipe in `Examples.md`
> (the Kubernetes restore `Job` uses `CLICKHOUSE_SCHEMA_RESTORE_SERVICES` = all replicas and
> `CLICKHOUSE_DATA_RESTORE_SERVICES` = first replica per shard). The only incremental-specific aspect is that
> the single `--data` node attaches both the increment's own parts and the `required` parts pulled from the base
> backups; replication then makes the siblings identical.

### Scenario 27: Parallel INSERT / ALTER during incremental backup and restore

**Goal:** Verify the concurrency behavior documented in
[Concurrent INSERT / ALTER During Backup and Restore](#concurrent-insert--alter-during-backup-and-restore):
that an incremental backup taken against a table that is being written/merged/altered stays **consistent and
correct**, that concurrent merges only change how much the increment re-uploads (not correctness), that a racing
column-type `ALTER` is rejected rather than corrupting the backup, and that restore is meant for a quiesced
target.

> This is the `partA + partB → partC` question made concrete. `clickhouse-backup` never stops merges or locks
> the table; a backup's data is the `ALTER TABLE ... FREEZE` snapshot of the **active** parts at freeze time.
> The merge sub-case overlaps [Scenario 13](#scenario-13); this scenario adds the explicit INSERT and ALTER
> races on top.

**Steps:**

*Case A — parallel INSERT during create (snapshot boundary):*

1. Create a `MergeTree` table, insert a known set (count `N0`), and take full `base_backup`.
2. Start a slow/continuous `INSERT` in the background, and while it runs take `inc_backup`
   (`create --diff-from-remote=base_backup`). Record the increment's stored rows.
3. Restore `inc_backup` onto a clean node and compare against the source snapshot.

*Case B — parallel merge produces `partC` (the diagram):*

4. On a fresh table with `SYSTEM STOP MERGES`, insert twice into one partition to get two parts `partA`, `partB`;
   take `base_backup` (records `partA`, `partB`).
5. Run `SYSTEM START MERGES` / `OPTIMIZE TABLE ... FINAL` so `partA + partB` merge into a single `partC`, then
   take `inc_backup`.
6. Inspect what `inc_backup` uploaded (new vs. reused parts) and restore it onto a clean node; compare data.

*Case C — parallel data-rewriting ALTER / mutation during create:*

7. On a populated table, start `ALTER TABLE ... UPDATE ...` (a mutation), and while it is in progress take
   `inc_backup` with `backup_mutations` enabled.
8. Restore onto a clean node (with `restore_as_attach` where required) and compare data.

*Case D — parallel column-type ALTER racing the backup (guard):*

9. Trigger a state where active parts have inconsistent column types (e.g. begin `ALTER TABLE ... MODIFY COLUMN`
   on a large table so some active parts are converted and some are not) and attempt `create` with the default
   `check_parts_columns: true`. Then repeat with `--skip-check-parts-columns`.

*Case E — parallel INSERT during restore:*

10. Restore `inc_backup` with `--data` onto a table while a background `INSERT` writes overlapping rows; compare
    the count. Then repeat with a default `restore` (drop + recreate) and observe behavior of the concurrent
    writer.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Case A — snapshot boundary | The increment contains exactly the parts that were **active at freeze time**: rows inserted before freeze are present, rows inserted after are not. No half-parts; the restored data matches the corresponding source snapshot exactly (per [§3.1](#measuring-data-equivalence)) |
| Case B — merge correctness | Restore of `inc_backup` reproduces all rows with **no loss and no corruption** |
| Case B — dedup effect | If the merge finished before the increment's freeze, `inc_backup` **uploads `partC` as new data** (it is not in the base by name/fingerprint), so the increment is larger than a pure delta; if the merge had not finished, `partA`/`partB` are **reused** and the increment is near-empty. Both restore correctly |
| Case C — mutation captured | The in-progress mutation is recorded and the restored table reflects the mutated data (pending mutations carried forward with `restore_as_attach`); no corruption |
| Case D — column-type guard | With the default `check_parts_columns: true` the backup **aborts** with *"inconsistent data types for active data part"*; with `--skip-check-parts-columns` it proceeds (and the tester must accept the inconsistency risk) |
| Case E — restore concurrency | `--data` with a concurrent writer yields **duplicated/overlapping rows** (additive, no corruption); a default `restore` drops and recreates the table, so the concurrent writer may hit *"table doesn't exist"* or lose writes during the window — confirming restore should target a quiesced table |

> [!NOTE]
> All of the above is engine-atomicity behavior, not a `clickhouse-backup` guarantee of transactional isolation
> across a live workload. The safe operational rule: incremental **backup** creation may run against a live,
> writing table (a racing column-type `ALTER` aborts the backup rather than corrupting it), but **restore**
> should run against a quiesced target.

### Scenario 28: Incremental chain across a changed backup storage or folder

**Goal:** Verify the storage-location rules in
[Backup Storage Location and Chain Portability](#backup-storage-location-and-chain-portability): that a chain is
resolved **by name in the single configured remote**, that changing the storage type or `path` breaks the chain
with a clear "not found" error, that copying the **whole** chain to a new location restores correctly, and that
`rebase` produces a self-contained backup that is portable on its own.

> The increment stores only `required_backup: <name>` — no endpoint, type, or path. Everything below follows
> from name-based resolution against whatever remote the current config points to.

**Steps:**

*Case A — chain works within one storage/path (baseline):*

1. With `remote_storage` pointed at storage S1 and `path=P1`, create `base_backup` then `inc_backup`
   (`create_remote --diff-from-remote=base_backup`). Confirm `inc_backup`'s `metadata.json` has
   `required_backup: base_backup`. Restore `inc_backup` onto a clean node and compare against the source
   (per [§3.1](#measuring-data-equivalence)).

*Case B — change the folder/prefix (chain breaks):*

2. Change config `path` to `P2` (same bucket, empty prefix) and attempt `restore_remote inc_backup` (or
   `download inc_backup`).

*Case C — change storage type / empty storage (chain breaks):*

3. Point `remote_storage` at a different, empty storage S2 (or a different type) and attempt
   `restore_remote inc_backup`.

*Case D — migrate the whole chain (works):*

4. Copy the **entire** chain (`base_backup` **and** `inc_backup`, preserving names and directory layout — plus
   the referenced `object_disk_path` data if the tables are on object disks) from S1/P1 to the new location
   using a storage-native copy (e.g. `aws s3 sync`). Point config at the new location and restore `inc_backup`.

*Case E — rebase makes a portable, self-contained backup:*

5. In S1/P1 run `rebase` on `inc_backup` (see [Scenario 11](#scenario-11)) so it becomes self-contained
   (`required_backup` empty). Copy **only** that backup to the new location and restore it there.

*Case F — split chain is not possible (negative):*

6. Attempt to create `inc_backup` with `--diff-from-remote=base_backup` while `base_backup` exists **only** in a
   different storage than the one the current config points at.

**Expected result:**

| What is checked | Expected |
| ----- | -------- |
| Case A — baseline | `inc_backup` records `required_backup: base_backup`; restore reproduces the source exactly |
| Case B — changed prefix | Restore/download **fails** with `'base_backup' is not found on remote storage` (the base lives under the old prefix `P1`, not `P2`); no data is restored |
| Case C — changed storage | Same "not found" failure — the base does not exist in S2; the chain cannot be resolved |
| Case D — full migration | After copying the **whole** chain (and object-disk data, if any) to the new location, `inc_backup` restores correctly — resolution is by name + layout, not endpoint |
| Case E — rebase portability | The rebased backup has **no** `required_backup`, so copying just that one backup to the new location restores successfully on its own |
| Case F — split chain | The create/upload **fails** (or cannot find the base) because both the diff read and later download use the single configured remote; a chain cannot span two storages |

> [!NOTE]
> The safe operational patterns for moving storage are: (a) copy the **entire** chain atomically (all backup
> names, full directory layout, plus `object_disk_path` data for object disks), (b) `rebase` a backup to make it
> self-contained and move just that one, or (c) start a **new full backup** in the new location and begin a
> fresh chain. Never move only some backups of a chain.

