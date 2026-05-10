# Lexicon Proposal C — "Deposito" (Italian-flavoured, hybrid)

**Voice:** Italian-first; the Italian word leads, the English label tracks.
**Audience optimised for:** P1 (Andrea, Italian owner), Italian users at large; degrades gracefully to English.
**Risk profile:** Distinctive — leans into the product's Italian origin and uses the Italian noun where it carries weight.

This is the proposal the user opened the audit with: **Sorgente → Backup locale → Deposito remoto → Snapshot periodiche.** This document treats those four words as the seed and proposes a coherent system around them, with English equivalents.

---

## The four-stage model (verbatim from the user)

```
┌─────────┐   pull    ┌──────────────┐   push    ┌──────────────┐   capture   ┌──────────────┐
│ SORGENTE│──────────▶│ BACKUP       │──────────▶│ DEPOSITO     │────────────▶│ SNAPSHOT     │
│ Source  │           │ LOCALE       │           │ REMOTO       │             │ Periodic     │
│         │           │ Local backup │           │ Remote depot │             │              │
└─────────┘           └──────────────┘           └──────────────┘             └──────────────┘
   noun: sorgente         noun: backup locale       noun: deposito              noun: snapshot
   verb: collega          verb: aggiorna            verb: deposita              verb: scatta
```

**Critical caveat:** This proposal **keeps the word "backup" in stage 2** ("backup locale"). This is a deliberate choice on the user's part. To avoid the overload that opened the audit, stage 3 must use a **different** word — hence "Deposito" — and the noun "backup" never appears alone, always qualified ("backup locale" or "backup off-site / al deposito").

---

## Term mapping table — IT (primary)

| Concept | Today's MFB term(s) | Proposal C — IT |
|---|---|---|
| **Stage 1 — source IMAP** | "Source", "Provider" | **Sorgente** (always with qualifier when ambiguous: "casella sorgente") |
| **Stage 1 — connection settings** | "IMAP Host / Port" | unchanged |
| **Stage 2 — local copy** | "Maildir", "Store", "Backup", "Sync" | **Backup locale** (the noun) — verb: **aggiorna** |
| **Stage 2 — action** | "Sync now" | **Aggiorna ora** |
| **Stage 2 — schedule** | "Sync schedule" | **Pianificazione backup locale** |
| **Stage 2 — filesystem path** | "Mail Store" / "Stores" | **Volume** (admin nav: "Volumi") |
| **Stage 3 — remote depot** | "Backup Destination" | **Deposito** (admin nav: "Depositi") |
| **Stage 3 — action** | "Backup", "Backup now" | **Deposita ora** (verb) — the per-account config is a **Profilo di deposito** |
| **Stage 3 — per-account config** | `AccountBackup` | **Profilo di deposito** |
| **Stage 4 — point-in-time** | "Snapshot" | **Snapshot** (loanword — universal) |
| **Stage 4 — retention** | "Retention preset" | **Politica di conservazione** |
| **Restore (mailbox-side)** | "Restore" | **Ripristino** |
| **Restore (depot-side)** | "Restore from snapshot" | **Recupera dal deposito** |
| **Recovered placeholder account** | "Backup {name}" | **Casella recuperata: {nome} ({data})** |
| **Group** | "Group" | **Gruppo** |

## Term mapping table — EN (secondary)

| Concept | Proposal C — EN |
|---|---|
| Source IMAP | **Mailbox** / **Source** |
| Local copy | **Local backup** (kept verbatim — it's the user's chosen English) |
| Refresh now | **Refresh** (or stay literal: "Update local backup") |
| Filesystem path | **Volume** |
| Remote depot | **Depot** |
| Per-account config | **Depot profile** |
| Snapshot | **Snapshot** |
| Retention | **Retention policy** |
| Restore (mailbox) | **Restore** |
| Restore (depot) | **Recover from depot** |

---

## The qualifier discipline

Proposal C only works if "backup" is **never used alone** as a noun. Every appearance must be qualified:

- "Backup locale" (the local mirror) ✓
- "Backup al deposito" / "Backup off-site" (the snapshot job) ✓
- "Backup" (alone) ✗ — banned

This is harder to enforce than retiring "backup" entirely (Proposals A and B). It also requires a **lint rule or PR-review check** to prevent regression. **Cost of enforcement is real.**

---

## Sample copy (before / after)

**Empty Accounts page:**
- Before: "No accounts yet. Add your first email backup."
- After (IT): "Nessuna casella. **Collega una casella** per iniziare il backup locale."
- After (EN): "No mailboxes yet. **Connect a mailbox** to start the local backup."

**Account-detail headings:**
- Before: "Offsite Backup"
- After (IT): "Deposito (off-site)"
- After (EN): "Depot (off-site)"

**Admin/Backup page title:**
- Before: "Backup Destinations"
- After (IT): "Depositi"
- After (EN): "Depots"

**"Backup Now" button:**
- Before: "Backup Now"
- After (IT): "Deposita ora"
- After (EN): "Deposit now" — *or* "Back up now" (the qualifier disambiguates because the page context is the depot section)

**Status pill:**
- Before: "Backed up 5 minutes ago"
- After (IT): "Aggiornata 5 min fa · Deposito: 6 h fa"
- After (EN): "Refreshed 5 min ago · Last depot: 6 h ago"

**"Restore" success flash:**
- Before: "Snapshot restored as 'Backup Andrea (2026-05-10)'"
- After (IT): "Recuperata in 'Andrea recuperata (2026-05-10)'. La casella è sospesa — verifica e abilita."
- After (EN): "Recovered into 'Recovered Andrea (2026-05-10)'. The mailbox is suspended — review and enable."

---

## Pros

- **Aligned with the user's mental model from minute one.** The four words come from Andrea's own framing.
- **Italian-led design** — for a project where the owner and the primary contributors think in Italian, and where the Italian word is concrete (Deposito = a place where things are deposited; well-understood physical metaphor).
- **"Deposito"** is dignified and slightly formal — works for compliance angles (P3) without being overly corporate.
- **Keeps "backup" with a qualifier** — avoids the cost of teaching users to forget the word.
- **"Snapshot"** stays — universal.
- **Italian "Volumi" for stores** is more user-friendly than "Stores" or "Mail Stores".
- **Lower DB-rename cost than A or B** — `BackupDestination` → `Depot` is one rename; `AccountBackup` → `DepotProfile` is one rename.
- **Reinforces brand identity** — MFB is an Italian self-hosted project; the lexicon doesn't need to ape American SaaS conventions.

## Cons

- **English depot** is a less-common word in software — users coming from restic/Kopia/Vorta will look for "Repository" first.
- **"Backup locale" + "Deposito"** still keeps two related words in play. Discipline (the qualifier rule) must be enforced.
- **Verb "Deposita"** is unusual; "Deposit" in English software UX feels banking-flavored. May be confusing to non-native speakers.
- **Risk of looking parochial** for the international audience MFB might one day target.
- **No clear English idiomatic verb** for "deposita" — "Deposit", "Send to depot", "Push to depot" all feel awkward.

## Risks

- The **qualifier discipline** is fragile. One PR slipping in "Backup failed" without the qualifier reintroduces the overload. **Mitigate with** a lint rule (`grep -E '\bbackup\b' templates/ | grep -v 'backup locale\|backup off'`).
- For non-Italian users, "Deposito" requires explanation. The empty-state copy must teach the term explicitly.

## Effort estimate

- Label rename in templates: **M** (qualifiers everywhere — more strings touched than Proposal A).
- Empty-state copy + tooltips: **M**.
- DB renames: **S-M** (one model rename).
- Documentation rewrite: **M**.
- Lint rule for qualifier discipline: **S**.

**Total: M.** Slightly cheaper than A and B at the implementation level; slightly more expensive at the maintenance level (qualifier discipline).

---

## Bilingual asymmetry note

Proposal C is the only proposal where the **Italian label is the source of truth** and the English label is derived. Proposals A and B treat the lexicons as parallel and equally authoritative. If MFB will ever be released in a fully internationalised form (gettext / fluent), Proposal A or B is more conventional. If MFB stays Italian-led indefinitely, Proposal C is the most natural.
