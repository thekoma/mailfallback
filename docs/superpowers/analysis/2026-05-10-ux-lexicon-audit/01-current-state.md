# Current state — synthesis

This is the synthesized view of MFB's current state, drawn from six discovery passes. Each section ends with a pointer to the scratch file that holds the exhaustive evidence.

---

## TL;DR (the four most important findings)

1. **The word "backup" is doing two completely different jobs.** It currently denotes both the **local mbsync mirror** (e.g., "Start first backup", "Backed up 5 min ago", flash messages) AND the **restic offsite system** (e.g., "Backup Destinations", admin nav). 148 occurrences across templates and routers; the local meaning predominates in user-facing strings. This is the single largest source of confusion.

2. **The word "destination" is overloaded across two flows.** "Backup Destination" = the remote depot (admin/backup); "Destination account" / "destination server" = the IMAP target during restore (/restore). 43 occurrences. Two unrelated concepts share the same word.

3. **"Snapshot", "Store", "Maildir" are clean.** Single, consistent meanings; no synonyms. These are the lexicon's bright spots and should be preserved or even leaned on harder.

4. **The chain is invisible.** No screen visualises "Source → Local → Depot → Snapshots". Users discover the offsite feature by accident (collapsed `<details>` block at the bottom of the account page); the dashboard surfaces local sync stats but ignores offsite health.

---

## Inventory snapshot

| Surface | Count |
|---|---|
| Models | 11 (User, MailStore, Account, SyncJob, Group, StoreMigration, RestoreJob, AuditLog, BackgroundTask, BackupDestination, AccountBackup) |
| Enums | 9 (UserRole, AuthType, SyncState, JobStatus, MigrationStatus, TaskStatus, RestoreMode, BackendType, RetentionPreset, BackupStatus) |
| Routes | 60+ across 9 routers (40+ HTML, 10+ API, 16+ HTMX partials) |
| Top-level nav items | 4 user (Dashboard, Accounts, Restore, Webmail) + 6 admin (Users, Stores, Backups, Groups, System, Audit Log) + Profile/Logout |
| Env vars | 42, all `MAILFALLBACK_` prefixed |
| Compose services | 5 (db, mailfallback, dovecot, webmail, tika) |
| Docs pages | 27 across 5 sections |
| Last 30 days | 129 commits — 48 feat / 36 fix / 19 docs |
| Background workers | 3 ThreadPools (sync x4, backup x2, restore x2) + APScheduler |
| External binaries called | 4 (mbsync, restic, doveadm, tika) |

→ Detail: [`scratch/ia-routes.md`](scratch/ia-routes.md), [`scratch/data-model.md`](scratch/data-model.md), [`scratch/runtime-ops.md`](scratch/runtime-ops.md), [`scratch/config-surface.md`](scratch/config-surface.md), [`scratch/recent-history.md`](scratch/recent-history.md).

---

## Vocabulary overlap (the heart of the problem)

| Term | Count | What it currently denotes | Problem |
|---|---|---|---|
| **backup** | 148 | (a) local mbsync mirror; (b) restic offsite system; (c) the verb for both | Two distinct concepts share one word |
| **sync** | 210 | (a) IMAP pull from source to local; (b) any scheduled work | Sometimes interchangeable with "backup" |
| **destination** | 43 | (a) restic remote depot; (b) target IMAP account in /restore | Two distinct concepts share one word |
| **store** | 254 | local filesystem path for Maildir | Clean, single meaning |
| **snapshot** | 20 | restic point-in-time | Clean, single meaning |
| **archive** | 5 | restore-from-snapshot icon, page title | Marginal — but "archive" is a major term in competitor space |
| **source** | 35 | (a) origin IMAP server; (b) the account being restored from | Mostly clean |
| **maildir** | 31 | local mirror directory | Clean but admin-only |

→ Full list with file:line evidence: [`scratch/vocab-inventory.md`](scratch/vocab-inventory.md).

### Per-concept synonym density

| Concept (canonical) | Number of distinct labels in current UI |
|---|---|
| Source IMAP | 4 (`source account`, `source`, `provider`, `IMAP host`) |
| **Local mirror** | **7** (`sync`/`syncing`/`sync now`, `backup`, `first backup`, `backed up`, `maildir`, `messages`, `mailbox`) |
| **Remote depot** | **4** (`backup destination`, `destination`, `offsite backup`, `offsite backup destination`) |
| Snapshot | 1 (`snapshot`) |
| Local store (filesystem) | 3 (`store`, `mail store`, `stores`) |

The two most confused concepts (local mirror, remote depot) have the most synonyms. Snapshot — the cleanest concept — has one.

---

## Information architecture today

### Sidebar (top → bottom)

```
Dashboard          (user)   — overview stats
Accounts           (user)   — list of email accounts
Restore            (user)   — IMAP restore (source → target)
Webmail            (user)   — Roundcube link, opt-in via env var

── ADMIN ──
Users              (admin)
Stores             (admin)  — local filesystem paths
Backups            (admin)  — restic destinations
Groups             (admin)
System             (admin)
Audit Log          (admin)

── BOTTOM ──
Profile, Logout
```

**IA observations:**
- "Backups" admin link → "Backup Destinations" page → only manages remote depots. There is no admin-level **per-account view of offsite health** — that lives only inside each account-detail page.
- "Restore" user link → IMAP-to-IMAP source/target flow. **Restoring from a restic snapshot** lives elsewhere (inside account-detail). The two restore concepts are different flows under the same word.
- "Stores" is admin-only. New users have no way to discover what a store is until they hit a permission boundary.

→ Detail: [`scratch/ia-routes.md`](scratch/ia-routes.md).

---

## Where each chain stage lives in the UI

| Stage | Primary screen | Secondary surfaces | Visibility |
|---|---|---|---|
| **Source IMAP** | Account form (/accounts/new) | Account detail (host/port/auth fields) | Visible |
| **Local mirror (mbsync)** | Account detail "hero" panel + sync history | Dashboard recent-activity feed; Accounts list status pill | Visible & dominant |
| **Local store (filesystem)** | Admin → Stores | Account detail "Migrate Store" section | Admin-only |
| **Remote depot** | Admin → Backups | Account detail collapsed "Offsite Backup" section | Admin only for setup; user-only for assignment |
| **Snapshots** | Account detail collapsed "Offsite Backup" → loaded via HTMX | /restore (mixed with IMAP restore) | Buried |

**Key gap:** the four stages are split across three different IA locations (account-detail page for stages 1 & 2 & 4, admin/backup for stage 3, /restore for snapshot recovery). No screen shows them together.

---

## Honesty audit (worst offenders)

The runtime-ops review surfaced 10+ user-facing claims worth re-examining. The most damaging:

| Claim | What it suggests | What it actually means |
|---|---|---|
| "Backup configured" (per-account badge) | A backup ran | An AccountBackup row exists; no proof of success |
| "Last sync: X ago" | Last successful sync | Last *attempted* sync; success not guaranteed |
| "Backup failed" (in flash message and sync_panel) | Offsite failure | Could be either local sync OR offsite restic |
| "No backup configured" (account detail HTMX response) | No offsite | Could be misread as no local sync either |
| "Snapshot restored as 'Backup X (date)'" success message | Restore done | Account is suspended pending manual activation |
| "Migration in progress" | Visible in UI | The migration banner only shows on the account detail page, not on the dashboard or accounts list |

→ Detail: [`scratch/runtime-ops.md`](scratch/runtime-ops.md).

---

## Data-model naming friction

The model layer mostly aligns with the desired four-stage vocabulary, with two HIGH-severity exceptions:

| Model / column | What it represents | Friction |
|---|---|---|
| `BackupDestination` | A remote depot (S3 / local restic repo) | Word "backup" leaks the local-mirror confusion into the API and the admin page title |
| `AccountBackup` | A per-account *config row* (destination FK + schedule + retention) | Sounds like an *artifact* (the backup itself); should be the *policy* |
| `BackupStatus` enum | Status of the most recent backup *job* | Easy confusion with `JobStatus` (sync) and `SyncState` (account) |
| `SyncJob` | A single mbsync run | Distinct from `Account.sync_state` and `JobStatus` enum |
| `MailStore` | Filesystem path for Maildir | OK, but uses "store" which is internal jargon |
| `BackgroundTask` | Async tasks (FTS reindex, force-resync) | Fine, but currently sparsely used |

The rename cost is mostly LOW (label-only) for the GUI; MED if we rename routes; HIGH if we rename DB tables (Alembic + tests). The recommendation in Phase 8 must factor this.

→ Detail: [`scratch/data-model.md`](scratch/data-model.md).

---

## Maturity vs polish surface

From recent-history analysis, the audit lands at a useful moment:

- **Stable enough to be relabeled:** sync, OAuth, OIDC, Dovecot, Roundcube, store migration, audit log.
- **Active and maturing:** restic offsite backup (12 commits in 30 days, including today), mail restore (16 commits), advanced search, FTS/Tika.
- **Spec'd but not implemented:** dark mode, audit logging UI, provider discovery, user-selectable storage, groups ownership.

The **restic offsite backup** is the youngest feature and the largest source of new vocabulary. We are renaming **before** it has shipped to production — the cheapest possible time.

→ Detail: [`scratch/recent-history.md`](scratch/recent-history.md).

---

## What's working today (worth preserving)

- The Account → Sidebar IA is intuitive once you know the model.
- The hero panel on account detail is genuinely good at communicating sync state in real time (HTMX polling, live progress, error surfacing).
- The system status sticky bar (recent feature) gives a global "what's the backend doing" without per-page noise.
- "Snapshot", "Store", "Maildir" terms are clean and should not be touched.
- The audit log + flash-message toast system is solid and reusable.
- mkdocs site at https://thekoma.github.io/mailfallback/ is published and the documentation vocabulary is more disciplined than the GUI's.

---

## What's broken today (audit will address)

1. **"Backup" overloading** — the headline issue.
2. **No chain visualisation** — users can't see where they are in Source→Local→Depot→Snapshot.
3. **Offsite feature is buried** — collapsed `<details>` block; no dashboard signal.
4. **Restore is two unrelated flows under one word.**
5. **Empty states / first-run copy don't teach the model.**
6. **Error messages don't say which system failed.**
7. **No per-org or per-account "evidence" view for compliance personas.**
8. **Restored-account "suspended" state is not communicated.**
9. **"Destination" collision between depot and IMAP-restore-target.**
10. **Sync vs Backup verbs used interchangeably.**

These are the inputs for the role critiques (Phase 4), the lexicon proposals (Phase 5), and the mockups (Phase 6).

---

## Reading order for the rest of the audit

1. `02-personas-and-journeys.md` — who, doing what.
2. `03-competitor-landscape.md` — what others call these things.
3. `critiques/04-*.md` — role-by-role rebuttal.
4. `lexicon/05-*.md` — three concrete vocabulary proposals.
5. `mockups/06-*.md` — what the screens look like after the rename.
6. `07-strategic-options.md` — A/B/C scope.
7. `08-recommendation.md` — final synthesis.
