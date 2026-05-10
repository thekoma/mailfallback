# Repositories (off-site backup)

A **Repository** is an off-site, encrypted store for snapshots of your mailboxes. Each Repository is a [restic](https://restic.net/) repository backed by an S3 bucket or a local filesystem path. Mailboxes pick a Repository in their **backup policy** to enable scheduled off-site backups.

This page is the admin's home for managing Repositories: adding them, testing them, monitoring their health, and recovering snapshots from them.

---

## The four-stage model

MFB keeps your mail safe in four stages:

```
┌─────────┐   pull    ┌──────────────┐   push    ┌──────────────┐   capture   ┌──────────────┐
│ SOURCE  │──────────▶│ LOCAL        │──────────▶│ REPOSITORY   │────────────▶│ SNAPSHOT     │
│ IMAP    │   sync    │ BACKUP       │  back up  │              │  retention  │              │
└─────────┘           └──────────────┘           └──────────────┘             └──────────────┘
```

- **Source** — the user's IMAP server (Gmail, Outlook, IMAP host, …).
- **Local backup** — the local Maildir kept by mbsync. Always-on, lives on the MFB host.
- **Repository** — the off-site, encrypted restic store. Configured here.
- **Snapshot** — a point-in-time capture stored inside a Repository.

The Repositories admin page covers stages 3 and 4. Stage 1 lives on each account's detail page; stage 2 is the **Local backup** on the same page.

---

## Adding a Repository

Open **Admin → Repositories** and use the *Add Repository* form at the bottom of the page.

### Common fields

| Field | Notes |
|---|---|
| **Name** | Human-friendly label, shown in account-detail pages and audit log. Example: `rustfs` or `Hetzner S3`. |
| **Backend Type** | `S3` for an S3-compatible object store, `Local` for a filesystem path. |
| **Restic Password** | The encryption passphrase for the restic repository. Treat this as a master key — see [Disaster Recovery](disaster-recovery.md). |
| **Skip TLS certificate verification** | For self-signed CA endpoints only. Production deployments must use a trusted certificate. The UI shows a yellow warning banner when this is enabled. |

### S3 fields

| Field | Notes |
|---|---|
| **S3 Endpoint** | Full URL of the S3 endpoint (e.g. `https://s3.amazonaws.com` or `https://s3.k8s.local`). |
| **S3 Bucket** | The bucket name. Each mailbox's snapshots are isolated under a per-account path inside this bucket. |
| **Access Key** | S3 access key ID. Encrypted in the database. |
| **Secret Key** | S3 secret access key. Encrypted in the database. |

### Local fields

| Field | Notes |
|---|---|
| **Local Path** | Filesystem path on the MFB host (or a volume mounted into the container). Each mailbox's snapshots are isolated under a per-account subdirectory. |

When you submit the form, MFB runs `restic init` against the Repository to verify the credentials. A failure leaves the form populated and shows a flash error; success persists the Repository.

---

## The status quartet

Each Repository row shows four columns of cached status:

| Column | Meaning |
|---|---|
| **Mailboxes** | Number of mailbox backup policies pointing at this Repository. |
| **Snapshots** | Sum of `last_snapshot_count` across all those mailboxes (i.e., snapshots stored in this Repository). |
| **Last back-up** | Most recent successful back-up across all mailboxes using this Repository. |
| **Health** | `Healthy` (last success ≤ 2d), `Stale` (older), `No back-up yet` (configured but never), `Empty` (no policies pointing here). |

These values are cached in the database and updated by `backup_worker` after each run — the page never shells `restic` to render. Reads are constant-time regardless of how many snapshots exist.

---

## Per-mailbox backup policy

Each mailbox can attach a **backup policy** to a Repository from its account-detail page (Off-site backup → Repository section). The policy specifies:

- **Repository** — which Repository to use.
- **Schedule** — cron expression. Default `0 2 * * *` (daily at 02:00).
- **Retention preset** — `Light` (7 daily / 4 weekly), `Standard` (30 daily / 12 weekly / 6 monthly), `Full` (90 daily / 52 weekly / 24 monthly), or `Custom`.

Triggering a back-up:

- **Scheduled** — APScheduler runs `backup_worker.execute_backup` on the cron.
- **Manual** — the *Back up now* button on the account-detail page submits to `/accounts/{id}/backup/now`.

Each successful back-up updates: `last_status=completed`, `last_run_at`, `last_successful_run_at`, `last_backup_at`, `last_snapshot_count`, `last_snapshot_at`.

---

## Recovering from a snapshot

Two entry points to the same flow:

1. **`/recover`** — top-level Restore page → "Recover from a snapshot". Lists mailboxes that have at least one snapshot.
2. **Account detail → Off-site backup → Repository → Snapshots** — the per-mailbox snapshot table.

Click *Restore this snapshot* on a row. MFB:

1. Runs `restic restore` to a temporary directory under the mail store: `{store_path}/.offsite-restore/{account-id}-{timestamp}/`.
2. Creates a new placeholder mailbox named `Recovered <name> (date)` pointing at that directory, with `suspended=true`.
3. Shows a flash message explaining the next step.

The recovered placeholder is **suspended**: Dovecot does not serve it (the userdb endpoint filters on `enabled AND NOT suspended`), so it cannot be opened in the webmail. This is deliberate — it's a chance for the operator to inspect or rename before exposing.

To complete the recovery: open the placeholder mailbox and click **Promote to live**. This clears the `suspended` flag; Dovecot starts serving the mailbox immediately under its new namespace.

To discard a recovery: delete the placeholder mailbox. The on-disk directory under `.offsite-restore/` is removed when the mailbox is deleted.

---

## Editing or deleting a Repository

| Action | UI | Notes |
|---|---|---|
| **Edit** | Pencil icon on the row — expands an inline form. | Change name, S3/local fields, Restic password, or insecure-TLS. New fields submit; existing-blank fields are left untouched. |
| **Test connection** | Plug-zap icon. | Re-runs `restic init` (idempotent) to verify credentials are still valid. |
| **Delete** | Red trash icon. | Disabled when ≥1 mailbox backup policy still references this Repository. Drain those first by reassigning or removing them from the affected mailboxes. |

Deleting a Repository **does not delete** snapshots in the underlying S3 bucket or local path. To purge snapshots from disk, run `restic forget --prune` against the repository directly using the encryption password.

---

## What MFB does NOT do (for Repositories)

- **It does not back up the MFB database itself.** See [Disaster Recovery](disaster-recovery.md). Without your `MAILFALLBACK_SECRET_KEY` and a Postgres dump, snapshots are unrecoverable.
- **It does not migrate snapshots between Repositories.** Moving from one Repository to another means starting a fresh chain in the new Repository — old snapshots stay in the old one until you decide what to do with them.
- **It does not garbage-collect on-disk recovered placeholders.** The `.offsite-restore/{account-id}-{timestamp}/` directory persists until the placeholder mailbox is deleted.

---

## Audit log

Every Repository action is logged with display labels:

| Underlying action | Display label |
|---|---|
| `backup_destination.create` | Repository created |
| `backup_destination.edit` | Repository edited |
| `backup_destination.delete` | Repository deleted |
| `account.backup_configure` | Backup policy configured |
| `account.backup_now` | Manual back-up triggered |
| `account.backup_restore` | Recovered from snapshot |
| `account.promote_recovered` | Recovered mailbox promoted to live |

Filter by these in the [Audit Log](audit.md) page.
