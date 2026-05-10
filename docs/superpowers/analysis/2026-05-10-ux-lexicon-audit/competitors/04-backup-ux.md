# Competitor: backup tooling UX

## Tools covered
- **restic** (CLI) + ResticBrowser / restatic (third-party GUIs)
- **Borg** + **Vorta** (cross-platform desktop GUI)
- **Duplicati** (web UI, cross-platform)
- **Kopia** (web UI + KopiaUI desktop)
- **Time Machine** (macOS, system-integrated)
- **Synology Hyper Backup** (DSM appliance)
- **Backblaze Personal Backup** (consumer cloud, opinionated)

Each has to teach a casual user the same loop — "**source data → repository → snapshot/policy → restore**" — without the user reading the man page. MFB inherits restic's underlying terminology; the question is which surface vocabulary the ecosystem has converged on.

## Lexicon table

| Concept | restic (CLI) | Vorta (Borg) | Duplicati | Kopia | Time Machine | Synology Hyper Backup |
|---|---|---|---|---|---|---|
| **Source data** | path argument to `backup` | "Source" tab; "files and folders to back up" | "Source Data" wizard step | "Source" (path) registered with a Policy | "Backed Up Items" (mostly implicit: whole disk) | "Data Source" / "Application" / "LUN" picker |
| **Repository (remote depot)** | "Repository" (`-r` URL) | "Repository" (Local / Remote / BorgBase) | "Destination" / "Backend" (S3, FTP, SMB...) | "Repository" (storage location, encrypted) | "Backup Disk" (single physical/networked target) | "Backup Destination" / "Repository" (`.hbk` bundle) |
| **Snapshot** | "Snapshot" (immutable point-in-time, has ID) | "Archive" (Borg term) — labelled "Archives (or snapshots)" in docs | "Version" / "Snapshot" / "Restore Point" (used interchangeably) | "Snapshot" (point-in-time of a Source) | "Backup" (one per hour, listed by date in starfield UI) | "Version" (numbered, dated) |
| **Retention / forget** | `forget` + `--keep-{last,hourly,daily,weekly,monthly,yearly}` then `prune` | "Pruning" (Schedule tab → Prune Options); same keep-N-of-each-period grid | "Backup Retention" (5 modes: Smart, Custom, Keep N, Keep all, Delete after) | "Retention Policy" (slot grid: latest/hourly/daily/weekly/monthly/annual) | Implicit: hourly→24h, daily→1mo, weekly→until full, no UI knobs | "Rotation Scheme" (Smart Recycle, From earliest version, Customised) |
| **Restore** | `restore <snapshot-id> --target /path` | "Restore" button per archive → choose target dir, "Extract selected" or full | "Restore" wizard (2 steps): pick version + files, then destination | "Restore" → mount, full restore, or "Download File/Directory" zip | "Enter Time Machine" starfield, swipe-back UI; "Restore" returns file in place | "Restore Wizard" → choose task, version, target volume |
| **Schedule** | external (cron / systemd timers) | "Schedule" tab: every N hours / daily / cron-like | "General" wizard step → schedule + retention | "Snapshot Frequency" inside Policy (per-source) | Hardcoded hourly; toggle on/off only | "Backup Schedule" + "Integrity Check Schedule" |

Cross-cutting observations:

- **"Repository" is the consensus term** for the offsite store (5 of 7). Duplicati is the lone holdout with "Destination/Backend" and its forum has years of "what is a backend" confusion threads. MFB's current "backup destination" reads as Duplicati-style.
- **"Snapshot" beats "Archive" and "Version"** for new users. Vorta docs disambiguate "Archives (or snapshots)"; Duplicati's interchangeable use of three terms is a known wart.
- **Retention is universally "policy + slot grid"**, never raw `forget`/`prune` jargon. Every GUI hides the two-step cleanup behind one button.

## Empty-state and first-run flows

### Vorta (hobbyist desktop)

Vorta punts onboarding to a tutorial video — its empty state is a near-blank window with "Add Repository ▾" as the only obvious affordance. The dropdown branches: **New Repository** (local / BorgBase / SSH), **Add Existing Repository**, **Initialize remote**.

Once a repo exists, five tabs appear in order: **Repository · Sources · Schedule · Archives · Misc**. The tab order *is* the mental model: pick the depot, point at the data, define when, browse what came out, tweak the rest. New users get lost in two predictable places: (a) the "Prune Options" vs "Schedule" boundary, and (b) the absent "Restore" tab — restore lives as a button on each row of the **Archives** tab.

### Kopia (hobbyist with docs patience)

KopiaUI runs a strict guided wizard:

1. **Pick storage type** — 10-tile grid (S3, B2, GDrive, SFTP, filesystem...). Big icons, one-line descriptions.
2. **Storage details + password** — scary mandatory copy: "Kopia cannot recover your password. Store it somewhere safe."
3. **Repository connected** — confirmation card; "Snapshots" left-nav becomes live.
4. **Define a Source by path** — file picker; initial snapshot fires immediately with a progress card.
5. **Policy** — pre-populated with sensible defaults (hourly + 4-week retention).

Kopia's "repository → source → policy → snapshot" is essentially the four-stage MFB model. Wizard discipline: **one decision per screen**, nothing else editable. Power users go CLI; the GUI never exposes more than the 80% path.

### Counter-example: Duplicati

Duplicati's wizard asks for an **encryption passphrase on step 1** — before the user has even picked a destination — and its forums fill with "what should I put here" threads. Cautionary tale for MFB: don't ask the user about a thing whose context they don't yet have.

## Status-surface patterns

MFB needs to communicate four numbers per account: **last snapshot age**, **next snapshot ETA**, **repo size**, **snapshot count**. Here's how the field handles them:

| Surface | Vorta | Duplicati | Kopia | Time Machine | Backblaze |
|---|---|---|---|---|---|
| **Last snapshot age** | "Last backup: 2h ago" in status bar at bottom of window | Per-job card: "Last successful: yesterday 21:14" + relative time | Per-source row: green dot + "5 minutes ago" | Menu-bar tooltip: "Latest Backup: Today, 14:02" | Tray text: "Last backup: Today 3:02pm" |
| **Next snapshot ETA** | "Next Backup: Tomorrow 02:00" header strip on Schedule tab | "Next scheduled: in 4h 12m" on job card | "Next snapshot in: 47m" inline on source row | Not surfaced (hourly is implicit) | "Next: in 1 hour" or "Backup is paused" |
| **Repo size** | Footer of Archives tab: "Original: 412 GB · Compressed: 210 GB · Deduplicated: 84 GB" | Per-job: "Backup size: 84.2 GB / 12 versions" | Per-repo dashboard: storage used + dedup ratio bar | Disk usage bar on the Time Machine prefpane | "Files backed up: 1.4M" + "Data backed up: 312 GB" |
| **Snapshot count** | Archives tab: row count + "12 archives" caption | "12 versions available" on job card | "Snapshots: 248" tile on source detail | Implicit (starfield depth) | Not surfaced as a count — only "Version History" timeline |

Patterns worth lifting:

- **Triplet for size** (original / compressed / deduplicated) is the Borg/Vorta trust-builder — it makes "is it actually working" answerable at a glance. restic exposes the same numbers; MFB can surface them per-account and per-destination.
- **Relative-time-first, absolute-time-on-hover** is universal. "2h ago" with `title="2026-05-10 12:14:03 UTC"`.
- **Coloured status dot** (green / amber / red / grey-paused) prefixed to every row — Kopia, Duplicati, Vorta. MFB already uses this for sync; extend to backup.
- **"Next" is as important as "Last"**. Home Assistant 2025.1 explicitly called this out: "a page that shows you exactly when your last backup took place and when the next one is scheduled. Instant peace of mind!" If MFB shows only "last", users still wonder if it's still alive.

## Restore UX patterns

The interesting question for MFB: is "restore creates a new suspended account" a known pattern, or are we inventing?

| Tool | In-place | New location | New "thing" / sandbox | Default |
|---|---|---|---|---|
| restic | Yes (`--target /`) | Yes (`--target /tmp/x`) | No | No default — explicit `--target` required |
| Vorta | Yes ("Extract to original") | Yes ("Extract to…") | No | "Extract to…" pre-selected |
| Duplicati | Yes ("Original location") | Yes ("Pick location") | No | "Original" radio default |
| Kopia | Yes (`kopia restore`) | Yes | "Mount as a virtual drive" (read-only filesystem view) | Mount is the GUI default; restore is CLI |
| Time Machine | Yes (per-file in starfield) | Yes (drag out of TM window) | No | In-place |
| Synology Hyper Backup | Yes | Yes (different volume / share) | No | "Restore to original" default with a "Restore to a different shared folder" toggle |
| Backblaze | No (in-place doesn't exist for cloud) | Yes (download zip) | "Restore by Mail" (physical drive shipped) | Download zip |

MFB's **"restore = new suspended account"** has clear precedent: it is most analogous to **Kopia's mount** (read-only, isolated, doesn't touch the live system) and **Synology's "restore to a different shared folder"** (default-safe, never overwrites live data). The "suspended" qualifier is MFB-specific and good — it prevents the restored account from re-pulling from a live source and clobbering the snapshot.

What MFB should learn:
- **Default to the safe variant.** Synology and Vorta default to "extract to a new place" because the irreversible variant should require an extra click. MFB already does this.
- **Confirmation copy must name the snapshot.** Kopia: "Restore snapshot `k4f3e6…` from 2026-05-08 14:02 to `/restore/2026-05-10`?" — date + ID. MFB's restore confirmation should identify the snapshot, not just the destination.
- **A post-restore "what now?" panel is mandatory.** Time Machine's restore shows you the file and you're done. MFB creates a new entity the user must *do something with* — the success screen needs "this account is suspended; browse it via Webmail, then delete it or re-enable IMAP sync."

## What MFB can borrow

1. **Adopt "Repository" (or its IT translation "Deposito remoto") as the canonical term for the offsite store.** Six of seven competitors converge here; MFB's current "Backup Destination" reads as Duplicati-flavoured and Duplicati is the outlier.
2. **Rename UI surfaces around "Snapshot" not "Backup".** "Backup" is a *verb* in this ecosystem; the noun for the artifact is "snapshot". MFB's current overloading of "backup" maps directly onto the Italian critique that opened this audit.
3. **Per-account status row with the four-number quartet** (last age / next ETA / size triplet / snapshot count) — modelled on Kopia and Vorta's source rows.
4. **First-run wizard with one decision per screen.** Kopia is the gold standard. Five screens for an MFB account: source IMAP → local store → schedule → repository → policy. Don't show step N+1 until step N is valid.
5. **Default-safe restore with explicit naming of the snapshot in the confirmation modal.** Synology + Kopia pattern. The new-suspended-account model is fine; just label it like a real restore from a real point in time.
6. **Three-number size disclosure (logical / on-disk / deduplicated)** wherever a repository is shown. Borg's killer feature for trust-building, free for MFB because restic exposes the same numbers.

## References

- Kopia, *Getting Started Guide* — https://kopia.io/docs/getting-started/
- Kopia, *Repositories* — https://kopia.io/docs/repositories/
- Vorta for BorgBackup, *Usage* — https://vorta.borgbase.com/usage/
- Vorta for BorgBackup, *Local Backups* — https://vorta.borgbase.com/usage/local/
- restic, *Removing backup snapshots* — https://restic.readthedocs.io/en/stable/060_forget.html
- Duplicati, *Community docs: using the graphical user interface* — https://docs.duplicati.com/community-docs/community-docs-using-the-graphical-user-interface
- Duplicati, *Retention settings* — https://docs.duplicati.com/configuration-and-management/retention-settings
- Duplicati, *Restoring files* — https://docs.duplicati.com/getting-started/restoring-files
- Synology, *Hyper Backup — Restoration* — https://www.synology.com/knowledgebase/DSM/help/HyperBackup/restore
- Synology, *Methods to restore Hyper Backup data and LUN* — https://kb.synology.com/DSM/tutorial/What_methods_are_available_for_restoring_Hyper_Backup_backup_data_and_LUN
- Backblaze, *Version History* — https://www.backblaze.com/computer-backup/docs/version-history
- Backblaze, *Restore app v9.0 announcement* — https://www.backblaze.com/blog/restore-like-never-before-introducing-backblaze-computer-backup-v9-0/
- Home Assistant 2025.1 release notes (next-backup-time pattern) — https://www.home-assistant.io/blog/2025/01/03/release-20251/
- Andrea Grandi, *My Backup Strategy with Borg, Vorta and BorgBase* — https://www.andreagrandi.it/posts/my-backup-strategy/
- odd.blog, *Borg-ui and Vorta are nice BorgBackup frontends* — https://odd.blog/2026/01/05/borg-ui-and-vorta-are-nice-borgbackup-frontends/
