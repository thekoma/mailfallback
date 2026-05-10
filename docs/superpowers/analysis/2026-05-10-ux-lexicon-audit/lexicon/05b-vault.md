# Lexicon Proposal B — "Vault" (consumer-friendly, trust-leaning)

**Voice:** Reassuring, accessible, consumer-grade.
**Audience optimised for:** P4 (Gmail refugee), P5 (first-hour install), P2 (family IT helper).
**Risk profile:** Bolder — coins distinct words, optimises for trust over technical precision.

---

## The four-stage model

```
┌─────────┐   pull    ┌──────────────┐   push    ┌──────────────┐   capture   ┌──────────────┐
│ MAILBOX │──────────▶│ FALLBACK     │──────────▶│ VAULT        │────────────▶│ SNAPSHOT     │
│ Source  │  copy     │ COPY         │  protect  │ Off-site     │             │ Saved point  │
│         │           │ Live local   │           │ encrypted    │             │              │
└─────────┘           └──────────────┘           └──────────────┘             └──────────────┘
   noun: mailbox          noun: fallback copy      noun: vault                  noun: snapshot
   verb: connect          verb: copy / refresh     verb: protect                verb: save / take
```

**Verb discipline:** "Refresh" / "Copy" replace "sync" for stage 2; "Protect" replaces "backup" as a verb for stage 3 (the noun "backup" is retired entirely). "Save" / "take" for snapshots.

This proposal is the most aggressive break from the restic / Kopia world. It's also the most aligned with **the product name "MailFallBack"**: stage 2 is literally called the "fallback copy". The product's own name does the lexical heavy lifting.

---

## Term mapping table — EN

| Concept | Today's MFB term(s) | Proposal B |
|---|---|---|
| **Source IMAP** | "Source account", "Provider" | **Mailbox** (the user's email account at Gmail/etc.) |
| **Source connection settings** | "IMAP Host / Port" | unchanged |
| **Local copy** | "Maildir", "Store path", "Backup" | **Fallback copy** (or "Local copy") |
| **The action of refreshing the local copy** | "Sync now", "Backup now" | **Refresh now** / **Refresh schedule** |
| **Local filesystem path** | "Mail Store" / "Stores" | **Storage volume** |
| **Remote depot** | "Backup Destination" | **Vault** |
| **The action of saving to the vault** | "Backup" | **Protect now** (verb) — and the per-mailbox config is a **Protection plan** |
| **Per-account config** | `AccountBackup` | **Protection plan** |
| **Point-in-time** | "Snapshot" | **Saved point** (consumer-warm) — OR keep **Snapshot** as a less-warm but more-recognised alternative |
| **Retention** | "Retention preset" | **Retention rule** (consumer phrase from Backupify et al.) |
| **Restore (mailbox-side)** | "Restore" | **Restore** |
| **Restore (depot-side)** | "Restore from snapshot" | **Bring back** (or "Recover") |
| **Recovered placeholder account** | "Backup {name}" suspended | **Recovered mailbox: {name} ({date})** |
| **Group** | "Group" | **Shared mailbox** (when a group sees many mailboxes) — adopted from MailStore vocabulary |

## Term mapping table — IT

| Concept | Today's MFB term(s) | Proposal B — IT |
|---|---|---|
| **Mailbox** | "Account" | **Casella** |
| **Fallback copy** | (currently called "backup") | **Copia di fallback** (or **Copia locale**) |
| **Refresh now / schedule** | "Sync" | **Aggiorna** + **Programma di aggiornamento** |
| **Storage volume** | "Store" | **Volume** |
| **Vault** | "Backup Destination" | **Cassaforte** (literal IT for "vault" — strong, evocative, well understood) |
| **Protect now (verb)** | "Backup now" | **Proteggi ora** |
| **Protection plan** | (none) | **Piano di protezione** |
| **Snapshot / Saved point** | "Snapshot" | **Snapshot** (loanword) or **Punto salvato** |
| **Retention rule** | "Retention" | **Regola di conservazione** |
| **Restore** | "Restore" | **Ripristino** |
| **Bring back / Recover** | "Restore from snapshot" | **Recupera** |
| **Recovered mailbox** | "Backup {name}" | **Casella recuperata: {nome}** |

---

## Sample copy (before / after)

**Empty Accounts page:**
- Before: "No accounts yet. Add your first email backup."
- After (EN): "No mailboxes yet. **Connect a mailbox** to start your fallback copy."
- After (IT): "Nessuna casella. **Collega una casella** per iniziare la tua copia di fallback."

**Account-detail "Offsite Backup" heading:**
- Before: "Offsite Backup"
- After (EN): "Vault — off-site protection"
- After (IT): "Cassaforte — protezione off-site"

**Admin/Backup page title:**
- Before: "Backup Destinations"
- After (EN): "Vaults"
- After (IT): "Cassaforti"

**"Backup Now" button:**
- Before: "Backup Now"
- After (EN): "Protect now"
- After (IT): "Proteggi ora"

**Status pill:**
- Before: "Backed up 5 minutes ago"
- After (EN): "Refreshed 5 min ago · Protected 6 h ago"
- After (IT): "Aggiornata 5 min fa · Protetta 6 h fa"

**"Restore" success flash:**
- Before: "Snapshot restored as 'Backup Andrea (2026-05-10)'"
- After (EN): "Brought back into 'Recovered Andrea (2026-05-10)'. Suspended pending review."
- After (IT): "Recuperata in 'Andrea recuperata (2026-05-10)'. Sospesa in attesa di verifica."

---

## Pros

- **Maximally trust-leaning vocabulary** — "Vault", "Protect", "Recovered" are the words consumers use when they're scared of losing data.
- **Reinforces the product name** — "MailFallBack" + "fallback copy" = the user understands the product before clicking anything.
- **Unambiguous verbs:** "Refresh" and "Protect" never collide. "Backup" the noun is gone.
- **Italian "Cassaforte"** is one of the strongest words in the lexicon — every Italian speaker knows what a cassaforte is. Bigger emotional payload than "deposito" or "repository".
- **"Bring back"** for restore-from-snapshot is the human verb. P4-friendly.
- **Onboarding leverage:** every word teaches the model.

## Cons

- **Risk of looking too consumery** for P1 (Andrea) and P3 (small-org IT). Andrea may sneer at "Cassaforte". Mitigate with admin-mode tooltips that show the technical equivalent ("Vault = restic repository at s3://...").
- **"Saved point"** is awkward. Most likely "Snapshot" survives even in this proposal because retiring it is too costly.
- **"Protect"** is a verb claim some users will hold MFB to: protect against what? Mitigate with explicit copy ("Protect = a versioned, encrypted copy off-site").
- **DB rename cost is the same as Proposal A** — `BackupDestination` → `Vault`, `AccountBackup` → `ProtectionPlan`. MED.
- **Loses recognisability for users coming from Kopia / Vorta / restic** who would search for "Repository". Mitigate with subtitle.
- **Italian "Casella" for both source mailbox AND recovered mailbox** is fine but the recovered one needs a clear suffix.

## Risks

- "Vault" is also a HashiCorp product. Some users may misread (low risk; context disambiguates).
- "Bring back" may feel infantile. Acceptable alternative: "Recover".
- Retiring "Backup" entirely as a noun is invasive. Some external docs / forum threads about MFB will reference the old term for years.

## Effort estimate

- Label rename in templates: **S-M** (slightly more strings to change because verbs are renamed too).
- Empty-state and tooltip copy: **M-L** (this proposal leans on copy more than A).
- DB renames: **M** (same as Proposal A).
- Documentation rewrite: **L** (the marketing voice changes too).

**Total: M-L; this is also the proposal with the highest copy-quality investment because the words have to earn the trust they imply.**
