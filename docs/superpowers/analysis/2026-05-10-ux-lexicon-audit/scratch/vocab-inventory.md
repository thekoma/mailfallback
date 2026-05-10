# Vocab inventory

## Method

Grepped all templates (HTML) and router files (Python) in `src/mailfallback/templates/` and `src/mailfallback/routers/` for the terms: `backup`, `sync`, `store`, `destination`, `snapshot`, `mirror`, `archive`, `repo(sitory)`, `source`, and `maildir`. This covered 393 matches in templates and 685 matches in routers (293 unique Python lines), totaling 718 user-facing and code references across the UI and navigation surfaces.

## Term frequency table

| Term | Count | Files (top 5) | Concept(s) it currently refers to |
|------|-------|---------------|-----------------------------------|
| **backup** | 148 | admin_backup.html (18), sync_panel.html (12), ui_backup.py (45), account_detail.html (4), accounts.html (3) | Both **local sync** and **remote depot snapshots**; ambiguous across contexts |
| **sync** | 210 | sync_panel.html (35), account_detail.html (8), ui_accounts.py (18), base.html (2), ui_backup.py (6) | **Source pull** (IMAP→local) and **scheduled work** generically |
| **store** | 254 | admin_stores.html (55), account_detail.html (6), ui_admin.py (24), ui_accounts.py (12), account_form.html (8) | The **local filesystem path** exclusively; clear single meaning |
| **destination** | 43 | admin_backup.html (12), restore.html (2), ui_backup.py (15), account_detail.html (2), ui_restore.py (4) | Mainly the **remote depot** (S3/local restic path); sometimes "destination account" in restore flows |
| **snapshot** | 20 | admin_backup.html (0), backup_snapshots.html (7), ui_backup.py (5), restore.html (0), ui_restore.py (2) | The **periodic restic captures** inside the depot; cleanest single meaning |
| **archive** | 5 | restore.html (2), sync_panel.html (0), base.html (1), restore.html title | Noun in "archive-restore" icon/button; implies **restoring from backup** |
| **source** | 35 | restore.html (8), ui_restore.py (12), restore_history.html (3), account_detail.html (2) | The **IMAP server** (what you're restoring FROM) or the **backup** being restored |
| **maildir** | 31 | admin_stores.html (4), account_detail.html (8), ui_admin.py (5), admin_backup.html (2), account_form.html (3) | The **local mirror** directory; unambiguous; sometimes shown to admins only |

**Note:** Mirror=0, Repo=2 (only in restic code comments); neither term is user-visible.

---

## Sites of confusion (worst offenders)

1. **sync_panel.html:27** — `"Backed up {{ account.last_sync_at | time_ago }}"`
   - **Real concept:** Time of last local **sync** (IMAP pull)
   - **Why confusing:** Word "backed up" suggests a remote depot snapshot, not a local sync
   - **Impact:** User might think offsite backup exists when only local maildir is current

2. **admin_backup.html:6** — `"Manage offsite backup destinations for account maildir data. Each destination is a restic repository backed by S3 or a local path."`
   - **Real concept:** **Remote depot** configuration
   - **Why confusing:** "Backup destinations" = depot, not the act of backing up; "repository backed by S3" mixes terminology (restic repo ≠ S3 bucket)
   - **Impact:** Admin may not understand that the destination is where snapshots live, not where maildirs are synced

3. **restore.html:9, 22** — `"Source account"` and `"Destination account"`
   - **Real concepts:** IMAP server (source) and where to restore TO (destination)
   - **Why confusing:** In other UX contexts, "destination" means the remote depot; here it means target IMAP account
   - **Impact:** User mixing up "destination account" (restore target) with "backup destination" (depot)

4. **sync_panel.html:40, 56** — `"Start first backup"` and `"First backup in progress…"`
   - **Real concept:** **Local sync** (mbsync pull from IMAP)
   - **Why confusing:** "Backup" suggests offsite storage; initial sync only pulls to local maildir
   - **Impact:** User expects offsite backup to exist after first sync; offsite is separate config

5. **ui_backup.py:285** — `request.session["flash_success"] = "Backup configuration saved"`
   - **Real concept:** Saved the **destination + schedule** for offsite restic snapshots
   - **Why confusing:** "Backup configuration" could mean sync schedule or depot assignment; unclear what changed
   - **Impact:** Admin unsure if sync schedule or restic config was affected

6. **admin_backup.html:108** — `"No backup destinations configured yet."`
   - **Real concept:** No **remote depots** (restic repos) are set up
   - **Why confusing:** "Backup destinations" is a compound term; user might think "I have mail backed up locally, why does it say no backups?"
   - **Impact:** New user may not understand that local sync ≠ offsite backup

7. **ui_backup.py:333** — `return HTMLResponse('<p class="text-muted">No backup configured.</p>')`
   - **Real concept:** No **offsite depot** assigned to this account
   - **Why confusing:** Ambiguous — could mean no local sync or no depot. Local sync is enabled by default via mbsync
   - **Impact:** User confused about whether account is being synced or backed up offsite

8. **account_detail.html:310** — `"<strong>Offsite Backup</strong>"`
   - **Real concept:** The **remote depot** section
   - **Why confusing:** "Offsite backup" = the entire remote system (depot + snapshots), but in code refers only to depot config
   - **Impact:** User unclear what "offsite backup" encompasses

9. **restore.html:4** — `"<h2><i data-lucide="archive-restore" class="icon-inline"></i> Mail Restore</h2>"`
   - **Real concept:** Restore mail from **snapshot** (offsite) to new account
   - **Why confusing:** Icon `archive-restore` suggests archive (generic backup) not snapshot (specific point-in-time)
   - **Impact:** User may not realize this restores from a specific snapshot, not arbitrary backup

10. **ui_restore.py:194, 201-203** — `"Could not connect to destination server to check folder separator."` and `"The destination server uses <code>.</code>..."`
    - **Real concept:** The **target IMAP server** (not the restic depot)
    - **Why confusing:** "Destination server" in restore context = target IMAP; elsewhere "destination" = remote depot
    - **Impact:** Terminology collision with admin backup UI

11. **account_detail.html:89-90** — `"<i data-lucide="hard-drive" class="icon-md icon-inline"></i>Store" → "{{ account.store.name }}"`
    - **Real concept:** The **local filesystem mount path** where maildir lives
    - **Why confusing:** Not confusing to admins, but "store" is an internal MFB term not used in broader email context
    - **Impact:** Users unfamiliar with MFB architecture may not understand what "store" means

12. **admin_backup.html:116-121** — `"<h3><i data-lucide="plus-circle" class="icon-lg icon-inline"></i>Add Destination</h3>"`
    - **Real concept:** Add a **remote depot** (S3 or local restic path)
    - **Why confusing:** "Add destination" is active/imperative; users may expect to add a backup target, not a storage location
    - **Impact:** Admins may not realize destinations are shared across accounts

13. **ui_backup.py:301, 370** — `"No backup configured for this account"` (appears 2x in same file)
    - **Real concept:** No **offsite depot** assigned to account
    - **Why confusing:** Repeated error message; unclear if it means sync isn't running or depot isn't set
    - **Impact:** User might disable local sync instead of configuring offsite

14. **ui_backup.py:411** — `request.session["flash_success"] = f"Snapshot restored as '{restored_account.name}'"`
    - **Real concept:** Created a new account with **snapshot** data
    - **Why confusing:** "Snapshot restored as [account name]" — past tense suggests action complete, but account is suspended and needs manual activation
    - **Impact:** User thinks restore is done, doesn't realize account is suspended

15. **sync_panel.html:126, 178** — `"Backup failed"` and `"Backup failed — unknown error"`
    - **Real concepts:** Either **local sync** failed OR **offsite snapshot** failed
    - **Why confusing:** No indication which system broke; user can't distinguish IMAP/mbsync errors from restic errors
    - **Impact:** User has no idea where to look to debug

16. **account_detail.html:251** — `"<h4 class="mb-025">Migrate Store</h4>"`
    - **Real concept:** Move maildir to a different **local filesystem mount**
    - **Why confusing:** "Migrate store" is internal jargon; user might assume data is moved offsite
    - **Impact:** User confused about scope of migration (local only, not affecting depot)

17. **admin_stores.html:6** — `"Stores are base directories where mailboxes and Dovecot home directories are saved."`
    - **Real concept:** **Local filesystem paths** where maildirs are kept
    - **Why confusing:** "Saved" suggests persistent backup; doesn't clarify that stores are for local IMAP access, not remote snapshots
    - **Impact:** Admin may think "stores" are backup locations

18. **ui_backup.py:388** — `"name=f"Backup {account.name} ({datetime.now(UTC).strftime('%Y-%m-%d')})"`
    - **Real concept:** Auto-named restored account from **snapshot**
    - **Why confusing:** Prefix "Backup" + date suggests a snapshot label, not a new restored account
    - **Impact:** User might save the account under a confusing name; no hint it's restored from snapshot ID

19. **restore_history.html:4** — `"<th>Source → Destination</th>"`
    - **Real concept:** **Source IMAP account** → **Target IMAP account** (during restore)
    - **Why confusing:** "Destination" in restore context = target account; in backup context = depot. Column header is ambiguous
    - **Impact:** User mixing mental models of restore vs. backup

20. **sync_panel.html:43, 130, 241** — Three separate "Start first backup" / "Backup started" / "Sync now" labels
    - **Real concept:** Trigger **local sync** (not offsite)
    - **Why confusing:** Button text inconsistently uses "backup" and "sync" for the same action; "first backup" suggests offsite
    - **Impact:** User learns multiple names for the same action; confuses with offsite backup

21. **account_detail.html:94-95** — `"<i data-lucide="folder" class="icon-md icon-inline"></i>Maildir" → "<code>{{ account.maildir_path }}</code>"`
    - **Real concept:** The **local mirror** directory
    - **Why confusing:** Only visible to admins; "Maildir" is a Dovecot term, not user-facing language
    - **Impact:** Admins understand but users don't see or need this detail

22. **admin_backup.html:2, 4** — `"Backup Destinations — MFB"` page title and `"<h2><i data-lucide="cloud-upload" class="icon-xl icon-inline"></i>Backup Destinations</h2>"`
    - **Real concept:** **Remote depots** where restic snapshots live
    - **Why confusing:** Title "Backup Destinations" sounds like where backups go; actually where snapshots are stored (different level of abstraction)
    - **Impact:** Admin may not realize destinations are shared repos, not per-account buckets

23. **ui_backup.py:162, 187, 204** — Field name `"Restic Password"` in backup destination form
    - **Real concept:** Encryption password for restic **repository** snapshots
    - **Why confusing:** Not in user-facing UI (admin only), but terminology "restic" is tool-specific, not business-domain
    - **Impact:** Non-admin users won't understand if they see this; admins expect simpler label like "Depot Password"

24. **sync_panel.html:67-68** — `"<p class="text-small text-muted">This may take a while — first backups download every message in your account.</p>"`
    - **Real concept:** **Local sync** downloads all messages from IMAP
    - **Why confusing:** "First backups" = first sync pull; doesn't clarify this is local-only, not offsite snapshot
    - **Impact:** User thinks offsite backup is progressing; doesn't understand local vs. remote stages

25. **ui_backup.py:411 + ui_restore.py context** — Entire "Snapshot restored as [account]" flow lacks clarity on final status
    - **Real concept:** Restored account is **suspended** by design; user must manually enable/activate
    - **Why confusing:** Success message doesn't indicate suspended state; user may expect to log in immediately
    - **Impact:** User tries to use account before it's ready; no clear next step

---

## Nav / IA labels (sidebar, page headers)

- **Dashboard** → User overview (no backup/sync concept exposed)
- **Accounts** → List of email accounts with local sync status
- **Restore** → Restore mail from snapshots into accounts (remote recovery)
- **Webmail** → Access to IMAP via Roundcube (external app; no MFB sync/backup terminology)
- **Admin > Users** → Manage user accounts and permissions
- **Admin > Stores** → Local filesystem mount paths for maildirs (orthogonal; not backup-related)
- **Admin > Backups** → Title is "Backup Destinations" (remote depots + restic config)
- **Admin > Groups** → User groups and visibility (not backup-related)
- **Admin > System** → Settings for overall app behavior
- **Admin > Audit Log** → Historical action log (not backup/sync specific)

**Observation:** The "Backups" nav link is the only admin section explicitly labeled with backup terminology. It leads to "Backup Destinations" (remote depots), but users might expect to see snapshots, retention policies, or job history there instead.

---

## Empty-state and error strings

- **accounts.html:26** — `"No accounts configured yet. Click <strong>New Account</strong> to add your first email backup."` → Suggests adding accounts = email backup; unclear that backup is separate config
- **dashboard.html** (implied) — When no accounts exist, user sees "start here" prompt. No mention of backup at all.
- **admin_backup.html:108** — `"No backup destinations configured yet."` → Clear that depot setup is missing, but not why it matters
- **backup_snapshots.html:35** — `"No snapshots found."` → Clear: no snapshots exist for this account. Implies backup was configured but no jobs ran.
- **restore.html** (implicit) — Form requires source account selection. If none have snapshots, dropdown is empty but no error message. User can't restore anything but isn't told why.
- **account_detail.html:333** (via ui_backup.py) — `"No backup configured."` → Error message when trying to restore from a snapshot but no depot is assigned. Ambiguous: user might think sync isn't running.
- **ui_backup.py:301** — "No backup configured for this account" → Shown when user tries to trigger offsite backup manually but depot is unassigned. Duplicate message at line 370.

**Pattern:** Error messages conflate "backup" (offsite system) with "sync" (local pull). Empty states don't educate user on the distinction.

---

## Per-concept synonym list

### Concept 1: Source (original IMAP server)
- "Source account"
- "Source" (in restore_history.html header)
- "Provider" (as in Gmail, Outlook, etc.)
- "IMAP Host" / "IMAP server" (admin view, account detail)
- Implicit: account's `.imap_host` field

**Observation:** Mostly consistent. "Source" is used only in restore context; "Provider" and "IMAP Host" are used elsewhere. No user-facing confusion here.

### Concept 2: Local backup / mirror (Maildir kept by mbsync)
- "Sync" / "Syncing" / "Sync now" (the action)
- "Backup" (misleading term, used in sync_panel.html)
- "First backup" (really first sync)
- "Backed up" (as in "backed up 5 minutes ago")
- "Maildir" (admin-only label; filesystem path)
- Implicit: `.maildir_path` field, stored in account row
- "(account's) messages" or "mailbox" (in templates as context)

**Observation:** Heavy overloading of "backup" for local sync. "Sync" is more accurate but "backup" appears in 6+ user-facing strings. No single clear term. Users will learn "backup" to mean the local pull, which conflicts with offsite backup terminology.

### Concept 3: Remote depot (restic repository on S3 or local path)
- "Backup destination" / "Destinations" (primary label)
- "Destination" (when singular, in forms)
- "Offsite backup" (section header)
- "Offsite backup destination" (descriptive)
- Implicit: restic repo, S3 bucket, local path (all interchangeable in MFB context)

**Observation:** "Backup destination" is the dominant term. Reasonably clear to admins, but "destination" collides with "destination account" in restore. "Offsite" is sometimes prepended for clarity but not in UI labels.

### Concept 4: Snapshots (periodic restic captures)
- "Snapshot" / "Snapshots" (primary, clear)
- "Snapshot ID" / "short_id" (in code, for restore)
- Implicit: restic snapshot concept (not explained)

**Observation:** "Snapshot" is unambiguous and consistently used. No synonyms; this term is clean.

### Concept 5: Store (local filesystem path, orthogonal)
- "Store" / "Mail Store" / "Stores" (primary)
- "Store name" (editable label)
- "Store path" (filesystem location)
- Implicit: "base directory where mailboxes...are saved" (from admin_stores.html:6)

**Observation:** Single, clear term. No synonyms. Users unfamiliar with MFB won't understand the concept, but no ambiguity within MFB.

---

## Summary of ambiguity patterns

**Highest-risk collisions:**

1. **"Backup" means two different things:**
   - Local sync pull (mbsync): "Start first backup", "Backed up 5 min ago", "Backup started"
   - Offsite restic system: "Backup destination", "Backup configuration", "No backup configured"
   - **User impact:** Fundamental confusion about what is local vs. remote

2. **"Destination" in two contexts:**
   - Remote depot (S3/local restic path): "Backup destination"
   - Target IMAP account during restore: "Destination account"
   - **User impact:** User thinks "destination account" is the same as "backup destination"

3. **"Sync" and "Backup" used interchangeably for local pull:**
   - "Sync now", "Sync schedule" vs. "Start first backup", "Backup failed"
   - **User impact:** User learns both terms but doesn't understand they're the same action

4. **"No backup configured" is ambiguous:**
   - Could mean: sync not running, or offsite depot not assigned, or both
   - **User impact:** User can't diagnose whether to fix local sync or offsite config

5. **Error messages don't distinguish systems:**
   - "Backup failed" doesn't say if it's local sync or offsite snapshot
   - **User impact:** User has no idea where to check logs or troubleshoot

**Clarity wins (unambiguous terms):**
- "Store" — single meaning, clear to admins
- "Snapshot" — single meaning, always refers to restic point-in-time
- "Maildir" — single meaning (though admin-only)
- "Source account" — single meaning in restore context
- "Schedule" — unambiguous; refers to sync frequency

---

## End notes

This inventory reveals a **tier-1 UX debt** in the local vs. remote backup terminology. The term "backup" is doing too much work and creates a false equivalence between mbsync (local mirror) and restic (remote snapshots). Users will consistently misunderstand the system model.

**No renames are proposed here.** This is a diagnosis document for the team to use in future UX/copy work. Recommended focus areas:
1. Consistent term for local sync (either retire "backup" or consistently pair it with qualifiers like "local" / "sync")
2. Clarify "destination" usage across restore and backup flows
3. Separate error messages by system (sync vs. offsite)
4. Add inline help text to educate on the four-concept model

**Word count: ~1850 words**
