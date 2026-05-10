# Recent history digest — MailFallBack (MFB)

## Method

- Branch checked: `main` (and `analysis/lexicon-ux-2026-05-10`)
- Time window: last 30 days (since ~2026-04-10)
- Commit count in window: 129
- Commands: `git log --oneline -100`, `git log --pretty=format:'%s' --since='30 days ago'`, `git log --pretty=format: --name-only --since='30 days ago' | sort | uniq -c | sort -rn`

## Commit summary by theme

129 commits over 30 days. Type breakdown:
- feat: 48 · fix: 36 · docs: 19 · refactor: 5 · chore: 10 · test/ci/style: 3

### Major themes

**1. Backup feature (offsite + restic) — 12 commits.** Newest and most expansive. Started with design spec 2026-05-10. Touches models, services, workers, UI screens, Docker integration. Representative commits: `feat: add BackupDestination and AccountBackup models with migration`, `feat: add backup worker with thread pool executor`, `feat: backup scheduler integration`, `feat: insecure TLS option for backup destinations`.

**2. Full-Text Search (FTS) + Tika — 8 commits.** Iterative stabilization. Pattern of trial-and-error around Dovecot 2.4 FTS module quirks: `fix: disable fts_decoder_driver — module not in official Dovecot 2.4 image`, `fix: enable Tika FTS — fts_decoder is built into fts plugin, not separate`, `fix: FTS search working end-to-end`. Eventual claim of working end-to-end in commit `4bde4e2`.

**3. Mail restore (source → target) + advanced search — 16 commits.** Comprehensive feature spanning 3+ weeks. Spec filed 2026-05-06. Restore worker is the most-touched file in the window (12 edits). Polish iterations visible: resizable columns, sortable, responsive tables, advanced search panel.

**4. Centralized config generation — 7 commits.** Infrastructure refactor: MFB now generates Dovecot + Roundcube configs at startup. Spec 2026-05-09. Eliminates static config anti-pattern. `feat: add config_generator service`, `refactor: docker-compose uses generated config volumes`, `chore: remove static Dovecot and Roundcube config files`.

**5. System status panel + async tasks — 4 commits.** New global sticky bar for backend state visibility. Spec 2026-05-09. `feat: system status panel, async FTS/resync, show-all-users toggle`, `refactor: move system status to global sticky bar visible on every page`.

**6. Security fixes — 4 commits.** Cluster of ~117 issues fixed. `fix: 87 security and bug issues — CRITICAL RCE, auth bypass, SSRF, XSS, IDOR, rate limiting`, plus follow-ups for medium severity. Mostly server-side, no widespread UI churn.

**7. Password / toast UX polish — 6 commits.** Global toast notifications via session flash, password length lowered to 8, SSO password reset flow, error feedback in profile page.

**8. OAuth2 / OIDC refinement — 5 commits.** Webmail OAuth split from Dovecot OAuth, introspection URL added, `fix: authorization bypass in OAuth, account update, and group edit`.

**9. Docker hardening — 5 commits.** Rootless container, named-volume permission inheritance, init-confs container removed, restic added to Dockerfile.

**10. MkDocs documentation site — 4 commits.** New public docs at https://thekoma.github.io/mailfallback/. ASCII diagrams converted to Mermaid.

**11. Store migration + Dovecot — 3 commits.** Edge cases: cancel flag, separator escaping, IMAP reconnection on broken pipe.

## Maturity assessment

| Feature area | Maturity | Evidence |
|---|---|---|
| Sync (mbsync) | Stable | No changes in 30d; core feature; scheduler stable. |
| Restic offsite backup | In-flight (early) | 12 commits, design spec today, UI just added. Worker + scheduler integrated. Not yet production-validated. |
| FTS / Tika | In-flight (stabilizing) | 8 commits of config fixes; multiple Dovecot quirks; "working end-to-end" but pattern is trial-and-error. |
| Mail restore (IMAP→IMAP) | In-flight (maturing) | 16+ commits, polish iterations visible (resizable/sortable/responsive). Most-touched file. |
| Advanced search | In-flight (maturing) | Spec 2026-05-06. Field toggles, type/date/scope filters integrated into restore. |
| OAuth2 / OIDC | Stable with refinement | Webmail OAuth separated; introspection URL; bypass fixed. |
| Dovecot integration | Stable | Rootless, perms hardened, dynamic config. |
| Roundcube webmail | Stable | Patch removed (PHP 8.4 no longer needed). Read-only access works. |
| Store migration | Stable | Edge cases handled (separator collision, reconnection). |
| Restore (recovery from backup) | In-flight (new) | Restic restore → temporary suspended account → manual fix-up. Three-layer orchestration. |
| Audit log | Stable | No recent changes. |
| Groups | Stable with fixes | Auth bypass closed. No new features. |

## Hotspots — 5 most modified files (last 30 commits)

1. `src/mailfallback/routers/ui_admin.py` — 16 edits. Backup destinations, system status, settings.
2. `src/mailfallback/services/restore_worker.py` — 12 edits. Restore execution, IMAP read/write, reconnection.
3. `src/mailfallback/templates/base.html` — 11 edits. Sidebar, system status bar.
4. `src/mailfallback/static/css/style.css` — 11 edits. Table resizing, responsive, toasts, sticky bar.
5. `src/mailfallback/app.py` — 11 edits. Startup, migration, scheduler, config generator.

Pattern: admin UI + restore worker + global styling. Backup, restore, and config generation are the three big moving pieces.

## In-flight work (uncommitted clues)

### Design specs filed in `/docs/superpowers/specs/`

| Date | Spec | Status |
|---|---|---|
| 2026-05-10 | Offsite backup with restic | Filed; implementation underway |
| 2026-05-09 | System status panel + async tasks | Filed; complete |
| 2026-05-09 | Centralized config generation | Filed; complete |
| 2026-05-06 | Advanced search | Filed; complete |
| 2026-05-06 | Mail restore (source → target) | Filed; complete |
| 2026-05-05 | Dark mode + audit logging | Filed; *not yet visible in commits* |
| 2026-05-01 | Provider-aware account flow | Filed; *not yet visible in commits* |
| 2026-05-01 | User-selectable storage | Filed; *not yet visible in commits* |
| 2026-05-01 | Groups ownership | Filed; *not yet visible in commits* |
| 2026-04-30 | UUID-based Maildir | Filed; completed in prior sessions |
| 2026-04-29 | Roundcube webmail integration | Filed; completed in prior sessions |

Implication: dark mode, audit logging, provider discovery, storage selection, groups ownership are spec'd but not code-committed. They're queued behind backup + restore + FTS stabilization.

No `STATE.md` / `TODO.md` in repo. No TODO/FIXME/XXX hits in `src/mailfallback/`.

Active branch is the audit branch we're on now.

## Open contradictions and tensions

**1. Backup terminology ambiguity (active audit).** "Backup" is used loosely:
- Restic remote = "backup"
- Local Maildir retention = implicit "backup"
- Account restore from restic = "restore"
- IMAP source-target restore = also "restore"

This audit's purpose. The kickoff identifies the four-stage model (Source → Local backup → Remote depot → Snapshots).

**2. Per-account vs shared restic repo.** Decision is per-account. Risk: shared S3 credentials across accounts; mitigation is path isolation. Not called out clearly to the user.

**3. FTS module fragility.** Pattern of `disable → re-enable → re-test → claim done` suggests fragility against Dovecot version drift.

**4. OAuth2 split.** Webmail OAuth and Dovecot OAuth diverged — two flows to test, document, troubleshoot.

**5. Restore scope creep.** Restore worker was designed for source→target between MFB accounts. New flow uses it for restic-restore→ephemeral-account→IMAP-restore. Three layers of indirection.

## Summary

A codebase in active polish.

- Newest: offsite backup with restic (design spec today, 12 commits).
- Maturing: mail restore + advanced search.
- Stabilizing: FTS.
- Hardened: security (117 issues fixed), containers.
- Queued: dark mode, audit logging, provider discovery, storage selection, groups ownership.

The audit kickoff on lexicon recognizes "backup" overloading as key UX debt. Next 30 days likely focus: lexicon standardization, restic completion + production validation, FTS stability, restore validation in production-like settings.
