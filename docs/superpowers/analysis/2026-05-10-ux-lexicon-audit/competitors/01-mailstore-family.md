# Competitor: MailStore family + mail-archiver

> Evidence collected 2026-05-10 from MailStore help (Home + Server v8/v13/current), MailStore corporate blog, and the s1t5/mail-archiver source tree. mail-archiver strings are shown as localization keys — the only stable identifier across languages.

## Products covered

**MailStore Home** — free Windows desktop email archiver (single-user, fat client). Three-column UI: navigation, archive tree, message list. No web UI, no remote storage concept — the archive is one or more directories on disk.

**MailStore Server** — commercial product, the de-facto SMB reference. Multi-user model, an `Administrative Tools` tree, explicit separation of "master database" + multiple "archive stores". Treats the archive as the live, authoritative system; backup of the archive is a separate downstream concern.

**mail-archiver** (s1t5/mail-archiver, ASP.NET Core 10, ~1.8k stars) — the only directly comparable open-source competitor to MFB. Web UI with dark mode, Microsoft Graph + IMAP, Jobs/Logs/Users in the sidebar, archived mail browsed via web rather than IMAP. No remote depot, no snapshot — the Postgres DB *is* the archive.

## Lexicon they use

### MailStore Home

| MFB stage | MailStore Home term | UI evidence |
|---|---|---|
| Source IMAP | "Email Account" / "Server Type" (IMAP, POP3, Exchange) | Setup wizard panels |
| The configuration unit | "Archiving profile" | "Each separate source of messages will be configured in MailStore Home as an archiving profile" |
| The local copy | "MailStore Home archive" / "My Archive" | Left-column root node is literally `My Archive` |
| Storage location | (implicit, single file-system path) | No first-class concept |
| Retention | "Deletion Rules" (acts on the *source*, not the archive) | Wizard step |
| Snapshots / versions | — (none) | Not exposed |
| Restore | — (no restore-to-mailbox action; user reads in the client or exports) | Not exposed |
| Schedules | "Run" (manual) or scheduled task on the OS | Right-click profile → "Create Task on <COMPUTERNAME>" |

### MailStore Server

| MFB stage | MailStore Server term | UI evidence |
|---|---|---|
| Source IMAP | "Email Server" archiving (sub-categories: Public Cloud Services, Email Servers, Email Files, Email Clients) | `Archive Email` page |
| Configuration unit | "Archiving profile" | Same as Home |
| Local copy | "Archive" / "the archive" — singular, logical | "Storage Locations Management to adjust the paths to the archive stores" |
| Storage location | "Storage Locations" → "Archive Stores" (Internal / External) | `Administrative Tools > Storage > Storage Locations` |
| Status of a store | `Archive here`, `Normal`, `Write-Protected`, `Disabled`, `Locked` | Storage Locations Management UI |
| Retention | "Deletion Rules" on profile (not on archive) | Profile wizard |
| Snapshots / versions | "Snapshot" used **only** to describe filesystem-level backups via VSS — never an internal feature | Backup and Restore page |
| Restore | "Restore" — meaning *restore the archive itself from backup*, not *restore to a mailbox* | Backup and Restore page |
| Backup | "Backup" — explicitly **a separate, downstream operation on the archive store files** | "MailStore Server is designed as a live system and therefore should be protected by backups against data loss." |
| Move to another disk | "Detach" / "Attach" | Storage Locations |

The hierarchy MailStore Server teaches is **Master database → Archive stores → Emails**, with archive stores carrying their own status (`Archive here` is the write-target, exactly one).

### mail-archiver

| MFB stage | mail-archiver term | Evidence |
|---|---|---|
| Source IMAP | `MailAccounts` / `EmailAccount` / `Provider` (IMAP, Microsoft 365, Import Only) | `Views/MailAccounts/Index.cshtml` |
| Configuration unit | the account itself | one row per source, no separate "profile" abstraction |
| Local copy | "Archive" / "Search Archive" | Sidebar entry `Archive`, dashboard button `SearchArchive` |
| Storage location | — (single PostgreSQL DB, hidden) | Not exposed |
| Retention | `AutoDelete` (column on the accounts table, days) | `MailAccounts/Index` table header |
| Snapshots / versions | — | Not exposed |
| Restore | "Restore" / "BatchRestore" / "AsyncBatchRestore" — restore archived mail back to a destination IMAP mailbox | `Views/Emails/Restore.cshtml`, `BatchRestore.cshtml` |
| Sync state | `Status` = Enabled / Disabled, `LastSync`, `NotSynced` | `MailAccounts/Index` |
| Background ops | "Jobs" + `BackgroundJobsActive` banner | Sidebar `Jobs`, dashboard alert |
| Import | `Import`, `ImportMBox`, `ImportEml` | Buttons on accounts page |
| Export | `Export`, `ExportStatus`, `EmlImportStatus` | Per-account action |

Notable: `Restore` in mail-archiver means **the inverse of archiving** (push back to a live mailbox). MailStore Server uses `Restore` to mean **disaster recovery of the archive store itself**. Same word, opposite direction. MFB needs to pick one.

## IA / main screens

### MailStore Server (admin client)

```
Administrative Tools
├── Storage
│   ├── Storage Locations    (master DB path, base directory, store table)
│   └── Archive Profiles
├── Users and Privileges
├── Compliance
├── Jobs                      (scheduled archiving + scripts)
└── Hardware and Software
Main panel: Archive (folder tree of users → mailboxes → folders)
```

The IA is **storage-first** for admins and **folder-tree-first** for end-users. The two views never collide because end-users never see Storage Locations.

### mail-archiver (web UI)

Sidebar (from `_Layout.cshtml`): Dashboard · Archive · MailAccounts (admin/self-manager) · Users (admin) · Jobs (admin, with active-count red badge) · Log.

Dashboard sections in order: title + Search Archive button → active-jobs alert → four stat cards (`Emails`, `Accounts`, `AttachmentsCount`, `Storage`) → two charts (`EmailsPerMonth`, `TopSenders`) → account overview table → recent emails list → quick-actions grid. This is the closest analogue to MFB's current dashboard.

## How they describe "the chain" to the user

MailStore is unusually disciplined about distinguishing the layers. From their evangelism blog (2018-01-25) and current docs:

- "Email archiving primarily serves the purposes of documentation and preventing data loss."
- "A backup can only back up data over a limited period of time and restore it if necessary."
- "Backing up your email server does not replace a proper email archive in any way … a backup cannot replace the functions of archiving."
- "MailStore Server is designed as a live system and therefore should be protected by backups against data loss."

Read together, MailStore's mental model for the user is:

```
Mailbox (source)  ──archiving profile──▶  Archive (the live system, queryable, compliant)
                                                    │
                                                    └──backup (your problem, snapshot/VSS/files)──▶  Backup copy
```

MailStore *intentionally* refuses to call its archive a "backup", because in their value proposition the archive is the source of truth and the live mailbox can drop messages (deletion rules, journaling) without compromising compliance. The backup of the archive is a separate downstream chore.

mail-archiver is much looser. The Reddit announcement and XDA review use "archive", "backup", "self-hosted backup" interchangeably. The product itself does not draw a line between "archive" and "backup of archive". There is no second-tier copy — the Postgres DB is it.

Neither product has any concept matching MFB's **remote depot** or **snapshots**. This is genuinely novel territory for MFB, and it's also where the team's confusion is rooted: the four-stage chain is more elaborate than anything in the competitive set.

## Onboarding flow

**MailStore Home** — Step 1 is `Archive Email`. User picks a source category (`Email Account` / `Email Client` / `Email Files`), runs a wizard, names the profile, profile lands in `Saved Profiles`. Pressing `Run` creates the archive on first execution. The archive directory is chosen at install time and forgotten.

**MailStore Server** — Admin install asks for the master database path. After that, Step 1 is also `Archive Email`. Storage management is presented as an *advanced* topic — a fresh install ships with one auto-created archive store and the admin doesn't have to think about it until growth forces a split.

**mail-archiver** — Empty-state copy on the accounts page is `NoEmailAccountsConfigured` + `AddFirstAccount`. There is no concept of stores or storage to configure. The user goes directly from install to adding their first IMAP account.

## What MFB can steal

1. **"Archiving profile" as the unit of configuration.** MFB currently calls it "Account", which conflates the *user's email identity* with the *job that pulls from it*. Borrowing MailStore's separation — Account (source identity, credentials, OAuth) vs. Profile (what we pull, retention, schedule) — would unblock the "one account, multiple sync configurations" use case and stop overloading the word "Account".
2. **A status enum on the storage tier.** MailStore Server's `Archive here / Normal / Write-Protected / Disabled / Locked` is a small, ordered vocabulary that tells the admin instantly which store is the write-target. MFB's stores currently have no analogous status, and the "default store for new accounts" concept is implicit. Make it explicit: one store is `Active` (write-target), others are `Normal` or `Read-only` or `Detached`.
3. **mail-archiver's "Jobs" badge in the sidebar.** A red counter on a sidebar entry whenever background work is in flight is a much better always-on signal than MFB's per-page sync status. Cheap to implement and aligns with the "live status" feedback the user already values.
4. **`Detach` / `Attach` verbs for store lifecycle.** Better than `Migrate` because they describe the operation at the storage tier without implying mail movement. MFB's store migration is one specific kind of detach-and-attach; the verbs scale to other operations (offline maintenance, swap to bigger disk).
5. **MailStore's didactic separation between "the live system" and "the backup of the live system".** Steal the *concept* even if you don't steal the words. MFB's chain is `Source → Local backup → Remote depot → Snapshots`; renaming "Local backup" to something that signals "this is the live system" (Archive? Mirror? Local copy?) and reserving the word "backup" for the depot+snapshot tier would mirror MailStore's clarity.

## What MFB should NOT copy

1. **MailStore's overloading of "Restore".** In Server it means *recover the archive itself from a filesystem backup*. In mail-archiver and almost every other tool, it means *push messages back into a live mailbox*. MFB will need both meanings (restic-restore the depot vs. write-back to an IMAP server) and must pick distinct words. Suggest `Recover` for depot-side (filesystem) and `Restore` for mailbox-side (write to IMAP) — or vice versa, but not the same word for both.
2. **mail-archiver's unsegmented dashboard.** Six stat cards, two charts, an accounts table, a recent-emails list, and a quick-actions grid — all on one page, all undifferentiated. No grouping by chain stage. For a system with four chain stages, MFB should resist the urge to do the same; group dashboard widgets by Source / Local / Remote / Snapshot so each card teaches the model.
3. **MailStore Home's "Saved Profiles" + double-click-to-run desktop metaphor.** Works for a single-user fat client; doesn't translate to a self-hosted server with multiple users. MFB is closer to mail-archiver in deployment shape, so it should follow mail-archiver's account-list-with-status-and-actions table pattern, not the saved-profile metaphor.

## References

- MailStore Home — Archiving Email: https://help.mailstore.com/en/home/Archiving_Email
- MailStore Home — Accessing the Archive: https://help.mailstore.com/en/home/Accessing_the_Archive
- MailStore Server — Storage Locations: https://help.mailstore.com/en/server/Storage_Locations
- MailStore Server — Backup and Restore: https://help.mailstore.com/en/server/Backup_and_Restore
- MailStore Server — Email Archiving Basics: https://help.mailstore.com/en/server/Email_Archiving_with_MailStore_Basics
- MailStore Server — Archiving Email (source categories): https://help.mailstore.com/en/server/Archiving_Email
- MailStore corporate blog — "Is Email Archiving the same as Email Backup?" (2018-01-25): https://www.mailstore.com/en/blog/2018/01/25/what-is-email-archiving/
- mail-archiver — `Views/Shared/_Layout.cshtml`: https://raw.githubusercontent.com/s1t5/mail-archiver/main/Views/Shared/_Layout.cshtml
- mail-archiver — `Views/MailAccounts/Index.cshtml`: https://raw.githubusercontent.com/s1t5/mail-archiver/main/Views/MailAccounts/Index.cshtml
- mail-archiver — `Views/Home/Index.cshtml`: https://raw.githubusercontent.com/s1t5/mail-archiver/main/Views/Home/Index.cshtml
- mail-archiver — `Views/Emails/` directory listing (Restore, BatchRestore, AsyncBatchRestore): https://github.com/s1t5/mail-archiver/tree/main/Views/Emails
- XDA review of mail-archiver (2025-11-02): https://www.xda-developers.com/open-source-tool-perfect-self-hosted-solution-managing-emails/
- Reddit r/selfhosted launch thread (2025-07-09): https://www.reddit.com/r/selfhosted/comments/1lveeub/my_self_hosted_email_archive/
