# QA-SRS013 ClickHouse Backup Utility Incremental Backups
# Software Requirements Specification

## Table of Contents

* 1 [Revision History](#revision-history)
* 2 [Introduction](#introduction)
* 3 [Terminology](#terminology)
    * 3.1 [SRS](#srs)
    * 3.2 [Data Part](#data-part)
    * 3.3 [Incremental Backup](#incremental-backup)
    * 3.4 [Base Backup](#base-backup)
    * 3.5 [Backup Chain](#backup-chain)
    * 3.6 [Part Reuse](#part-reuse)
    * 3.7 [rebase](#rebase)
    * 3.8 [Embedded Backup Mode](#embedded-backup-mode)
    * 3.9 [Restored Data Matches The Source](#restored-data-matches-the-source)
* 4 [Requirements](#requirements)
    * 4.1 [General](#general)
        * 4.1.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Create.DiffFromRemote](#rqsrs-013clickhousebackuputilityincrementalcreatedifffromremote)
        * 4.1.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Create.DiffFrom](#rqsrs-013clickhousebackuputilityincrementalcreatedifffrom)
        * 4.1.3 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.UploadByPart](#rqsrs-013clickhousebackuputilityincrementaluploadbypart)
        * 4.1.4 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Chain](#rqsrs-013clickhousebackuputilityincrementalchain)
        * 4.1.5 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.SupportedTableEngines](#rqsrs-013clickhousebackuputilityincrementalsupportedtableengines)
    * 4.2 [Part Reuse and Deduplication](#part-reuse-and-deduplication)
        * 4.2.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.UnchangedParts](#rqsrs-013clickhousebackuputilityincrementalreuseunchangedparts)
        * 4.2.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.ChangedPart](#rqsrs-013clickhousebackuputilityincrementalreusechangedpart)
        * 4.2.3 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.NoChanges](#rqsrs-013clickhousebackuputilityincrementalreusenochanges)
        * 4.2.4 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.DeletedData](#rqsrs-013clickhousebackuputilityincrementalreusedeleteddata)
        * 4.2.5 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.MergedParts](#rqsrs-013clickhousebackuputilityincrementalreusemergedparts)
        * 4.2.6 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.PartFormats](#rqsrs-013clickhousebackuputilityincrementalreusepartformats)
    * 4.3 [Restore](#restore)
        * 4.3.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore](#rqsrs-013clickhousebackuputilityincrementalrestore)
        * 4.3.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.Chain](#rqsrs-013clickhousebackuputilityincrementalrestorechain)
        * 4.3.3 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.EarlierBackup](#rqsrs-013clickhousebackuputilityincrementalrestoreearlierbackup)
        * 4.3.4 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.BaseFromRemote](#rqsrs-013clickhousebackuputilityincrementalrestorebasefromremote)
        * 4.3.5 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.BaseFromLocal](#rqsrs-013clickhousebackuputilityincrementalrestorebasefromlocal)
        * 4.3.6 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.MissingBase](#rqsrs-013clickhousebackuputilityincrementalrestoremissingbase)
        * 4.3.7 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.NonEmptyTarget](#rqsrs-013clickhousebackuputilityincrementalrestorenonemptytarget)
        * 4.3.8 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.DataOnly](#rqsrs-013clickhousebackuputilityincrementalrestoredataonly)
        * 4.3.9 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.Partitions](#rqsrs-013clickhousebackuputilityincrementalrestorepartitions)
        * 4.3.10 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.DifferentSchema](#rqsrs-013clickhousebackuputilityincrementalrestoredifferentschema)
        * 4.3.11 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.DifferentPartitionBy](#rqsrs-013clickhousebackuputilityincrementalrestoredifferentpartitionby)
    * 4.4 [Retention](#retention)
        * 4.4.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Retention.RequiredBackups](#rqsrs-013clickhousebackuputilityincrementalretentionrequiredbackups)
    * 4.5 [Rebase Command](#rebase-command)
        * 4.5.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Rebase.SelfContained](#rqsrs-013clickhousebackuputilityincrementalrebaseselfcontained)
    * 4.6 [Watch Command](#watch-command)
        * 4.6.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Watch.Chains](#rqsrs-013clickhousebackuputilityincrementalwatchchains)
        * 4.6.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Watch.Restore](#rqsrs-013clickhousebackuputilityincrementalwatchrestore)
    * 4.7 [Upload](#upload)
        * 4.7.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Upload.Resume](#rqsrs-013clickhousebackuputilityincrementaluploadresume)
    * 4.8 [Partitions](#partitions)
        * 4.8.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Partitions.Scope](#rqsrs-013clickhousebackuputilityincrementalpartitionsscope)
    * 4.9 [Object Disks](#object-disks)
        * 4.9.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.ObjectDisk.S3](#rqsrs-013clickhousebackuputilityincrementalobjectdisks3)
    * 4.10 [Embedded Backups](#embedded-backups)
        * 4.10.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Embedded.BaseBackup](#rqsrs-013clickhousebackuputilityincrementalembeddedbasebackup)
    * 4.11 [Patch Parts](#patch-parts)
        * 4.11.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.PatchParts.PartitionsMiss](#rqsrs-013clickhousebackuputilityincrementalpatchpartspartitionsmiss)
        * 4.11.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.PatchParts.ApplyPatches](#rqsrs-013clickhousebackuputilityincrementalpatchpartsapplypatches)
    * 4.12 [ClickHouse Version Compatibility](#clickhouse-version-compatibility)
        * 4.12.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Versions.Supported](#rqsrs-013clickhousebackuputilityincrementalversionssupported)
        * 4.12.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Versions.MixedChain](#rqsrs-013clickhousebackuputilityincrementalversionsmixedchain)
    * 4.13 [Cluster](#cluster)
        * 4.13.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.Sharded](#rqsrs-013clickhousebackuputilityincrementalclustersharded)
        * 4.13.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.Replicated.Schema](#rqsrs-013clickhousebackuputilityincrementalclusterreplicatedschema)
        * 4.13.3 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.Replicated.Data](#rqsrs-013clickhousebackuputilityincrementalclusterreplicateddata)
        * 4.13.4 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.NonReplicated](#rqsrs-013clickhousebackuputilityincrementalclusternonreplicated)
    * 4.14 [Concurrent Operations](#concurrent-operations)
        * 4.14.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Insert](#rqsrs-013clickhousebackuputilityincrementalconcurrentinsert)
        * 4.14.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Merge](#rqsrs-013clickhousebackuputilityincrementalconcurrentmerge)
        * 4.14.3 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Mutation](#rqsrs-013clickhousebackuputilityincrementalconcurrentmutation)
        * 4.14.4 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.InconsistentColumnTypes](#rqsrs-013clickhousebackuputilityincrementalconcurrentinconsistentcolumntypes)
        * 4.14.5 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Restore](#rqsrs-013clickhousebackuputilityincrementalconcurrentrestore)
    * 4.15 [Backup Storage Location](#backup-storage-location)
        * 4.15.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Storage.SingleRemote](#rqsrs-013clickhousebackuputilityincrementalstoragesingleremote)
        * 4.15.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Storage.ChangedLocation](#rqsrs-013clickhousebackuputilityincrementalstoragechangedlocation)
        * 4.15.3 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Storage.ChainMigration](#rqsrs-013clickhousebackuputilityincrementalstoragechainmigration)
    * 4.16 [Backup Size](#backup-size)
        * 4.16.1 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Size.SmallerThanFull](#rqsrs-013clickhousebackuputilityincrementalsizesmallerthanfull)
        * 4.16.2 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Size.InsertPattern](#rqsrs-013clickhousebackuputilityincrementalsizeinsertpattern)
        * 4.16.3 [RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Size.LargeDataset](#rqsrs-013clickhousebackuputilityincrementalsizelargedataset)
* 5 [References](#references)

## Revision History

This document is stored in an electronic form using [Git] source control management software hosted in the [GitHub Repository].
All the updates are tracked using the [Revision History].

## Introduction

This [SRS] covers the requirements for **incremental backups** of the [clickhouse-backup] utility.

An incremental backup stores only the data that changed since a previous backup instead of copying the whole
table again, which makes backups smaller and faster. The requirements below define how an increment is created,
which data it stores and reuses, how it is restored, and how the dependency on its base backup is kept intact by
retention, `rebase`, `watch`, and storage moves.

## Terminology

### SRS

Software Requirements Specification.

### Data Part

An immutable set of files that [ClickHouse] writes for a MergeTree-family table. A part is never modified in
place — changes produce new parts, and background merges replace several parts with one new part.

### Incremental Backup

A backup created with `--diff-from-remote=<name>` or `--diff-from=<name>` that stores only the parts that are
not already present in its base backup.

### Base Backup

The backup an incremental backup is built on top of. It is recorded in the increment's `metadata.json` as
`required_backup: <name>`.

### Backup Chain

The linear sequence of backups formed by the `required_backup` links, from an incremental backup back to the
full backup that starts the chain.

### Part Reuse

The decision to not upload a part again because the base backup already holds it. Reuse is decided by the
part's **name** and a **content fingerprint** of its files (`hash_of_all_files`, or a CRC64 of `checksums.txt`
on older ClickHouse versions).

### rebase

The command that copies all reused parts of a chain into a chosen incremental backup, removes its
`required_backup` link, and turns it into a self-contained full backup.

### Embedded Backup Mode

The mode enabled by `use_embedded_backup_restore: true`, where [clickhouse-backup] uses the native ClickHouse
`BACKUP` / `RESTORE` commands and ClickHouse itself computes the difference against `base_backup`.

### Restored Data Matches The Source

The restored table and the source table SHALL return the same row count (`SELECT count()`) and the same
order-independent fingerprint over all columns (`SELECT sum(cityHash64(*)), count()`). For deduplicating or
aggregating engines both values SHALL be taken with `FINAL` or after `OPTIMIZE TABLE ... FINAL` on both sides.

## Requirements

### General

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Create.DiffFromRemote
version: 1.0

The [clickhouse-backup] utility SHALL support creating an incremental backup against a base backup that lives on
remote storage using `--diff-from-remote=<name>` with the `create`, `create_remote`, `upload`, and `watch`
commands.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Create.DiffFrom
version: 1.0

The [clickhouse-backup] utility SHALL support creating an incremental backup against a base backup that is
present locally using `--diff-from=<name>` with the `create_remote` and `upload` commands.

For the same data change, `--diff-from` and `--diff-from-remote` SHALL store only the changed data and SHALL
restore identical data.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.UploadByPart
version: 1.0

The [clickhouse-backup] utility SHALL require `upload_by_part: true` (the default) for `--diff-from-remote`.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Chain
version: 1.0

Each incremental backup SHALL record exactly one base backup in its `metadata.json` as
`required_backup: <name>`, forming a linear chain that ends at a full backup.

The chain depth is not limited by the utility.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.SupportedTableEngines
version: 1.0

Incremental part reuse SHALL apply to MergeTree-family tables and their `Replicated*` variants,
because only these engines store data as parts.

Tables of engines that have no parts (`Memory`, `Log`, `TinyLog`, `Set`) SHALL still be backed up, but SHALL NOT
participate in parts reuse.

### Part Reuse and Deduplication

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.UnchangedParts
version: 1.0

An incremental backup SHALL reuse every part whose name and fingerprint match a part in its base backup, and
SHALL upload only the parts that are new or changed.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.ChangedPart
version: 1.0

When a part has the same name as a part in the base backup but different file contents (for example after
`OPTIMIZE TABLE ... FINAL` or a mutation), the incremental backup SHALL upload the new content and SHALL NOT
skip it as unchanged.

The restored table SHALL contain the rewritten data, not the old one.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.NoChanges
version: 1.0

An incremental backup taken when no data changed SHALL store only metadata and no table data, and SHALL still
restore the complete data of the chain.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.DeletedData
version: 1.0

When data is deleted before the increment is created (for example `ALTER TABLE ... DROP PARTITION`), the
restored table SHALL NOT contain the deleted data.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.MergedParts
version: 1.0

When a background merge or `OPTIMIZE TABLE ... FINAL` replaces parts that are present in the base backup with a
new merged part, the incremental backup SHALL upload the merged part as new data, because its name and
fingerprint do not match the base.

The restore SHALL still be correct, only the space saving is reduced.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Reuse.PartFormats
version: 1.0

Part reuse and restore SHALL behave identically for all physical part layouts:

* part type `Wide` and `Compact`
* on-disk storage format `Full` and `Packed`

At minimum `Wide`+`Full`, `Compact`+`Full`, and one `Packed` combination SHALL be covered.

### Restore

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore
version: 1.0

Restoring an incremental backup SHALL produce a table whose data matches the source
at the moment that backup was created.

An incremental backup SHALL store the complete table schema of its own, so the schema step SHALL NOT need the
base backup.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.Chain
version: 1.0

Restoring an incremental backup SHALL automatically collect the reused parts from every earlier backup of the
chain, up to and including the full backup, without the user naming them.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.EarlierBackup
version: 1.0

Restoring a backup from the middle of a chain SHALL produce the data as it was when that backup was created,
and SHALL NOT include data added by later increments.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.BaseFromRemote
version: 1.0

Restore of an incremental backup SHALL download reused parts from remote storage by default.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.BaseFromLocal
version: 1.0

If the base backup is already on the local disk, restore SHALL take reused parts from there.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.MissingBase
version: 1.0

When a backup of the chain is missing from remote storage, `download` / `restore_remote` of a dependent
incremental backup SHALL fail with an error naming the missing backup
(`'<name>' is not found on remote storage`) and SHALL NOT leave a partially restored table.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.NonEmptyTarget
version: 1.0

A plain `restore` of an incremental backup (and `restore --rm`) SHALL drop and recreate the target table before
attaching parts. A populated target SHALL end up matching the source with no duplicated rows.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.DataOnly
version: 1.0

A data-only restore (`restore --data`) SHALL NOT drop the target table and SHALL attach the backup's parts on
top of the existing data.

Overlapping rows SHALL become duplicated rows (this is intentional behaviour). The attached parts SHALL stay valid and queryable.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.Partitions
version: 1.0

`restore --data --partitions=<list>` SHALL drop and replace only the named partitions and SHALL leave every
other partition of the target untouched.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.DifferentSchema
version: 1.0

When the target table has a different schema than the backup:

* a plain `restore` (or `--rm`) SHALL drop the target by name and recreate it from the backup's `CREATE`
  statement, so the final table has the backup's schema and data.
* `restore --data` SHALL keep the target's schema and rely on ClickHouse validation of `ATTACH PART`, failing
  with an error when the structures are incompatible.

The utility SHALL NOT merge or reconcile the two differing schemas.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Restore.DifferentPartitionBy
version: 1.0

`restore --data` into a table whose `PARTITION BY` differs from the backup SHALL fail on `ATTACH PART`, because
the target computes a different `partition_id` than the part has. No data SHALL be attached and no silent
re-partitioning SHALL happen.

The same restore run with `--rm` SHALL succeed, since the table is recreated from the backup's own `CREATE`.

### Backup Retention

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Retention.RequiredBackups
version: 1.0

Remote retention (`backups_to_keep_remote`) SHALL keep the newest N backups.
It SHALL NOT delete an older backup if a kept backup still depends on it via `required_backup`.
The newest backup SHALL remain restorable after the cleanup.

### Rebase Command

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Rebase.SelfContained
version: 1.0

The `rebase` command SHALL copy every reused part of the chain into the chosen incremental backup on remote
storage and SHALL clear its `required_backup`, turning it into a full backup.

After `rebase`, the earlier backups of the chain SHALL be deletable and the rebased backup SHALL still restore
correctly on its own.

### Watch Command

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Watch.Chains
version: 1.0

The `watch` command SHALL create a full backup every `--full-interval`.
It SHALL create an incremental backup every `--watch-interval`.
Each increment SHALL use the previous backup as its base with `--diff-from-remote`.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Watch.Restore
version: 1.0

The latest backup created by `watch` SHALL restore correctly.

### Upload

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Upload.Resume
version: 1.0

An interrupted incremental upload SHALL finish when the same upload is run again.
It SHALL NOT upload data that was already uploaded.
The finished backup SHALL restore correctly.

### Partitions

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Partitions.Scope
version: 1.0

`--partitions=<list>` SHALL limit an incremental backup to the named partitions only, and those partitions
SHALL restore correctly.

### Object Disks

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.ObjectDisk.S3
version: 1.0

Incremental backups of tables stored on an S3 object disk SHALL reuse unchanged object-disk data instead of
copying it again, and SHALL restore data that matches the source.

This applies to ClickHouse versions that support the object-disk type under test (S3 from 21.8, GCS over S3 from
22.6, Azure Blob from 23.3).

### Embedded Backups

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Embedded.BaseBackup
version: 1.0

With `use_embedded_backup_restore: true`, `create --diff-from-remote` SHALL pass the base to ClickHouse as
`BACKUP ... SETTINGS base_backup=...`.
The incremental backup SHALL store only the changes relative to the base.
The restored data SHALL match the source.

This applies to ClickHouse 22.8+. `--diff-from` is not used in embedded mode.

### Patch Parts

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.PatchParts.PartitionsMiss
version: 1.0

A lightweight `UPDATE` writes a patch part in a separate partition named `patch-<hash>-<original_partition_id>`.
[clickhouse-backup] has no special handling for patch parts.
An incremental backup with `--partitions` set to the original partition SHALL NOT include that patch part.
The restored data SHALL NOT contain the pending update.

This applies to ClickHouse 25.8+.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.PatchParts.ApplyPatches
version: 1.0

[clickhouse-backup] has no special handling for patch parts from a lightweight `UPDATE`.
After `ALTER TABLE ... APPLY PATCHES` (or after background merges finish), the update is written into Wide or Compact parts.
No `patch-<hash>-<original_partition_id>` partition SHALL remain.
An incremental backup SHALL include that update.
The restored data SHALL contain the updated data.

This applies to ClickHouse 25.8+.

### ClickHouse Version Compatibility

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Versions.Supported
version: 1.0

Regular incremental backups (`--diff-from`, `--diff-from-remote`) SHALL be supported on every ClickHouse version
of the TestFlows matrix: `22.3`, `22.8`, `23.3`, `23.8`, `24.3`, `24.8`, `25.3`, `25.8`, `26.3`.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Versions.MixedChain
version: 1.0

A chain whose base backup was created on ClickHouse version X and whose increments are created on a newer
version Y SHALL work, because part reuse is decided only by part name and fingerprint and never by the
ClickHouse version.
The following SHALL be true:

* Parts not rewritten by the upgrade SHALL be reused from the base.
* Parts rewritten on Y SHALL be uploaded as new data.
* Each backup's `metadata.json` SHALL report the `clickhouse_version` that created it, and this field SHALL NOT
  gate reuse, download, or restore.
* The restored table SHALL match the source.

### Cluster

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.Sharded
version: 1.0

On a sharded cluster, each node SHALL keep its own full → incremental chain under a per-node backup name, and
restoring every node from its own increment SHALL reproduce the combined cluster data.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.Replicated.Schema
version: 1.0

For `Replicated*MergeTree` tables, the schema step (`restore_remote --schema --rm`) SHALL be run on every
replica, so each replica has its own table definition and its own replica entry in Zookeeper/Keeper.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.Replicated.Data
version: 1.0

For `Replicated*MergeTree` tables, the data step (`restore_remote --data`) SHALL be run on only the first
replica of each shard.

* That replica SHALL attach the increment's own parts plus the `required` parts pulled from the chain.
* The sibling replicas SHALL receive the same data through native replication and SHALL NOT need the backup
  chain locally.
* Running the data step on more than one replica of the same shard SHALL duplicate rows;
  `check_replicas_before_attach: true` (the default) SHALL guard against this.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Cluster.NonReplicated
version: 1.0

For plain (non-`Replicated`) MergeTree-family tables, data SHALL NOT propagate between nodes. Restoring data on
one node SHALL leave the other nodes empty, so the data step SHALL be run on every node that must hold the data.

### Concurrent Operations

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Insert
version: 1.0

An incremental backup created while a table is being written SHALL contain exactly the parts that were active at
`ALTER TABLE ... FREEZE` time. Rows inserted before the freeze SHALL be in the backup. Rows inserted after it
SHALL NOT, and no partially written part SHALL be included.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Merge
version: 1.0

A merge running during backup creation SHALL affect only how much the increment uploads, not correctness:

* If the merge finished before the freeze, the new merged part SHALL be uploaded as new data.
* If it did not finish, the original parts SHALL be reused from the base.

In both cases the restore SHALL match the source, with no data loss and no corruption.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Mutation
version: 1.0

An incremental backup created while a mutation (`ALTER TABLE ... UPDATE` / `DELETE`) is in progress SHALL upload
the new mutation parts as new data and SHALL record in-progress mutations when `backup_mutations` is enabled, so
a restore (with `restore_as_attach` where required) SHALL reflect the mutated data.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.InconsistentColumnTypes
version: 1.0

When a concurrent `ALTER TABLE ... MODIFY COLUMN` leaves active parts with inconsistent column types, backup
creation with the default `check_parts_columns: true` SHALL abort with *"inconsistent data types for active data
part"* instead of producing a backup.

With `--skip-check-parts-columns` the backup SHALL proceed.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Concurrent.Restore
version: 1.0

Restore SHALL be intended for a target that is not being written to:

* `restore --data` with a concurrent writer SHALL produce duplicated / overlapping rows, without corruption.
* A plain `restore` SHALL drop and recreate the table, so a concurrent writer may see *"table doesn't exist"* or
  lose writes during that window.

### Backup Storage Location

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Storage.SingleRemote
version: 1.0

An incremental backup SHALL record only the **name** of its base backup — no endpoint, storage type, bucket, or
path — and SHALL resolve it in the currently configured remote storage.

The whole chain SHALL therefore live in the same `remote_storage`, the same bucket/host, and under the same
`path` prefix. Creating an increment whose base exists only in a different storage SHALL fail.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Storage.ChangedLocation
version: 1.0

Pointing the configuration at a different storage, a different storage type, or a different `path` prefix
without moving the chain SHALL make `download` / `restore_remote` of an incremental backup fail with
`'<base>' is not found on remote storage`, and SHALL NOT restore any data.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Storage.ChainMigration
version: 1.0

Copying the **entire** chain to a new location, preserving backup names and directory layout (including the
referenced `object_disk_path` data for object-disk backups), SHALL let the incremental backup restore correctly
from the new location.

A backup made self-contained with `rebase` SHALL be portable on its own, without the rest of the chain.

### Backup Size

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Size.SmallerThanFull
version: 1.0

An incremental backup SHALL be reported by `clickhouse-backup list` as being significantly smaller than a full
backup of the same table, in proportion to the amount of data that changed.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Size.InsertPattern
version: 1.0

For the same amount of new data, many small inserts SHALL produce a larger incremental backup than a few large inserts.
Parts rewritten by a merge SHALL be uploaded again.
Each case SHALL restore its own source data correctly.

#### RQ.SRS-013.ClickHouse.BackupUtility.Incremental.Size.LargeDataset
version: 1.0

On a large dataset with a small change, the incremental backup SHALL be far smaller and faster than the full backup.
Most of the data SHALL be reused from the full backup.
The restored data SHALL match the source.
The run SHALL complete without running out of memory or disk space.

## References

* **clickhouse-backup:** https://github.com/Altinity/clickhouse-backup
* **ClickHouse:** https://clickhouse.tech
* **How incremental backups work with remote storage:** https://github.com/Altinity/clickhouse-backup/blob/master/Examples.md#how-incremental-backups-work-with-remote-storage
* **Commands and options:** https://github.com/Altinity/clickhouse-backup/blob/master/Manual.md
* **Limitations:** https://github.com/Altinity/clickhouse-backup/blob/master/ReadMe.md#limitations
* **ChangeLog:** https://github.com/Altinity/clickhouse-backup/blob/master/ChangeLog.md
* **Requirements GitHub Repository:** https://github.com/Altinity/clickhouse-backup/blob/master/test/testflows/clickhouse_backup/requirements/incremental/requirements.md
* **Requirements Revision History:** https://github.com/Altinity/clickhouse-backup/commits/master/test/testflows/clickhouse_backup/requirements/incremental/requirements.md
* **Git:** https://git-scm.com/


[SRS]: https://github.com/Altinity/clickhouse-backup/blob/master/test/testflows/clickhouse_backup/requirements/incremental/requirements.md
[clickhouse-backup]: https://github.com/Altinity/clickhouse-backup
[ClickHouse]: https://clickhouse.tech
[GitHub Repository]: https://github.com/Altinity/clickhouse-backup/blob/master/test/testflows/clickhouse_backup/requirements/incremental/requirements.md
[Revision History]: https://github.com/Altinity/clickhouse-backup/commits/master/test/testflows/clickhouse_backup/requirements/incremental/requirements.md
[Git]: https://git-scm.com/
[GitHub]: https://github.com/
