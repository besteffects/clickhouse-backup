# QA-STP ClickHouse Backup Incremental Backups
# Software Test Plan

(c) 2026 Altinity Inc. All Rights Reserved.

**Author:** vsviderskyi

**Date:** July 16, 2026

## Table of Contents

* 1 [Introduction](#introduction)
* 2 [How Incremental Backups Work in clickhouse-backup](#how-incremental-backups-work-in-clickhouse-backup)
* 3 [Testing Approach](#testing-approach)
* 4 [Scope and Relationship to Existing Tests](#scope-and-relationship-to-existing-tests)
* 5 [Timeline](#timeline)
* 6 [Commands and Options Used](#commands-and-options-used)
* 7 [Test Environment](#test-environment)
* 8 [References](#references)
* 9 [Human Resources And Assignments](#human-resources-and-assignments)
* 10 [Release Notes](#release-notes)
* 11 [Test Scenarios](#test-scenarios)
    * [Scenario 1: Create and restore a single incremental backup](#scenario-1)
    * [Scenario 2: Incremental backup after adding new data](#scenario-2)
    * [Scenario 3: Incremental backup when nothing changed](#scenario-3)
    * [Scenario 4: A changed part with the same name is uploaded again](#scenario-4)
    * [Scenario 5: Incremental backup after deleting data](#scenario-5)
    * [Scenario 6: Restore from a long chain of incremental backups](#scenario-6)
    * [Scenario 7: Restore an earlier backup in the chain](#scenario-7)
    * [Scenario 8: Restore reused parts from a local backup or by download](#scenario-8)
    * [Scenario 9: Keep backups that other backups depend on](#scenario-9)
    * [Scenario 10: Restore an incremental backup when its base is missing](#scenario-10)
    * [Scenario 11: Make an incremental backup self-contained (rebase)](#scenario-11)
    * [Scenario 12: Resume an interrupted incremental upload](#scenario-12)
    * [Scenario 13: Incremental backups while ClickHouse merges parts](#scenario-13)
    * [Scenario 14: Compare local-base and remote-base incremental backups](#scenario-14)
    * [Scenario 15: Incremental backup of tables on S3 object disks](#scenario-15)
    * [Scenario 16: Incremental backup using the embedded BACKUP engine](#scenario-16)
    * [Scenario 17: Incremental backup limited to selected partitions](#scenario-17)
    * [Scenario 18: Incremental backups on a two-node sharded cluster](#scenario-18)
    * [Scenario 19: Incremental backup with a large amount of data](#scenario-19)
    * [Scenario 20: Automatic incremental backup chains with `watch`](#scenario-20)
    * [Scenario 21: How insert size and merges affect incremental backup size](#scenario-21)

## Introduction

This test plan describes how to test **incremental backups** in `clickhouse-backup`.

An incremental backup stores only the data that changed since a previous backup, instead of copying
everything again. This makes backups faster and smaller. The previous backup an incremental backup is built on
top of is called its **base backup**.

The goal of this plan is to confirm that:

* An incremental backup stores only the new or changed data, not the whole table again.
* Restoring an incremental backup gives back exactly the same data as the original.
* A part that changed but happens to keep the same name is still saved correctly (not mistaken for unchanged
  data).
* Backups that other backups depend on are not accidentally deleted.
* Incremental backups keep working after interruptions, during background activity in ClickHouse, and on
  larger amounts of data.

## How Incremental Backups Work in clickhouse-backup

ClickHouse stores table data in immutable files on disk called **data parts**. A part is written once and is
never modified in place; when data changes, ClickHouse writes new parts and eventually merges old ones into
new ones.

`clickhouse-backup` uses this to make incremental backups: when it creates an incremental backup, it compares
the current parts against the parts already saved in the base backup. Parts that already exist in the base
backup are **reused** (their data is not uploaded again) and are simply marked as coming from the base backup.
Only genuinely new parts are uploaded.

Because of this, an incremental backup on its own is not complete — it points to its base backup (and the base
may point to an even earlier one, forming a **chain**). When restoring, `clickhouse-backup` collects the reused
parts from the earlier backups in the chain automatically.

The chain is **linear** — each backup records exactly one base backup, so every
new incremental backup adds one more link. There is **no fixed limit** on chain depth in `clickhouse-backup`;
it follows the chain of base backups recursively until it reaches the full backup at the start. In practice the
depth is controlled by how the backups are managed, not by the tool:

* The `watch` command (and scheduled backups) periodically makes a new *full* backup, which starts a fresh
  chain, so the depth resets each full cycle.
* The `rebase` command collapses a chain by turning an incremental backup into a self-contained one (see
  [Scenario 11](#scenario-11)).
* Retention (`backups_to_keep_remote`) only removes backups that no remaining chain still needs, so it never
  breaks a chain that is still in use.

**Chain compaction (`rebase`).** The `rebase` command turns an incremental backup into a complete, standalone
backup. It works by walking the whole chain (from the chosen backup back to its full backup), finding every
part that was "reused" from an earlier backup, and copying those parts into the chosen backup. The copy happens
directly on the remote storage (a server-side copy), so data is not downloaded and re-uploaded. Afterwards the
backup no longer records a base backup — it becomes a full backup on its own, and all of its former earlier
backups can be deleted without affecting it. This works for regular remote backups uploaded with
`upload_by_part: true`; it is not available for embedded backups.

There is no exact depth at which a chain becomes "too long" — `clickhouse-backup` enforces no limit. What grows
with each extra link is *cost*, and `rebase` is the tool to control it:

* **Restore/download cost.** To download or restore an incremental backup, every ancestor up to the full backup
  must be reachable, and reused parts are resolved by walking the chain one level at a time. A longer chain
  means more remote metadata reads and more places a part may have to be fetched from, so restore takes longer.
* **Retention cost.** Every backup in a chain must be kept for as long as the newest backup that depends on it
  exists (see [Scenario 9](#scenario-9)). A long chain therefore pins many old backups in remote storage; they
  cannot be deleted individually.
* **Fragility.** If any single backup in the chain is lost or corrupted, every backup that depends on it becomes
  unrestorable (see [Scenario 10](#scenario-10)).

So the practical rule is: compact with `rebase` when you want to shorten restore time, or when you need to delete
old backups but a later increment still depends on them. The scheduled `watch` workflow does this automatically
with `full_type=rebase`, and retention can trigger it via `rebase_before_remove_old_remote: true`.

This matches how `rebase` is described in the `clickhouse-backup` source code:

> Rebase - copy required parts from the required backups chain into backupName on remote storage
> and remove the required_backup dependency, so backupName becomes a full backup

The official description of this behavior is in the clickhouse-backup documentation (see
[References](#references)).

## Testing Approach

Each test follows the same simple idea:

1. Put a table into a known state and make a full backup (call it the **base state**).
2. Change the data in a controlled way and make an incremental backup (**state 1**, **state 2**, and so on for
   longer chains).
3. Restore the latest backup onto an empty table (or a clean node) and check the result.

For every test we confirm two things:

* **The data is correct** — after restore, the table contains exactly the same rows as the original (checked by
  comparing row counts and the actual row contents).
* **Only the changes were stored** — the incremental backup is much smaller than a full backup would be. This
  is visible from the reported backup size (for example in `clickhouse-backup list`). This matters because a
  broken incremental backup that secretly copied everything would still restore correctly, so a data check
  alone is not enough to prove the feature works.

**A note on background merges.** ClickHouse merges parts in the background, which renames and rewrites them.
This can change which parts count as "new" between two backups and is the main cause of unstable test results.
Tests that need parts to stay stable stop merges (`SYSTEM STOP MERGES`) or insert into separate partitions so
the changed parts are predictable. One test deliberately runs *with* merges to check behavior under that
condition.

The most important tests to run first (they cover the core promises and the riskiest failure) are
[Scenario 1](#scenario-1), [Scenario 4](#scenario-4), [Scenario 6](#scenario-6), [Scenario 9](#scenario-9),
and [Scenario 10](#scenario-10).

## Scope and Relationship to Existing Tests

The internal mechanics of incremental backups (how parts are marked as reused, checksums, object-disk copying,
rebase internals) are already exercised by the Go integration tests under `test/integration/`, which run
against a single ClickHouse node and inspect backup metadata directly.

This plan focuses on **end-to-end, user-visible behavior** and on **multi-node scenarios** that the
single-node integration tests cannot cover. Where a scenario overlaps with existing integration coverage, this
is noted so the same thing is not tested twice for no reason.

## Timeline

The testing of `clickhouse-backup` incremental backups SHALL be started on July 20, 2026 and be completed by
August 7, 2026.

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
| `rebase` (command) | Turn an incremental backup into a self-contained one (see [Scenario 11](#scenario-11)) |
| `use_embedded_backup_restore` (config) | Use ClickHouse's built-in BACKUP engine instead of the file-level approach (see [Scenario 16](#scenario-16)) |

## Test Environment

The tests use the standard TestFlows environment for `clickhouse-backup`, which starts:

* Two ClickHouse nodes, `clickhouse1` and `clickhouse2`, and a ZooKeeper node for coordination.
* A `clickhouse_backup` container running the `clickhouse-backup` binary under test.
* Remote storage backends available in the environment (for example MinIO for S3, plus FTP and SFTP servers).

Most single-node scenarios run on `clickhouse1`. Scenarios that need more than one node (for example the
sharded-cluster scenario) use both `clickhouse1` and `clickhouse2` via the predefined `sharded_cluster`
configuration. To confirm a restore is complete, tests drop the table (or use a clean node) and restore into
an empty target.

## References

The following stable sources describe the behavior tested here:

* clickhouse-backup project and documentation: <https://github.com/Altinity/clickhouse-backup>
* How incremental backups work with remote storage:
  <https://github.com/Altinity/clickhouse-backup/blob/master/Examples.md#how-incremental-backups-work-with-remote-storage>
* Commands and options (`create`, `create_remote`, `upload`, `restore`, `rebase`, `watch`, `--diff-from`,
  `--diff-from-remote`, `--partitions`): <https://github.com/Altinity/clickhouse-backup/blob/master/Manual.md>
* Changelog: <https://github.com/Altinity/clickhouse-backup/blob/master/ChangeLog.md>

## Human Resources And Assignments

The following team members SHALL be dedicated to this effort:

* Vitalii Sviderskyi (regression tests)
* Vitaliy Zakaznikov (manager, regression tests)
* Eugene Klimov (clickhouse-backup)

## Release Notes

* https://github.com/Altinity/clickhouse-backup/blob/master/ChangeLog.md
* https://github.com/Altinity/clickhouse-backup/blob/master/Examples.md

---

## Test Scenarios

### Scenario 1: Create and restore a single incremental backup
<a id="scenario-1"></a>

**Goal:** Confirm the basic flow works — a full backup, then one incremental backup, then a correct restore.

**Steps:**

1. Create a partitioned `MergeTree` table and insert a first batch of data.
2. Make a full remote backup: `create_remote base_backup`.
3. Insert a second batch of data into a new partition.
4. Make an incremental backup: `create_remote --diff-from-remote=base_backup inc_backup`.
5. Drop the table and restore the incremental backup: `restore_remote inc_backup`.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Data after restore | The table contains both batches of data, identical to before the drop |
| Backup size | `inc_backup` is much smaller than `base_backup` (only the new data was stored) |

### Scenario 2: Incremental backup after adding new data
<a id="scenario-2"></a>

**Goal:** Confirm that when only new data is added, the unchanged data is reused (deduplicated) and not stored
again.

**Steps:**

1. Full backup of a table with data in partition A.
2. Stop merges, then insert new data only into a new partition B.
3. `create_remote --diff-from-remote=<full> inc_backup`.
4. Restore `inc_backup` onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Data after restore | Both partitions A and B are present and correct |
| Backup size | The incremental backup size corresponds roughly to partition B only |

### Scenario 3: Incremental backup when nothing changed
<a id="scenario-3"></a>

**Goal:** Confirm that an incremental backup taken with no data changes stores almost nothing but still
restores the full data.

**Steps:**

1. Full backup of a table; stop merges so parts stay identical.
2. Without changing any data, `create_remote --diff-from-remote=<full> inc_backup`.
3. Restore `inc_backup` onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Backup size | The incremental backup is nearly empty (only bookkeeping, no data) |
| Data after restore | The table matches the original data exactly |

### Scenario 4: A changed part with the same name is uploaded again
<a id="scenario-4"></a>

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

| Check | Expected |
| ----- | -------- |
| Data after restore | The restored data reflects the rewritten content, not the old content |
| Backup content | The changed data was actually stored in the incremental backup (it was not skipped as "unchanged") |

### Scenario 5: Incremental backup after deleting data
<a id="scenario-5"></a>

**Goal:** Confirm that deleting data (dropping a partition) before an incremental backup is reflected on
restore.

**Steps:**

1. Full backup of a table with partitions A and B.
2. `ALTER TABLE ... DROP PARTITION B`.
3. `create_remote --diff-from-remote=<full> inc_backup`.
4. Restore `inc_backup` onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Data after restore | Only partition A is present; partition B is gone |

### Scenario 6: Restore from a long chain of incremental backups
<a id="scenario-6"></a>

**Goal:** Confirm that a chain of several incremental backups can be restored correctly, collecting reused
parts from all earlier backups in the chain.

**Steps:**

1. Full backup (`base_backup`).
2. Add data and make `inc1` based on `base_backup`.
3. Add more data and make `inc2` based on `inc1`.
4. Add more data and make `inc3` based on `inc2`.
5. Restore the newest backup `inc3` onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Data after restore | The table contains all data added across the full backup and every increment |
| Chain used | The restore automatically pulls the reused parts from the earlier backups in the chain |

### Scenario 7: Restore an earlier backup in the chain
<a id="scenario-7"></a>

**Goal:** Confirm that restoring a backup from the middle of a chain gives the data as it was at that point in
time, not the latest data.

**Steps:**

1. Build the chain `base_backup` → `inc1` → `inc2` → `inc3` as in Scenario 6.
2. Restore `inc1` (not the newest) onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Data after restore | The table matches the data as it was when `inc1` was taken (not `inc2` or `inc3`) |

### Scenario 8: Restore reused parts from a local backup or by download
<a id="scenario-8"></a>

**Goal:** Confirm that restore works whether the base backup's data is already present locally or has to be
downloaded from remote storage.

**Steps:**

1. Build `base_backup` → `inc1` on remote storage.
2. Case A: keep `base_backup` present locally, then restore `inc1` (reused parts come from the local base).
3. Case B: with no local backups present, restore `inc1` (reused parts are downloaded from remote).

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Case A | Restore succeeds; data matches the original |
| Case B | Restore succeeds; data matches the original |
| Both cases | Produce identical restored data |

### Scenario 9: Keep backups that other backups depend on
<a id="scenario-9"></a>

**Goal:** Verify retention never deletes a backup that a later incremental backup still depends on.

**Steps:**

1. Build a chain of 5 backups using `--diff-from-remote`.
2. Set `backups_to_keep_remote = 3` and run the remote cleanup.
3. Download and restore the latest backup onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Backups kept | Older backups that the kept chain still needs are NOT deleted, even though the limit is 3 |
| Data after restore | The latest backup restores correctly using its full chain |

### Scenario 10: Restore an incremental backup when its base is missing
<a id="scenario-10"></a>

**Goal:** Confirm that if the base backup is missing, restoring the incremental backup fails clearly instead of
producing wrong or partial data.

**Steps:**

1. Build `base_backup` → `inc1`.
2. Delete `base_backup` from remote storage so the chain is broken.
3. Attempt to restore `inc1`.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Result | The restore fails with a clear error that names the missing base backup |
| No bad data | No half-restored or corrupted table is left behind; the tool does not crash |

### Scenario 11: Make an incremental backup self-contained (rebase)
<a id="scenario-11"></a>

**Goal:** Confirm the `rebase` command turns an incremental backup into a complete, standalone backup so it no
longer needs its earlier backups. ("Rebase" here means copying the reused parts into the incremental backup
itself.)

**Steps:**

1. Build `base_backup` → `inc1` → `inc2`.
2. Run `rebase inc2`.
3. Delete the earlier backups `base_backup` and `inc1`.
4. Restore `inc2` onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| After rebase | `inc2` no longer depends on any earlier backup |
| Data after restore | `inc2` restores correctly even though its earlier backups were deleted |

### Scenario 12: Resume an interrupted incremental upload
<a id="scenario-12"></a>

**Goal:** Confirm that an incremental backup upload that is interrupted can resume and finish correctly without
re-uploading data it already handled.

**Steps:**

1. Full backup, then add new data.
2. Start uploading the incremental backup and interrupt it partway (stop/restart the process).
3. Run the same upload again to let it resume.
4. Restore the finished incremental backup onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Resume | The second run completes the backup without errors |
| No repeated work | Data already uploaded (and reused parts) are not uploaded again |
| Data after restore | The restored data matches the original |

### Scenario 13: Incremental backups while ClickHouse merges parts
<a id="scenario-13"></a>

**Goal:** Confirm the chain still restores correctly when ClickHouse merges parts in the background between
backups (merges rename and rewrite parts).

**Steps:**

1. Full backup with merges enabled.
2. Allow or trigger background merges, add some new data, then make an incremental backup.
3. Restore the incremental backup onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Data after restore | The restored data matches the original despite the merges |

### Scenario 14: Compare local-base and remote-base incremental backups
<a id="scenario-14"></a>

**Goal:** Confirm that both ways of choosing a base backup — a local base (`--diff-from`) and a remote base
(`--diff-from-remote`) — produce the same result for the same data changes.

**Steps:**

1. Make the same data change on top of the same full backup twice, once for each option:
   * with `--diff-from-remote=<full>`;
   * with `--diff-from=<full>` (keeping the full backup available locally).
2. Restore each incremental backup onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Both options | Store only the changed data and restore correctly |
| Comparison | Both restored tables contain identical data |

### Scenario 15: Incremental backup of tables on S3 object disks
<a id="scenario-15"></a>

**Goal:** Confirm incremental backups work for tables whose data is stored on an S3 object disk inside
ClickHouse, reusing unchanged data instead of copying it again.

> Note: This overlaps with existing Go integration coverage of object-disk incremental backups; it is included
> here for the end-to-end / multi-node environment.

**Steps:**

1. Create a table that uses an S3 object-disk storage policy; make a full remote backup.
2. Add new data, then make an incremental backup with `--diff-from-remote`.
3. Restore the incremental backup onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Reuse | Unchanged object-disk data is reused, not copied again (incremental backup stays small) |
| Data after restore | The restored data matches the original |

### Scenario 16: Incremental backup using the embedded BACKUP engine
<a id="scenario-16"></a>

**Goal:** Confirm incremental backups work when `clickhouse-backup` uses ClickHouse's built-in `BACKUP`
command (the "embedded" mode, enabled by `use_embedded_backup_restore: true`). In this mode ClickHouse itself
computes the difference against the base backup.

**Steps:**

1. With `use_embedded_backup_restore: true`, make a full remote backup.
2. Add data, then make an incremental backup with `--diff-from-remote`.
3. Restore the incremental backup onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Reuse | The incremental backup stores only the changes relative to the base |
| Data after restore | The restored data matches the original |

> Note: Embedded mode does not support sharded-operation mode, so this scenario is single-node only.

### Scenario 17: Incremental backup limited to selected partitions
<a id="scenario-17"></a>

**Goal:** Confirm that `--partitions` can limit an incremental backup to specific partitions and still restore
those partitions correctly.

**Steps:**

1. Create a table with partitions A, B, and C; make a full backup.
2. Add data to B and C, then make an incremental backup with `--diff-from-remote` and `--partitions=B,C`.
3. Restore the selected partitions onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Scope | Only partitions B and C are included in the incremental backup |
| Data after restore | The restored data for B and C matches the original |

### Scenario 18: Incremental backups on a two-node sharded cluster
<a id="scenario-18"></a>

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

| Check | Expected |
| ----- | -------- |
| Per-node chains | Each node has its own full → incremental chain |
| Data after restore | The combined data across both shards matches the original cluster data |

### Scenario 19: Incremental backup with a large amount of data
<a id="scenario-19"></a>

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

| Check | Expected |
| ----- | -------- |
| Small delta | The incremental backup is far smaller and faster than the full backup, in proportion to the small change |
| Reuse | Most of the data is reused from the full backup, not copied again |
| Data after restore | Row counts and row contents match the original |
| Stability | The run completes without running out of memory or disk space |

### Scenario 20: Automatic incremental backup chains with `watch`
<a id="scenario-20"></a>

**Goal:** Confirm the `watch` command automatically creates a repeating pattern of full and incremental
backups and cleans up old ones correctly.

**Steps:**

1. Start `watch` with short intervals (a full backup every so often, incremental backups in between) and a
   remote-retention limit.
2. Add data between intervals so each cycle produces a new incremental backup based on the previous one.
3. After several cycles, restore the latest backup onto an empty table.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Chain built | Incremental backups are created based on the previous backup; full backups appear on schedule |
| Cleanup | Old backups that are no longer needed are removed, but backups still needed by a chain are kept |
| Data after restore | The latest backup restores correctly |

### Scenario 21: How insert size and merges affect incremental backup size
<a id="scenario-21"></a>

**Goal:** Confirm and document that the size of an incremental backup depends not only on how much data was
added, but also on how it was inserted and on background merges — inserting the same data in a few large
inserts (with merges quiet) produces a smaller incremental backup than many tiny inserts or heavy merges.

**Steps:**

1. Make a full backup of a baseline table.
2. Case A: add a fixed amount of new data using a few large inserts, with merges quiet.
3. Case B: add the same amount of data using many small inserts and/or by triggering merges.
4. Make an incremental backup for each case and compare their sizes.

**Expected result:**

| Check | Expected |
| ----- | -------- |
| Size difference | Case A produces a smaller incremental backup than Case B for the same amount of new data |
| Data after restore | Both cases restore to the same correct data |
