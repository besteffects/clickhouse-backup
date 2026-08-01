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