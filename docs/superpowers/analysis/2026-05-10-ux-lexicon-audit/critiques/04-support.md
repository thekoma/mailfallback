# Support engineer critique

*Voice: tier-2 support, 500 installs, half my Monday is the same six tickets in different fonts.*

## Bottom line

- **"Backup" will be 60% of my queue.** Every ticket title contains it, meaning three different things — five minutes per ticket disambiguating before I can triage.
- **No "is the chain healthy?" view I can ask the user to screenshot.** Dashboard shows local sync; offsite health hides in collapsed per-account `<details>`. I'll be asking for kubectl logs to answer questions the UI should answer.
- **Restore produces a suspended `Backup X (date)` account and strands the user.** This workflow generates my hardest, most emotional tickets — users who already lost data and now can't get it back without me.

---

## Top 20 tickets I would see in week 1

1. **"My backup says it worked but I don't see any new emails."** — User clicked "Sync Now" yesterday, sees "Last sync: 18h ago" today and assumes nothing happened. Reality: sync ran on schedule at 03:00, the timestamp updated, but the dashboard never says "and N new messages arrived". *Severity: UX bug — missing feedback loop.*

2. **"How do I actually back up my email?"** — User configured an account, sees "first-sync" turn into a green checkmark, thinks they're done. Doesn't realise the local Maildir is single-point-of-failure and that the offsite restic feature is what they actually meant by "backup". *Severity: wrong mental model created by overloaded "backup" word.*

3. **"Backup failed — what do I do?"** — Flash message says "Backup failed" with no qualifier. Was it the local mbsync or the restic snapshot? User pastes screenshot, I have to ask "which backup". *Severity: UX bug — error attribution missing.*

4. **"I restored a snapshot but my mail isn't back."** — Restore created `Backup gmail.com (2026-05-09)` as a suspended account. User can see it in the list but can't open it, can't log into Roundcube as it, doesn't know what "suspended" means here. *Severity: actual UX bug — restore is incomplete by design with no copy explaining the next manual step.*

5. **"The disk is full and now nothing works."** — mbsync ENOSPC silently lands in the per-job log; sync turns red but the user can't tell from the dashboard that the cause is disk. Storage stat card shows a number, not a percentage of capacity. *Severity: UX bug — Storage stat card is informational, not diagnostic.*

6. **"My Gmail has 80,000 messages but MFB shows 12,000."** — First sync still in progress; user doesn't realise mbsync is paginated and slow. The "syncing" badge gives no ETA, no "X of Y messages". *Severity: docs gap + UX bug — no progress indicator for cold-start syncs.*

7. **"Why can't I delete this account?"** — Account has snapshots in restic. User clicks Delete; either it succeeds and they panic about whether snapshots are gone, or it fails with a generic error. The relationship between Account deletion and AccountBackup retention is undocumented. *Severity: docs gap + actual bug (unspecified UX).*

8. **"I changed my Gmail password and now everything is broken."** — OAuth2 token refresh failing; sync_state is error; "last_error" tooltip says "Failed to refresh OAuth2 token" without telling the user to re-authorize. *Severity: UX bug — error message lacks remediation copy.*

9. **"I added an S3 bucket and 'test connection' worked but 'backup now' fails."** — restic init succeeds (bucket reachable, password OK) but the backup itself fails on permissions for `PutObject`. Test only validates a subset of the operations. *Severity: actual bug — test_destination doesn't exercise write path realistically.*

10. **"My second user can't see his account."** — Admin created two users, assigned an account to one, but the other user is also configured against the same source. Account-owners many-to-many is invisible in the new-account form. User configures duplicate. *Severity: UX bug — F10 from the personas doc; no warning on collision.*

11. **"I see 'migration in progress' but the dashboard says everything is fine."** — Store migration banner only shows on the account-detail page. Dashboard shows green sync stats from before the migration started. User confused. *Severity: UX bug — migration state is not a first-class status on overview screens.*

12. **"Roundcube login fails for one specific account."** — User enabled the account *during* a migration; Dovecot SQL passdb returns `migrating=true` and silently denies. Login error is generic "auth failed". *Severity: actual bug — auth path doesn't explain why.*

13. **"What does 'Backup Destination' mean? Is that where my emails are going?"** — P5/P4 reading the admin sidebar for the first time. The word "destination" is colliding with the IMAP "destination account" in /restore. *Severity: lexicon — the headline finding of this audit.*

14. **"Can I trust this for my real email or is it experimental?"** — User read "first-sync (orange)", "Backup configured", "Snapshots: 3" and can't form a confidence judgment. No "evidence" view. *Severity: docs gap + UX gap — Giulia's persona (P3) hits this hardest.*

15. **"I deleted a destination and now my account page shows a red error I can't dismiss."** — Foreign-key dangling between AccountBackup and BackupDestination not caught at delete time. *Severity: actual bug — likely.*

16. **"My retention policy is 'standard' — what does that delete and when?"** — Preset names (light/standard/full) appear in the UI with no hover, no link to docs, no calculation of "next forget runs at X, will delete N snapshots". *Severity: docs gap.*

17. **"FTS search returns nothing for old mail."** — User did a force-resync of a freshly migrated store; Tika hasn't reindexed yet but search box looks fully functional. *Severity: UX bug — no "indexing in progress" indicator on search.*

18. **"How do I move from local-disk backup to S3 without losing snapshots?"** — Currently no in-product path (F9 in personas doc). Will have to walk users through manual restic migration. *Severity: docs gap + missing feature.*

19. **"My OIDC group sync removed an account I needed."** — `sso_sync` group lost a member upstream; MFB silently revoked their visibility on shared accounts. No audit message at the user's level. *Severity: UX bug — too-silent destructive action.*

20. **"I get 'account migration in progress' on every sync attempt — for two days."** — Migration crashed mid-copy; lifespan resume kicked in but is stuck on a flaky filesystem. User sees "Try again later" forever. No "force-cancel migration" UI. *Severity: actual bug + UX gap.*

---

## "Sev 1" patterns

Three go straight to Sev 1 — data integrity, false confidence, trust loss.

- **Tickets #2 + #4 — "I thought I was backed up; I wasn't / I am, but I can't restore."** Same disease, two ends. The UI calls a local mirror "backup" then calls the restore output a "Backup X (date)" suspended account. Mental model never includes a working restore loop. **This is the trust-killer.**
- **Ticket #5 — "Disk full, sync silently broken."** Real data isn't being written; user thinks jobs are running. The Storage stat card needs to be a *health* indicator with a threshold, and "Errors" needs to call out ENOSPC by name.
- **Ticket #15 — "Deleted destination, now everything's red."** Cascade that turns users into ex-users. They blame MFB; data is fine; my job is reassurance. Cause: FK cleanup not enforced or not surfaced.

**Common thread:** the UI's optimistic claims (last sync timestamp, "Backup configured", "Snapshots: 3") carry no confidence qualifier. Honesty is Sev-1 prevention.

---

## What I want self-service'd in the UI

Each of these cuts my queue measurably.

1. **"Re-authorize this OAuth account"** button on the account detail page when `sync_state == error AND last_error LIKE '%refresh%'`. One click → OAuth2 redirect → done.
2. **Status popovers** on every coloured pill (`first-sync`, `idle`, `syncing`, `error`, `suspended`, `migrating`). 30 words each, link to docs.
3. **Restore wizard, not a placeholder account.** After restic restore, ask: "Make this the live mailbox? [Replace] [Keep both] [Inspect first]". The current suspended-account output is a developer's intermediate state leaking to users.
4. **Disk capacity gauge** on the dashboard, not raw bytes. Yellow at 80%, red at 95%, with a "Free up space" link to snapshot pruning.
5. **Per-account backup health pill** (separate from sync pill) on the accounts list: `local: ok / offsite: stale 7d`. Two pills, two stories.
6. **"Test connection" that exercises write + read + delete** on backup destinations, not just init. Display the four-step result, not a binary go/no-go.
7. **Sync ETA on the syncing badge.** Coarse "12k of ~80k" beats a spinner. mbsync output already provides it; we're discarding it.
8. **Per-snapshot "Restore preview"**: "will create N folders, M messages, X GB on target". Lives on the snapshot row.
9. **Migration cancel / force-complete admin button** on the migration banner, with a confirm modal explaining consequences.
10. **"Notify me when this fails" toggle per account.** Reuses configured SMTP, or a prominent banner on next login.
11. **First-run checklist on the empty dashboard.** ① Connect a mailbox ② Wait for first sync ③ Open Webmail ④ Add a backup destination ⑤ Schedule retention. Replaces "Add your first email backup" CTA.
12. **"Last successful sync" separate from "Last sync attempt"** on the account row. Two timestamps where there's one. Cuts ticket #1 in half.

---

## Documentation that MUST exist

If these aren't in `/docs/src/` and linked from the UI's empty states and error messages, 80% of my tickets become docs lookups I shouldn't be doing.

- **"What is a backup vs a sync vs a snapshot in MFB?"** A glossary page, the first thing a new user reads. Currently no equivalent in `/docs/src/`. Should be linked from the empty state on /accounts.
- **"How to restore a mailbox from a snapshot, end-to-end."** `/docs/src/user-guide/restore.md` exists but covers only the IMAP-to-IMAP flow. Needs a second half on snapshot restore + reactivating the suspended account.
- **"Recovering from disk full."** No equivalent today. Should link from any ENOSPC error.
- **"OAuth re-authorization."** Probably belongs in `/docs/src/admin-guide/oauth-providers.md`, currently absent.
- **"Migrating a store from local to S3 (or vice versa)."** Missing entirely. F9 from personas.
- **"What happens when I delete an account."** Particularly the relationship to retained snapshots. Today undocumented.
- **"Reading a sync log."** What "Box ... has no Far counterpart" means, what is normal mbsync chatter vs an actual problem. Tier-1 staff need this too.
- **"How fast should the first sync be?"** A page about expectations: "10k messages / hour over typical residential bandwidth". Pre-empts ticket #6.
- **"Retention presets explained with examples."** What `light/standard/full` actually retain over 1 / 6 / 12 months on a mailbox of size N.
- **"Health check / status page reading guide."** Once we expose `/healthz` outputs to non-admins, we need to explain them.

The existing `/docs/src/getting-started/` and `/docs/src/admin-guide/` are competent reference docs but nothing in there reads like a *playbook for when something goes wrong*. That's what support relies on.

---

## Status pages, runbooks, dashboards

Most MFB logic happens in subprocesses, thread pools, APScheduler — invisible from the GUI. To support 500 users I need:

- **`/admin/system/internals`** — APScheduler job table (next/last run, last result), thread-pool depths for sync/backup/restore, the `_running_logs` and `_backup_progress` dicts. Today only reachable via Python REPL.
- **`/admin/system/orphans`** — surfaces `store_service.detect_orphans()` (UUID dirs with no DB row). Every Postgres restore produces this question.
- **Sync log viewer** with grep + tail and filters by exit_code/date. Today raw inside an account panel.
- **`/admin/system/restic`** — runs `restic snapshots` / `restic stats` per destination on demand, cached. So I don't have to ask users to `docker exec`.
- **A "support bundle" download** zipping: anonymized DB dump, last 1000 sync log lines, last 100 audit events, container versions. One link, 80% solved.
- **User-facing `/status` page** (auth-optional behind VPN): Dovecot up/down, last successful sync per account, last successful snapshot per destination. For Marco-the-family-proxy to bookmark.
- **Runbooks in the repo** for: stuck migration, OAuth refresh storm, restic repo lock, Tika OOM, ENOSPC recovery, Postgres restore. Nothing in `/docs/src/admin-guide/` covers these today.

---

## The one ticket I never want to see

**"I tried to restore my mail and now I can't see any of my mail."**

The worst failure MFB can produce: a user who already had a problem (disk failure, accidental deletion) uses the recovery feature and it makes things worse — or appears to. The current flow produces a suspended `Backup foo@bar.com (2026-05-09)` account, leaves Dovecot pointing at the original (broken or empty) Maildir, and gives no in-product next step. A panicked user breaks things further.

**Make it impossible by design:**

1. **Restore is a guided two-step.** Step 1 extracts to staging (current behaviour). Step 2 is an explicit "Activate this restore" wizard: replace the live mailbox, keep both, or discard. No state where staged data is undiscoverable from the account page.
2. **The original Maildir is never overwritten in-place.** It's renamed to `{uuid}.pre-restore-{timestamp}/` and shown as recoverable "previous state" for 30 days, then GC'd with a warning email.
3. **Snapshot list is cached in MFB's DB**, not only queried live from restic. If the destination is unreachable mid-restore, user sees what was available and a clear "destination unreachable, retry?" — not an empty list that looks like data loss.

---

## Things the current-state doc gets wrong from support angle

1. **It treats "Backup" overloading as a vocabulary issue. From support's chair it's an attribution issue.** When a flash message says "Backup failed", I don't just need a different word — I need the message to say *which subsystem failed and what the user should do*. Renaming "Backup Destination" to "Remote Depot" without fixing the error-message attribution will reduce confusion for new users by 30% and reduce my mean-time-to-triage by zero.

2. **The doc lists "Snapshot, Store, Maildir are clean — preserve them"**, but at least one of those (Snapshot) is going to *create* tickets the moment a non-technical user (P4 Luca) sees it without context. "Snapshots: 3" on the account page sounds like backup confirmation; users will not know what restic is, will not look up the term, and will assume "snapshot = full backup of everything, every time". The clean lexicon needs explanatory copy, not just consistency.

3. **The doc frames the "honesty audit" as a UI/copy concern.** From support, it's a database-shape concern. The fix isn't only a label rewrite — it's adding columns: `last_successful_sync_at`, `last_attempted_sync_at`, `last_successful_backup_at`, `last_attempted_backup_at`. Two timestamps where today there is one. The label problem follows naturally; the support-burden problem doesn't get fixed by labels alone.
