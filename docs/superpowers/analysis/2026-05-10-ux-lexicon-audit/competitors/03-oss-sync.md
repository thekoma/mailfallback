# Competitor: OSS IMAP sync & archive ecosystem

> Scope of this file: study how the open-source IMAP-sync and email-archive
> tools that MFB users are likely to know name the same concepts MFB names.
> Goal: feed the lexicon decision with concrete prior art instead of guesses.

## Tools covered

| Tool | Category | Why it matters to MFB |
|---|---|---|
| **isync / mbsync** | Bidirectional IMAP↔Maildir sync (engine MFB wraps) | Every MFB user who reads logs sees its words. Ground truth. |
| **OfflineIMAP / offlineimap3** | Bidirectional IMAP↔Maildir sync (Python, declining) | Direct conceptual peer of mbsync; many migrated *from* it. |
| **imapsync** | One-shot IMAP→IMAP migration | Different category. Useful as the contrast that defines what MFB *isn't*. |
| **getmail / fetchmail** | POP3/IMAP retrievers (mail-server feeders) | Older mental model still common in homelab docs. |
| **MailPiler** | Full-text email **archive** (compliance) | Closest competitor when MFB users say "archive". |
| **Cyrus IMAP** archive/backup features | Server-side tiered storage + replication-backups | Largest IMAP server in the OSS world; sets the canonical sense of "archive partition". |

---

## Vocabulary table

| Concept | mbsync (isync) | OfflineIMAP | imapsync | getmail | fetchmail | MailPiler | Cyrus archive |
|---|---|---|---|---|---|---|---|
| The thing being defined as a job | `Channel` | `[Account name]` | (no config — flags) | `[retriever]`+`[destination]` pair | `poll` block | "archive job" / `pilerimport` run | n/a (server-side) |
| Remote IMAP source | `IMAPStore` referencing `IMAPAccount` (`Far` side) | `[Repository …]` of `type = IMAP` (`remoterepository`) | `--host1 --user1 --password1` | `[retriever] type = SimpleIMAPSSLRetriever` | `poll … proto IMAP user …` | Source inside `pilerimport --imap` | Remote replica via `sync_client` |
| Local destination | `MaildirStore` (`Near` side) | `[Repository …]` of `type = Maildir` (`localrepository`) | `--host2 --user2 --password2` (also IMAP) | `[destination] type = Maildir / Mboxrd / MDA_external` | `mda` line / local MDA | Internal MySQL+filesystem store | `partition-default` / `archivepartition-name` |
| Cleartext password / credential | `Pass`, `PassCmd` | `remotepass`, `remotepassfile`, OAuth2 keys | `--password1/2`, `--passfile1/2` | `password`, `password_command` | `password` | DB-stored creds | n/a |
| Folder selection | `Patterns *` (with `!` excludes) | `folderfilter` (Python lambda) | `--folder`, `--include`, `--exclude` | `mailboxes = ('INBOX', …)` | `folder` | `--folder` to `pilerimport` | n/a |
| Folder name rewrite | `Flatten`, `MapInbox`, hierarchy delimiter swap | `nametrans` (Python lambda) | `--regextrans2` | n/a | n/a | n/a | n/a |
| Direction of data | `Sync Pull \| Push \| New \| ReNew \| Delete \| Flags \| All \| None` | `sync_deletes`, `readonly`, `[mbnames]` | one-way by default; `--delete2` for symmetry | one-way fetch | one-way fetch | one-way ingest | bidirectional `sync_client` rolling/push/pull |
| "Make missing folders on the other side" | `Create None \| Near \| Far \| Both` | `createfolders = yes/no` (per-repo) | `--no-foldersizes` etc. (implicit) | `move_on_delete` for IMAP | n/a | n/a | n/a |
| "Permanently delete on the other side" | `Expunge None \| Near \| Far \| Both` | `sync_deletes`, `expunge` | `--expunge1`, `--expunge2`, `--delete`, `--delete2` | `delete = true` + `delete_after` | `keep` / `flush` / `fetchall` | retention rules | `expire_mode`, `delete_mode`, `cyr_expire` |
| Cap on retained messages | `MaxMessages`, `ExpireUnread` | `maxage`, `maxsize` | `--maxsize`, `--maxage` | `max_message_size`, `max_messages_per_session` | `limit`, `fetchsizelimit` | retention policy | `expire_mode` + `cyr_expire` |
| Snapshot / point-in-time | **not a concept** | **not a concept** | not applicable | not applicable | not applicable | "archive contains the email forever (until retention expires)" | `ctl_backups` chunks (deprecated 3.10+) |
| Tiered cold storage | n/a | n/a | n/a | n/a | n/a | n/a | `archive_after`, `archive_keepflagged`, `archivepartition-…` |
| Read-only re-access of synced mail | external (Dovecot, mu4e) | external | n/a | external | external | built-in webmail-like UI with full-text | native IMAP front-end |
| Test the connection without doing work | `mbsync --dryrun` | `--dry-run` | `--justconnect`, `--justlogin`, `--justfolders`, `--justfoldersizes` | `getmail --dump` (fields), `--rcfile` smoke | `fetchmail -c` (check), `-v` | `pilerimport --dry-run` | `synctest` |
| Group of jobs | `Group g : channelA, channelB` | `accounts = a, b, c` in `[general]` | shell `for` loop over CSV | one rc per account | multi-`poll` in one rc | per-domain config | n/a |
| Auth mechanism choice | `AuthMechs` | `auth_mechanisms` | `--authmech1/2` | implicit per retriever class | `auth` | n/a | `sasl_mech_list` |
| Connection encryption | `SSLType None\|STARTTLS\|IMAPS`, `TLSType` (1.4+) | `ssl`, `starttls`, `tls_level` | `--ssl1/2`, `--tls1/2` | `SimpleIMAPSSLRetriever` class | `ssl`, `sslfingerprint` | n/a | `tls_*` |

---

## MFB inheritance

MFB's "Sync Settings" panel today (`partials/sync_settings_fields.html`) leaks
the full mbsync vocabulary verbatim. Each row below is a label or option a real
user sees in the form right now.

| MFB UI label / option | Inherited from | Verdict |
|---|---|---|
| **Direction** *Pull / Push / Full / None* | mbsync `Sync` | Labels are helpful. Rename "None" → "Disabled". |
| **Create Mailboxes** *Near / Both / Far / None* | mbsync `Create` + `Far`/`Near` | Confusing. Rewrite *"When the source has a new folder…"* → *Mirror locally / Mirror on both / Don't create*. |
| **Expunge Deleted** *None / Near / Far / Both* | mbsync `Expunge` | Confusing twice (RFC 3501 verb + Far/Near). Rewrite *"When a message is marked for deletion…"* with plain *Keep / Delete locally / Delete on source / Delete on both*. |
| **Folder Patterns** placeholder `* ![Gmail]* "[Gmail]/Sent Mail"` | mbsync `Patterns` | A three-sigil mini-DSL. Replace with a Sync/Skip per-folder picker populated by `LIST`; keep the raw field behind a disclosure. |
| **Max Messages (0 = unlimited)** | mbsync `MaxMessages` | Wrong unit. Rename *"Keep at most N most-recent messages per folder"* and add a size cap. |
| **SubFolders Format = Verbatim** (disabled) | mbsync `SubFolders` | Not user-facing. Drop the field; document in architecture. |
| **Pipeline Depth** | mbsync `PipelineDepth` | Move to "Advanced", off by default. |
| **Disable IMAP Extensions** | mbsync `DisableExtension` | Rename *"Server quirks / workarounds"*; provide a curated dropdown of known toggles. |
| **Preserve message arrival dates** | mbsync `CopyArrivalDate` | Plain English already. Keep. |
| **Timeout (seconds)** | mbsync `Timeout` | Move under "Source connection" — belongs with the connection, not the sync policy. |

The biggest single inheritance problem is **Far/Near**. mbsync renamed
Master/Slave → Far/Near in 1.4 (Feb 2021) for ethical reasons; the new pair is
*more* abstract, not less. OfflineIMAP's `localrepository` / `remoterepository`
is the right answer for a GUI — it tells you *what* each side is.

---

## What the CLI ecosystem teaches

1. **OfflineIMAP's `localrepository` / `remoterepository` is the right pair
   for a GUI.** It names *what* each side is. MFB should adopt *Source* and
   *Local backup* and never write Far/Near in a UI string.

2. **imapsync's `--just*` family is the gold standard for "preview before
   commit".** `--justconnect / --justlogin / --justfolders / --justfoldersizes`
   give a four-rung confidence ladder. MFB has a single "Test connection"; it
   should split into *Login → List folders → Estimate size* so users fail fast
   at the right rung.

3. **Cyrus's separation of `archive_after` (tier) from `expire` (delete)
   is the cleanest model.** MFB conflates "expunge" with "retention". Adopt
   Cyrus's two axes: *tiering* (which store) and *retention* (when to delete)
   are different policies, on different pages. This makes the restic layer
   legible: snapshots are simply another tier.

4. **MailPiler's deliberate refusal of the word "backup".** Their docs say
   *archive* on every page. A Piler user always knows whether they're running
   ingest, retention, search, or restore. MFB's mixed lexicon today —
   "backup destinations" + "sync jobs" + "snapshots" + "stores" — is exactly
   what Piler avoided.

5. **getmail's 4-section grammar (`[retriever] / [destination] / [options] /
   [filter]`) maps cleanly to a wizard.** MFB's "Add account" form crams
   source + credentials + sync policy + storage + schedule into one screen. A
   4-step wizard (*Where it comes from / Where it lands / How often / What's
   allowed*) mirrors getmail's sections and is more discoverable.

---

## What MFB should hide

Terms MFB exposes today that should arguably stay in the `.mbsyncrc` and never
appear in the GUI, with the rationale per term:

| Term in current UI | Why hide it | Where it should live |
|---|---|---|
| `Far`, `Near` | Mbsync rename of Master/Slave; meaningless to users. | Generated `.mbsyncrc` only. |
| `Channel` | mbsync's word for the join. Users see an *Account*. | `.mbsyncrc` only. |
| `Patterns` with `!` and `%` | IMAP4 wildcard mini-DSL with three sigils. | Hide behind "Advanced — raw mbsync patterns"; default UI = folder checkboxes. |
| `Expunge` | RFC 3501 verb. Users say *delete*. | Replace with "Permanently delete". |
| `SubFolders Verbatim` | Required by LAYOUT=fs; not actionable. | Architecture docs only. |
| `PipelineDepth` | Performance knob most users never touch. | "Advanced" disclosure. |
| `DisableExtension COMPRESS=DEFLATE` | Implies the user knows IMAP capability strings. | Curated "Provider quirks" dropdown. |
| `MaxMessages` vs `MaxSize` | Two competing caps with no explained relationship. | Single retention page, two labeled sliders. |
| `CopyArrivalDate` (the keyword) | Internal field name that sometimes leaks into error toasts. | Strip from forwarded errors. |
| "Sync" as the noun for a job | Now overloaded with the restic backup work. | Reserve "Sync" for IMAP→Maildir; "Backup" for restic; "Snapshot" for restic point-in-time. |

A useful litmus test, applied to every label in the form: **could a Roundcube
user read it cold and act on it?** If the answer requires a `man mbsync` tab,
the label is leaking the engine.

---

## References

- mbsync(1) Debian manpage — https://manpages.debian.org/bullseye/isync/mbsync.1.en.html
- isync upstream notes (Master/Slave deprecation, 1.4.0 changelog, Feb 2021) — https://sourceforge.net/p/isync/isync/ci/v1.4.0/tree/NEWS
- OfflineIMAP reference config — https://github.com/OfflineIMAP/offlineimap/blob/master/offlineimap.conf
- OfflineIMAP folder filtering / nametrans — https://www.offlineimap.org/doc/nametrans.html
- mbsync vs OfflineIMAP head-to-head, anarcat 2021 — https://anarc.at/blog/2021-11-21-mbsync-vs-offlineimap/
- imapsync FAQ (general & folders) — https://imapsync.lamiral.info/FAQ.d/FAQ.General.txt — https://imapsync.lamiral.info/FAQ.d/FAQ.Folders_Selection.txt
- imapsync Arch manpage — https://man.archlinux.org/man/extra/imapsync/imapsync.1.en
- getmail6 configuration reference — https://getmail6.org/configuration.html
- getmail6 example rc files — https://github.com/getmail6/getmail6/blob/master/docs/getmailrc-examples
- Fetchmail manual — https://www.fetchmail.info/fetchmail-man.html
- MailPiler features page — https://www.mailpiler.org/features/
- MailPiler import/export docs — https://docs.mailpiler.com/piler-ee/import-export/
- Cyrus IMAP imapd.conf reference (3.2) — https://www.cyrusimap.org/3.2/imap/reference/manpages/configs/imapd.conf.html
- Cyrus IMAP backups admin guide (deprecated 3.10+) — https://www.cyrusimap.org/3.4/imap/reference/admin/backups.html
- MFB current sync-settings template — `src/mailfallback/templates/partials/sync_settings_fields.html`
- MFB mbsyncrc generator — `src/mailfallback/services/mbsync_config.py`
