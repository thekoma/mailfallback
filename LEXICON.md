# MailFallBack lexicon

> Source of truth for MFB's user-facing vocabulary. See `docs/superpowers/analysis/2026-05-10-ux-lexicon-audit/08-recommendation.md` for the design reasoning.

The single source of truth for user-facing vocabulary in the MFB GUI, error messages, notification text, and docs. **Every PR that introduces or modifies user-facing copy must comply with this table.** Internal code (variable names, model names, log lines) is exempt; only what users read is governed.

**Language:** English only. The product has not shipped a public release; there is no user base to translate for. If localisation ever happens (Italian first, possibly), it will be docs-first; the GUI may follow much later. The audit's earlier bilingual proposals are preserved in `docs/superpowers/analysis/2026-05-10-ux-lexicon-audit/` as historical context.

---

## The four-stage model

```
┌─────────┐   pull    ┌──────────────┐   push    ┌──────────────┐   capture   ┌──────────────┐
│ SOURCE  │──────────▶│ LOCAL        │──────────▶│ REPOSITORY   │────────────▶│ SNAPSHOT     │
│ IMAP    │   sync    │ BACKUP       │  back up  │              │  retention  │              │
│         │           │              │           │              │             │              │
└─────────┘           └──────────────┘           └──────────────┘             └──────────────┘
```

Every screen of MFB should let the user point at where they currently are in this chain. The dashboard chain hero (Wave 4) is the load-bearing teaching surface.

---

## Binding term table

| Concept | UI label | Internal/Code (legacy table or column name, not user-facing) |
|---|---|---|
| Source IMAP server (technical field) | **Source** | `Account.imap_host` |
| The user's email account at Gmail/etc. | **Mailbox** | `Account` |
| Local copy on this server | **Local backup** | mbsync output to Maildir |
| Action: refresh local copy | **Sync now** | `SyncJob` execution |
| Schedule for local refresh | **Local backup schedule** | `Account.sync_schedule` |
| Filesystem path for local copies | **Mail store** (admin nav: **Mail stores**) | `MailStore` |
| Off-site repository | **Repository** (admin nav: **Repositories**) | `BackupDestination` table; `Repository` Python class |
| Action: push to repository | **Back up now** | `submit_backup()` |
| Per-account off-site config | **Backup policy** | `account_backups` table; `BackupPolicy` Python class |
| Point-in-time | **Snapshot** | restic snapshot |
| Retention | **Retention policy** | `RetentionPreset` |
| Restore (mailbox-side, IMAP→IMAP) | **Restore** | `RestoreJob` |
| Recover (depot-side, snapshot→new mailbox) | **Recover** | `restore_snapshot()` |
| Recovered placeholder mailbox | **Recovered mailbox: {name} ({date})** | New `Account` row, suspended |

---

## Banned words and forbidden combinations

Use of bare "Backup" as a noun is **banned**. Always qualify:

| ❌ Banned | ✅ Use instead |
|---|---|
| "Backup configured" | "Off-site policy set" / "Backup policy set" |
| "Backup failed" | "Local backup failed" / "Snapshot failed" / "Sync failed" |
| "Backup destination" / "Backup Destinations" | "Repository" / "Repositories" |
| "Backup now" | "Sync now" (local) / "Back up now" (off-site → Repository) |
| "Last sync: 5m ago" | "Last sync 5 min ago" / "Last back-up X ago" (off-site) |
| "First backup" / "First sync" | "First sync" / "Initial sync in progress" |
| "Snapshot restored as 'Backup X'" | "Recovered into 'Recovered X'. The mailbox is suspended." |
| "Stores" (admin nav, alone) | "Mail stores" |
| "Mail Store" / "Mail Stores" (Title Case) | "Mail store" / "Mail stores" (sentence case) |

---

## Capitalisation rules

- **Sentence case** for all UI labels, headings, and buttons. Not Title Case.
  - ✅ "Connect a mailbox"
  - ❌ "Connect A Mailbox"
- **Loanwords kept lowercase** except in headings: `snapshot` (noun), `Snapshot` (when starting a sentence or heading).
- **Product nouns capitalized in body text** when referring to the concept: "Open the Repositories page", "Configure your Local backup schedule".

---

## Voice and tone

- **Direct, declarative.** Users are admins; they don't need pleasantries.
  - ✅ "Mailbox luca@example.com — local backup failed 2 h ago"
  - ❌ "It seems there might be an issue with your mailbox..."
- **One verb per concept.** The verb table is binding:
  - Source → "connect"
  - Local backup → "sync"
  - Repository → "back up"
  - Snapshot → "capture" (rarely used as verb)
  - Restore (mailbox-side, IMAP→IMAP) → "restore"
  - Recover (depot-side, snapshot→new mailbox) → "recover"
- **Empty states teach.** Every empty state must answer: what is this, what's the next action.
- **Errors propose action.** "X failed because Y. <Suggested next step>." pattern.
- **Avoid uppercase emphasis** ("CRITICAL", "ATTENTION"). Use icon + word ("⚠ Attention needed").

---

## Sentence templates

### Empty states

```
No <plural-noun> yet. <Verb> <determiner> <noun-phrase> to start.
```

Examples:
- "No mailboxes yet. **Connect a mailbox** to start your local backup."
- "No snapshots yet. **Back up now** to create the first snapshot."
- "No repositories configured. **Add a repository** to enable off-site backup."

### Failures

```
<Subject> <verb> <consequence>. <Suggested action>.
```

Examples:
- "Local backup failed for luca@example.com (auth error). Re-check the app password."
- "Repository rustfs-s3 unreachable. Check the endpoint or the credentials."

### Confirmations

```
<This action> will <consequence>. <One-line warning>. Continue?
```

Examples:
- "Recover snapshot 4f3a2b? This creates a new suspended mailbox; review and enable when ready."

### Inline help (tooltip)

```
<Term>: <one-clause definition>. <Optional second clause for context>.
```

Examples:
- "Repository: an off-site, encrypted store for snapshots. One repository can hold snapshots from many mailboxes."

---

## Enforcement

An **advisory** pre-commit hook (`scripts/lexicon-check.sh`) and CI step warn (do not fail) when bare `backup` appears in `src/mailfallback/templates/` or in router flash messages without an allowed qualifier (`local`, `off-site`, `configuration`, `destination`, `policy`, `repository`, `snapshot`, `completed`, `failed`, `started`, `now`, `history`, `profile`).

False positives go in `scripts/.lexicon-allowlist` with a one-line reason.

Legacy audit-action strings (e.g., `backup_destination.create`) render via `get_action_label()` in `src/mailfallback/services/audit_service.py`. The underlying action strings stay stable for backward compatibility of historical audit rows.

---

## Glossary (for new contributors)

- **Source**: the user's IMAP server (Gmail, Outlook, etc.). Read-only from MFB's point of view.
- **Local backup**: the local Maildir kept by mbsync. Always-on, lives on the same host as MFB. *This is what makes MFB a "fallback" mailbox.*
- **Repository**: the off-site restic storage location. S3 or local-disk. Encrypted independently. Configured per-installation; mailboxes pick which one to use via their backup policy.
- **Snapshot**: a point-in-time capture inside a Repository. Created on schedule per a backup policy. Recovered (not "restored") via the Recover flow.
- **Mail store**: a filesystem path where local backups physically live on the server. Admins manage these in the Mail stores page. *Kept verbatim from the legacy term — it's already in the data model and didn't cause confusion in the audit.*

---

## When in doubt

Ask Andrea, or open an issue tagged `lexicon` with a reference to this file. Do not silently introduce new vocabulary.
