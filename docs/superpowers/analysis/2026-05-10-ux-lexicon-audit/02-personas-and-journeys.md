# Personas & journeys

This document defines the people MFB exists for, then walks each of them through the product. It is the load-bearing input for the lexicon and IA decisions in later phases. **No persona is fictional in the bad sense — each maps to real homelab/self-hosting users we can plausibly name.**

---

## How these personas were chosen

Three forces shape MFB's audience:

1. **Self-hostability bias.** MFB is Docker / K8s / Compose. Anyone who installs it can edit YAML and read logs.
2. **Mail criticality.** Email is the most boring critical service — everyone has it, almost nobody curates it. The product addresses regret, not enthusiasm.
3. **The Gmail exit.** Many self-hosters' first encounter with the topic is "I want my Gmail archive" or "I'm leaving Google".

The five personas below cover the realistic span. We avoid the corporate-ECM persona on purpose — MFB is not eDiscovery / journaling / GDPR-archive software, even though some terms overlap.

---

## P1 — Andrea (the homelab owner)

**Role:** Senior Python engineer, 15+y experience. Runs K8s at home with Authentik SSO. Builds MFB.
**Tech literacy:** Expert. Reads source.
**What they want:** Total control. Low UI friction once configured. Strong observability when things break.
**What they fear:** Silent failure; "it said it worked but didn't"; magic that hides what's happening.
**Mental model:** Already correct. Knows the difference between a Maildir, an mbsync run, and a restic snapshot.
**Lexicon tolerance:** High. Will accept jargon if it's accurate; rejects jargon that's wrong or imprecise.

**What MFB does for P1 today:** mostly fine.
**What MFB risks doing wrong:** dumbing down language to the point P1 can't tell what's happening underneath. P1 needs jargon to be *available* even if it's not the default surface.

---

## P2 — Marco (family IT helper)

**Role:** Sysadmin or developer who hosts services for parents/spouse on a NAS or small home server.
**Tech literacy:** High personally; **acts as proxy** for users with low literacy.
**What they want:** "Just works" for the family member. "Click here to recover" if something blows up. Clear status they can check at a glance once a month.
**What they fear:** Getting a 22:00 phone call from mom asking why she can't see emails from 2018. Dataloss that costs an emotional debt to admit.
**Mental model:** Mostly correct, but doesn't always remember which step does what. Treats MFB like fire-and-forget once configured.
**Lexicon tolerance:** Medium. Words have to be *self-explanatory* because Marco may not be the one reading them six months from now.

**What MFB does for P2 today:** the offsite backup feature is exactly what they want, but they can't tell at a glance whether it's running and they don't know what "Backup Destination" means without clicking.
**What MFB risks doing wrong:** treating the dashboard as "for Andrea" rather than "for Marco".

---

## P3 — Giulia (small-org IT, 5–50 users)

**Role:** Sole IT person at a co-op, NGO, or small consultancy. Replacing or augmenting Backupify-like SaaS.
**Tech literacy:** Solid sysadmin. Less of a coder.
**What they want:** A defensible answer to "is our mail safe?" they can show their director. A retention policy they can document. Per-user evidence that backups happened.
**What they fear:** A board meeting where someone asks "and what if Google deletes our mailboxes?" and they have no slide.
**Mental model:** Compliance-ish. Thinks in policies, retention windows, audit trails.
**Lexicon tolerance:** Medium. Wants serious words ("retention", "snapshot", "depot") but not impenetrable jargon.

**What MFB does for P3 today:** missing — there's no per-org reporting view, no retention-policy presets framed as compliance, no "evidence" pane.
**What MFB risks doing wrong:** the product reads as a "tinkerer's toy" because the language is loose. Fix the words and Giulia's perception shifts.

---

## P4 — Luca (Gmail refugee)

**Role:** Individual user leaving (or having left) Gmail. Often a long-time Gmail user with 15+ years of mail. Found MFB via a "self-host your email backup" forum thread.
**Tech literacy:** Variable. Can run docker-compose. Can't necessarily debug a Dovecot ACL.
**What they want:** Move years of Gmail to a place they own. Browse it. Search it. Sleep better.
**What they fear:** Losing the archive in transit. Discovering, six months later, that the "backup" didn't include attachments. Discovering the local Maildir requires a still-paid Gmail account to read it.
**Mental model:** Largely wrong. Conflates "backup" and "archive". Doesn't know the local copy is browsable via Roundcube. Doesn't realize the local copy itself can be lost — needs an offsite too.
**Lexicon tolerance:** Low. Words have to mean what consumers think they mean.

**What MFB does for P4 today:** has the capability but the UI doesn't market the local-copy + offsite story clearly. The first-run experience does not teach the model.
**What MFB risks doing wrong:** Luca configures only "the backup feature" (= restic offsite) without realizing the local Maildir is the primary copy. Or vice versa: configures the local copy only, doesn't realize it's still single-point-of-failure.

---

## P5 — "First Hour" (any new install)

Not a person — a moment. The first sixty minutes after `docker compose up -d`.
**State:** Logged in as `admin`/`changeme`. Dashboard is empty. No accounts. No stores configured. Has read at most one paragraph of docs.
**What they want:** to know what to do next.
**What they fear:** typing the wrong thing into a credential field; getting locked out; deleting the wrong thing.
**Mental model:** none. Whatever the empty state says, that becomes their model.
**Lexicon tolerance:** zero. Every word is a teaching moment.

**What MFB does for P5 today:** The empty state on /accounts says "Add your first email backup". This is the **first time the user sees the word "backup"** — and what they'll add is in fact an *account*, which then enables a *sync*, which produces a *local copy*, which can later be *backed up offsite*. The current label is wrong.
**What MFB risks doing wrong:** wrong first impressions are sticky. Whatever P5 reads first becomes their lifelong vocabulary.

---

## Persona ranking by leverage

If we have to optimize the lexicon for one persona, optimize for **P5** (wins all five) and **P4** (the largest realistic prospective audience). Andrea and Marco will accept whatever lands as long as it's accurate. Giulia is a stretch goal that benefits as a side effect.

---

## End-to-end journeys

### Journey A — P5/P4: "First account, first sync, first hour"

**Steps the user actually takes today:**

1. `docker compose up -d`. Dashboard at :8000.
2. Login as admin / changeme. **Sees empty dashboard with stat cards (Accounts: 0, Messages: 0, Storage: 0 B, Errors: 0).**
3. Clicks "Accounts" in sidebar. Empty state: *"No accounts yet. Add your first email backup."*
4. Clicks the link → /accounts/new. Form asks for: provider, name, email, host, port, TLS, auth type, credentials.
5. Submits. Account row created. Status pill: "first-sync" (orange).
6. ~30s later, mail starts streaming in. User watches the sync log.
7. Clicks "Webmail" in sidebar. Logs in. **Sees their mail, locally, in Roundcube.** Mind blown.
8. Wonders what happens if the disk dies.
9. Clicks the account row. Sees a long detail page. Eventually finds the collapsed "Offsite Backup" section. Expands. Sees: "No offsite backup configured. *Add a destination first*." link to /admin/backup.
10. Goes to /admin/backup. Adds destination (S3 or local). Returns to account detail. Configures schedule + retention.
11. Clicks "Backup Now". Watches it run. Goes back to dashboard.

**Where the language fails them today:**

- Step 3: "Add your first email backup" — they just want to **connect** an email account. The word "backup" misframes the whole product. Better: *"Connect your first mailbox"*.
- Step 5: status "first-sync" — fine, but the chain hasn't been visualized yet. The user doesn't see where they are in the process.
- Step 9: the user discovers offsite backup BURIED inside the account-detail page. Two clicks deep. They may never see it. The product fails to teach the chain.
- Step 9 cont: "Backup Destination" — to most users this is the place files go, but the word is recent UX jargon. "Remote depot" / "Off-site repository" / "Cloud archive" all might land better.
- Step 10: "Backup Now" — backup of what? The user already had a local Maildir. They didn't know that was distinct from "backup".

### Journey B — P2: "I've been away three months"

User comes back to check on the family mail server.

1. Logs in. Looks at dashboard. Sees: Accounts 4, Messages 87,322, Storage 12 GB, Errors 0.
2. **Wants to know:** has each account been getting fresh mail? Did the offsite ever fail? Is the disk filling up?
3. Today, the dashboard surfaces "Recent Activity" (sync events) and a "Needs Attention" panel. Storage is shown as a single number. There is **no surface for offsite-backup health at the dashboard level** unless you remember to drill into each account.

**Where the language fails them:**
- "Errors" stat card lumps sync and backup errors together.
- "Storage" is local Maildir storage; offsite storage isn't shown anywhere on the dashboard.
- The notion of "this is healthy" vs "this is silently degraded" isn't presented as a status traffic light.

### Journey C — P3: "Audit week"

The director asks: "show me what we keep, where, for how long."

1. Logs in.
2. **Wants to produce a report:** per account, where the local copy lives (which store), what offsite is configured (which destination, what schedule, what retention), when the last successful sync and the last successful snapshot happened, how much space each consumes.
3. Today, this requires opening every account-detail page and copying numbers. There is no "report" view.

**Where the language fails them:**
- "Retention" appears in MFB only as a preset name (light/standard/full). Not as a documented policy.
- The offsite "destination" doesn't expose its own size, snapshot count, etc., on the admin/backup page.
- Audit log exists but is action-flavored, not state-flavored.

### Journey D — P1: "Something broke at 03:00"

Sync failed silently overnight.

1. Logs in. Dashboard shows "Errors: 1".
2. **Wants:** find the failed account quickly, see the last log lines, decide next action (retry, fix credentials, ignore).
3. Today, there's a "Needs Attention" panel; works for sync errors. For backup errors, the AccountBackup row has `last_error` and `last_status` — but the dashboard doesn't surface backup failures separately. P1 has to know to drill in.

**Where the language fails them:**
- "Sync" failure vs "Backup" failure are conceptually different (the source IMAP failed vs the offsite repo failed) but the dashboard doesn't label them distinctly.

### Journey E — P4: "The disk died"

The local store is gone. Offsite snapshots survive.

1. User panics. Reinstalls MFB on a new disk (or new host).
2. Restores Postgres from a database dump (assumed — outside MFB scope).
3. Logs in. Sees the configured destinations and per-account backup configs.
4. Goes to /restore. Picks an account. Picks a snapshot. Restores.
5. **Today's behavior:** restore creates a *new suspended account* called "Backup {name} ({date})" with `imap_host="restored"`, `port=0`. The data lives at `{store_path}/.offsite-restore/{account.id}-{timestamp}/`.
6. **Open question for the user:** how do I make this restored account take the place of the original? How do I point Dovecot at the restored Maildir?

**Where the model fails them:**
- The chain "restore → suspended placeholder account → manual fix-up" is an Andrea-grade workflow. P4 is lost.

---

## Failure-mode shortlist (cross-cut from journeys)

| # | Failure | Persona that hits it | Today's surface |
|---|---|---|---|
| F1 | Sync fails silently overnight | All | Needs Attention panel + dashboard errors |
| F2 | Offsite backup fails silently | All | AccountBackup.last_error, only on account detail |
| F3 | Disk fills up | P1, P2 | Storage stat card; not actionable |
| F4 | Offsite repo unreachable (S3 down, TLS expired) | All | Generic "test failed" |
| F5 | User can't find the offsite feature | P5, P4 | Buried under collapsed `<details>` |
| F6 | User configures offsite but never the local sync (semantic confusion) | P5, P4 | Possible because "backup" is overloaded |
| F7 | User assumes restic snapshots include attachments — they do, but uncertainty exists | P4 | No copy explains it |
| F8 | User wants to delete an account, fears losing the offsite snapshots too | All | Currently unspecified UX |
| F9 | User wants to migrate offsite from local-disk depot to S3 | P3 | Currently no in-product path |
| F10 | Two accounts with same email but different stores → confusion | P2 | UI shows both; user can't tell |

---

## What this drives in later phases

- The lexicon must distinguish **the local copy** from **the offsite snapshots** at every glance.
- The dashboard must surface **chain health**, not just individual stats.
- The first-run / empty-state copy is the highest-leverage rewrite in the entire app.
- The /restore experience needs a P4-friendly path, not just an Andrea-friendly path.
- The admin/backup page needs to teach that *destinations are storage targets*, separate from per-account configs.

These are the inputs the role critiques (Phase 4), lexicon design (Phase 5), and mockups (Phase 6) will work against.
