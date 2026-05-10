# MFB UX & Lexicon Audit — Kickoff

**Date:** 2026-05-10
**Branch:** `analysis/lexicon-ux-2026-05-10`
**Mode:** Autonomous multi-agent workday (~2h, target ≥500 interactions)
**Scope:** Analysis & docs only. No code changes.
**Owner of decisions:** Andrea (review at 13:00).

---

## Why this audit exists

The user observation that opened it:

> "Il termine 'backup' è usato lascamente in MFB ma dobbiamo ragionare sui termini usati in MFB per non fare confusione. Vanno corrette le label e dobbiamo trovare un linguaggio chiaro per guidare l'utente. Il concetto è (per singolo account): **Sorgente → Backup locale → Deposito remoto → Snapshot periodiche**. Questa cosa andrebbe prodotto anche in grafica."

What this audit must produce, in priority order:

1. A **canonical 4-stage data-flow model** that every screen reinforces.
2. A **lexicon** (IT + EN) that maps every current label to a final term, with a clear term-disambiguation table.
3. **Mockups** of the key screens after the rename and the addition of a flow diagram.
4. A **strategic options** document ranking how invasive the change should be.
5. A **synthesized recommendation** with a phased rollout.

Out of scope today: implementation, DB migrations, route renames. Those become a separate `make-plan` after Andrea approves.

---

## The four-stage mental model (working hypothesis)

```
┌─────────┐   pull    ┌──────────────┐   push    ┌──────────────┐   capture   ┌─────────────┐
│ SORGENTE│──────────▶│ BACKUP LOCALE│──────────▶│ DEPOSITO     │────────────▶│ SNAPSHOT    │
│ Source  │  mbsync   │ Local backup │  restic   │ REMOTO       │  scheduled  │ (point-in-  │
│ IMAP    │           │ Maildir +    │           │ Remote depot │  retention  │  time)      │
│ server  │           │ retention    │           │ S3 / disk    │             │             │
└─────────┘           └──────────────┘           └──────────────┘             └─────────────┘
                       owns: mbsync,             owns: restic repo,            owns: restic
                       Dovecot read-only,        encrypted, dedup,             snapshots,
                       grace period for          credentials.                  retention
                       deletes.                                                 policies.
```

This model has to be **load-bearing**: every page should make the user able to point at where in this chain they currently are.

---

## Personas roster (used by all critique phases)

| ID | Name | Profile | Primary need |
|---|---|---|---|
| P1 | Andrea (homelab admin) | Self-hosts on K8s, reads code, is the owner. | Total control, low UI friction, observability. |
| P2 | Family IT helper | Manages mailboxes for parents/spouse on a NAS. | "Just works", clear status, recoverable on disaster. |
| P3 | Small-org IT (5–50 users) | Replaces Backupify-like tooling for a co-op or NGO. | Compliance angle: prove the data is safe. |
| P4 | Ex-Gmail refugee | Wants to escape Gmail, wants their archive forever. | Trust: "is my mail really mine, retrievable?" |
| P5 | New install, first hour | Anyone going through the empty-state experience. | Discoverability: what is this thing, how do I use it? |

---

## Roles for the multi-agent run

Each phase dispatches role-tagged agents. Roles:

- **PM** — feature inventory, scope discipline, prioritization.
- **UX designer** — flows, IA, screen-level critique, mockups.
- **Sysadmin** — operational realism, failure modes, backups-of-backups.
- **User advocate** — talks like the personas, voices their confusion.
- **Security** — threat model on each new term/flow.
- **i18n / copy editor** — Italian and English clarity.
- **Competitor analyst** — landscape research, lexicon comparisons.
- **Principal designer** — final synthesizer, plays bad cop on weak proposals.

---

## Workday agenda

| Phase | Window | Deliverable |
|---|---|---|
| 0 | 11:00–11:05 | This kickoff, branch, dirs. |
| 1 | 11:05–11:35 | `01-current-state.md` — current vocabulary + IA inventory. |
| 2 | 11:35–11:55 | `02-personas-and-journeys.md`. |
| 3 | 11:55–12:15 | `03-competitor-landscape.md`. |
| 4 | 12:15–12:35 | `critiques/04-*.md` — one per role. |
| 5 | 12:35–12:50 | `lexicon/05-*.md` — three proposals. |
| 6 | 12:50–13:05 | `mockups/06-*.md` — ASCII per screen. |
| 7 | 13:05–13:15 | `07-strategic-options.md`. |
| 8 | 13:15–13:25 | `08-recommendation.md` (synthesis). |
| 9 | 13:25–13:?? | Iterate until 13:00 wall clock or convergence. |

(Phase windows are aspirational — actual time depends on agent latency. The hard stop is the 13:00 wall clock.)

---

## How "interactions" are counted

Each agent dispatch = 1 interaction. Each Bash/Read/Edit/Write inside this main session = 1 interaction. Target: ≥500. The number is a heuristic for **depth**, not a literal KPI; if convergence happens earlier, the next round goes into critique-of-critique.

---

## Exit criteria

A successful audit ends with:

- A single **`08-recommendation.md`** that a future me could hand to `make-plan` without further design discussion.
- A **rename mapping table** complete enough that a future PR could grep-replace label-by-label.
- **Mockups** detailed enough that a frontend dev knows the IA without re-asking.
- An honest **risk register**: what could backfire, what we deliberately chose not to do.
