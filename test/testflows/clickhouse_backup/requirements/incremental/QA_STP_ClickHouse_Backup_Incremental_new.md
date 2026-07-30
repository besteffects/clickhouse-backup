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