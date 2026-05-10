# Strategic options — A / B / C / D

The lexicon and the IA fixes can be done at four different scopes. Picking the wrong scope is more dangerous than picking the wrong words.

This document presents four scopes — from "do almost nothing structural" to "rebuild the IA" — and ranks them on cost, risk, and time-to-value. The recommendation in `08-recommendation.md` picks one (with optional add-ons) and lays out the phased rollout.

---

## Decision dimensions

Each option is scored against:

- **Lexicon depth** — does it touch only labels, also code, also DB?
- **IA depth** — does it move sections, add new pages, add new components?
- **DR posture clarity** — does it materially make the user understand the chain?
- **Effort (calendar)** — wall time to complete, single-developer pace.
- **Risk of regret** — likelihood we have to redo the work in 6 months.
- **Persona payoffs** — who benefits and how much.

---

## Option A — Lexicon polish only ("paint the bikeshed")

**Description:** Rename user-facing strings in templates and flash messages to align with one of the three lexicon proposals. No DB changes. No IA changes. No new screens. The "Offsite Backup" section stays buried where it is. The dashboard stays the same. Just the words change.

**What ships:**
- 50–80 string replacements in `templates/*.html` and router flash messages.
- A short docs section explaining the new vocabulary.
- Updated MkDocs site to match.

**What does NOT ship:**
- No `BackupDestination` → `Repository` model rename.
- No chain widget.
- No dashboard rework.
- No /restore split.

**Effort:** 3–5 days (one developer).
**Risk of regret:** **HIGH.** The lexicon-only fix has no IA leverage — the user is still confused because the chain isn't visualised, the off-site is still buried, the empty dashboard still doesn't teach. We will do this work again. Worse, having renamed once, the *next* rename will look like indecision.
**Personas helped:** P1 (homelab admin) marginally, because labels are now precise. P5 (first hour) barely helped — the empty state still doesn't teach the model.

**When this is the right answer:** Never as a final state. Could be a stepping stone if Option B is approved but the team wants a fast first PR.

---

## Option B — Lexicon + IA promotion ("ship the chain")

**Description:** Adopt one lexicon proposal AND promote the off-site backup feature to a first-class concept. Add the chain widget (sticky variant). Rewrite the empty dashboard. Reorder the account-detail sections (off-site moves to position 2). Split /restore into the two-flow chooser. Keep DB names as-is (legacy `BackupDestination` etc.) but show the new labels in the GUI.

**What ships:**
- Lexicon rename: 50–80 strings.
- New chain widget component (Variant A — sticky sub-header).
- Empty dashboard redesign (teaching copy + single CTA).
- Account-detail section reorder + off-site section default-open when configured.
- /restore entry chooser (two cards: "Recover from snapshot" / "Move mail between mailboxes").
- Two-pill status on Accounts list (Mirror / Repository).
- Notification microcopy rewrite.

**What does NOT ship:**
- No model rename.
- No wizard for adding a repository (still inline form).
- No status quartet (size, snapshot count) on the Repositories admin page.
- No per-org reporting.

**Effort:** 3–5 weeks (one developer, includes copy review and a CSS pass).
**Risk of regret:** **LOW.** The IA changes are durable; the lexicon changes are durable; the model rename is the only thing deferred and it's pure tech-debt that doesn't user-facing matter. If we later decide to rename models, we can without redoing this work.
**Personas helped:** P1, P2, P4, P5 all materially. P3 partially (the chain widget gives them something to point at; the per-org report still doesn't exist).

**When this is the right answer:** **Default recommendation.** Best risk/effort/payoff balance. Includes the highest-leverage onboarding fix (empty dashboard) and the biggest perception fix (off-site no longer buried).

---

## Option C — Full restructure ("v1.0")

**Description:** Everything in Option B, plus rename DB tables (Alembic migration), build the Kopia-style wizard, build the status quartet on the Repositories page, build a per-org compliance report, and split the BackgroundTask UI surface. Position MFB as a v1.0 product.

**What ships beyond Option B:**
- `BackupDestination` → `Repository` table rename (Alembic).
- `AccountBackup` → `BackupPolicy` (or Proposal-specific) table rename.
- Repository wizard (Type → Connect → Test) — Kopia pattern.
- Status quartet on Repositories admin: snapshot count, total size, dedup ratio, next-scheduled run.
- Per-org compliance report (per mailbox: where, what schedule, what retention, last successful sync, last successful snapshot, sizes).
- Audit log additions (events from the security critique gap list).
- Re-shoot all docs site screenshots.

**Effort:** 8–12 weeks (one developer).
**Risk of regret:** **MEDIUM.** Most of the work is durable, but the per-org report is a P3 stretch goal; if MFB never gets P3 users, this is wasted code. The model rename is hard to reverse.
**Personas helped:** All five. Especially P3 (compliance report unlocks the "show this to my director" use case).

**When this is the right answer:** When the founder commits to actively pursuing the small-org IT (P3) audience, not just homelab users. If P3 is a "nice to have", Option B is better.

---

## Option D — Words stay, docs improve ("if you're tired")

**Description:** Don't change anything in the GUI. Instead, write a single high-quality "How MFB really works" page in the docs site that explicitly maps the four-stage model and the term mapping. Link to it from the dashboard footer. Add inline tooltips on the most-confusing labels.

**What ships:**
- One new docs page.
- Inline tooltip help on 5-8 labels.
- Footer link from dashboard to the docs page.

**Effort:** 2 days.
**Risk of regret:** **HIGHEST.** This treats the symptom, not the cause. Users still misread labels; the docs page is read by the 5% who are willing to read docs.
**Personas helped:** P1 (would read docs), P3 (would read docs). P2/P4/P5 won't read docs.

**When this is the right answer:** Stopgap for a product that's about to be sunsetted. **Not the right answer for MFB.**

---

## Comparison matrix

| Dimension | A | B | C | D |
|---|---|---|---|---|
| Lexicon depth | labels | labels | labels + DB | none |
| IA depth | none | medium | full | none |
| Chain visualised? | no | YES (sticky widget) | YES (widget + dashboard hero) | no |
| Empty-state teaches model? | no | YES | YES | doc only |
| Off-site promoted? | no | YES (section reorder) | YES (+ wizard) | no |
| /restore disambiguated? | partial (microcopy) | YES (entry chooser) | YES | no |
| DB rename? | no | no | YES | no |
| Compliance report (P3)? | no | no | YES | no |
| Effort (calendar) | 1 wk | 4 wks | 10 wks | 2 d |
| Risk of regret | HIGH | LOW | MED | HIGHEST |
| Reversibility | high | medium | low | high |
| **Net recommendation** | **stepping stone only** | **DEFAULT** | **if P3 is in scope** | **never** |

---

## Sequencing — if you pick B or C

The recommended phased rollout:

### Wave 1 (week 1, before any rename ships)
- Pick the lexicon proposal (A/B/C from `lexicon/`).
- Write the term-mapping table as a single source of truth.
- Add a CONTRIBUTING note: "When you write user-facing copy, use the Repository (or Vault, or Deposito) lexicon — see `LEXICON.md`."
- Tooling: optional — add a CI check that fails if `git diff` introduces forbidden words (e.g., `Backup Destination`).

### Wave 2 (weeks 2–3)
- Lexicon rename in templates (the 50–80 strings).
- Notification microcopy rewrite.
- Account detail section reorder + open-by-default for configured offsite.
- Two-pill status on Accounts list.

### Wave 3 (weeks 3–4)
- Chain widget (sticky variant) — implementation, CSS, polling logic.
- Dashboard hero card variant — alternative location for the chain.

### Wave 4 (week 4)
- Empty dashboard redesign.
- /restore entry chooser.
- Repository admin page status columns (snapshot count, size, health).

**Stop here for Option B.** Continue for Option C:

### Wave 5 (weeks 5–6) — Option C only
- DB rename: `BackupDestination` → `Repository` etc. Single Alembic migration. Test sweep.
- Repository wizard (Kopia pattern).

### Wave 6 (weeks 7–10) — Option C only
- Audit log additions.
- Per-org compliance report.
- Docs site screenshot refresh.

---

## What is NOT a strategic option

Things that have been mooted but are out of scope of this audit:

- **A full mobile-first redesign** — premature; usage data doesn't justify it yet.
- **Mark-down to React migration** — different problem entirely; HTMX is fine for this use case.
- **Multi-tenant SaaS-isation** — different product.
- **Dark mode** — already spec'd separately.
- **i18n with gettext** — already spec'd; should follow the rename, not precede it.
- **Splitting Account from Archiving Profile (à la MailStore)** — interesting idea raised in competitor research but adds significant complexity; defer until a clear use case emerges.

---

## Picking between A / B / C / D

Founder questions to settle before picking:

1. **Is P3 (small-org IT) a real audience or a hypothetical?** If real, lean to C. If hypothetical, B.
2. **How much developer time can the audit consume in the next month?** B fits in a month. C does not.
3. **Are we OK with the GUI labels diverging from the DB schema for a while?** If yes, B works without DB rename. If no, only A or C.
4. **Do we want the rename to land before the offsite backup feature ships to early users?** If yes, the lexicon rename (Wave 2) is the highest priority and should land first.
5. **Is there appetite for a CI check / lint rule to enforce the lexicon discipline?** If yes, Proposal C ("Deposito" with qualifier discipline) becomes feasible. If no, prefer Proposal A or B (less discipline-dependent).

The recommendation document folds these into a single proposed plan.
