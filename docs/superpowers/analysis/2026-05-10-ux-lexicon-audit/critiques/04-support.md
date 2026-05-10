# Support engineer critique

## TL;DR
- The "backup" overload is not a UX nit, it is a ticket-volume multiplier — every Marco/Luca call about "my backup is broken" requires me to ask "which one" before I can triage.
- 80% of inbound will be the same five questions (where's my mail, did the snapshot run, why is the restore "suspended", what's a store, how do I share an account). All five are answerable with empty-state copy and one chain diagram.
- Sev-1 risk concentrates in restore: silent data divergence, restored-but-suspended accounts, and snapshot deletion on account delete. None of those are bugs, they are model gaps.

## Top 20 tickets

| # | Ticket title (user voice) | Real issue | Why UI causes it | Severity |
|---|---|---|---|---|
| 1 | "My backup failed but my mail is still arriving" | Offsite restic failed; local mbsync is fine | Both labeled "backup"; flash message "Backup failed" is ambiguous | Mental-model |
| 2 | "I added an account but I don't see any backup running" | User added account, no destination assigned, expects offsite by default | Empty state says "Add your first email backup" — implies done | UX |
| 3 | "I restored from a snapshot, where did my mail go?" | Restored to suspended `Backup X (date)` placeholder account | Success toast says "restored", doesn't say suspended | Bug (copy) |
| 4 | "Why is my Maildir taking 12GB if I have offsite?" | User thinks offsite replaces local; local is the primary | Chain never visualized; offsite framed as additive only verbally | Mental-model |
| 5 | "I deleted an account, are my snapshots gone?" | Unspecified — snapshots may or may not survive on the depot | UI never warns about snapshot lifecycle on account delete | UX/Bug |
| 6 | "Backup Destination test passed but no snapshots appear" | Destination created, but no AccountBackup config attached | "Backup configured" badge shows on test, not on actual scheduled run | Docs |
| 7 | "Last sync 5 min ago but no new mail" | Last *attempted* sync, not last successful sync with new messages | Label conflates attempted/successful | UX |
| 8 | "Roundcube shows old emails as unread after restore" | Restic restore preserved `cur/` but Dovecot reindexed seen-state | No copy explains restore-state semantics | Docs |
| 9 | "How do I share an account with my wife?" | Group concept not discoverable from account detail | Groups are admin-only; ownership UI shows "Owners" but no Add-Group affordance | UX |
| 10 | "I changed my Gmail app password and sync stopped" | Credential rotation; no re-auth prompt, just silent fail | Error surfaces only on account detail, not as inbox notification | UX |
| 11 | "I see two of the same account in my inbox" | Same email on two stores or shared via group + direct ownership | UI doesn't dedupe namespace listing nor warn on duplicate-add | UX |
| 12 | "S3 destination says 'unreachable' — is my data lost?" | Network/credential issue; existing snapshots safe | Generic "test failed", no distinction between "can't reach" vs "lost depot" | UX |
| 13 | "Disk filled up and now nothing works" | Local store full; mbsync fails, Dovecot read-fails | Storage card not actionable, no soft-quota, no alert | Bug |
| 14 | "Migration is stuck" | Migration completed but banner only shows on account detail | Banner not on dashboard or accounts list | UX |
| 15 | "I'm an admin but Stores menu confuses me" | "Store" is internal jargon for filesystem path | Sidebar surfaces it without onboarding | Docs |
| 16 | "Why does Restore ask for an IMAP server?" | /restore page mixes IMAP-target restore with snapshot restore | Two distinct flows under one word | Mental-model |
| 17 | "I configured offsite but never did first backup" | "Backup Now" button buried inside collapsed details | Discoverability — feature is two clicks deep | UX |
| 18 | "Snapshot retention deleted my old mail" | Retention applies to depot snapshots, not local Maildir, but user fears both | "Retention" preset names not explained at point of choice | Docs |
| 19 | "OAuth token expired and I can't tell" | Token refresh failed silently | No banner, only sync errors with cryptic provider messages | Bug |
| 20 | "I can't login but admin says I exist" | `migrating=true` blocks Dovecot login by design | No user-visible "your mailbox is being moved" page | UX |

## Sev-1 patterns
Tickets 3, 5, and 13 escalate. **#3 (restore-as-suspended)** because the user believes data is recovered while it sits in a placeholder; if they delete the original they end up half-broken with no second chance. **#5 (snapshots on account delete)** because the policy is undefined — either users lose the only remaining copy or admins keep paying S3 for orphaned snapshots forever; either way it is trust loss. **#13 (disk full)** because mbsync writes partial Maildirs and Dovecot starts returning errors mid-session — this is the only one that can actively corrupt state, not just confuse.

## Self-service wins
- "Which backup failed?" — split flash messages and badges into "Sync" vs "Snapshot" so the user reads it once. Lives in `sync_panel` partial and account-detail hero.
- "Run a backup now" — surface "Snapshot now" button on account row, not buried in `<details>`. Lives in the accounts table actions kebab.
- "Test my OAuth token" — one-click re-auth on account detail next to the auth field, not "edit account → save → fail".
- "Show snapshot count + last success per account" — column on accounts table, replaces the "backup configured" boolean badge.
- "Storage breakdown" — dashboard card splits Local vs Offsite with per-destination subtotals; click drills into per-account.
- "Migration progress on dashboard" — promote the banner from account detail to a sticky system-status row.
- "Duplicate-account warning at create time" — block or warn when same email exists on another store.
- "Re-attach restored snapshot to original account" — single button on the restored placeholder, replaces manual fix-up.

## Docs that must exist
- "Local copy vs offsite snapshot — what each one does and when each one saves you." Single page, one diagram, linked from every empty state.
- "After a restore: what is a 'suspended account' and how do I activate it?" Walk-through with screenshots, including the Dovecot re-point step.
- "Credential rotation: Gmail app passwords, OAuth tokens, IMAP passwords." Per-provider section.
- "Retention presets explained" — what light/standard/full actually delete and when.
- "Sharing an account with another user via Groups."
- "Disaster recovery runbook: disk died, MFB host died, Postgres lost."

## Internal tooling I need
- Per-user impersonation (read-only) so I can see what they see without asking for screenshots.
- A "support snapshot" button that bundles last 100 audit-log lines + sync errors + backup errors + env config (redacted) into a tarball the user can attach to the ticket.
- Server-side health endpoint that exposes per-account chain status (source-ok, local-ok, offsite-ok, last-snapshot-age) for paging.
- A queryable view over `AccountBackup.last_status` and `last_error` aggregated across all users, sortable by failure age — so I find the silent failures before users do.
- A "force resync" and "force snapshot now" admin overlay that doesn't require logging in as the user.

## The one ticket I never want to see
"I restored my account from a snapshot, deleted the placeholder because it looked weird, and now my mail is gone." This is where every concept failure compounds: restore creates an unfamiliar artifact ("Backup X (date)" suspended account), the user does not recognize it as the recovered data, and the destructive action (delete account) silently nukes the only recovered copy because the suspended state offered no protection. Make this impossible by requiring an explicit "promote to active" or "merge into original" step before delete is allowed on any `restored=true` account, and gate the delete with a hard confirmation that lists what will be lost from disk vs depot.

## Things current-state doc gets wrong
- It treats the offsite feature as "buried" but supportable; in practice, if a user does not see it on first run they configure only the local copy and call us six months later when the disk dies — that is a billable hour, not a polish item.
- It calls "Snapshot, Store, Maildir" the lexicon's bright spots; "Store" is the second-most-confusing word after "backup" in support tickets — admins parse it, end users read it as "the place where my email is stored" (which can mean the Maildir, the offsite depot, or both).
