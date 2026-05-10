# Recommendation — synthesised proposal

This is the audit's terminal document. It picks one strategic scope, one lexicon proposal, and one rollout shape, and explains the reasoning. A future `make-plan` should be able to consume this without reopening the design.

---

## TL;DR — the recommendation

1. **Scope: Option B (lexicon + IA promotion).** No DB rename. Roughly 4 weeks of single-developer work in 4 waves.
2. **Lexicon: Proposal C (Deposito) — adopted as the baseline because it is the user's own framing, with one targeted hybrid graft from Proposal A: use "Repository" / "Repo" as the English label for "Deposito" in the bilingual term-mapping table.** This gives Italian users `Deposito` and English users `Repository` without forcing a non-standard English word.
3. **Lock the chain widget in. Sticky variant on every page; the dashboard hero variant on the empty dashboard.**
4. **Fix the four worst overpromise strings before anything else** — they're security-adjacent.
5. **Defer the model rename** (`BackupDestination` → `Repository`) to a follow-on Option C, gated on actual P3 demand.

---

## Why Option B beats A, C, and D

| | Option A — labels only | **Option B — labels + IA** | Option C — full restructure | Option D — docs only |
|---|---|---|---|---|
| Lexicon win | Yes | **Yes** | Yes | No |
| IA win | No | **Yes** | Yes | No |
| Risk of redo | HIGH | **LOW** | MED | HIGHEST |
| Effort | 1 wk | **4 wks** | 10 wks | 2 d |
| Personas helped | P1 | **P1, P2, P4, P5** | All five | P1 |

Option A is rejected because the dominant findings from Phase 4 critiques are **IA failures, not lexicon failures**: the off-site is buried (UX critique), the chain isn't visualised (UX + user-advocate), the restore-as-suspended-mailbox limbo is unresolved (support sev-1), the dashboard doesn't teach (user advocate). Renaming labels around these IA failures leaves them in place.

Option C is rejected for now because P3 (small-org IT) is not yet a validated audience. The compliance report and the model rename are large, partially-irreversible bets. They become correct **after** P3 demand surfaces. Until then, B's IA work is fully reusable as a foundation for C.

Option D is rejected because docs only reach 5% of users; the other 95% read the GUI.

---

## Why Proposal C with a Repository graft

Phase 5 produced three coherent lexicons. Phase 4 critiques pulled in two directions:

- **The Italian voice (kickoff + i18n critique)** wants the user's own framing: Sorgente, Backup locale, Deposito, Snapshot.
- **The competitor research (03-competitor-landscape)** wants alignment with restic / Kopia / Vorta / Borg, where "Repository" is consensus.

A pure-IT vocabulary loses recognisability for the international audience; a pure-restic vocabulary loses Andrea's mental model. **The graft resolves both:** keep the Italian-led Proposal C as the conceptual spine, but use **"Repository"** as the English equivalent for **"Deposito"**, instead of the awkward "Depot" originally proposed in 05c.

### Final term-mapping table (binding)

| Concept | IT label | EN label | Notes |
|---|---|---|---|
| Source IMAP server | **Sorgente** | **Source** | Used only on technical fields; the warm noun is "Casella". |
| The user's email account at Gmail/etc. | **Casella** | **Mailbox** | The warm noun. |
| Local copy on this server | **Backup locale** | **Local backup** | Verbatim from kickoff. Verb: aggiorna / refresh. |
| Action — refresh local copy | **Aggiorna ora** | **Refresh now** | Replaces "Sync now". |
| Schedule for local refresh | **Pianificazione backup locale** | **Local backup schedule** | |
| Filesystem path for the local copy | **Volume** | **Storage volume** | Replaces "Mail Store". Admin nav: "Volumi" / "Volumes". |
| Off-site repository | **Deposito** | **Repository** | Compromise: IT keeps the user's framing, EN aligns with restic/Kopia. Admin nav: "Depositi" / "Repositories". |
| Action — push to repository | **Deposita ora** | **Back up now** | One verb each language. EN keeps "back up" (two words); IT uses Deposita. |
| Per-account off-site config | **Profilo di deposito** | **Backup policy** | Per-mailbox: which repository, which schedule, which retention. |
| Point-in-time | **Snapshot** | **Snapshot** | Universal; loanword in IT. |
| Retention | **Politica di conservazione** | **Retention policy** | |
| Restore (mailbox-side, IMAP→IMAP) | **Ripristino** | **Restore** | The /restore page flow. |
| Recover (depot-side, snapshot→new mailbox) | **Recupera** | **Recover** | Distinct verb to eliminate the overload. |
| Recovered placeholder mailbox | **Casella recuperata: {nome} ({data})** | **Recovered mailbox: {name} ({date})** | Suspended pending review. |

This is the **binding lexicon** for the rollout. `LEXICON.md` should be added to the repo root with this table.

### Qualifier discipline

"Backup" alone is **banned** as a noun. Always: "backup locale", "backup off-site al deposito", "recupera dal deposito". A pre-commit / CI lint check should fail if `git diff` introduces `\bBackup\b` (case-insensitive) without one of the qualifiers, in templates or routers. Cost: ~1 day to set up, prevents indefinite regression.

---

## The chain widget — final spec

**Sticky variant** (always on, every page):

```
●Source ──→ ●Local backup ──→ ●Deposito ──→ ●Snapshot
```

- 4 colored dots (green / yellow / red / grey).
- Counts under each: "N caselle / N healthy / N depositi / N snapshot".
- Each segment is a link to its primary screen.
- Sticky below the system-status bar.
- Dismissible per-user; default-on for users with `created_at < 30 days ago`.

**Dashboard hero variant** (only on empty dashboard):

The teaching version with full descriptions of each stage. Shown when accounts.count == 0.

**Per-account variant** (only on /accounts/{id}):

Same shape as sticky, but scoped to one mailbox.

This widget is the largest single new component in Option B and the most defining new addition.

---

## Four "before anything else" fixes (security-adjacent)

These four come out of the security and sysadmin critiques and should land in **Wave 1** before the lexicon rename, because they fix lies the UI is currently telling:

1. **"Backup configured" badge → "Off-site policy set"**. Today's badge implies safety; truth is "a policy exists; we don't know if any back-up succeeded". Replace with a status that includes "last successful back-up: X ago" or a clear "no successful back-up yet" state.

2. **"insecure_tls" toggle on Repository forms → labelled "Skip TLS certificate verification — self-signed CA only"**, with a yellow warning banner when enabled. Today the toggle is a single checkbox with no visible severity.

3. **"Snapshot restored as 'Backup X (date)'" success flash → "Recovered into 'Recovered X (date)'. The mailbox is suspended. Review and enable when ready, or delete to drop the recovered data."** Today's message implies "done"; the recovered mailbox sits in limbo and is the support team's #1 sev-1 ticket.

4. **Document the SECRET_KEY + Postgres double-loss scenario.** A new docs page: "What you need to keep safe to recover MFB itself." Restic snapshots are useless without `MAILFALLBACK_SECRET_KEY` (used to encrypt the restic password) and a Postgres backup (which holds the `BackupDestination` rows). This isn't surfaced anywhere today and is the single biggest disaster-recovery footgun.

---

## Specific UI changes to ship in Option B (the contract)

### Wave 1 — Honesty + foundations (week 1)

- The four security-adjacent fixes above.
- Add `LEXICON.md` to repo root with the binding term table.
- Add a CI check or pre-commit hook for the qualifier discipline.
- Write a "What MFB really does" doc page (one page; the four-stage model + lexicon definition).

### Wave 2 — Lexicon rename (weeks 2–3)

- Find/replace the 50–80 user-facing strings per the binding table.
- Notification microcopy rewrite (ref `06-mockups.md` §9).
- Update flash messages to be specific (which system failed; what to do).
- "Mail Store" → "Volume" rename in admin pages and account detail.

### Wave 3 — IA promotion (weeks 3–4)

- Account detail section reorder: Local backup → Off-site backup (open by default if configured) → Source → Volume → Sharing → Danger.
- Two-pill status on Accounts list (Local backup pill + Repository pill).
- /restore page entry chooser (two cards: "Recupera da snapshot" / "Sposta posta tra caselle").

### Wave 4 — Chain widget + empty states (week 4)

- Sticky chain widget component, present on every page.
- Empty-Dashboard redesign with the teaching copy.
- Empty-Accounts redesign ("Collega una casella per iniziare").
- Repository admin page status columns (snapshot count, total size, health).

After Wave 4, Option B is shipped. Option C waves 5–6 are the upgrade path.

---

## Out of scope of this rollout (deliberate)

- DB table renames (`BackupDestination`, `AccountBackup`). Stays as legacy until Option C.
- New wizard for adding a Repository — current inline form continues.
- Per-org compliance report (P3 stretch).
- Audit log signing / immutability (security stretch).
- Kopia-style account-creation wizard (P5 stretch — covered in mockups but not in Option B).
- "Promote restored maildir to live" button (mentioned in sysadmin critique as the biggest DR UX win — *should be added to Option C*).

---

## Risk register and mitigations

| Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|
| Users miss the rename and complain about familiar labels | MED | LOW | Release notes + a "what changed" entry in the docs. |
| Qualifier discipline regresses without lint check | HIGH | MED | Pre-commit hook (Wave 1, day 1). |
| Chain widget feels gimmicky | LOW | LOW | Make it dismissible per-user. |
| Off-site promotion confuses existing users (off-site moves position) | LOW | LOW | First wave includes "What changed in this release" toast on next login. |
| The team picks Option C halfway through | MED | MED | Be explicit at Wave 4 that the project is shippable; Option C is a separate commit. |
| Italian/English term divergence makes search docs harder | LOW | MED | The `LEXICON.md` becomes the docs canonical reference; both languages link to it. |

---

## How to know this worked

After Wave 4, the audit's success criteria:

1. **First-hour test:** put a fresh user in front of the empty dashboard. Within 60 seconds, can they tell what MFB does and what to click? Today: no. Target: yes.
2. **Off-site discoverability test:** show an existing user the new account-detail page. Within 30 seconds, can they find where to set up off-site backup? Today: no. Target: yes.
3. **Failure-recovery test:** make sync fail for one account. Within 30 seconds, can the user identify which account and that it was a sync failure (not a backup failure)? Today: no. Target: yes.
4. **Restore test:** ask a user "what does it mean to recover from a snapshot?". Today: confusion. Target: clear "creates a recovered mailbox in suspended state for review".
5. **Lexicon test:** read the GUI top to bottom. Find any usage of bare "backup" without qualifier. Today: ~20 sites. Target: zero.

The first four are user tests. The fifth is a grep.

---

## What this audit deliberately did not do

- **Decide which features to add.** This audit assumed the feature set as-shipped (mbsync sync, restic offsite, IMAP restore) and only addressed how it's named, organised, and explained.
- **Spec the implementation.** That's `make-plan` territory. The mockups are guidance, not implementation.
- **Re-litigate the "is this a backup or an archive" identity question.** Phase 3 / NAS-homelab competitor pass argued for "mirror with fallback". This audit accepts that framing and bakes it into Proposal C.
- **Build a new feature for P3.** Compliance reporting is gated on real demand; the lexicon rollout doesn't need it.

---

## Handover

The next step is `make-plan` against this recommendation. The plan should:

- Map each Wave above to a phased implementation plan.
- Identify the specific files and string-locations from `scratch/vocab-inventory.md` for the rename pass.
- Specify the `LEXICON.md` schema and the lint/CI check.
- Sequence the four "before anything else" fixes ahead of the lexicon work.
- Estimate test coverage delta (notification text changes need test updates).

A separate proposal — once P3 demand is real — can extend this with the Option C waves (DB rename, wizard, compliance report, "promote recovered mailbox" button).
