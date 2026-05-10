# Competitor: MailStore family + mail-archiver

> Evidence collected 2026-05-10 from MailStore help (Home + Server v8/v13/current), MailStore corporate blog, and the s1t5/mail-archiver source tree. mail-archiver strings are shown as localization keys — the only stable identifier across languages.

## Products covered

**MailStore Home** — free Windows desktop email archiver (single-user, fat client). Three-column UI: navigation, archive tree, message list. No web UI, no remote storage concept — the archive is one or more directories on disk.

**MailStore Server** — commercial product, the de-facto SMB reference. Multi-user model, an `Administrative Tools` tree, explicit separation of "master database" + multiple "archive stores". Treats the archive as the live, authoritative system; backup of the archive is a separate downstream concern.

**mail-archiver** (s1t5/mail-archiver, ASP.NET Core 10, ~1.8k stars) — the only directly comparable open-source competitor to MFB. Web UI with dark mode, Microsoft Graph + IMAP, Jobs/Logs/Users in the sidebar, archived mail browsed via web rather than IMAP. No remote depot, no snapshot — the Postgres DB *is* the archive.

## Lexicon they use

### MailStore Home

- **Source IMAP** — "Email Account" / "Server Type" (IMAP, POP3, Exchange) in the setup wizard.
- **Configuration unit** — "Archiving profile". Quote: "Each separate source of messages will be configured in MailStore Home as an archiving profile."
- **Local copy** — "MailStore Home archive". Left-column root node is literally `My Archive`.
- **Storage location** — implicit, a single file-system path. No first-class concept.
- **Retention** — "Deletion Rules" (acts on the *source*, not the archive).
- **Snapshots / Restore** — neither exposed. Read-in-client or export.
- **Schedules** — "Run" (manual) or `Create Task on <COMPUTERNAME>` (delegates to the OS scheduler).

### MailStore Server

- **Source IMAP** — `Archive Email` page, four source categories: Public Cloud Services, Email Servers, Email Files, Email Clients.
- **Configuration unit** — "Archiving profile" (same as Home).
- **Local copy** — "Archive" / "the archive" — a single logical view across all stores.
- **Storage location** — `Administrative Tools > Storage > Storage Locations` → "Archive Stores" (Internal / External).
- **Store status** — `Archive here` (write-target, exactly one) · `Normal` · `Write-Protected` · `Disabled` · `Locked`.
- **Retention** — "Deletion Rules" on the profile (acts on source, not on archive).
- **Snapshots** — the word "Snapshot" appears only to describe filesystem-level VSS backups, never as an internal feature.
- **Restore** — means *restore the archive itself from backup*, not *push to a mailbox*.
- **Backup** — explicitly a separate, downstream operation on the archive-store files: "MailStore Server is designed as a live system and therefore should be protected by backups against data loss."
- **Move to another disk** — "Detach" / "Attach".

The hierarchy MailStore Server teaches is **Master database → Archive stores → Emails**.

### mail-archiver

From `Views/MailAccounts/Index.cshtml` and `Views/Home/Index.cshtml`:

- **Source IMAP** — `MailAccounts` / `EmailAccount` / `Provider` badges (`IMAP`, `Microsoft 365`, `Import Only`).
- **Configuration unit** — the account itself. One row per source, no separate "profile" abstraction.
- **Local copy** — "Archive" / `SearchArchive`. Sidebar entry `Archive`.
- **Storage location** — not exposed; single Postgres DB, hidden.
- **Retention** — `AutoDelete` (column on the accounts table, expressed in days).
- **Snapshots** — not exposed.
- **Restore** — `Restore` / `BatchRestore` / `AsyncBatchRestore` — push archived mail back to a destination IMAP mailbox.
- **Sync state** — `Status` (Enabled / Disabled), `LastSync`, `NotSynced`.
- **Background ops** — sidebar `Jobs` with active-count badge + `BackgroundJobsActive` dashboard banner.
- **Import / Export** — `Import`, `ImportMBox`, `ImportEml`, `Export`, `ExportStatus`.

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

MailStore is unusually disciplined about distinguishing the layers. Direct quotes from their docs and evangelism blog:

- "Email archiving primarily serves the purposes of documentation and preventing data loss."
- "A backup can only back up data over a limited period of time and restore it if necessary."
- "Backing up your email server does not replace a proper email archive in any way … a backup cannot replace the functions of archiving."
- "MailStore Server is designed as a live system and therefore should be protected by backups against data loss."

The mental model they teach:

```
Mailbox (source) ──profile──▶ Archive (live, queryable, compliant) ──VSS/files──▶ Backup copy
```

MailStore *intentionally* refuses to call its archive a "backup": in their value proposition the archive is the source of truth, and the live mailbox can drop messages (deletion rules, journaling) without compromising compliance. The backup of the archive is a separate downstream chore that they don't ship.

mail-archiver is looser. The Reddit announcement and XDA review use "archive", "backup", "self-hosted backup" interchangeably. There is no second-tier copy — the Postgres DB is it.

Neither product has any concept matching MFB's **remote depot** or **snapshots**. This is genuinely novel territory for MFB, and it's where the team's confusion is rooted: the four-stage chain is more elaborate than anything in the competitive set.

## Onboarding flow

**MailStore Home** — Step 1 is `Archive Email`. User picks a source category (`Email Account` / `Email Client` / `Email Files`), runs a wizard, names the profile, profile lands in `Saved Profiles`. Pressing `Run` creates the archive on first execution. The archive directory is chosen at install time and forgotten.

**MailStore Server** — Admin install asks for the master database path. After that, Step 1 is also `Archive Email`. Storage management is presented as an *advanced* topic — a fresh install ships with one auto-created archive store and the admin doesn't have to think about it until growth forces a split.

**mail-archiver** — Empty-state copy on the accounts page is `NoEmailAccountsConfigured` + `AddFirstAccount`. There is no concept of stores or storage to configure. The user goes directly from install to adding their first IMAP account.

## What MFB can steal

1. **"Archiving profile" as the unit of configuration.** MFB's current "Account" conflates the *email identity* with the *sync job*. Splitting Account (credentials, OAuth) from Profile (what we pull, retention, schedule) unblocks "one account, multiple sync configs" and stops overloading "Account".
2. **A status enum on the storage tier.** MailStore Server's `Archive here / Normal / Write-Protected / Disabled / Locked` instantly tells the admin which store is the write-target. Make MFB's implicit "default store for new accounts" explicit: one store is `Active`, others `Normal` / `Read-only` / `Detached`.
3. **mail-archiver's sidebar Jobs badge.** A red counter on a sidebar entry whenever background work is in flight is a much better always-on signal than per-page sync status, and aligns with the live-status feedback the user already values.
4. **`Detach` / `Attach` verbs for store lifecycle.** Better than `Migrate`: they describe the storage-tier operation without implying mail movement. Store migration becomes one specific detach-and-attach; the verbs also scale to maintenance and disk swaps.
5. **MailStore's didactic separation between "the live system" and "the backup of the live system".** Steal the *concept* even if not the words. Renaming MFB's "Local backup" to something that signals "this is the live system" (Archive? Mirror? Local copy?) and reserving "backup" for the depot+snapshot tier mirrors MailStore's clarity.

## What MFB should NOT copy

1. **MailStore's overloading of "Restore".** In Server it means *recover the archive from a filesystem backup*; in mail-archiver and most other tools it means *push messages back into a live mailbox*. MFB will need both (restic-restore the depot vs. write-back to IMAP) and must pick distinct words. Suggest `Recover` for depot-side and `Restore` for mailbox-side — but not the same word for both.
2. **mail-archiver's unsegmented dashboard.** Six stat cards, two charts, an accounts table, a recent-emails list, and a quick-actions grid — all undifferentiated, no grouping by chain stage. For a four-stage system, MFB should group dashboard widgets by Source / Local / Remote / Snapshot so each card teaches the model.
3. **MailStore Home's "Saved Profiles" + double-click-to-run metaphor.** Single-user fat-client pattern; doesn't translate to a multi-user self-hosted server. Follow mail-archiver's account-list-with-status-and-actions table instead.

## References

MailStore Home:
- [Archiving Email](https://help.mailstore.com/en/home/Archiving_Email)
- [Accessing the Archive](https://help.mailstore.com/en/home/Accessing_the_Archive)

MailStore Server:
- [Storage Locations](https://help.mailstore.com/en/server/Storage_Locations)
- [Backup and Restore](https://help.mailstore.com/en/server/Backup_and_Restore)
- [Email Archiving Basics](https://help.mailstore.com/en/server/Email_Archiving_with_MailStore_Basics)
- [Archiving Email — source categories](https://help.mailstore.com/en/server/Archiving_Email)
- Corporate blog, ["Is Email Archiving the same as Email Backup?"](https://www.mailstore.com/en/blog/2018/01/25/what-is-email-archiving/) (2018-01-25)

mail-archiver (s1t5):
- [`Views/Shared/_Layout.cshtml`](https://raw.githubusercontent.com/s1t5/mail-archiver/main/Views/Shared/_Layout.cshtml)
- [`Views/MailAccounts/Index.cshtml`](https://raw.githubusercontent.com/s1t5/mail-archiver/main/Views/MailAccounts/Index.cshtml)
- [`Views/Home/Index.cshtml`](https://raw.githubusercontent.com/s1t5/mail-archiver/main/Views/Home/Index.cshtml)
- [`Views/Emails/`](https://github.com/s1t5/mail-archiver/tree/main/Views/Emails) (Restore, BatchRestore, AsyncBatchRestore)
- [XDA review](https://www.xda-developers.com/open-source-tool-perfect-self-hosted-solution-managing-emails/) (2025-11-02)
- [Reddit r/selfhosted launch thread](https://www.reddit.com/r/selfhosted/comments/1lveeub/my_self_hosted_email_archive/) (2025-07-09)
