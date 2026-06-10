# Repository UX Hardening — Design

**Date:** 2026-06-10
**Status:** Approved
**Scope:** Wizard pre-create connection test with rich feedback, per-attachment
restic passwords with validation, snapshot metadata tags with backfill, and
visible backup destinations. Follow-up to the S3 repository management cycle
(PR #174, spec `2026-06-10-s3-repo-management-design.md`).

## Context

PR #174 shipped clean probing, repository inventory with orphan attach, and
encrypted configuration snapshots. First-hand testing surfaced four UX gaps:

1. The wizard only reports connection failures after submit — the probe runs
   server-side before saving, but the admin cannot test while still filling
   the form, and the test result says only "Connection OK".
2. Attached foreign prefixes encrypted with a different restic password render
   as "unreadable" with no way to supply the right password (known deferral
   from the previous cycle).
3. Prefixes are bare account UUIDs — in a bucket listing nothing says which
   mailbox a backup belongs to.
4. A mailbox's backup policy shows the target repository but not the
   destination prefix.

A fifth request — user/mailbox access control on repositories, modeled after
mail stores (`allowed_stores`) — is **deliberately split into its own next
cycle** (larger structural change: model, enforcement, admin UI).

## Decisions (approved during brainstorming)

- Branch: new cycle `feat/repo-ux` from main (PR #174 merged first).
- Attach with a password **validates immediately**: if the sub-repo cannot be
  opened with the effective password, the attach is rejected.
- Metadata via **native restic tags** (not a sidecar file): visible from any
  restic client and already present in the `restic snapshots --json` output
  the inventory consumes.
- Backfill of existing snapshots is **in scope**, as an explicit admin action,
  limited to prefixes classified as `account` (orphans cannot be backfilled —
  their owner is unknown; that is precisely the problem tags solve going
  forward).
- Per-attachment password lives as a **nullable Fernet column on
  `repository_attachments`** (NULL = use the repository password). A separate
  credentials table was rejected as YAGNI.

## 1. Wizard pre-create test + rich feedback

**New endpoint** `POST /admin/backup/test-connection` (admin-only): accepts the
wizard's connection fields as form data (`backend_type`, `s3_endpoint`,
`s3_bucket`, `s3_access_key`, `s3_secret_key`, `local_path`, `insecure_tls`),
builds a transient (never persisted) `Repository` — same helper pattern as the
DR restore flow — and runs:

1. `s3_probe.probe()` (reachability + write permission), then
2. `repo_inventory.list_prefixes()` for the count of existing sub-repos.

Response is a partial:
- ok, N>0 → "Connection OK — N existing backup(s) found"
- ok, N=0 → "Connection OK — empty repository"
- probe ok but listing fails → "Connection OK" (count is best-effort)
- probe fails → the error pill (existing truncated-error markup)

**Wizard step 2** gains a "Test connection" button (HTMX, result inline below
the connection fields). The restic password is not needed: the probe and the
prefix listing never open a restic repo. The blocking work runs off the event
loop (`run_in_threadpool` or sync-def route, consistent with the DR routes).

**Per-row Test button** switches to the same enriched partial (probe + count)
so post-create tests also report "Connection OK — N backups found".

## 2. Per-attachment restic password

**Migration 016**: `repository_attachments.restic_password` (String, nullable,
Fernet-encrypted at rest). NULL means "use the repository's password".

**Effective password resolution**: `restic_service.build_env()` gains an
optional encrypted-password override parameter. A small helper
(`effective_password(attachment)` or equivalent) centralizes the
attachment-then-repository fallback. All consumers thread it through:

- `repo_inventory.prefix_detail()` — accepts the optional override so attached
  prefixes are read with their own password.
- The account snapshots panel (`account_backup_snapshots`) — lists attached
  sources with the attachment's effective password.
- `recovery_service.create_recovery()` source-override path — restores with
  the attachment's effective password.

**Attach flow**: the attach form gains an optional password field
("Leave blank to use the repository password"). On submit, the route opens the
sub-repo (`restic snapshots`) with the effective password **before** creating
the row; failure → flash error, nothing saved. The provided password is stored
Fernet-encrypted.

**Update password**: in the Contents panel, attached rows whose detail comes
back unreadable get an "Update password" action (small form, same validation:
reject if the new password still cannot open the repo).

## 3. Snapshot metadata tags

**Tagging new snapshots** (`backup_worker` / `restic_service.run_backup`):
account backups carry `--tag mfb:email=<email_address>` and
`--tag mfb:name=<account name>`; configuration snapshots carry
`--tag mfb:config`. Tag values are passed verbatim (restic tags are free-form
strings; commas must be avoided or escaped since restic treats them as
separators within one `--tag` flag — one flag per tag avoids the issue).

**Surfacing**: `restic snapshots --json` already includes `tags`.
`prefix_detail()` extracts `mfb:email=`/`mfb:name=` from the newest snapshot
and returns them; the Contents panel shows the email next to the prefix
(orphans become recognizable). The account snapshots panel may show tags but
is not required to.

**Backfill**: a per-repository "Tag existing snapshots" action in the Contents
panel. For each prefix classified `account`, it runs
`restic tag --add mfb:email=<email> --add mfb:name=<name>` for the snapshots
that lack the tags (restic tag operations rewrite snapshot metadata only —
data blobs are untouched). Reports per-prefix counts in a flash message.
Orphan and config prefixes are skipped.

## 4. Visible backup destination

In the account detail's backup section, a read-only line:

> Destination: `<repository name>` → `<prefix>`

where `<prefix>` is the account UUID, rendered as copyable `<code>` text.
The decrypted bucket/path is **not** shown: the account page is visible to
non-admin owners and nothing on it decrypts repository fields today — the
repository name plus prefix is enough to locate the backup from the
Repositories admin page.

The Repositories page row tooltip ("Mailboxes" count) is unchanged; the
Contents panel already shows account-classified prefixes with mailbox names.

## Data migration

One Alembic migration (016): add nullable `restic_password` column to
`repository_attachments`. Atomic with the model change (drift hook).

## Testing

- Transient test endpoint: probe ok+count, ok+empty, ok+listing-fails,
  probe-fails; admin-only.
- Attach validation: correct password saves (encrypted), wrong password saves
  nothing; blank password falls back to repository password; update-password
  action validates the same way.
- Effective-password threading: prefix_detail / snapshots panel /
  create_recovery use the attachment override when present (assert restic env
  receives the override).
- Tags: run_backup receives the `--tag` flags; config backup tagged
  `mfb:config`; prefix_detail extracts tags from the newest snapshot; backfill
  tags only untagged account-prefix snapshots and skips orphans/config.
- Destination line renders repository name + prefix on the account page.

## Out of scope (next cycles)

- Repository access control for users/mailboxes (allowed-repositories model,
  the mail-store analogy) — **next cycle**.
- Point-in-time restore for folder/full presets (standing deferral).
- Attach "adoption" semantics (future backups into an old prefix).
- Static asset cache-busting (noted during PR #174 verification).
