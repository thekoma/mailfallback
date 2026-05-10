# Competitor: NAS + homelab self-hosted

## Products covered

- **Synology MailPlus Server** — full mail server package for DSM, backup delegated to Hyper Backup and storage protected by Btrfs Snapshots / Snapshot Replication.
- **Synology Mail Server** (older, non-Plus) — occasionally used by homelabbers as an "archive-only" target.
- **QNAP QmailAgent** — email *backup* app (not a mail server) that aggregates IMAP accounts onto a QNAP NAS.
- **Mailcow (dockerized)** — popular self-hosted mail server, archive-adjacent only via shell scripts and ZFS snapshots.
- **NethServer / NS8** — Italian-rooted Linux server distro that bundles **Piler** as the dedicated archive app.
- **Mailpiler (Piler)** — open-source, compliance-grade email archive wrapped by several homelab platforms.
- **Tutanota / Tuta** export — manual `.eml` zip export from the web client.
- **Proton Mail Bridge** + **Proton Mail Export Tool** — CLI that decrypts and writes mail to disk.
- **Thunderbird + ImportExportTools NG** — the de-facto "individual" archive workflow.
- **imapsync, mbsync, OfflineIMAP** — the CLI primitives MFB wraps; included for vocabulary baseline.

## How NAS apps frame "archive" vs "backup"

The Synology / QNAP world has an *unusually* mature vocabulary because NAS vendors had to teach their users this lexicon over a decade of marketing. The terms are not interchangeable on the box; each maps to a different product.

| Synology term | What it means | Where it lives |
| --- | --- | --- |
| **Snapshot** | Point-in-time, copy-on-write Btrfs reference of a share | Storage Manager |
| **Snapshot Replication** | Sends snapshots to a second Synology — fast, *block-level*, intended for hot/standby restore | Snapshot Replication app |
| **Hyper Backup** | Versioned, deduplicated, *encapsulated* backup to NAS / USB / Cloud — recovered with the Hyper Backup Vault tool | Hyper Backup app |
| **Active Backup for Business** | Agent-based backup of clients (PCs, VMs, file servers) into a Synology | ABB app |
| **MailPlus archive** | A *user-level* mailbox folder — "archived emails appear in this mailbox" — purely an Inbox→Archive move, like Gmail's archive | Inside MailPlus webmail |

So Synology has *four* distinct concepts an English speaker might call "backup" or "archive": Snapshot ≠ Backup, Backup ≠ Replication, and an "archived" message in MailPlus is just a webmail Inbox→folder move.

QNAP is more direct: **QmailAgent** is marketed as *"The Best Email Backup Solution — Hassle-free Email Backup without Building a Mail Server"*, then internally splits the function with *"Backup mode"* (view backed-up data) and *"Archive backed-up emails to cold storage to release your NAS storage"* — so for QNAP, **archive = tier the backup down to cheaper storage**, not "save mail forever".

This does *not* map cleanly to MFB. MFB is closest to QmailAgent's "backup mode" (a passive shadow of someone else's IMAP server) but has no point-in-time semantics, no encapsulated archive, no compliance hold, no e-discovery. Calling MFB "backup" puts it next to Hyper Backup — a comparison it will lose. Calling it "archive" puts it next to Mailpiler — also a comparison it will lose, because Piler does journaling, retention, legal hold, and tamper-proof signing. The honest framing is **"local IMAP mirror"** or **"fallback mail copy"** — a continuously refreshed read-only twin you switch to when the upstream provider is gone.

## Mailcow / NethServer mail server posture

These are MFB's **complements**, not competitors.

**Mailcow** is a primary mail server (Postfix + Dovecot + Rspamd + SOGo). Its docs use the word *"backup"* exclusively — there is no separate "archive" product. The provided helper script (`backup_and_restore.sh`) writes an rsync of `vmail/`, dumps MariaDB, Redis, and crypt material, and recommends layering ZFS / Btrfs snapshots *on the destination*. The language is operational: *"Backup all, delete backups older than n days"* — backups are rotation tools, not preservation tools. Built-in archive UX is effectively absent; users are pushed to **Mailpiler-as-a-second-server**, run alongside Mailcow with SMTP journaling. Several recent community videos and Cloudron / NethServer integration guides explicitly pair the two: *"Self-Hosted Email Archiving with Mailpiler & Mailcow"*.

**NethServer (NS8)** is a turnkey Linux server with a separate **Piler** application listed in its app catalog. The NS8 Piler doc is a perfect example of the lexicon: *"Piler is an open-source mail archiving solution. This Piler application for NS8 configures a Mail server instance to act as its archive."* Note the role separation: there is a Mail server *and* an archive, the archive has its own *"auditor users"* role, and the admin **cannot read** the archived messages — only auditors can. This is the *compliance* framing: archive = legal/discovery-grade record, with retention duration in days configured in the UI.

For MFB, the takeaway is that "archive" — as understood by a Mailcow / NethServer admin — implies SMTP journaling, an auditor role, retention policy, immutability. MFB has none of these. A homelab user evaluating MFB next to NS8+Piler will walk away disappointed if the marketing promises "archive".

## Thunderbird + Proton/Tutanota Bridge + manual archive

This is where the *consumer* vocabulary lives, and it is much messier than the vendor vocabulary.

- **Thunderbird ImportExportTools NG** describes itself as *"various tools for importing and exporting messages."* The verbs in the UI are **Export** and **Import**. The author uses **"backup"** only for the *profile-level* feature: *"Auto profile backup with schedule on shutdown."* For mail content, it's always export, with formats `.mbox`, `.eml`, `.html`, `.pdf`, `.csv`. Community articles ("Thunderbird: archiving emails with an email client") then re-label the same operation as **"archive"**: *"Save an email archive in MBOX"*, *"exports the entire email archive in MBOX."* So "archive" in the consumer Thunderbird world is just *a folder of exported mbox/eml files, kept somewhere safe*.
- **Proton Mail Export Tool**: described as *"It helps you export decrypted emails from your Proton Mail account to your device."* The CLI prompt then asks you to type **B** for **Backup** or **R** for **Restore**. So Proton uses three words for the same operation: marketing says **export**, the CLI says **backup**, and users in the wild call it **download my mail**.
- **Tutanota / Tuta**: no first-class export. The pattern users describe on r/tutanota is *"select all, three-dot menu, Export → zip of .eml files."* The verbs they use: **export**, **archival export**, *"backup before final account closure"*, *"keep a copy"*.
- **imapsync** (the closest CLI ancestor of MFB): self-described as *"an IMAP transfer tool. The purpose of imapsync is to migrate IMAP accounts or to backup IMAP accounts."* The man page extends this to *"syncing, copying, migrating and archiving email mailboxes between two IMAP servers."* All four verbs in one sentence — but notably, imapsync explicitly notes: *"Imapsync can't backup nor restore email messages to or from a local …"* — i.e., the moment the destination is *local*, the word "backup" is contested.

The dominant consumer phrases for "I want to keep my mail forever" are, in rough order of frequency observed across r/selfhosted, HN, and forum threads:

1. *"keep a copy of my email"* / *"keep my emails forever"*
2. *"local backup of IMAP"*
3. *"export my mailbox"*
4. *"archive my old emails"*
5. *"mirror my mail to my NAS"*
6. *"in case my provider goes kaput"* — the disaster framing

## Empty-state language patterns

Quoted first-run / product-page lines from the surveyed products:

1. **QmailAgent** — *"The Best Email Backup Solution — Hassle-free Email Backup without Building a Mail Server."* (product hero)
2. **QmailAgent** — *"Backup and protect email data – with 100% data integrity."* (sub-header)
3. **QmailAgent** — *"Centrally backup and manage multiple email accounts."* (feature card)
4. **Synology MailPlus** — *"Privately-owned mail service on a Synology NAS, providing multiple domain centralization management."* (active-server framing)
5. **Synology MailPlus help** — *"Allows archiving a mailbox to make all the archived emails appear in this mailbox."* (in-app archive = move-to-folder)
6. **Mailpiler** — *"A comprehensive email archiving solution with powerful features for compliance, security, and efficiency."* (compliance framing)
7. **Mailpiler** — *"Native SMTP server for seamless email archiving without additional infrastructure."* (journaling)
8. **NethServer / NS8 Piler** — *"Piler is an open-source mail archiving solution. This Piler application for NS8 configures a Mail server instance to act as its archive."* (role split)
9. **Proton Mail Export Tool** — *"It helps you export decrypted emails from your Proton Mail account to your device."* (export framing)
10. **Thunderbird ImportExportTools NG** — *"Adds various tools for importing and exporting messages."* (literal description)
11. **imapsync** — *"The Mailbox Changer."* (tagline) and *"Email IMAP tool for syncing, copying, migrating and archiving email mailboxes between two imap servers, one way, and without duplicates."* (man page)

Pattern: vendors of *primary* products (Synology MailPlus, Mailcow) avoid the word "archive" for their server's own mail. Vendors of *dedicated* archive products (Piler, NethServer's Piler app) use "archive" with a compliance / legal flavor. Vendors of *bridge / transfer* tools (imapsync, Proton Export, ImportExportTools NG) avoid both and use **export / sync / transfer / migrate**.

## Implications for MFB

1. **Do not call MFB an "archive."** That word, in the homelab + NAS world, is owned by Piler-class products and implies journaling, retention policy, auditor roles, legal hold, and tamper-proof storage. MFB has none of these. A homelab user comparing MFB to Piler or NethServer-Piler on the word "archive" will feel misled.
2. **Do not call MFB a "backup" without a qualifier.** In the Synology vocabulary, "Backup" lives next to Hyper Backup — versioned, encapsulated, deduplicated, recoverable through a vault tool. MFB writes a *live mirror*, not versioned snapshots. The QNAP QmailAgent lexicon is the closest match ("email backup app" = continuous IMAP copy onto local storage), and even QNAP found it useful to invent a separate "Backup mode" sub-term.
3. **Lean into "fallback" + "mirror" + "local copy."** These are honest to what MFB does: a continuously refreshed Maildir mirror plus a Dovecot front-door so you can read your mail when Gmail / iCloud / your provider is down. The product name *Mail**Fall**Back* already encodes this; the UI copy should reinforce it. "Fallback IMAP" is also a phrase Roundcube / Thunderbird users already understand from multi-account setups.
4. **Position in disaster-recovery vocabulary, not compliance vocabulary.** The dominant phrasing in homelab forums is *"in case my provider goes kaput"* and *"keep a copy on my NAS."* That is DR (disaster recovery), not compliance / legal. Use words like **resilience**, **continuity**, **read-only fallback**, **always-available copy**.
5. **Expect users to *call it* a backup anyway.** Even Proton's own export tool prints "type B for Backup". Documentation should accept that incoming vocabulary while the product UI itself uses precise terms. A short glossary in the docs (Backup vs Archive vs Mirror vs Fallback) — explicitly contrasting MFB with Hyper Backup, Mailpiler, and Thunderbird export — would do more to set expectations than any in-app tooltip.

## References

- Synology MailPlus Server backup help: https://kb.synology.com/en-global/DSM/help/MailPlus-Server/mailplus_server_backup?version=7
- Synology MailPlus product spec: https://www.synology.com/en-br/dsm/7.3/software_spec/mailplus
- Synology Hyper Backup vs Snapshot Replication (Marius Hosting): https://mariushosting.com/synology-hyper-backup-vs-snapshot-replication/
- Synology forum — "Mail Server as an archive": https://community.synology.com/enu/forum/1/post/194586
- QNAP QmailAgent product page: https://www.qnap.com/en/software/qmailagent
- QNAP blog — QmailAgent Backup Mode: https://blog.qnap.com/en/qmailagent-use-nas-to-manage-mails-in-multiple-mailboxes-all-at-once/
- QNAP FAQ — IMAP backup setup: https://www.qnap.com/en/how-to/faq/article/how-to-configure-qmailagent-to-backup-e-mail-from-an-imap-server
- Mailcow backup docs: https://docs.mailcow.email/backup_restore/b_n_r-backup/
- Mailcow Borgmatic / cold-standby: https://docs.mailcow.email/third_party/borgmatic/third_party-borgmatic/, https://docs.mailcow.email/backup_restore/b_n_r-coldstandby/
- NethServer NS8 Piler docs: https://docs.nethserver.org/projects/ns8/en/latest/piler.html
- NethServer community — email archiving feature request: https://community.nethserver.org/t/email-archiving-solution/3325
- NethServer community — archive in WebTop: https://community.nethserver.org/t/webtop-mail-is-there-a-way-to-archive-mail/19864
- Mailpiler features: https://www.mailpiler.org/features/
- Mailpiler home: https://www.mailpiler.org/
- Proton Mail Export Tool: https://proton.me/support/proton-mail-export-tool
- Proton Bridge MBOX/EML import-export: https://proton.me/support/import-export-mbox-eml-using-proton-mail-bridge
- Thunderbird ImportExportTools NG (IT listing): https://services.addons.thunderbird.net/IT/thunderbird/addon/importexporttools-ng/
- ProjectTRACKS — Thunderbird archiving guide: https://www.projecttracks.be/mozilla-thunderbird-archiving-emails-with-an-email-client
- imapsync site: https://imapsync.lamiral.info/
- imapsync GitHub README: https://github.com/imapsync/imapsync
- mbsync vs OfflineIMAP (anarcat): https://anarc.at/blog/2021-11-21-mbsync-vs-offlineimap/
- IMAP backup with offlineimap (dermitch): https://www.dermitch.de/post/imap-backup-with-offlineimap/
