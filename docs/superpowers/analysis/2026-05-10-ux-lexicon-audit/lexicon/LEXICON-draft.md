# LEXICON.md (DRAFT for repo root)

> This file is the proposed skeleton that the recommendation in `08-recommendation.md` calls for in Wave 1. It would live at `/LEXICON.md` in the repo root after Andrea approves it. Until then, this draft sits in the audit folder.
>
> **v1.1 update (post-Andrea decisions):**
> - "Repository" is the chosen term in **both languages** (graft killed).
> - "Mail store" is **kept verbatim** (no rename to Volume/Storage/Archivio).
> - IT verb for stage 2 is **"Sincronizza"**, not "Aggiorna".

---

# MailFallBack lexicon

The single source of truth for user-facing vocabulary in the MFB GUI, error messages, notification text, and docs. **Every PR that introduces or modifies user-facing copy must comply with this table.** Internal code (variable names, model names, log lines) is exempt; only what users read is governed.

---

## The four-stage model

```
┌─────────┐   pull    ┌──────────────┐   push    ┌──────────────┐   capture   ┌──────────────┐
│SORGENTE │──────────▶│ BACKUP       │──────────▶│ DEPOSITO     │────────────▶│ SNAPSHOT     │
│ Source  │  refresh  │ LOCALE       │  back up  │ Repository   │  retention  │ Snapshot     │
│ IMAP    │           │ Local backup │           │              │             │              │
└─────────┘           └──────────────┘           └──────────────┘             └──────────────┘
```

Every screen of MFB should let the user point at where they currently are in this chain. The chain widget (sticky on every page) is the load-bearing component for this.

---

## Binding term table

| Concept | Italian (UI) | English (UI) | Internal/Code (legacy, not user-facing) |
|---|---|---|---|
| Source IMAP server (technical field) | **Sorgente** | **Source** | `Account.imap_host` |
| The user's email account at Gmail/etc. | **Casella** | **Mailbox** | `Account` |
| Local copy on this server | **Backup locale** | **Local backup** | mbsync output to Maildir |
| Action: refresh local copy | **Aggiorna ora** | **Refresh now** | `SyncJob` execution |
| Schedule for local refresh | **Pianificazione backup locale** | **Local backup schedule** | `Account.sync_schedule` |
| Filesystem path for local copies | **Mail store** (admin nav: **Mail stores**) | **Mail store** (admin nav: **Mail stores**) | `MailStore` |
| Off-site repository | **Repository** (admin nav: **Repositories**) | **Repository** (admin nav: **Repositories**) | `BackupDestination` |
| Action: push to repository | **Esegui backup ora** | **Back up now** | `submit_backup()` |
| Per-account off-site config | **Profilo di backup** | **Backup policy** | `AccountBackup` |
| Point-in-time | **Snapshot** | **Snapshot** | restic snapshot |
| Retention | **Politica di conservazione** | **Retention policy** | `RetentionPreset` |
| Restore (mailbox-side, IMAP→IMAP) | **Ripristino** | **Restore** | `RestoreJob` |
| Recover (depot-side, snapshot→new mailbox) | **Recupera** | **Recover** | `restore_snapshot()` |
| Recovered placeholder mailbox | **Casella recuperata: {nome} ({data})** | **Recovered mailbox: {name} ({date})** | New `Account` row, suspended |

---

## Banned words and forbidden combinations

Use of bare "Backup" as a noun is **banned**. Always qualify:

| ❌ Banned | ✅ Use instead |
|---|---|
| "Backup configured" | "Backup locale configurato" / "Profilo di backup impostato" |
| "Backup failed" | "Backup locale fallito" / "Snapshot fallito" / "Sincronizzazione fallita" |
| "Backup destination" | "Repository" |
| "Backup now" | "Sincronizza ora" (local) / "Esegui backup ora" (off-site → Repository) |
| "Sync" / "Syncing" / "Synced" | "Sincronizza" / "Sincronizzazione in corso" / "Sincronizzato" |
| "Last sync: 5m ago" | "Ultima sincronizzazione: 5 min fa" |
| "First sync" | "Prima sincronizzazione" / "Primo backup locale" |
| "Snapshot restored as 'Backup X'" | "Recuperato in 'X recuperata'. Casella sospesa." |
| "Stores" (admin nav plural alone) | "Mail stores" (sempre col qualificatore) |

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
  - ✅ "Mailbox luca@example.com — backup failed 2 h ago"
  - ❌ "It seems there might be an issue with your mailbox..."
- **One verb per concept**. The verb table is binding:
  - Source → "connect" / "collega"
  - Local backup → "sync" / "sincronizza"
  - Repository → "back up" / "esegui backup"
  - Snapshot → "capture" / "scatta" (rarely used as verb)
  - Restore (mailbox-side, IMAP→IMAP) → "restore" / "ripristina"
  - Recover (depot-side, snapshot→new mailbox) → "recover" / "recupera"
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
- "Nessuna casella. **Collega una casella** per iniziare il backup locale."
- "No snapshots yet. **Back up now** to create the first snapshot."

### Failures

```
<Subject> <verb> <consequence>. <Suggested action>.
```

Examples:
- "Local backup failed for luca@example.com (auth error). Re-check the app password."
- "Backup locale fallito per luca@example.com (errore di autenticazione). Verifica la password applicazione."
- "Repository rustfs-s3 unreachable. Check the endpoint or the credentials."

### Confirmations

```
<This action> will <consequence>. <One-line warning>. Continue?
```

Examples:
- "Recover snapshot 4f3a2b? This creates a new suspended mailbox; review and enable when ready."
- "Recupera lo snapshot 4f3a2b? Verrà creata una casella sospesa: verifica e abilita quando pronto."

### Inline help (tooltip)

```
<Term>: <one-clause definition>. <Optional second clause for context>.
```

Examples:
- "Repository: an off-site, encrypted store for snapshots. One repository can hold snapshots from many mailboxes."
- "Deposito: archivio off-site cifrato per gli snapshot. Un deposito può contenere snapshot di più caselle."

---

## Enforcement

A pre-commit hook (`scripts/lexicon-check.sh`) runs `grep -rEi '\bbackup\b' src/mailfallback/templates/ src/mailfallback/routers/` and fails if it finds bare `backup` not followed by `locale|locale\)|off-site|al deposito|policy|profilo|on this server` (and the EN equivalents). False positives go in `.lexicon-allowlist` with a one-line reason.

The check is also run in CI on every PR to `main`.

---

## Glossary (for new contributors)

- **Sorgente / Source**: the user's IMAP server (Gmail, Outlook, etc.). Read-only from MFB's point of view.
- **Backup locale / Local backup**: the local Maildir kept by mbsync. Always-on, lives on the same host as MFB. *This is what makes MFB a "fallback" mailbox.*
- **Repository**: the off-site restic storage location. S3 or local-disk. Encrypted independently. Configured per-installation; mailboxes pick which one to use via their backup policy. **Same word in IT and EN.**
- **Snapshot**: a point-in-time capture inside a Repository. Created on schedule per a backup policy. Recovered (not "restored") via the Recover flow.
- **Mail store**: a filesystem path where local backups physically live on the server. Admins manage these in the Mail stores page. *Kept verbatim from the legacy term — it's already in the data model and didn't cause confusion in the audit.*

---

## When in doubt

Ask Andrea, or open an issue tagged `lexicon` with a reference to this file. Do not silently introduce new vocabulary.
