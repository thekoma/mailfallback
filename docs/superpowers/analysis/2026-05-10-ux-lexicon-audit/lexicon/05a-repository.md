# Lexicon Proposal A — "Repository" (technical, restic-aligned)

**Voice:** Technical, precise, aligned with the underlying tooling.
**Audience optimised for:** P1 (Andrea, homelab owner), P3 (small-org IT — compliance-friendly), P2 (family IT helper).
**Risk profile:** Conservative — minimal coining, maximum recognisability for users who know restic / Kopia / Vorta / Borg.

---

## The four-stage model

```
┌─────────┐   pull    ┌──────────────┐   push    ┌──────────────┐   capture   ┌──────────────┐
│ MAILBOX │──────────▶│ MAIL MIRROR  │──────────▶│ REPOSITORY   │────────────▶│ SNAPSHOT     │
│ Source  │  mirror   │ Local copy   │  backup   │ Off-site     │  retention  │ Point-in-    │
│ IMAP    │  schedule │ on this host │  job      │ restic repo  │             │ time         │
└─────────┘           └──────────────┘           └──────────────┘             └──────────────┘
   noun: mailbox          noun: mirror              noun: repository            noun: snapshot
   verb: connect          verb: mirror              verb: back up               verb: capture
```

**Verb discipline:** "Sync" disappears entirely as a user-facing verb. "Mirror" becomes both noun and verb for stage 2; "back up" (two words) is the verb for stage 3. "Snapshot" stays as the artifact.

---

## Term mapping table — EN

| Concept | Today's MFB term(s) | Proposal A | Notes |
|---|---|---|---|
| **Stage 1 — Source IMAP server** | "Source account", "Provider", "IMAP host" | **Mailbox** (when speaking of the user-owned object) / **Source** (when speaking of the technical endpoint) | "Mailbox" is the noun the user thinks in; "Source" appears only on technical fields. |
| **Stage 1 — connection settings** | "IMAP Host / Port / TLS / Auth" | unchanged | Already standard. |
| **Stage 2 — local copy** | "Maildir", "Store" (filesystem path), "Backup", "Sync", "First backup" | **Mail mirror** (or just "Mirror") | Borrows from OfflineIMAP `localrepository`. Verb: "to mirror". |
| **Stage 2 — the action of pulling** | "Sync now", "Sync schedule", "Backup now" | **Mirror now** / **Mirror schedule** / **Mirror status** | One verb everywhere for stage 2. |
| **Stage 2 — the local filesystem path** | "Mail Store" / "Stores" (admin nav) | **Storage volume** (or just "Volume") | Decouples from the noun "mirror". A volume holds many mirrors. |
| **Stage 3 — remote depot** | "Backup Destination", "Destination" | **Repository** (or "Repo") | restic / Kopia / Vorta consensus. |
| **Stage 3 — the action of capturing** | "Backup", "Backup Now" | **Back up** (verb) / **Backup** (noun for the *job*, not the artifact) | "Back up" two words = verb, "Backup" one word = noun. Industry standard distinction. |
| **Stage 3 — the per-account configuration** | `AccountBackup` | **Backup policy** (per-mailbox) | "Policy" because it includes schedule + retention. |
| **Stage 4 — point-in-time** | "Snapshot" | **Snapshot** | Unchanged. Universal term. |
| **Stage 4 — retention** | "Retention", "Retention preset" | **Retention policy** | Unchanged plural. |
| **Restore (mailbox-side, into IMAP)** | "Restore" (in /restore page, source→target) | **Restore** | Keep for IMAP-to-IMAP recovery. |
| **Restore (depot-side, from snapshot)** | "Restore from snapshot" (also called Restore) | **Recover** | MailStore's distinction — eliminates the overload. |
| **Restored placeholder account** | "Backup {name} ({date})" — suspended account | **Recovered mailbox: {name} ({date})** | Past tense + noun = clear status. |
| **Group-shared visibility** | "Group" | **Group** | Unchanged. |

## Term mapping table — IT

| Concept | Today's MFB term(s) | Proposal A — IT label | Notes |
|---|---|---|---|
| **Mailbox / Source** | "Account / Source" | **Casella** (mailbox) / **Sorgente** (technical) | "Casella" is the warm IT word; "Sorgente" only on tech fields. |
| **Mail mirror** | — (currently called "backup") | **Specchio** (mirror) | Direct translation works; metaphorical but clear. Alternative: **Copia locale**. |
| **Mirror now / schedule** | "Sincronizza" / "Sync" | **Aggiorna** ("update") + **Pianificazione specchio** | Avoids "sincronizza" (long) and "sync" (anglicism). |
| **Storage volume** | "Store" | **Volume** | Direct cognate. |
| **Repository / Repo** | "Backup Destination" | **Repository** (loanword, technical IT users accept) | Avoid "deposito" (Proposal C uses this — see comparison). |
| **Back up (verb) / Backup (noun)** | "Backup", overloaded | **Esegui backup** (verb) / **Backup** (noun) | Italian doesn't have the two-word/one-word split; verb gets the prefix. |
| **Backup policy** | (none) | **Politica di backup** | |
| **Snapshot** | "Snapshot" | **Snapshot** | Loanword universally accepted in IT tech context. |
| **Retention policy** | "Retention" | **Politica di retention** (or **Conservazione**) | "Retention" is loanword; "conservazione" is the formal IT word. |
| **Restore (mailbox)** | "Restore" | **Ripristino** | Standard IT word. |
| **Recover (depot-side)** | "Restore from snapshot" | **Recupera** | Different verb avoids overload. |
| **Recovered mailbox** | "Backup {name}" | **Casella recuperata: {nome} ({data})** | |
| **Group** | "Group" | **Gruppo** | |

---

## Sample copy (before / after)

**Empty Accounts page:**
- Before: "No accounts yet. Add your first email backup."
- After (EN): "No mailboxes yet. **Connect a mailbox** to start mirroring."
- After (IT): "Nessuna casella. **Collega una casella** per iniziare lo specchio."

**Account-detail "Offsite Backup" section heading:**
- Before: "Offsite Backup"
- After (EN): "Off-site backup → Repository"
- After (IT): "Backup off-site → Repository"

**Admin/Backup page title:**
- Before: "Backup Destinations"
- After (EN): "Repositories"
- After (IT): "Repository"

**Per-account status pill (when backed up):**
- Before: "Backed up 5 minutes ago"
- After (EN): two pills: "Mirrored 5 min ago · Backed up 6 h ago"
- After (IT): "Specchiato 5 min fa · Backup 6 h fa"

**"Restore" success flash:**
- Before: "Snapshot restored as 'Backup Andrea (2026-05-10)'"
- After (EN): "Recovered into mailbox 'Recovered Andrea (2026-05-10)'. The mailbox is suspended — review and enable when ready."
- After (IT): "Recuperato nella casella 'Andrea recuperata (2026-05-10)'. La casella è sospesa — verifica e abilita quando pronto."

---

## Pros

- **Recognisable:** users coming from restic / Kopia / Vorta / Borg see a familiar word ("Repository").
- **Consistent:** "Mirror / Repository / Snapshot" cleanly maps to the three artifacts.
- **Verb discipline:** eliminates the sync/backup overload by retiring "sync" and constraining "backup" to mean the off-site action only.
- **The "Mirror" word reframes stage 2** — users stop expecting it to be the disaster-recovery layer.
- **Resolves the restore overload** with "Recover" vs "Restore".
- **Storage volume** decouples the filesystem-path concept from the data-path concepts.

## Cons

- **"Repository"** is technical. P4 (Gmail refugee) may not know it. Mitigate with empty-state copy ("Repository = the remote storage location for your snapshots").
- **"Mirror"** is metaphorical. Some users may think it means "live two-way sync" (it's one-way). Mitigate with copy ("a one-way mirror that keeps your mail safe locally").
- **Italian "Specchio"** has a faint religious / fairy-tale ring; some Italian users may feel it's not technical enough.
- **DB rename cost:** `BackupDestination` → `Repository`, `AccountBackup` → `BackupPolicy`. MED cost (Alembic + tests + a few service files).
- **"Storage volume"** introduces a new word for an old concept; even though "Store" is internal jargon, renaming it has admin-page churn.

## Risks

- Existing users (and competitor analysts) may anchor on "Backup Destination" and not transition. **Mitigation:** keep "Backup destination" as a recognisable subtitle on the Repository admin page for 1-2 releases.
- "Mirror" may collide with mailbox-software vocabulary (e.g., Outlook "mirror folders"). **Mitigation:** prefix with "Mail" when the context isn't obvious — "Mail mirror".

## Effort estimate

- Label rename in templates: **S** (~50-80 strings).
- New empty-state copy + tooltips: **M**.
- Renaming `BackupDestination` model + `AccountBackup` table: **M-L** (Alembic migration, ~30 references, ~390 tests to verify).
- Documentation rewrite: **M**.

**Total: M-L for full adoption; S-M for labels-only adoption.**
