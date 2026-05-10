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

| MFB UI label / option | Inherited from | Helpful or confusing? | Verdict |
|---|---|---|---|
| **Direction** with values *Pull only (remote → local) / Push only (local → remote) / Full (bidirectional) / None* | mbsync `Sync` keyword | **Helpful** for the *labels*, **confusing** for the *option name "None"*. Direction with arrows is good UX; "None" sounds like "skip the field". | Keep label. Rename "None" → "Disabled". |
| **Create Mailboxes** with values *Near (local only) / Both / Far (remote only) / None* | mbsync `Create` + `Far`/`Near` | **Confusing.** Far/Near is the *least* discoverable rename in mbsync 1.4. Even mbsync power users had to relearn it. We hand the user that vocabulary with no glossary. | Rewrite as *"When the source has a new folder…"* with options *Mirror it locally only / Mirror it on both sides / Don't create*. Hide "Far". |
| **Expunge Deleted** with same Near/Far/Both/None | mbsync `Expunge` | **Confusing twice over.** "Expunge" is an IMAP4 verb (RFC 3501); end users say "permanently delete". And then Near/Far comes back. | Rewrite as *"When a message is marked for deletion…"* with *Keep it / Permanently remove locally / Permanently remove on source / Permanently remove on both*. |
| **Folder Patterns** with placeholder `* ![Gmail]* "[Gmail]/Sent Mail"` | mbsync `Patterns` (IMAP wildcards `*` `%` and `!` exclude) | **Confusing.** This is a mini-DSL with three sigils. Even the placeholder cannot be parsed by a non-mbsync user. | Replace with a tri-state per-folder picker (Sync / Skip) populated by `LIST` + a free-text "advanced patterns" field hidden behind a disclosure. |
| **Max Messages (0 = unlimited)** | mbsync `MaxMessages` | Mostly OK, but the unit is wrong: users want "messages per folder" or "size cap"; raw count is mbsync's old way of capping the slave. | Rename to *"Keep at most N most-recent messages per folder"* and offer a size-based alternative. |
| **SubFolders Format = Verbatim** (disabled) | mbsync `SubFolders Verbatim\|Maildir++\|Legacy` | Not user-facing concept. We force Verbatim because of LAYOUT=fs. | Drop the field entirely; document the choice in the architecture page. |
| **Pipeline Depth (0 = unlimited)** | mbsync `PipelineDepth` | **Confusing.** A user who knows what it means won't be using a GUI; a user who doesn't can't tell whether the default is fast or safe. | Move to "Advanced" hidden by default; add a tooltip. |
| **Disable IMAP Extensions** placeholder `COMPRESS=DEFLATE` | mbsync `DisableExtension` | **Confusing for the GUI**, indispensable for some Gmail / Yahoo workarounds. | Keep but rename to *"Server quirks / workarounds"* and provide a curated dropdown of known toggles instead of free text. |
| **Preserve message arrival dates** | mbsync `CopyArrivalDate` | **Helpful.** Plain English, action-oriented, default-on. | Keep as-is. |
| **Timeout (seconds)** | mbsync `Timeout` | OK but homeless — should be paired with the connection block, not the sync block. | Move under "Source connection". |

The biggest single inheritance problem is **Far/Near**. mbsync renamed
Master/Slave → Far/Near in 1.4 (Feb 2021) for ethical reasons; the new pair is
*more* abstract, not less. Outside the .mbsyncrc, no mail vocabulary uses these
words. By comparison, OfflineIMAP uses `localrepository` / `remoterepository`,
which any user can read.

---

## What the CLI ecosystem teaches

Patterns the OSS CLIs get right that MFB could borrow today, ranked by leverage:

1. **OfflineIMAP's `localrepository` / `remoterepository` is the right semantic
   pair for a GUI.** It says *what* each side is, not where it sits in some
   abstract topology. MFB should adopt *Source* (remote IMAP being protected)
   and *Local backup* (Maildir on disk) and never use Far/Near in a UI string.

2. **imapsync's `--just*` family is the gold standard for "preview before you
   commit".** `--justconnect` / `--justlogin` / `--justfolders` /
   `--justfoldersizes` give the user a four-rung ladder of confidence before
   they start moving bytes. MFB has a single "Test connection" button. We
   should split it into "Test login → List folders → Estimate size" so the user
   can fail fast at the right rung.

3. **Cyrus's separation of `archive_after` (move to slow disk) from `expire`
   (actually delete) is the cleanest model in the ecosystem.** MFB conflates
   "expunge" (mbsync's term for "permanently delete on a side") with
   "retention" (how long we keep things in the local backup). We should adopt
   Cyrus's two-axis model: *tiering* (which store) and *retention* (when to
   delete) are different policies, with different defaults, on different
   pages. This also makes the restic layer legible: snapshots are *another*
   tier.

4. **MailPiler's deliberate refusal of the word "backup".** Their docs say
   *archive* on every page, never *backup* or *sync*. The discipline is
   striking and pays off: a Piler user always knows whether they're running
   ingest, retention, search, or restore. MFB's mixed lexicon today
   ("backup destinations" + "sync jobs" + "snapshots" + "stores") is exactly
   what Piler avoided.

5. **getmail's strict 4-section grammar (`[retriever]`, `[destination]`,
   `[options]`, `[filter]`) maps cleanly to a wizard.** Each section is one
   step, each step has one decision. MFB's "Add account" form crams source +
   credentials + sync policy + storage choice + scheduling into one screen.
   A 4-step wizard mirroring getmail's mental sections (*Where it comes from /
   Where it lands / How often / What's allowed*) would be more discoverable.

---

## What MFB should hide

Terms MFB exposes today that should arguably stay in the `.mbsyncrc` and never
appear in the GUI, with the rationale per term:

| Term in current UI | Why it should be hidden | Where it should live |
|---|---|---|
| `Far`, `Near` | Mbsync-internal renaming of Master/Slave. Carries no meaning to a user. Replaceable by *Source* / *Local backup*. | Generated `.mbsyncrc` only. |
| `Channel` | mbsync object name. The user sees an *Account*; "Channel" is the engine's word for the join. | `.mbsyncrc` only. |
| `Patterns` with `!` and `%` syntax | An IMAP4 wildcard mini-DSL. Two distinct sigils plus quoting rules. | Move behind an "Advanced — raw mbsync patterns" disclosure for power users. Default UI = checkbox list of folders. |
| `Expunge` | RFC 3501 verb for "remove flagged-for-deletion messages from the mailbox". Users say *delete*. | Replace with "Permanently delete" in the GUI. |
| `SubFolders Verbatim` | Implementation choice required by LAYOUT=fs. Not actionable. | Architecture docs only. |
| `PipelineDepth` | Performance knob; a wrong value silently corrupts nothing but slows down throughput. Most users never touch it. | "Advanced" disclosure, off by default. |
| `DisableExtension COMPRESS=DEFLATE` etc. | Provider-specific workarounds. The free-text field implies the user knows IMAP capability strings. | Curated "Provider quirks" dropdown with named entries (e.g., "Yahoo: disable COMPRESS"). |
| `MaxMessages` count vs `MaxSize` bytes | Two competing caps with no relationship explained in UI. | Single retention page with two clearly labeled sliders. |
| `CopyArrivalDate` (the keyword, not the toggle) | Internal mbsync field name occasionally surfacing in error messages. | Hide from labels (we already do); also strip from any error toast we forward. |
| The literal word "Sync" as a noun for the job entity | Inherited from mbsync's `Sync` keyword *and* getmail's job loop. Now overloaded with the Backup-destination work. | Reserve "Sync" for the IMAP→Maildir step only; use "Backup" for restic and "Snapshot" for restic point-in-time. The four-stage model in the kickoff already implies this split. |

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
