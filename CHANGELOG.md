## [2026.07.4] - 2026-07-18

### Other

- Merge pull request #210 from thekoma/feat/index-walk-optimization

Index walk optimization: zero-write steady state (fixes the WAL churn from bug.md)

### Performance

- Skip-parse walk with write-on-change reconcile — one bulk SELECT, zero writes at steady state *(index)*

### Testing

- Pin incremental insert leaves existing rows untouched *(index)*
## [2026.07.3] - 2026-07-18

### Documentation

- Inline secrets guide, derivations table and conditional NOTES *(chart)*

### Features

- Opt-in inlineSecrets mode — chart-rendered env Secrets with vault-pointer passthrough and coupled-key derivation *(chart)*

### Fixes

- String-coerce inline secret annotation values *(chart)*

### Maintenance

- Inline-secrets fixture + derivation/override/annotation assertions *(chart)*

### Other

- Merge pull request #207 from thekoma/feat/chart-inline-secrets

Chart: opt-in inlineSecrets mode (vault-pointer friendly)
## [2026.07.2] - 2026-07-17

### Documentation

- NOTES and README with secret contract and install guide *(chart)*

### Features

- Native TLS config via MAILFALLBACK_DOVECOT_TLS *(dovecot)*
- Restore https scheme behind TLS-terminating proxies in generated custom.php *(webmail)*
- Skeleton — Chart.yaml, values contract, bjw-s common 5.0.1 dependency *(chart)*
- Common-library translation layer (4 controllers, journal invariants baked in) *(chart)*
- Optional Certificate, HTTPRoutes and no-SSO SecurityPolicies (default off) *(chart)*

### Fixes

- RunAsUser 1000 on wait-config init + robust route-shield restore *(chart)*
- Register bjw-s helm repo before dependency build *(ci)*

### Maintenance

- Lint + render assertions + kubeconform *(chart)*
- Package and push the helm chart to ghcr OCI (before tagging) *(release)*

### Other

- Merge pull request #203 from thekoma/feat/helm-chart

Official Helm chart (bjw-s common) + native TLS/proxy config generation
## [2026.07.1] - 2026-07-17

### Features

- Env-gated NFS-safe mail settings (MAILFALLBACK_DOVECOT_NFS) *(dovecot)*

### Fixes

- SSO users can set their first password; fix portaled kebab actions in admin users *(profile)*
- Uv run --no-sync — the runtime must never revalidate against PyPI (venv is baked at build) *(docker)*

### Other

- Merge pull request #201 from thekoma/feat/dovecot-nfs

NFS-safe dovecot settings + SSO first-password fix + admin kebab actions fix
## [2026.07.0] - 2026-07-13

### Dependencies

- Update python docker tag to v3.14 *(deps)*
- Update astral-sh/setup-uv action to v8 *(deps)*
- Update softprops/action-gh-release action to v3 *(deps)*
- Update postgres docker tag to v18 *(deps)*
- Update gitleaks/gitleaks-action digest to ff98106 *(deps)*
- Update astral-sh/setup-uv action to v8.3.1 *(deps)*
- Update docker/setup-buildx-action digest to bb05f3f *(deps)*
- Update docker/login-action digest to af1e73f *(deps)*
- Update docker/build-push-action digest to 53b7df9 *(deps)*
- Update astral-sh/setup-uv action to v8.3.2 *(deps)*
- Update softprops/action-gh-release digest to 3d0d988 *(deps)*
- Update actions/checkout action to v7 *(deps)*
- Update gitleaks/gitleaks-action action to v3 *(deps)*
- Update vendored frontend libs + Renovate vendor pipeline *(deps)*
- Update peter-evans/create-pull-request action to v8 *(deps)*

### Documentation

- Update README with dark mode and audit logging features
- Update RESUME.md for session 11 handoff
- Add MAILFALLBACK_METRICS_API_KEY and Dovecot vars to .env.example
- System status panel + async background tasks design spec
- Add MkDocs Material documentation site
- Replace ASCII art diagrams with Mermaid
- Prevent variable names from wrapping in config tables
- Add Repositories admin page + lexicon polish
- Drop Italian column — English-only is the only language *(lexicon)*
- Note non-atomic ensure_mounted race for future hardening *(mount)*
- Clarify /restore/jump creates persistent Recoveries *(restore)*
- Update module docstring to deep search terminology *(search)*
- Pin dovecot image to 2.4.2 in deploy manifests
- Sync budget/initial-sync notes
- Add runtime flow diagrams (sync, restore, off-site, auth) *(architecture)*
- Clarify auth diagram — user login vs per-account OAuth2 *(architecture)*
- Add security model page *(architecture)*
- Correct multi-tenant write-access description + lexicon *(architecture)*
- Add notifications (Apprise) page *(user-guide)*
- Add sync budget + watchdog page *(admin-guide)*
- Fix sync-budget lexicon (local sync is 'sync', not 'backup') *(admin-guide)*
- Document restore staging workspace + attachment search *(user-guide)*
- Fix stale default admin credential (changeme -> changeme1234!)
- Regenerate screenshots with demo-only data
- Refresh README (features, verified quick start, docs link, demo hero)
- Final-review fixes (notifications edit wording, restore chip label, flows heading)
- CalVer release flow in CLAUDE.md

### Features

- MailFallBack — complete implementation (sessions 1–8)
- Add User.preferences JSONB column and AuditLog model
- Add PATCH /api/preferences endpoint for theme persistence
- Add audit_service.log_action() with action labels
- Add dark mode with toggle, localStorage, and CSS custom properties
- Add admin audit log page with filters and pagination
- Wire audit logging into admin and config operations
- Wire audit logging into account and sync operations
- Enable full-text search via Dovecot fts-flatcurve
- Add RestoreJob model and migration 007
- Add restore audit action labels
- Add restore_service with job CRUD and validation
- Add restore_worker with IMAP read/write execution
- Add REST API endpoints for restore operations
- Add mailbox browse and search API via Dovecot IMAP
- Add restore UI page with sidebar link and HTMX partials
- Use ephemeral Dovecot users for restore IMAP access
- Add restore mode switching, search panel, and custom folder prefix
- Advanced search with field toggles, type/date/scope filters
- Sortable columns, select all, column toggle, responsive table
- Add To and Message-ID columns to search results
- Resizable columns, Folder always toggleable
- Amber/yellow warning badge for partial restore success
- Detect target IMAP hierarchy separator for folder mapping
- Auto-reconnect on broken pipe during restore
- Session 11 — restore UX, scheduler fix, JS split
- Admin toggle to show all users' restore jobs
- Expose Dovecot OpenMetrics on port 9900
- System status panel, async FTS/resync, show-all-users toggle, separator warning
- Add config_generator service with Dovecot and Webmail templates
- Generate component configs at app startup
- Webmail nav link respects webmail_enabled flag
- Add SVG favicon (mail-check icon)
- Tika FTS attachment indexing + modules section in Settings page
- Auto-purge FTS indexes and reindex when Tika config changes
- Toast notifications for password change and admin errors
- Green toast on successful admin password reset
- Global toast notifications via session flash messages
- Add BackupDestination and AccountBackup models with migration
- Add restic subprocess wrapper service with tests
- Add backup worker with thread pool executor and tests
- Add backup destinations admin UI with sidebar navigation
- Backup scheduler integration
- Backup status in system bar and restore badge
- Add restic to Dockerfile
- Test backup destination on create and on-demand
- Edit backup destination — inline expandable form
- Insecure TLS option for backup destinations
- Wave 1a — LEXICON.md + advisory CI lexicon-check *(lexicon)*
- Wave 1b — four honesty-adjacent fixes *(ux)*
- Wave 2 — English-only lexicon rename + audit display labels *(ux)*
- Wave 2.5 — Alembic migration for AccountBackup progress columns *(backup)*
- Wave 3 — IA reorder, two-pill Repository column, /recover route split *(ux)*
- Wave 4 — chain hero, empty states, Repository status quartet *(ux)*
- First-run setup checklist on dashboard *(ux)*
- Force admin password change at first login *(security)*
- First-time explainer callout on the chain hero *(ux)*
- Account wizard — 3-step flow for /accounts/new *(ux)*
- Repository wizard — 3-step Add Repository on /admin/backup *(ux)*
- Recoveries as Account sub-objects, never synced
- Nest recoveries under accounts + fix /accounts wrapping *(ui)*
- Account detail page bento redesign + linked-view treemap *(ui)*
- /restore Calendar of Safety + bug fix on /restore/move *(ui)*
- Add recovery workspace tunables (TTL, max mounts, backend) *(config)*
- Kind/last_accessed_at/ttl_minutes columns + migration 013 *(recovery)*
- Create_recovery accepts kind + ttl_minutes (default persistent) *(recovery)*
- Ensure_mounted — idempotent ephemeral Recovery creation *(mount)*
- Touch_mount + force_unmount *(mount)*
- Cleanup_idle_mounts — sweeps expired ephemerals *(mount)*
- Hourly mount-cleanup job *(scheduler)*
- Extract _search_namespace_for_query helper *(restore)*
- Workspace search endpoint — mount + dedup by Message-Id *(restore)*
- /restore workspace shell — presets, sidebar, status strip *(ui)*
- Workspace JS — search wiring, preset toggles, result rendering *(ui)*
- Workspace search returns per-source locations (folder, uid) *(restore)*
- Restore selected groups by source and submits one job per source *(restore)*
- /restore workspace styles — presets, sidebar, badges, status strip *(ui)*
- Workspace cost preview — count snapshots in range live *(restore)*
- Wire workspace Advanced controls — search body + TTL override *(restore)*
- Differentiate 3 workspace presets — search/folder/full flows *(restore)*
- Multi-field search panel — Subject/From/To/Body + type filter *(restore)*
- Replace date inputs with visual range slider (two handles) *(restore)*
- Slider thumbs polish + debounced cost preview with loading state *(restore)*
- Introduce Alpine.js for workspace — declarative reactive UI *(restore)*
- Replace slider with flatpickr inline calendar + snapshot day dots *(restore)*
- Schema + models for messages/snapshot_messages/rebuild_status *(mail_index)*
- Upsert_message_set walks live Maildir, parses headers *(mail_index)*
- Record_snapshot — bulk INSERT bits for alive messages *(mail_index)*
- Prune_snapshot — DELETE bits for a given snapshot_id *(mail_index)*
- Post-sync hook calls index_service.upsert_message_set *(sync)*
- Post-backup record_snapshot + post-prune prune_snapshot hooks *(backup)*
- Search_service Phase 1 — Postgres index query with auth + filters *(search)*
- POST /api/restore/search endpoint (cross-account, paginated) *(api)*
- Phase 2 body filter via Dovecot SEARCH on Phase 1 survivors *(search)*
- /workspace/search becomes wrapper around search_service (deprecated) *(api)*
- Mfb CLI entry point with `mfb index status|rebuild-account` subcommands *(cli)*
- List_files helper — restic ls --json --recursive parser *(restic)*
- Mfb index backfill-snapshots — populate bits via restic ls *(cli)*
- Deep_search_timeout_seconds setting (default 10s) *(config)*
- _parse_message_id_from_fetch helper *(search)*
- _dovecot_body_search — full-folder live body search with deadline *(search)*
- Deep search unions body matches into index query (SQL), drops survivor filter *(search)*
- Deep search param on search endpoints + partial in wrapper response *(api)*
- Replace Body checkbox with Deep search toggle + partial banner *(ui)*
- Redesign pass 1 — Geist, neutral sidebar, calm chips, themed restore *(ui)*
- Redesign pass 2 — self-hosted assets, dashboard hierarchy, zero inline styles *(ui)*
- Redesign pass 3 — composed empty states for admin tables *(ui)*
- Redesign pass 4 — System page structure and naming *(ui)*
- Redesign pass 5 — audit log and profile polish *(ui)*
- Redesign pass 6 — Mail safety chain stages as cards *(ui)*
- Wizard provider cards use real brand logos *(ui)*
- Account wizard visual pass *(ui)*
- Restore filters align and react to deep search *(ui)*
- Boto3 connection probe without restic side effects *(backup)*
- Inline HTMX feedback for repository connection test *(backup)*
- RepositoryAttachment model and config-backup columns *(backup)*
- Repository inventory service (prefix listing + classification) *(backup)*
- Repository contents panel with orphan attach/detach *(backup)*
- Restore recoveries from attached repository prefixes *(backup)*
- Encrypted full-config export/import with preserved IDs *(backup)*
- Config backup runner and latest-config fetch via restic *(backup)*
- Scheduled encrypted config snapshots per repository *(backup)*
- Disaster-recovery config restore from repository *(backup)*
- Restic password override and snapshot tags *(backup)*
- Per-attachment restic password column *(backup)*
- Pre-create connection test with prefix count *(backup)*
- Per-attachment restic passwords with validation *(backup)*
- Tag snapshots with mailbox metadata *(backup)*
- Backfill metadata tags on existing snapshots *(backup)*
- Show snapshot destination prefix on the account page *(ui)*
- User_allowed_repositories table with upgrade backfill *(backup)*
- Admin-managed per-user repository grants *(backup)*
- Enforce repository grants on policy configure *(backup)*
- Filter policy repositories to the user's allowed set *(ui)*
- Include repository grants in config snapshots *(backup)*
- Restore actions in repository contents panel *(backup)*
- Mail_index.attachments table + has_attachments flag *(index)*
- Parse attachment metadata on first index of each message *(index)*
- Backfill-attachments CLI, resumable per-message *(index)*
- Attachment metadata and hash in search results *(search)*
- Dump_file for single-file snapshot extraction *(restic)*
- In-app message preview from live maildir or snapshot *(restore)*
- Resolve-uids endpoint for restore-to-origin *(restore)*
- Cross-account search, attachment chips, preview pane in restore workspace *(ui)*
- Audited admin scope toggle for cross-user search *(restore)*
- Staging area models + migration *(staging)*
- Writable Staging/ namespace via userdb + global ACL *(staging)*
- Staging service — copy-in, reconcile, quota, lifecycle *(staging)*
- Scheduled cleanup of expired staging areas *(staging)*
- Status/add/empty endpoints with audit *(staging)*
- Push-upstream jobs from the staging area *(staging)*
- Staging bar, add-to-staging, push panel *(ui)*
- Attachment search with optional content matching *(search)*
- Attachment search and download endpoints *(restore)*
- Tika content extraction with caps and content backfill *(index)*
- Attachment search preset with download and content snippets *(ui)*
- Custom folder mode for push + folder_mapping hygiene *(staging)*
- Unified restore destination panel, folder multi-select, confirmations *(ui)*
- Compact time-range chips with popover calendar, all-time default *(ui)*
- Budget/pause/initial-sync columns (migration 021) *(sync)*
- Failure classifier (throttle/transient signatures) *(sync)*
- Budget resolution + ETA/backoff math *(sync)*
- Maildir byte sampler, crash-safe ledger, budget stop *(sync)*
- Throttle-aware worker — pause, priority pass, upstream totals *(sync)*
- Zombie job recovery sweep on startup *(sync)*
- Scheduler honors pauses, manual sync overrides *(sync)*
- Initial-sync progress, pause states, budget override *(ui)*
- Env-backed watchdog/runtime settings *(config)*
- Add needs_reauth SyncState + migration 022 *(models)*
- Park account in needs_reauth on invalid_grant token refresh *(sync)*
- Scheduler skips needs_reauth; reconnect + UI wired *(sync)*
- Stamp live-progress entries with updated_ts *(sync)*
- Re-drive incomplete initial sync via expired-pause on recovery *(sync)*
- In-flight watchdog reaper for stalled running jobs *(sync)*
- Deadline-bounded mbsync read + pool-health warning *(sync)*
- Pico-native first-sync progress, recap, drop dot grid *(ui)*
- Folder total in recap from STATUS pass (migration 024) *(sync)*
- Notification_channels + accounts.last_notified_state (migration 025) *(models)*
- Notification_service — Apprise send + guarded per-owner emit *(notify)*
- Emit notifications on sync problem transitions + stale check *(notify)*
- Profile UI to manage notification channels (CRUD + test) *(notify)*
- Link Apprise docs + copyable URL examples in profile *(notify)*
- Redesign channels as responsive cards + per-channel event editing *(notify)*
- Add NotificationChannel.payload_format (migration 026) *(notify)*
- JSON payload format + non-deduped activity emit paths *(notify)*
- Emit activity events on sync/initial-sync/restore/backup/account-add completion *(notify)*
- Accept activity events + payload_format in channel add/update *(notify)*
- UI for activity events (grouped) + per-channel text/JSON format *(notify)*
- Richer JSON envelope — account object + per-event details *(notify)*
- Port ACL to 2.4.3+ settings blocks (drop global acl file) *(dovecot)*
- Version.py single source + /healthz version field *(version)*
- Show app version in System page and sidebar footer *(ui)*
- Mailfallback_info metric with app version *(metrics)*
- OCI version/revision labels via build args *(docker)*
- Next_calver.sh CalVer computation with tests *(release)*
- Git-cliff changelog configuration *(release)*

### Fixes

- Configure Roundcube to search body via FTS and avoid multi-folder crash
- Add missing migration for groups and association tables
- Use String instead of Enum for shared jobstatus in migration 007
- Use last 4 chars of account UUID for Dovecot namespace prefix
- Split multi-word search into AND-joined IMAP TEXT criteria
- Hide Folder column properly when not in All folders scope
- Widen checkbox column and trigger search on Enter key
- Replace hx-vals with fetch for Start Restore button
- Add debug logging to restore worker, fix total count for selection mode
- Restore job status reflects actual outcome
- Selection mode only processes folders present in selected_uids
- Reconnect both source and target on IMAP abort/broken pipe
- Escape separator chars in folder names to prevent hierarchy collision
- XSS vulnerabilities in JS innerHTML and inline event handlers
- Authorization bypass in OAuth, account update, and group edit
- Migration safety — cancel flag, store guard, profile guard
- Metrics auth, import path traversal, password policy
- 5 medium security issues — OAuth CSRF, enumeration, store bypass, OIDC role, config injection
- Remaining 14 medium security issues
- 16 bug issues — validation, audit, metrics, error handling
- 87 security and bug issues — CRITICAL RCE, auth bypass, SSRF, XSS, IDOR, rate limiting
- Settings page shows correct status for async FTS/resync tasks
- Config volume permissions and path structure for Docker deployment
- Rootless container with init-confs pattern for volume permissions
- Remove init-confs container — named volumes inherit permissions from image
- Add oidc_userinfo_url setting for Dovecot OAuth2 introspection
- Disable fts_decoder_driver — module not in official Dovecot 2.4 image
- Enable Tika FTS — fts_decoder is built into fts plugin, not separate
- Use processStatus instead of reload for Dovecot health check
- SSO users can set password, admin reset catches errors, switch to SnappyMail
- Catch ValueError in profile password change, show proper feedback
- Show password_too_short and generic errors on admin users page
- Reduce minimum password length to 8 characters
- SSO admin without local password can reset user passwords
- FTS search working end-to-end in IMAP + Tika attachment indexing
- Move screenshots into docs/src/ and fix image paths for MkDocs *(docs)*
- Correct _run_restic call signature in test_destination
- Fix(dovecot)+docs: wave 1.5 — userdb filters suspended + disaster recovery

Plan: docs/superpowers/plans/2026-05-10-mfb-ux-lexicon-rollout.md § Phase 3.

Closes the bug where the Dovecot userdb endpoint
(/api/internal/dovecot/userdb/{username}) returned suspended accounts
in its namespaces list. The endpoint already filtered on Account.enabled
and MailStore.enabled but ignored Account.suspended, so the suspended
flag was effectively decorative as far as IMAP login went. After this
change, suspended accounts (including the "Recovered ..." placeholders
created by /accounts/{id}/backup/restore/{snapshot_id}) are NOT served
by Dovecot until the operator clicks "Promote to live" (Wave 1b).

Adds the disaster-recovery admin doc page documenting the
MAILFALLBACK_SECRET_KEY + Postgres double-loss footgun: snapshots in a
Repository are useless without both. Includes back-up cadence guidance,
the recovery procedure, and the "what MFB does NOT back up" section.

A small footer link from the /admin/backup page points the operator to
the new doc.

Verification: ruff clean, 394/394 tests pass (393 + 1 new
test_userdb_filters_suspended_accounts), `mkdocs build --strict` clean.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- Wave 2 follow-up — IDLE hero copy missed by lexicon rename *(ux)*
- Residual lexicon — sidebar/labels for Mail stores + Default mail store *(ux)*
- Force-pwd middleware exempts OIDC users + handles None hash *(security)*
- Break chain-hero explainer wall of text into a bullet list *(ux)*
- Chain hero stages are now real clickable links *(ux)*
- /recover wizard lands on the snapshot picker, not a dead anchor
- Workspace search — unpack conn tuple, cleanup temp user, require auth *(restore)*
- Snapshot-source restores — align namespace + strip dest prefix *(restore)*
- Workspace search quotes multi-word query, covers Subject OR From *(restore)*
- Workspace destination dropdown lists all accounts (not just protected) *(restore)*
- Em-dash in Recovery namespace breaks IMAP — use ASCII dash *(restore)*
- Unique namespace prefix per Recovery + dedupe userdb response *(recovery)*
- Workspace live SELECT uses account namespace prefix (was bare INBOX) *(restore)*
- Search button no longer full-width + tooltips don't overlap *(restore)*
- Invert range slider so today is on the right (visual time flow) *(restore)*
- Tooltip positioning uses explicit class names (nth-of-type didn't match) *(restore)*
- Set rangeStart/End directly on init + widen sidebar for calendar *(restore)*
- Workspace search iterates ALL folders of namespace (was INBOX only) *(restore)*
- Use IMAP TEXT for body search (matches all headers+body, like Roundcube) *(restore)*
- Phase 2 uses _sanitize_imap_string (strips CR/LF + control chars) *(search)*
- Upsert_message_set commits in batches of 1000 *(mail_index)*
- Backfill_snapshots skips already-processed snapshots (resumable) *(mail_index)*
- Wrapper resolves UID via Dovecot SEARCH HEADER for cycle-1 Restore Selected *(api)*
- Chunk body-search FETCH + restore keyword sanitization test *(search)*
- Enforce deep-search deadline between FETCH batches *(search)*
- Wizard OAuth path creates account before redirect *(ui)*
- Admin-btn text readable in light theme *(ui)*
- Deep search handles non-ASCII keywords via UTF-8 literal *(search)*
- Restore Mailbox select lists all accounts, not only protected *(ui)*
- SSO admins without local password no longer 500 *(dashboard)*
- Surface re-auth flow when the OAuth refresh token is rejected *(ui)*
- Lexicon-check qualifier match is case-insensitive on BSD awk *(scripts)*
- Layout fits narrow windows without horizontal scroll *(ui)*
- Restore workspace shows search params only for single-mail preset *(ui)*
- Restore workspace single-mail layout *(ui)*
- Tidy restore workspace filters panel *(ui)*
- Align restore folder/full preset flows *(ui)*
- Docker tag invalid on pull_request runs *(ci)*
- Harden s3 probe error handling *(backup)*
- Catch malformed endpoint ValueError in s3 probe *(backup)*
- Connection test no longer leaves junk repos in the bucket *(backup)*
- Repository create tests before commit, edit re-tests with rollback *(backup)*
- Polish inline repository test feedback *(ui)*
- Attachment cascade deletes and queryable config-backup status *(backup)*
- Prefix validation and contents panel hardening *(backup)*
- Resolve foreign-prefix maildir roots and surface attached snapshots *(backup)*
- Remap IDs on config import into seeded installs *(backup)*
- Reject reserved prefixes in repository attach *(backup)*
- Exclude config prefix from connection-test count *(backup)*
- Move blocking restic validation off the event loop *(backup)*
- Role-aware empty state for policy repositories *(ui)*
- Repositories page layout — page-wide, colspan, contained sub-rows *(ui)*
- OIDC login survives slow identity providers *(auth)*
- Attachment model relationship, FTS expr constant, docs *(index)*
- Pin part_index contract, honest size_bytes, parse-failure marker *(index)*
- Backfill skips missing files for retry, covers non-INBOX folders *(index)*
- Snapshot preview survives flag renames, bounded reads *(restore)*
- Single namespace-prefix source, log failed SELECTs *(restore)*
- Preview race guard, honest counts, scope-driven calendar, cleanups *(ui)*
- Snapshot-dates race guard, space-key activation, safe retry *(ui)*
- Neutralize Pico [role=button] styling on result rows *(ui)*
- XOAUTH2 authentication for OAuth2 restore targets *(restore)*
- XOAUTH2 for separator-warning destination probe *(restore)*
- Snapshot-dates scope consistency, flake root-cause, audit pin *(restore)*
- Correct ACL semantics comment, pin dovecot 2.4.2 *(staging)*
- Uncapped snapshot reads, atomic writes, lifecycle edges *(staging)*
- Typed items schema, route-order guard, escalation forensics *(staging)*
- Fail-safe job errors, push cancellation, partial-submit visibility *(restore)*
- Staging status visibility, x-cloak, panel tidiness *(ui)*
- Permanent delete — Trash is unreachable from read-only namespaces *(webmail)*
- Blank trash_mbox is the real direct-delete knob *(webmail)*
- Gate content columns on terms, pin headline opts, expr comment *(search)*
- Download hardening — nosniff, walk parity, page bounds, escalation flag *(restore)*
- Extraction summary log, hermetic disabled test, cap docstring *(index)*
- Sequence-guard message search, refresh stale comments, generic file icon *(ui)*
- Preview close, dynamic grids, attachment-aware preview actions *(ui)*
- Separator-safe custom roots, mUTF-7 folder encoding, surfaced errors *(restore)*
- Unambiguous folder picker, downloadable pane attachments, Preview email label *(ui)*
- Control-row liveness consistency, apply affordance *(ui)*
- Pin time-range popover to viewport on small screens *(ui)*
- Mobile preview overlay, grid min-width blowout, responsive polish *(ui)*
- Clickable attachment rows, x-if download action vs !important *(ui)*
- Att grid mobile order, unified multi-select staging for attachments *(ui)*
- Derive bottom offsets from real staging bar height *(ui)*
- Stack bottom dock — sheet clears action bar, panel above sheet *(ui)*
- Action bar joins the fixed dock on mobile *(ui)*
- Folder lists survive duplicate LIST lines, skip Noselect *(restore)*
- Budget re-arm at invocation boundary, pause hygiene (review) *(sync)*
- Anchor timeout signature, cap backoff exponent (review) *(sync)*
- Preserve existing budget/throttle pause on recovery (review) *(sync)*
- Guard reaped-job relabel + honest read-deadline docs (final review) *(sync)*
- Persist completed status before post-sync bookkeeping *(sync)*
- Widen accounts.maildir_size_bytes to BigInteger *(stats)*
- Show pause resume time as relative, not UTC wall-clock *(ui)*
- Record an audit entry for bulk 'sync all' *(audit)*
- Close audit-coverage gaps for state-changing actions *(audit)*
- Surface needs_reauth accounts in the dashboard Needs Attention panel *(ui)*
- Notify on sync timeout; scope stale check to actionable accounts (review) *(notify)*
- Emit on generic sync failure; prevent stale/pause re-notify oscillation (final review) *(notify)*
- Sync_completed reports live total_messages, not initial-sync counter *(notify)*
- Snapshot channel fields on caller thread — no ORM object into send thread (review) *(notify)*
- Scope recovery delete to authorized account + validate snapshot_id *(security)*
- Fullmatch snapshot/prefix validation + truthful delete audit (review) *(security)*
- SSRF hardening + reserve _restore_ username prefix *(security)*
- Close IPv4-mapped IPv6 SSRF bypass + friendly error on reserved username (review) *(security)*
- Restore-poll IDOR, OAuth-delete CSRF, legacy-KDF self-heal *(security)*
- Form 500s, template path, date parse, restore-cancel, config round-trip *(robustness)*
- Wire legacy-KDF self-heal into sync read path + commit robustness tests (review) *(security)*
- Pin resolved IP against DNS rebinding + finish form-500 guards *(security)*
- Re-validate account host at sync time (rebinding defense-in-depth) *(security)*
- Add FTS language config required by 2.4.x *(dovecot)*
- Use fileMatch in customManager (managerFilePatterns unsupported) *(renovate)*
- Drop **/vendor/** from ignorePaths so vendor.json is scanned *(renovate)*
- Idempotent tag step so release job retries succeed *(release)*
- Address CodeRabbit review *(release)*

### Maintenance

- Log folder renames caused by separator collision
- Remove RESUME.md — session handoff tracked in memory
- Add pytest-xdist for parallel test execution
- Run tests in parallel with pytest-xdist (-n auto)
- Remove static Dovecot and Roundcube config files
- Remove Roundcube PHP 8.4 patch — not needed with correct FTS config
- Add boto3 for S3 probing and inventory
- Docs, copy, and review carry-overs for repo management *(backup)*
- Stop tracking agent working artifacts
- Restart: unless-stopped on all services *(compose)*
- Add apprise for notification channels
- Least-privilege job permissions + SHA-pinned actions *(docs)*
- Bump image 2.4.2 -> 2.4.4 (latest stable) + docs *(dovecot)*
- Release-pr workflow maintains CalVer release PR *(release)*
- Tag-last release flow driven by release PR merge *(release)*

### Other

- Merge pull request #2 from thekoma/renovate/python-3.x

chore(deps): update python docker tag to v3.14
- Merge pull request #4 from thekoma/renovate/astral-sh-setup-uv-8.x

chore(deps): update astral-sh/setup-uv action to v8
- Merge pull request #5 from thekoma/renovate/softprops-action-gh-release-3.x

chore(deps): update softprops/action-gh-release action to v3
- Merge pull request #6 from thekoma/renovate/postgres-18.x

chore(deps): update postgres docker tag to v18
- Add restore page CSS
- Update volume path for pgsql 18
- Merge pull request #56 from thekoma/renovate/gitleaks-gitleaks-action-digest

chore(deps): update gitleaks/gitleaks-action digest to ff98106
- Add Images!
- Merge pull request #144 from thekoma/analysis/lexicon-ux-2026-05-10

docs: UX & lexicon audit + phased rollout plan (no code changes)
- Merge pull request #146 from thekoma/feat/lexicon-wave-1a

feat(lexicon): wave 1a — LEXICON.md + advisory CI lexicon-check
- Merge pull request #145 from thekoma/chore/simplify-backup-section

refactor(backup): cleanup of account-detail offsite section
- Merge pull request #147 from thekoma/feat/lexicon-wave-1b

feat(ux): wave 1b — four honesty-adjacent fixes
- Merge pull request #148 from thekoma/feat/lexicon-wave-1.5

fix(dovecot)+docs: wave 1.5 — userdb filters suspended + disaster recovery
- Merge pull request #149 from thekoma/feat/lexicon-wave-2

feat(ux): wave 2 — English-only lexicon rename + audit display labels
- Merge pull request #150 from thekoma/feat/lexicon-wave-2.5

feat(backup): wave 2.5 — Alembic migration for AccountBackup progress columns
- Merge pull request #151 from thekoma/feat/lexicon-wave-3

feat(ux): wave 3 — IA reorder + two-pill Repository column + /recover route split
- Merge pull request #152 from thekoma/feat/lexicon-wave-4

feat(ux): wave 4 — chain hero, empty states, Repository status quartet
- Merge pull request #153 from thekoma/feat/docs-lexicon-polish

docs: add Repositories admin page + lexicon polish
- Merge pull request #154 from thekoma/feat/lexicon-wave-5-db-rename

refactor: wave 5 — Python class rename Repository / BackupPolicy
- Merge pull request #155 from thekoma/chore/drop-italian-from-lexicon

docs(lexicon): drop Italian column — English-only is the only language
- Merge pull request #156 from thekoma/feat/first-run-setup

feat(ux): first-run setup checklist on dashboard
- Merge pull request #157 from thekoma/feat/force-default-password-change

feat(security): force admin password change at first login
- Merge pull request #158 from thekoma/feat/chain-hero-tooltip

feat(ux): first-time explainer callout on the chain hero
- Merge pull request #159 from thekoma/feat/account-wizard

feat(ux): account wizard — 3-step flow for /accounts/new
- Merge pull request #160 from thekoma/feat/repository-wizard

feat(ux): repository wizard — 3-step Add Repository
- Merge pull request #161 from thekoma/fix/middleware-skip-oidc

fix(security): force-pwd middleware exempts OIDC users + handles None hash
- Merge pull request #162 from thekoma/fix/chain-explainer-readability

fix(ux): break chain-hero explainer wall of text into a bullet list
- Merge pull request #163 from thekoma/fix/chain-stages-clickable

fix(ux): chain hero stages are now real clickable links
- /restore visual pass — synthesizes 5 UI specialist proposals *(ui)*
- Merge branch 'feat/recovery-model' — Recovery model + unified Restore workspace

Recovery refactor: snapshots become first-class Recovery sub-objects of
the source Account (no more fake suspended accounts), with a kind split
(persistent vs ephemeral, TTL-driven) and an hourly cleanup sweep.

/restore page rebuilt as a unified workspace driven by user intent
(single mail / folder / full mailbox), with snapshots mounted on demand
as ephemeral Dovecot namespaces and search routed via Dovecot IMAP
SEARCH cross-namespace. Restore action reuses the existing IMAP COPY
engine with a per-source-namespace fix.

Closes PR #164.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
- Polish workspace proportions — compact button, denser sidebar, hover states *(restore)*
- Dramatic visual polish for Alpine workspace — slider, chips, cards, badges *(restore)*
- Merge pull request #171 from thekoma/feat/mail-index

feat: index-based mail search with deep body search
- Merge pull request #172 from thekoma/feat/ui-redesign

feat: UI redesign pass 1+2 — Geist, theming, self-hosted assets + SSO/OAuth fixes
- Merge pull request #173 from thekoma/feat/ui-redesign-2

feat(ui): account wizard polish — brand logos, layout, OAuth flow cleanup
- Merge pull request #174 from thekoma/feat/repo-s3

feat: S3 repository management — clean probing, inventory/attach, encrypted config snapshots + DR restore
- Merge pull request #175 from thekoma/feat/repo-ux

feat: repository UX — pre-create test, per-attachment passwords, snapshot tags
- Merge pull request #176 from thekoma/feat/repo-access

feat: per-user repository access (allowed repositories)
- Merge feat/repo-access: contents-panel restore actions, repos layout, OIDC resilience

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- Merge pull request #177 from thekoma/feat/restore-attachments-search

feat: attachment index, cross-account search, message preview, restore-to-origin (restore cycle 1/3)
- Merge pull request #178 from thekoma/feat/restore-staging

feat: staging area with webmail curation + push upstream (restore cycle 2/3)
- Merge pull request #179 from thekoma/feat/restore-attachments-view

feat: attachment search view, download, Tika content search (restore cycle 3/3)
- Merge pull request #180 from thekoma/feat/sync-budget

feat: throttle-aware first sync with bandwidth budget, progress/ETA, crash recovery
- Merge feat/sync-watchdog-reauth: sync watchdog + needs_reauth

In-flight progress-stall watchdog reaps hung running jobs without a
restart; interrupted incomplete initial syncs re-drive via expired-pause;
a needs_reauth SyncState stops futile hourly retries on invalid_grant and
reuses the existing reconnect UI; mbsync read is deadline-bounded with a
pool-health warning. Migration 022 adds the enum value.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge fix/sync-completion-not-persisted: persist completion before bookkeeping

A clean sync committed job.status=completed only after the best-effort
post-sync bookkeeping; an intermittent stall in collect_account_stats then
stranded the job in 'running' forever, churning the watchdog. Commit the
terminal state immediately, before the bookkeeping.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge fix/maildir-size-bigint: stats for mailboxes over 2.1 GB

accounts.maildir_size_bytes was INTEGER; a >2.1 GB mailbox overflowed the
collect_account_stats UPDATE (NumericValueOutOfRange), so no stats persisted —
and pre-fix, collect's rollback on that failure also discarded the pending
completed status (the original churn trigger). Widened to BigInteger
(migration 023).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge fix/pause-resume-tz: relative pause resume time (no UTC skew)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge feat/first-sync-progress-pico: revive #182 first-sync progress

Pico-native <progress> for the initial-sync panel + recap card
(Folders X/Y · Messages X/Y · Downloaded), dropping the custom dot grid.
Folder denominator from accounts.initial_sync_total_folders (STATUS pass),
migration 024. Ported from the closed PR #182, adapted to current main
(resume_rel TZ fix, watchdog, bigint) with the migration renumbered.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge fix/audit-sync-all: complete audit-log coverage for mutating actions

Audit sweep: bulk sync-all, browser form login, own password change, own
store change, and sync-stop were state-changing but unaudited. All now log
a distinct action. Read-only endpoints intentionally stay unaudited.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge chore/compose-restart-policy: auto-restart all services

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge fix/dashboard-reauth-notice: dashboard flags needs_reauth accounts

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge feat/notifications: per-user Apprise problem notifications

Per-user notification channels (ntfy/Telegram/Gotify/... via Apprise) that
alert account owners on needs_reauth / sync_error / sync_paused / stale, on
channels they configure. Channel-independent of the backed-up mailbox;
best-effort daemon-thread sends; Apprise URLs Fernet-encrypted + masked;
add/delete audited. Migration 025.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge feat/notif-url-examples: Apprise docs link + copyable URL examples in profile

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge feat/notif-ui-redesign: responsive channel cards + per-channel event editing

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge fix/sync-completed-message-count: live message count in sync_completed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge feat/richer-json-envelope: structured account object + per-event details in JSON

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
- Merge fix/notify-channel-thread-snapshot: thread-safe channel snapshot in notification send

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- Merge fix/deepsec-findings: IDOR recovery delete, snapshot_id injection, docs.yml hardening

Fixes the 4 findings from the deepsec scan of 2026-07-06.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- Merge fix/deepsec-tp-round2: SSRF hardening + reserved _restore_ prefix

Closes 5 deepsec revalidation TPs (SSRF on update/discover/rebinding, dovecot
prefix data-loss). Includes IPv4-mapped IPv6 fix from review.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- Merge fix/deepsec-tp-round3: remaining deepsec TPs (8 fixes)

Security: restore-poll IDOR, OAuth-delete CSRF, legacy-KDF self-heal (wired
into the sync read path). Robustness: login/account form 500s, template path,
date parsing, restore-cancel race, config round-trip fidelity.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- Merge fix/deepsec-tp-round4: DNS-rebinding IP pinning + finish form-500 guards

Closes the residual TPs from re-revalidation: pin resolved IP on untrusted
IMAP connects (test-connection, create/edit) and complete the ui_accounts
form-missing-field guards. Sync-time mbsync rebinding documented as residual.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
- Merge pull request #189 from thekoma/docs/refresh

docs: refresh documentation (flows, security, gaps, screenshots, README)
- Merge pull request #188 from thekoma/renovate/astral-sh-setup-uv-8.x

chore(deps): update astral-sh/setup-uv action to v8.3.1
- Merge pull request #187 from thekoma/renovate/docker-setup-buildx-action-digest

chore(deps): update docker/setup-buildx-action digest to bb05f3f
- Merge pull request #186 from thekoma/renovate/docker-login-action-digest

chore(deps): update docker/login-action digest to af1e73f
- Merge pull request #185 from thekoma/renovate/docker-build-push-action-digest

chore(deps): update docker/build-push-action digest to 53b7df9
- Merge pull request #191 from thekoma/fix/dovecot-244

Bump Dovecot 2.4.2 -> 2.4.4 + port ACL/FTS config to 2.4.x
- Merge pull request #190 from thekoma/renovate/astral-sh-setup-uv-8.x

chore(deps): update astral-sh/setup-uv action to v8.3.2
- Merge pull request #192 from thekoma/renovate/softprops-action-gh-release-digest

chore(deps): update softprops/action-gh-release digest to 3d0d988
- Merge pull request #194 from thekoma/renovate/actions-checkout-7.x

chore(deps): update actions/checkout action to v7
- Merge pull request #195 from thekoma/renovate/gitleaks-gitleaks-action-3.x

chore(deps): update gitleaks/gitleaks-action action to v3
- Merge pull request #196 from thekoma/chore/frontend-vendor-updates

chore(deps): update vendored frontend libs + Renovate vendor pipeline
- Merge pull request #197 from thekoma/feat/release-pipeline

feat: CalVer release pipeline (release PR + tag-last + version in-app)
- Merge pull request #198 from thekoma/renovate/peter-evans-create-pull-request-8.x

chore(deps): update peter-evans/create-pull-request action to v8

### Refactoring

- Extract connect_imap() from imap_check for reuse
- Move system status to global sticky bar visible on every page
- Update settings for centralized config generation
- Docker-compose uses generated config volumes
- Dedicated webmail OAuth settings, clean up .env
- Cleanup of account-detail offsite section *(backup)*
- Wave 5 — Python class rename Repository / BackupPolicy
- Tighten partial access and trim deep field comment *(api)*
- Centralize repo-management inline handlers in core.js *(ui)*
- Share tag builder and guard orphan password kwarg *(backup)*

### Testing

- Add 9 tests for security fixes (#11, #12, #13)
- Tighten defaults test to exercise the kwarg path *(recovery)*
- Traversal pin at the escaping depth *(restore)*
- Harden failure_kind plain-string proof; fix drift-hook path
- Assert masked URL in profile GET; defensive URL split (review) *(notify)*
- Assert activity events + format selector + JSON badge render in profile (review) *(notify)*
- Assert account envelope fields are JSON-native strings (review) *(notify)*
- Assert ACL rights bound to the correct block (review) *(dovecot)*
