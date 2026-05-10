# MFB UX & Lexicon Audit — May 10, 2026

A multi-agent audit of MailFallBack's user-facing vocabulary and information architecture. Branch: `analysis/lexicon-ux-2026-05-10`.

**The recommendation is `08-recommendation.md`. Start there.** The other documents are evidence and reasoning that supports it.

---

## What this audit produced

Eight numbered documents (sequential reading order), three subdirectories of supporting material, and one final synthesis.

```
00-kickoff.md                     ← Why and how (start of the day)
01-current-state.md               ← What MFB looks like today (synthesis of scratch/)
02-personas-and-journeys.md       ← Who uses MFB, walking each through it
03-competitor-landscape.md        ← What others call these things (synthesis of competitors/)
07-strategic-options.md           ← A/B/C/D scope choices
08-recommendation.md              ← THE binding proposal — start here
09-iteration-principal-designer.md ← Principal-designer red team
09-iteration-engineer.md          ← Senior-engineer red team

critiques/
  04-pm.md                ← Product-manager voice
  04-ux.md                ← UX designer voice
  04-sysadmin.md          ← On-call sysadmin voice
  04-user-advocate.md     ← User-voice transcripts (P4, P2)
  04-security.md          ← Security architect voice
  04-i18n.md              ← Bilingual copy editor voice
  04-support.md           ← Tier-2 support engineer voice

lexicon/
  05a-repository.md       ← Lexicon Proposal A: technical/restic-aligned
  05b-vault.md            ← Lexicon Proposal B: consumer/trust-leaning
  05c-deposito.md         ← Lexicon Proposal C: Italian-led (the chosen baseline)

mockups/
  06-mockups.md           ← Before/after ASCII for key screens

competitors/
  01-mailstore-family.md  ← MailStore Home / Server, mail-archiver
  02-saas-backup.md       ← Backupify, Spanning, AvePoint, Afi, SkyKick
  03-oss-sync.md          ← isync/mbsync, OfflineIMAP, imapsync, MailPiler, Cyrus
  04-backup-ux.md         ← restic, Vorta, Duplicati, Kopia, Time Machine, Synology
  05-nas-homelab.md       ← Synology MailPlus, QNAP, Mailcow, NethServer/Piler

scratch/                  ← Raw discovery outputs (referenced, not authoritative)
  vocab-inventory.md      ← 718 references catalogued, 25 sites of confusion
  ia-routes.md            ← 60+ routes mapped to screens
  data-model.md           ← Models, enums, naming friction, rename costs
  runtime-ops.md          ← Schedulers, workers, failure modes, honesty audit
  config-surface.md       ← 42 env vars, 5 docker services, docs site map
  recent-history.md       ← 30-day commit digest, maturity assessment
```

---

## The headline finding

**"Backup" is the single biggest source of confusion**, used 148 times across templates and routers for two distinct concepts:
1. The local mbsync mirror ("Start first backup", "Backed up 5 minutes ago")
2. The restic offsite system ("Backup Destination", "Offsite Backup")

**"Destination" is the second-biggest** (43 occurrences) — also overloaded between the restic remote depot and the IMAP target during /restore.

**"Snapshot", "Store", "Maildir" are clean** — single, consistent meanings. Preserve them.

---

## The recommendation in one paragraph

Adopt the user's own four-stage framing (**Sorgente → Backup locale → Deposito → Snapshot**) as the conceptual spine, with English equivalents (**Source → Local backup → Repository → Snapshot**) where the English-speaking audience needs them. Do this as part of a 4-wave, 4-week IA + lexicon promotion (Strategic Option B). Add a sticky "chain widget" to every page so the user always knows where they are in the chain. Defer the DB-table rename (`BackupDestination` → `Repository`) to a future Option C, gated on actual P3 (small-org IT) demand. Land four security-adjacent honesty fixes in Wave 1 before the lexicon rename — they prevent the UI from telling lies users are currently believing.

---

## How the audit was run

- **Workday:** 2026-05-10, ~2 hours autonomous.
- **Mode:** parallel multi-agent dispatch with role personas (PM, UX, sysadmin, user advocate, security, i18n, support).
- **Phases:** 0 (kickoff) → 1 (discovery, 6 parallel) → 2 (personas) → 3 (competitor landscape, 5 parallel) → 4 (role critiques, 7 parallel) → 5 (3 lexicon proposals) → 6 (mockups) → 7 (strategic options) → 8 (recommendation) → 9 (red-team iteration).
- **Constraints:** analysis-only (no code changes); English deliverables with bilingual lexicon section; dedicated branch with incremental commits.

---

## What happens next

The recommendation hands off to a future `make-plan`. That plan should:

1. Map each of Wave 1–4 to a phased implementation plan.
2. Identify the specific files and string locations from `scratch/vocab-inventory.md` for the rename pass.
3. Specify the `LEXICON.md` schema and the lint/CI check.
4. Sequence the four "before anything else" honesty fixes ahead of the lexicon work.
5. Estimate test coverage delta.

A separate, later proposal can extend with Option C (DB rename, wizard, compliance report, "promote recovered mailbox" button) once P3 demand is real.

---

## Honest caveats

- This is **analysis, not implementation**. The mockups are guidance; pixels and exact component composition are for the implementer.
- The lexicon graft (Italian "Deposito" + English "Repository") is a compromise that may displease purists. The principal-designer red team in `09-iteration-principal-designer.md` argues whether this fudge holds up.
- The 4-week estimate is from the recommendation. The engineer red team in `09-iteration-engineer.md` reality-checks it against the actual codebase surface.
- The audit deliberately did NOT re-litigate the "is this a backup or an archive" identity question. It accepted the "mirror with fallback" framing from competitor research and built on it.
