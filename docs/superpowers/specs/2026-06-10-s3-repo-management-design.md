# S3 Repository Management — Design

**Date:** 2026-06-10
**Status:** Approved
**Scope:** Clean connection testing, repository content inventory + attach, encrypted
configuration backup, and disaster-recovery configuration restore.

## Context

MFB already supports off-site mail backups via restic (`Repository` model, table
`backup_destinations`; `restic_service.py`). The repository layout is one restic
sub-repository per account: `s3:{endpoint}/{bucket}/{account_id}` (or
`{local_path}/{account_id}` for the local backend).

Known gaps this cycle addresses:

1. The connection test (`test_destination`) runs `restic init` on a fake prefix
   `__mfb_connection_test__`, leaving junk in the bucket. Edits never re-test
   credentials. Feedback is a flash message after a full redirect.
2. There is no way to see what a repository actually contains, nor to re-attach
   backups left by a previous MFB installation (orphan prefixes whose account
   UUIDs no longer exist).
3. There is no configuration backup: losing the MFB database means losing the
   mapping between accounts and their restic prefixes, plus all credentials.

## Decisions (approved during brainstorming)

- Fix all three connection-test defects (no junk, re-test on edit, inline feedback).
- Inventory serves both audit (see everything, including orphans) and import.
- Attach = the orphan prefix becomes a **read-only restore source** for the chosen
  account. Future backups keep going to the account's own UUID prefix. No copying.
- Config backup is a **full disaster-recovery export** including credentials,
  re-encrypted under a user-supplied passphrase.
- Schedule: daily job + on-demand button.
- Config **restore is in scope**: fetch from repository on a fresh install.
- Architecture: restic stays the storage engine; **boto3** is added as a dependency
  for the two things restic cannot do (clean connection probing, bucket prefix
  listing).

## 1. Clean connection test

**New module `services/s3_probe.py`** (boto3):

- S3 test: `PUT` then `DELETE` a small probe object (`.mfb-probe-<uuid4>`) at the
  bucket root. Validates credentials, bucket existence, and write permission.
  Honors `s3_endpoint` (any S3-compatible store) and `insecure_tls`
  (`verify=False`).
- Local test: directory exists (or is creatable) and is writable.
- `restic_service.test_destination` is rewritten to call the probe; it no longer
  runs `restic init`.
- Legacy cleanup: on a successful test, best-effort deletion of any leftover
  `__mfb_connection_test__/` objects from previous MFB versions.

**Re-test on edit:** `admin_edit_backup_destination` applies the form values to the
ORM object in memory, runs the probe, and commits only on success; on failure it
rolls back and reports the error. Same rule for create (no committed row that
failed its test — the current code commits first and deletes after, leaving a
window).

**Inline feedback:** the add/edit forms and the per-row Test button become HTMX
requests returning a result partial (success/error rendered next to the form),
replacing the redirect + flash pattern for these actions.

**Declared limitation:** the probe validates reachability and permissions, not the
restic password. On a brand-new prefix the password *defines* the repository, so
there is nothing to check it against. The password is implicitly validated
whenever an existing repo is opened (inventory, attach, restore).

## 2. Repository inventory + attach

**New module `services/repo_inventory.py`:**

- Lists top-level prefixes: boto3 `list_objects_v2` with `Delimiter='/'` for S3,
  subdirectories of `local_path` for local.
- Classifies each prefix:
  - `account` — matches an existing `Account.id`;
  - `config` — the `__mfb_config__` prefix;
  - `attached` — present in `repository_attachments`;
  - `orphan` — none of the above.
- Per-prefix detail (snapshot count, latest snapshot time) comes from
  `restic snapshots --json` opened with the repository's restic password — this is
  where the password gets genuinely validated. Loaded lazily per prefix to keep
  the page fast.

**UI:** on the Repositories admin page each repository row gets an expandable
"Contents" panel (HTMX lazy load) listing prefixes with their classification,
snapshot count, and latest snapshot date. Orphan rows get an **Attach** action
(pick an account); attached rows get **Detach**.

**New table `repository_attachments`:**

| column        | type                                        |
|---------------|---------------------------------------------|
| id            | String PK (uuid4)                            |
| repository_id | FK → backup_destinations.id, CASCADE         |
| account_id    | FK → accounts.id, CASCADE                    |
| prefix        | String, the sub-repo prefix in the bucket    |
| created_at    | DateTime(timezone)                           |

Unique constraint on `(repository_id, prefix)`. Detach deletes the row only; the
bucket is never touched.

**Restore integration:** `recovery_service.create_recovery` accepts an optional
source override `(repository, prefix)` that bypasses the BackupPolicy lookup.
Snapshots from attached prefixes appear in the account's snapshot panel
(labelled as attached sources) and restore as normal `Recovery` rows
(`repository_id` = the attachment's repository).

## 3. Encrypted configuration backup

**New columns on `Repository`:**

- `config_backup_enabled` (bool, NOT NULL, server_default false)
- `config_backup_passphrase` (nullable string, Fernet-encrypted at rest; required
  and minimum 12 characters when the checkbox is enabled)
- `last_config_backup_at` (nullable timestamptz)
- `last_config_backup_status` (nullable string: ok/failed + error detail kept in
  audit log)

**New module `services/config_backup_service.py`:**

- `build_export(db) -> dict`: serializes users (including `password_hash`, role,
  store assignment, allowed stores), mail stores, groups (members + accounts),
  accounts (credentials decrypted from the local Fernet), repositories (their
  secrets decrypted), backup policies, and repository attachments.
  **Original UUIDs are included and preserved** — they are what makes restic
  prefixes and `maildir_path` values line up again after a restore.
- `encrypt_export(data, passphrase) -> bytes` /
  `decrypt_export(blob, passphrase) -> dict`: envelope JSON
  `{"schema_version": 1, "kdf": "scrypt", "salt": ..., "ciphertext": ...}`.
  Key derived from the passphrase with scrypt (random 16-byte salt, n=2^15, r=8,
  p=1), then Fernet for the ciphertext. Uses the `cryptography` package already
  in the dependency tree.
- `run_config_backup(db, repository)`: build → encrypt → write `mfb-config.json.enc`
  to a temp dir → `restic backup` it into the `__mfb_config__` sub-repo of that
  repository → apply retention `--keep-daily 30` → update status columns + audit
  log.

**Scheduling:** one APScheduler job per repository with the checkbox enabled
(`config-backup-<repo_id>`, daily at 03:00), managed in `scheduler.py` alongside
the existing sync/backup job reconciliation. A "Backup config now" button on the
repository row triggers it on demand. Last run time + status shown on the row.

## 4. Configuration restore (disaster recovery)

**Entry point:** the System admin page gets a "Restore configuration from
repository" form: backend type (S3/local), endpoint/bucket/keys or local path,
restic password, passphrase. On a fresh install the admin reaches it with the
default credentials.

**Flow:**

1. Fetch the latest `__mfb_config__` snapshot into a temp dir
   (`restic restore`), decrypt with the passphrase, validate `schema_version`.
2. **Preview step:** show counts (users, accounts, stores, groups, repositories,
   policies, attachments) before anything is written.
3. On confirm, import: recreate rows **preserving original IDs**, re-encrypting
   all secrets with the local Fernet key. Collisions (existing ID or username)
   are **skipped** and reported in the result summary.

After a successful restore the repository records themselves are back (the bucket
credentials were in the export), so scheduled mail backups resume on their own;
maildirs repopulate on the first sync; recoveries from existing snapshots are
immediately possible.

## Data migration

One Alembic migration (atomic with the model changes, per the drift hook):

- `repository_attachments` table.
- Four new columns on `backup_destinations` (`config_backup_enabled` NOT NULL with
  `server_default 'false'`; the other three nullable).

## Testing

- Unit tests with boto3 mocked (probe success/failure matrix: bad creds, missing
  bucket, no write permission) and restic mocked as in existing tests.
- Edit rollback test: failed probe leaves the stored repository unchanged.
- Inventory classification test: account/config/attached/orphan from a synthetic
  prefix list.
- Config backup round-trip: export → encrypt → decrypt → import into an empty DB;
  assert IDs preserved, secrets re-encrypted and decryptable with the local key,
  collisions skipped.
- Wrong-passphrase decrypt raises a clean, user-reportable error.

## Out of scope (noted for later cycles)

- Point-in-time restore for folder/full presets (range on RestoreCreate +
  snapshot selection in the worker) — next in queue from the 2026-06-09 handoff.
- Search-first UX work (Cycle 2 list).
- "Adoption" semantics for attach (future backups continuing into an old prefix).
