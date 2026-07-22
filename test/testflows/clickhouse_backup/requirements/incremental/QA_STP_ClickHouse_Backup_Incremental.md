# QA-STP ClickHouse Backup Incremental Backups
# Software Test Plan

(c) 2026 Altinity Inc. All Rights Reserved.

**Author:** vsviderskyi

**Date:** July 16, 2026

## Table of Contents


## Introduction

This test plan covers incremental backup testing of `clickhouse-backup`.

The main goal of this test plan is to validate that `clickhouse-backup` produces correct, space-efficient
incremental backups and can faithfully reconstruct ClickHouse data from a full backup plus a chain of
increments.

To validate this, the following properties SHALL be checked:

* **Delta correctness** — an incremental backup uploads only genuinely new data parts; parts already present
  in the base backup are marked `required: true` in metadata and are NOT re-uploaded.
* **Delta safety** — a part whose name matches a part in the base backup but whose content fingerprint
  (`hash_of_all_files` / legacy `checksums`) differs MUST be re-uploaded, never deduplicated.
* **Reconstruction fidelity** — restoring the tip of a backup chain reproduces the source data exactly
  (row counts and content checksums match), resolving `required` parts up the whole chain.
* **Chain integrity** — a backup referenced by another backup's `required_backup` pointer MUST NOT be deleted
  by retention; `rebase` can flatten a chain into a self-contained backup.
* **Resilience and scale** — interrupted incremental operations resume without re-uploading deduplicated
  parts and without data loss, including when background merges occur, on large multi-shard clusters.

## Testing Approach and Best Practices

The scenarios in this plan follow the industry-standard method for validating incremental backups, adapted to
`clickhouse-backup`'s part-level model. The same method is used to test other immutable-file / segment-based
systems whose backup model is architecturally comparable to ClickHouse (e.g. Apache Cassandra / ScyllaDB
hard-linked SSTable snapshots, Elasticsearch / OpenSearch incremental snapshots, Apache Druid / Pinot segment
deep-storage backups). In all of these, "incremental" means *"store only the immutable files that did not exist
in the previous backup"*, so the tests below are structured around the same principles.

**The snapshot–mutate–snapshot–restore loop.** Every chain test establishes a known state, backs it up, mutates
the data in a controlled way, takes an incremental backup, and restores the tip onto a clean target:

```text
state S0  → backup B0 (full)
mutate → state S1  → backup B1 (incremental, base=B0)
mutate → state S2  → backup B2 (incremental, base=B1)
restore(B2) on a clean target → assert restored == S2
```

**Two assertion levels.** Each scenario asserts at both levels, because a data-only check can pass while the
feature is silently broken (an "increment" that secretly re-uploaded everything still restores correctly):

* **Black-box (data level):** restored row counts and content checksums equal the source.
* **White-box (artifact level):** backup metadata proves the delta was computed correctly — correct
  `required_backup` pointer, `required: true` on carried-over parts with no remote data objects for them, new
  parts present, and increment `compressed_size` proportional to the actual change.

**Controlling non-determinism.** ClickHouse background merges rewrite/rename parts and are the main source of
flakiness. Scenarios that assert on specific `required` flags control merges explicitly (stop merges, or insert
into distinct partitions); scenarios that specifically test behavior under merges are called out.

**Four properties, repeated in different shapes.** Delta correctness, reconstruction fidelity, chain integrity,
and resilience/scale. The prioritized "must-have" scenarios that cover these four plus the single most dangerous
bug (unsafe deduplication) are: [Single Increment — Happy Path](#single-increment-happy-path),
[Fingerprint Mismatch Re-upload](#fingerprint-mismatch-re-upload),
[Deep Chain Reconstruction](#deep-chain-reconstruction),
[Retention Chain Protection](#retention-chain-protection), and
[Missing Base — Negative Restore](#missing-base-negative-restore).

## Timeline

The testing of `clickhouse-backup` incremental backups SHALL be started on July 20.
## Configuration Requirements

Incremental backups are computed at the level of immutable ClickHouse data parts. A backup carries a single
`required_backup` pointer to its immediate parent, forming a linear chain (full → inc1 → inc2 → ...). The
increment is calculated only while executing `upload` / `create_remote` (or the equivalent REST API calls);
see [Documentation References](#documentation-references).

### Diff Modes

Two mutually-exclusive diff modes select the base backup:

| Mode | Base location | Dedup computed at | Notes |
| ---- | ------------- | ----------------- | ----- |
| `--diff-from-remote=<name>` | Remote storage | `create` and/or `upload` | Requires `general.upload_by_part: true`. Used by `create`, `create_remote`, `upload`, and automatic `watch`. |
| `--diff-from=<name>` | Local only | `upload` | Base backup MUST exist as a local backup; dedup and fingerprint verification happen during upload. |

### Relevant Configuration Options

| Option | Purpose | Default |
| ------ | ------- | ------- |
| `general.upload_by_part` | MUST be `true` for remote incremental backups (per-part upload/dedup) | `true` |
| `general.backups_to_keep_remote` | Remote retention count; MUST protect backups referenced by a chain | `0` (keep all) |
| `general.rebase_before_remove_old_remote` | Rebase the oldest kept increment before pruning ancestors | `false` |
| `use_embedded_backup_restore` | Switch to ClickHouse-native incremental (`BACKUP ... SETTINGS base_backup=...`) | `false` |
| `general.upload_concurrency` / `general.download_concurrency` | Parallel part streams; primary throughput knobs for large data | tune to hardware |
| `s3.http_max_idle_conns_per_host` | Critical for saturating parallel streams to one endpoint (Go default is 2) | tune to hardware |

## Test Environment

The following artifacts and tools will be used:

* `clickhouse-backup` binary under test (regular, non-FIPS build).
* A ClickHouse server image supporting immutable-part `MergeTree` freezing (CH ≥ 21.4 for `os.Link`-based
  hardlinks of frozen shadow parts).
* At least one object-store remote backend (e.g. S3 / MinIO) and one file-protocol backend (e.g. SFTP or FTP),
  each using an isolated storage prefix/bucket per test run.
* A clean, separate ClickHouse instance (or a fresh multi-shard cluster) used exclusively as the restore
  target, to prove that a chain is self-sufficient.
* A deterministic dataset generator allowing control over which parts change between backups
  (insert into distinct partitions; `SYSTEM STOP MERGES` to keep parts stable; `OPTIMIZE ... FINAL` to force
  part churn).

> [!NOTE]
> Background merges rename/rewrite parts and are the main source of flakiness in incremental tests.
> Scenarios that assert on specific `required` flags MUST control merges explicitly (stop merges or use
> distinct partitions) unless the scenario's purpose is to test behavior under merges. This behavior is
> documented: the size of an increment depends on both ingest intensity and background-merge intensity
> (see [Documentation References](#documentation-references)).

## Terminology and Backup Chain Model

* **Full backup** — `required_backup` is empty; all parts stored in this backup.
* **Increment** — `required_backup` points at the immediate parent; parts already in the chain are
  `required: true` and stored only in an ancestor.
* **Chain** — the transitive closure of `required_backup` pointers from a backup up to its full base.
* **Rebase** — copying required parts into an increment so it no longer depends on ancestors
  (`required_backup` cleared, per-part `required` flags cleared).

## Metadata Assertion Model

Every scenario that verifies delta correctness SHALL inspect backup metadata, not only restored data.

Top-level `backup_name/metadata.json` fields:

| Field | Meaning | Typical assertion |
| ----- | ------- | ----------------- |
| `required_backup` | Immediate parent (empty = full) | Points at the expected base |
| `data_size` | Local shadow size at create | Present |
| `compressed_size` | Bytes actually uploaded | Increment ≪ full backup |
| `object_disk_size` | Object-disk blob bytes uploaded | Zero for fully-deduped object-disk parts |
| `data_format` | Compression format or `directory` | As configured |

Per-part metadata (`backup_name/metadata/<db>/<table>.json`, `Part` entries):

| Field | Meaning | Typical assertion |
| ----- | ------- | ----------------- |
| `name` | Part name | — |
| `required` | `true` → data lives in an ancestor, not this backup | Carried-over parts `true`; new/changed parts absent/`false` |

The part-level dedup decision uses `hash_of_all_files` (falling back to legacy `checksums`) so a name match with
a content change is NOT deduplicated (see [Implementation References](#implementation-references)).

## References

All claims and expected behaviors in this plan are derived from the `clickhouse-backup` source code and official
documentation listed below. Line numbers are indicative of the version at the time of writing and may drift.

### Implementation References

| Behavior | Location |
| --- | --- |
| `create` entry point with `diffFromRemote` | `pkg/backup/create.go` — `CreateBackup(...)` (~line 63) |
| `create_remote` = create + upload | `pkg/backup/create_remote.go` (~lines 11–31) |
| `upload` entry point with `diffFrom` / `diffFromRemote` | `pkg/backup/upload.go` — `Upload(...)` (~line 38) |
| `upload_by_part` required for remote incremental | `pkg/backup/upload.go` (~lines 433–434); default in `pkg/config/config.go` (~line 836) |
| Load base table metadata from remote / local | `pkg/backup/backuper.go` — `getTablesDiffFromRemote` (~531–561), `getTablesDiffFromLocal` (~508) |
| Part-name dedup at create (skip hardlink for required) | `pkg/filesystemhelper/filesystemhelper.go` — `addRequiredPartIfNotExists` (~467–491) |
| Fingerprint validation / demote `Required` on mismatch (issue #1307) | `pkg/backup/create.go` (~1081–1111) |
| Part-name + fingerprint dedup at upload | `pkg/backup/upload.go` — `markDuplicatedParts` (~832–878); skip upload of required parts (~929–935) |
| Move/hardlink frozen shadow to local backup | `pkg/filesystemhelper/filesystemhelper.go` — `MoveShadowToBackup` (~274–366), `LinkPartFromShadow` (~368–407) |
| Object-disk blobs skipped for required parts | `pkg/backup/create.go` — `uploadObjectDiskParts` (~1332–1344) |
| Embedded incremental via `base_backup` | `pkg/backup/create.go` (~683–689) |
| Top-level metadata `RequiredBackup` written | `pkg/backup/create.go` — `createBackupMetadata` (~1450–1457); final remote metadata `pkg/backup/upload.go` (~256–270) |
| Recursive download of `RequiredBackup` chain (issue #1384) | `pkg/backup/download.go` (~184–196) |
| Restore resolution of required parts (hardlink or download) | `pkg/backup/restore.go` — `prepareRequiredPartsForRestore` / `restoreRequiredPart` (~2381–2445) |
| Retention protects referenced chains | `pkg/storage/compression.go` — `GetBackupsToDeleteRemote` (~29–62) |
| Rebase copies required parts server-side, clears deps | `pkg/backup/rebase.go` |
| Metadata structs | `pkg/metadata/backup_metadata.go`, `table_metadata.go`, `part_metadata.go` |
| CLI flags (`--diff-from`, `--diff-from-remote`, `rebase`, `watch`) | `cmd/clickhouse-backup/main.go` |

### Documentation References

| Topic | Location |
| --- | --- |
| How incremental backups work with remote storage (part-level vs embedded, `required_backup`, recursive download, increment size vs merges) | `Examples.md` (~1195–1216) |
| How `watch` builds full + incremental sequences | `Examples.md` (~1230–1233) |
| Back up / restore a sharded cluster (per-shard, first-replica-per-shard, schema-on-all / data-on-first) | `Examples.md` (~80–108) |
| Sharded cluster incremental with Ansible (full on day 1, increments otherwise, `--diff-from`) | `Examples.md` (~110–146) |
| Performance tuning for large data (concurrency, `http_max_idle_conns_per_host`, compression) | `Examples.md` (~164–227) |
| CLI help: `create`, `create_remote`, `upload`, `rebase`, `watch` flags incl. `--partitions`, `full_type=rebase`, `delete_previous_cycle` | `Manual.md` (~31, 66, 103, 168–174, 375–393) |
| Requirements specification (RQ.SRS-013 IncrementalBackups) | `test/testflows/clickhouse_backup/requirements/requirements.md` |

## Human Resources And Assignments

The following team members SHALL be dedicated to this effort:

* Vitalii Sviderskyi (regression tests)
* Vitaliy Zakaznikov (manager, regression tests)
* Eugene Klimov (clickhouse-backup)

## Release Notes

* https://github.com/Altinity/clickhouse-backup/blob/master/ChangeLog.md
* https://github.com/Altinity/clickhouse-backup/blob/master/Examples.md

---

## Single Increment — Happy Path

**Objective:** Verify a full backup followed by one incremental backup deduplicates unchanged parts and
restores correctly.

**Steps:**

1. Create a `MergeTree` table partitioned by a controllable key; insert dataset A into partition `p1`.
2. `create_remote <full>`.
3. Insert dataset B into a new partition `p2` (new parts only).
4. `create_remote --diff-from-remote=<full> <inc1>`.
5. On a clean ClickHouse instance, `restore_remote <inc1>`.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Parent pointer | `<inc1>` metadata `required_backup == <full>` |
| Old parts deduplicated | Parts of `p1` marked `required: true`; no remote data objects for them under `<inc1>` |
| New parts uploaded | Parts of `p2` not `required`; present in remote data of `<inc1>` |
| Size efficiency | `<inc1>` `compressed_size` materially smaller than `<full>` |
| Data fidelity | Restored row count and content checksum equal source (A ∪ B) |

## Pure Additions Deduplication

**Objective:** Verify that when only new parts are added, all base parts are deduplicated.

**Steps:**

1. `create_remote <full>` over dataset A.
2. Insert only into new partitions (no changes to existing parts); stop merges to keep base parts stable.
3. `create_remote --diff-from-remote=<full> <inc>`.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Full dedup of base | Every base part is `required: true` in `<inc>` |
| Only new parts stored | Only the newly-inserted parts have remote data objects |
| Restore | Tip restore equals source |

## No-op Increment

**Objective:** Verify an increment taken with no data changes stores ~zero data and still restores the full
state.

**Steps:**

1. `create_remote <full>` over dataset A; stop merges.
2. Without any changes, `create_remote --diff-from-remote=<full> <inc>`.
3. `restore_remote <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| All parts required | Every part in `<inc>` is `required: true` |
| Near-zero payload | `<inc>` `compressed_size` ≈ metadata-only |
| Restore | Restored data equals dataset A |

## Fingerprint Mismatch Re-upload

**Objective:** Verify the delta-safety invariant — a part with the same name but different content is
re-uploaded, not deduplicated. (Highest-risk correctness case; see issue #1307 in
[Implementation References](#implementation-references).)

**Steps:**

1. `create_remote <full>` over dataset A.
2. Force a part to change contents while keeping (or colliding on) its name — e.g. `OPTIMIZE TABLE ... FINAL`
   or a mutation that rewrites an existing partition.
3. `create_remote --diff-from-remote=<full> <inc>`.
4. `restore_remote <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Mismatch detected | The changed part is NOT `required`; it is uploaded to `<inc>` |
| Fingerprint basis | Decision driven by differing `hash_of_all_files` / `checksums`, not just name |
| Data fidelity | Restore reflects the changed (post-`OPTIMIZE`/mutation) content, not stale base content |

## Dropped Partition / Deletions

**Objective:** Verify that dropping data before an increment is reflected on restore.

**Steps:**

1. `create_remote <full>` over partitions `p1`, `p2`.
2. `ALTER TABLE ... DROP PARTITION p2`.
3. `create_remote --diff-from-remote=<full> <inc>`.
4. `restore_remote <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Retained parts | `p1` parts `required: true` |
| Dropped data absent | Restored table contains only `p1`; `p2` data absent |

## Deep Chain Reconstruction

**Objective:** Verify recursive resolution of required parts across a chain deeper than two.

**Steps:**

1. Build chain: `create_remote <full>` → `--diff-from-remote=<full> <inc1>` → `--diff-from-remote=<inc1> <inc2>`
   → `--diff-from-remote=<inc2> <inc3>`, each adding a new partition.
2. `restore_remote <inc3>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Chain pointers | Each increment's `required_backup` points at its immediate parent |
| Recursive resolution | Download/restore pulls required parts from ancestors up the whole chain |
| Data fidelity | Restored data equals the accumulated state at `<inc3>` |

## Restore of an Intermediate Increment

**Objective:** Verify restoring a non-tip increment yields that increment's point-in-time state.

**Steps:**

1. Build chain `full` → `inc1` → `inc2` → `inc3` (as above).
2. `restore_remote <inc1>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Point-in-time state | Restored data equals state as of `<inc1>` (not `<inc2>`/`<inc3>`) |

## Local Hardlink vs Remote Download Resolution

**Objective:** Verify both required-part resolution paths (local hardlink and remote download) produce
identical results.

**Steps:**

1. Build `full` → `inc1` on remote.
2. Case A (hardlink): ensure `<full>` is present locally, then download/restore `<inc1>` so required parts are
   hardlinked from the local base.
3. Case B (download): with no local base present, download/restore `<inc1>` so required parts are downloaded
   from remote.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Case A | Required parts resolved via local hardlink; restore equals source |
| Case B | Required parts resolved via remote download; restore equals source |
| Equivalence | Both cases produce identical restored data |

## Retention Chain Protection

**Objective:** Verify retention never deletes a backup referenced by a `required_backup` chain.

**Steps:**

1. Build a 5-backup chain via `--diff-from-remote`.
2. Set `general.backups_to_keep_remote = 3` (or `BACKUPS_TO_KEEP_REMOTE=3`) and run remote cleanup.
3. Download and restore the latest backup on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Referenced ancestors kept | Backups still referenced by the kept chain are NOT deleted, even if outside the keep window |
| Latest restorable | Downloading the latest backup pulls its full chain and restores successfully |

## Missing Base — Negative Restore

**Objective:** Verify that restoring an increment whose base is missing fails cleanly with a clear error.

**Steps:**

1. Build `full` → `inc1`.
2. Delete `<full>` from remote (bypassing chain protection, e.g. explicit delete) so the chain is broken.
3. Attempt `restore_remote <inc1>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Clean failure | The operation exits non-zero with a specific error referencing the missing required backup |
| No silent partial restore | No partial/corrupt table is left presented as a successful restore; no crash |

## Rebase / Chain Flattening

**Objective:** Verify `rebase` makes an increment self-contained and restorable after ancestors are removed.

**Steps:**

1. Build `full` → `inc1` → `inc2`.
2. `rebase <inc2>` (CLI or `POST /backup/rebase/{name}`).
3. Delete ancestors `<full>` and `<inc1>`.
4. `restore_remote <inc2>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Flattened metadata | `<inc2>` `required_backup` cleared; previously `required` parts now stored in `<inc2>` |
| Self-sufficient restore | Restore succeeds after ancestors deleted; data equals state at `<inc2>` |

## Resume After Interruption

**Objective:** Verify an interrupted incremental upload resumes without re-uploading deduplicated parts.

**Steps:**

1. `create_remote <full>`; add new partitions.
2. Start `upload --diff-from-remote=<full> <inc>` and interrupt it mid-upload (kill/restart).
3. Re-run the same upload to resume.
4. `restore_remote <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Resume completes | Upload finishes on retry without error |
| No redundant work | Deduplicated (`required`) parts are not uploaded on resume |
| Data fidelity | Final restore equals source |

## Concurrent Merges During Chain

**Objective:** Verify chain integrity when background merges rewrite parts between backups.

**Steps:**

1. `create_remote <full>` with merges enabled.
2. Allow/trigger background merges, insert new data, then `create_remote --diff-from-remote=<full> <inc>`.
3. `restore_remote <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Correct dedup under churn | Merged (renamed/rewritten) parts are treated as new via fingerprint; unchanged parts still deduplicated |
| Data fidelity | Restored data equals source despite merges |

## Diff Mode Matrix

**Objective:** Verify `--diff-from` (local base) and `--diff-from-remote` (remote base) behave equivalently
for the same data changes.

**Steps:**

1. For each mode, build `full` → `inc` over identical data changes:
   * `--diff-from-remote`: dedup at create/upload against the remote base.
   * `--diff-from`: keep `<full>` locally; `upload --diff-from=<full>` dedups at upload.
2. Restore each `<inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Equivalent dedup | Both modes mark the same parts `required` and upload the same new parts |
| Equivalent restore | Both restored datasets equal the source and each other |

## Object-Disk Increment

**Objective:** Verify blob deduplication for `required` parts on object-disk-backed tables.

**Steps:**

1. Create an object-disk-backed table; `create_remote <full>`.
2. Add new parts; `create_remote --diff-from-remote=<full> <inc>`.
3. `restore_remote <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Blob dedup | Object-disk blobs for `required` parts are skipped (`object_disk_size` reflects only new blobs) |
| Data fidelity | Restore equals source |

## Embedded Incremental Backup

**Objective:** Verify the ClickHouse-native incremental path (`use_embedded_backup_restore: true`), which
deduplicates at the file/checksum level via `BACKUP ... SETTINGS base_backup=...`.

**Steps:**

1. With `use_embedded_backup_restore: true`, `create_remote <full>`.
2. Add data; `create_remote --diff-from-remote=<full> <inc>` (issues `BACKUP ... SETTINGS base_backup=<full>`).
3. `restore_remote <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Native base backup used | Increment created against the base via ClickHouse `base_backup` setting |
| Efficiency | Increment stores only changed files/checksums relative to the base |
| Data fidelity | Restore equals source |

## Partition-Scoped Increment

**Objective:** Verify `--partitions` restricts an incremental backup to selected partitions while preserving
correct dedup and restore. (`--partitions` is supported by `create` / `create_remote` / `upload`; see
[Documentation References](#documentation-references).)

**Steps:**

1. Create a table with partitions `p1`, `p2`, `p3`; `create_remote <full>`.
2. Add data to `p2` and `p3`; `create_remote --diff-from-remote=<full> --partitions=p2,p3 <inc>`.
3. `restore_remote --partitions=p2,p3 <inc>` on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Scope respected | Only `p2`/`p3` parts are considered; `p1` is not backed up in `<inc>` |
| Dedup within scope | Unchanged in-scope parts marked `required`; changed/new in-scope parts uploaded |
| Restore | Restored `p2`/`p3` data equals source |

## Sharded Cluster Incremental Backup

**Objective:** Verify per-shard incremental backup chains on a sharded cluster restore correctly, following the
documented sharded-cluster workflow (run on the first replica per shard; restore schema on all replicas, data on
first replica per shard). See [Documentation References](#documentation-references).

**Steps:**

1. Deploy a multi-shard cluster (e.g. 2 shards × 2 replicas) with `{shard}` in the remote `S3_PATH`.
2. On the first replica of each shard, create a per-shard full backup:
   `create_remote shard${shard}-full`.
3. Ingest new data on each shard, then create a per-shard increment:
   `create_remote --diff-from-remote=shard${shard}-full shard${shard}-inc1`.
4. On a fresh cluster of the same topology, restore schema on all replicas then data on the first replica per
   shard: `restore_remote --schema shard${shard}-inc1` (all replicas) then `restore_remote shard${shard}-inc1`
   (first replica per shard).

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Per-shard chains | Each shard has an independent `full → inc1` chain under its own `{shard}` remote path |
| Per-shard dedup | Each increment deduplicates its own base; unchanged parts `required` |
| Cluster fidelity | Aggregated restored data across shards equals the source cluster's data |
| Replicated schema | Schema present on all replicas; data present on the first replica per shard |

## Large-Scale Cluster Data Volume

**Objective:** Verify incremental backups remain correct and space/time-efficient at scale on a cluster holding
a large amount of data (many partitions, many parts, large total size), including throughput tuning and resume
under load.

**Steps:**

1. Deploy a multi-shard cluster and load a large dataset (e.g. billions of rows across hundreds of partitions,
   totaling a large on-disk size per shard). Record baseline size via `system.parts`.
2. Configure throughput knobs for large data:
   `upload_concurrency` / `download_concurrency` raised, `s3.http_max_idle_conns_per_host` raised, and a
   suitable `compression_*` profile (see [Documentation References](#documentation-references)).
3. Per shard, `create_remote shard${shard}-full`; record duration and `compressed_size`.
4. Mutate a small, known fraction of the data (append to a few new partitions and/or `OPTIMIZE FINAL` a few
   existing partitions), then per shard
   `create_remote --diff-from-remote=shard${shard}-full shard${shard}-inc1`; record duration and
   `compressed_size`.
5. During the increment upload, interrupt and resume once (`--resumable`) to exercise resume at scale.
6. On a fresh cluster, download and `restore_remote` the latest per-shard increment.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Proportional delta | Increment `compressed_size` and duration are proportional to the *changed* data, not the total dataset size (orders of magnitude smaller than the full backup) |
| Dedup at scale | The large majority of parts across all shards are marked `required`; only changed/new parts uploaded |
| Resume at scale | Interrupted upload resumes without re-uploading already-uploaded or deduplicated parts |
| Throughput | Backup/restore complete within expected time given the configured concurrency; no connection-pool starvation errors |
| Data fidelity | Full row-count and content-checksum comparison per shard equals the source after restore |
| Stability | No out-of-memory / disk-exhaustion; local shadow and temp space bounded (required parts not hardlinked/downloaded unnecessarily) |

## Watch / Scheduled Increment Chain

**Objective:** Verify the `watch` / scheduled workflow builds a full + incremental chain and manages retention,
including `full_type=rebase` and `delete_previous_cycle`. See [Documentation References](#documentation-references).

**Steps:**

1. Start `watch` with a short `--watch-interval` and `--full-interval` (or a `--schedule` cron chain), with
   `backups_to_keep_remote` set.
2. Ingest data between intervals so each cycle produces a new increment (auto `--diff-from-remote=<previous>`),
   with a periodic full backup.
3. For a scheduled chain, also exercise `full_type=rebase` and `delete_previous_cycle=true`.
4. After several cycles, restore the latest backup on a clean instance.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Chain built | Increments reference the previous backup; a full backup is created every `full-interval` |
| Rebase full type | With `full_type=rebase`, the scheduled full is produced by increment + server-side rebase instead of a full re-upload |
| Retention | Old backups without references are deleted; referenced chains are preserved; `delete_previous_cycle` removes the prior cycle after a successful full |
| Restore | Latest backup restores correctly |

## Ingest and Merge Intensity Effect on Increment Size

**Objective:** Verify (and document) that increment size depends on both ingest batching and background-merge
intensity, per the official guidance to use larger INSERT batches and avoid frequent mutations. See
[Documentation References](#documentation-references).

**Steps:**

1. `create_remote <full>` over a baseline dataset.
2. Case A: ingest the same volume of new rows using **large** INSERT batches (few large new parts), merges idle.
3. Case B: ingest the same volume using **many small** INSERTs and/or trigger heavy merges/mutations.
4. Create an increment for each case with `--diff-from-remote=<full>` and compare `compressed_size`.

**Expected result:**

| Test Assertion | Expected Result |
| --- | --- |
| Batch effect | Case A (large batches, idle merges) produces a smaller increment than Case B for the same logical data volume |
| Correctness | Both cases restore to the same logical data as the source |
| Documented behavior | Results are consistent with the documented dependency of increment size on ingest and merge intensity |
