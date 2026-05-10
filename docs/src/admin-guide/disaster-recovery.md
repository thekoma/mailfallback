# Disaster recovery

MailFallBack backs up your mail. **You are responsible for backing up MailFallBack itself.**

This page describes the minimum off-MFB safety net you need so that, after a host failure, you can restore service and recover your repositories.

## Why this matters

The off-site backup feature in MFB stores [restic](https://restic.net/) snapshots in a Repository (S3 bucket or local path). Those snapshots are useless on their own. To restore them you also need:

1. The **`MAILFALLBACK_SECRET_KEY`** environment variable used by the lost host. This is the Fernet key MFB uses to encrypt every credential in the database, including each Repository's restic password. Lose this key and the snapshots become un-decryptable.
2. A **recent PostgreSQL dump**. The database holds the `BackupDestination` rows (S3 endpoint, bucket, encrypted credentials), the `AccountBackup` rows (per-mailbox policy), the `Account` rows (mailbox metadata), the `MailStore` rows, and everything else MFB needs to reconstruct itself.

Without both, your encrypted snapshots are inaccessible.

## What to back up off-MFB

| Item | Where it lives | How often | Where to store |
|---|---|---|---|
| `MAILFALLBACK_SECRET_KEY` | `.env` / docker-compose / K8s Secret | Once at deploy; re-back-up only if rotated | A separate, encrypted, off-machine vault (password manager, secret store) |
| PostgreSQL dump | The `db` service's volume | Daily | A separate location from the host (e.g. another disk, another machine, cold storage) |
| `docker-compose.yml` and any custom configs | Repo root | Whenever you edit them | Your normal source-control workflow |

The MFB application code itself does not need a separate backup — it ships from this repository.

## How to back up PostgreSQL

The simplest reliable approach is a daily `pg_dump` from the running container. Adapt to your scheduler of choice (systemd timer, cron, K8s CronJob).

```bash
docker compose exec -T db pg_dump -U mailfallback mailfallback \
    | gzip > "mfb-$(date +%F).sql.gz"
```

Then ship `mfb-YYYY-MM-DD.sql.gz` and a copy of your `MAILFALLBACK_SECRET_KEY` to a place that is **not on the same machine** as the running stack.

For continuous protection consider streaming WAL archives (e.g. via [`pgbackrest`](https://pgbackrest.org/) or [`wal-g`](https://github.com/wal-g/wal-g)). For a homelab the daily `pg_dump` is usually enough.

## Recovery procedure

After total loss of the MFB host:

1. **Provision a new host.** Install Docker (or your usual orchestrator).
2. **Restore the secret key.** Set `MAILFALLBACK_SECRET_KEY` in the new environment to the **exact same value** the old deployment used. If you can't, every encrypted credential in the dump is gone.
3. **Restore PostgreSQL.** Bring up the `db` container against an empty volume, then load the dump:
   ```bash
   gunzip < mfb-YYYY-MM-DD.sql.gz \
       | docker compose exec -T db psql -U mailfallback mailfallback
   ```
4. **Bring up MFB.** `docker compose up -d`. Confirm the dashboard is reachable and your mailboxes appear.
5. **Re-discover snapshots.** For each mailbox with off-site backup, the existing `AccountBackup` config now points at the original Repository. The next scheduled back-up will resume; or you can trigger a manual back-up to verify the Repository is still reachable. Snapshots created before the disaster are visible under the mailbox's "Snapshots" section.
6. **Recover from a snapshot if needed.** If the local Maildir is also gone, use `Recover from snapshot` (see the [restore page](../user-guide/restore.md)) to rebuild a mailbox from the off-site copy. The recovered mailbox starts suspended — review it, then click **Promote to live** to enable it.

## What MFB does NOT back up

- **The MFB database itself.** Backing up the database is your job (this page).
- **Server configuration** (Dovecot, Roundcube). MFB regenerates these at startup from settings; the source is the database. Restoring the database restores the config.
- **Plain copies of the local Maildir.** If you want filesystem-level snapshots in addition to the off-site Repository, configure them at the storage layer (ZFS snapshots, LVM, etc.).

## Footgun summary

- **Lose `MAILFALLBACK_SECRET_KEY` ⇒ snapshots unrecoverable.** Treat it like a master key.
- **Lose Postgres ⇒ all configuration gone**, even if the snapshots themselves are intact in S3.
- **Use the same key on the recovery host.** Generating a new key disables every encrypted secret.
- **Test the recovery procedure.** A backup you have never restored is a backup that does not exist.
