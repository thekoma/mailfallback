# Mockups — before / after

ASCII mockups for the highest-leverage screens, showing the current state next to a proposed redesign. Where the proposed copy depends on a lexicon proposal, both Proposal A (Repository) and Proposal C (Deposito) labels are shown — Proposal B (Vault) is omitted from mockups (pick from the lexicon doc; the IA changes are the same).

These mockups are **not pixel-perfect** — they show IA, content hierarchy, and copy. Implementation detail (exact widget styling) is not the point.

---

## 1. Chain widget (the load-bearing new component)

Every page should let the user point at where they are in the chain. This is the single most important new component.

### Variant A — sticky sub-header

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ ●Source ──→ ●Mirror ──→ ●Repository ──→ ●Snapshot                         │
│  3 connected   3 ✓ today   2/3 healthy   17 saved                          │
└─────────────────────────────────────────────────────────────────────────────┘
```

- Each `●` is a colored dot: green (healthy), yellow (stale/warning), red (failed), grey (off).
- Clicking each segment navigates to its primary screen (Mailboxes / Mirror status / Repositories / Snapshots).
- The whole bar is **dismissible** for power users; default-on for new users for first 30 days.

### Variant B — dashboard hero card (shown only on Dashboard)

```
┌───────────────────────────────────────────────────────────────────┐
│ Your mail safety chain                                            │
│                                                                   │
│   📬 SOURCE        🪞 MIRROR        🏛️ REPOSITORY     📸 SNAPSHOT │
│   3 mailboxes ──→  Local copy ──→   Off-site repo  ──→ 17 saved  │
│   2 OAuth, 1 IMAP  Last: 5 min ago  Last: 6 h ago     ↑ daily    │
│                                                                   │
│   STATUS: ✓ Healthy chain. 1 mirror lagging — last refresh > 2h. │
└───────────────────────────────────────────────────────────────────┘
```

The dashboard hero teaches the model. The sticky variant reinforces it on every page.

---

## 2. Empty Dashboard (first-hour user, P5)

### CURRENT

```
┌─────────────────────────────────────────────────────────┐
│ Dashboard                                               │
├─────────────────────────────────────────────────────────┤
│  [Accounts: 0] [Messages: 0] [Storage: 0 B]             │
│  [Errors: 0]   [Users: 1]    [Stores: 1]                │
│                                                         │
│  Needs Attention                                        │
│  (nothing)                                              │
│                                                         │
│  Recent Activity                                        │
│  (nothing)                                              │
└─────────────────────────────────────────────────────────┘
```

The user sees six zero stats and two empty panels. No teaching, no next step.

### PROPOSED (Proposal A — "Repository")

```
┌──────────────────────────────────────────────────────────────────┐
│ Dashboard                                                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Welcome to MailFallBack.                                        │
│                                                                  │
│  MFB keeps your mail safe in two places:                         │
│   1. A local mirror on this server (always available)            │
│   2. Off-site snapshots in a repository you control (disaster    │
│      recovery)                                                   │
│                                                                  │
│  Start by connecting a mailbox.                                  │
│                                                                  │
│           ┌──────────────────────────────┐                       │
│           │ + Connect a mailbox          │                       │
│           └──────────────────────────────┘                       │
│                                                                  │
│  Already have a repository configured? You can import existing   │
│  snapshots later from the Repositories admin page.               │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### PROPOSED (Proposal C — "Deposito", Italian)

```
┌──────────────────────────────────────────────────────────────────┐
│ Dashboard                                                        │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Benvenuto in MailFallBack.                                      │
│                                                                  │
│  MFB tiene al sicuro la tua posta in due posti:                  │
│   1. Un backup locale su questo server (sempre disponibile)      │
│   2. Snapshot in un Deposito off-site che controlli tu           │
│      (disaster recovery)                                         │
│                                                                  │
│  Inizia collegando una casella.                                  │
│                                                                  │
│           ┌──────────────────────────────┐                       │
│           │ + Collega una casella        │                       │
│           └──────────────────────────────┘                       │
│                                                                  │
│  Hai già un Deposito configurato? Puoi importare snapshot        │
│  esistenti dalla pagina Depositi.                                │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

The proposed empty state **teaches the two-place model in three sentences**, then shows the single next action.

---

## 3. Populated Dashboard

### CURRENT

```
┌────────────────────────────────────────────────────────────────────┐
│ Dashboard                                                          │
├────────────────────────────────────────────────────────────────────┤
│  [Accounts: 4] [Messages: 87,322] [Storage: 12 GB]                 │
│  [Errors: 1]   [Users: 2]         [Stores: 1]                      │
│                                                                    │
│  Needs Attention                                                   │
│   ⚠ luca@example.com — sync failed 2 h ago                         │
│                                                                    │
│  Recent Activity                                                   │
│   ✓ andrea@gmail.com synced — 12 messages — 5 min ago              │
│   ✓ marco@example.com synced — 0 messages — 1 h ago                │
│   ✗ luca@example.com sync failed — auth error — 2 h ago            │
└────────────────────────────────────────────────────────────────────┘
```

### PROPOSED (with chain widget hero)

```
┌────────────────────────────────────────────────────────────────────┐
│ ● Source ──→ ● Mirror ──→ ● Repository ──→ ● Snapshot              │
│  4 mailboxes  3/4 healthy  1/2 reachable    17 saved (oldest: 30d) │
└────────────────────────────────────────────────────────────────────┘
                                                                     │
┌────────────────────────────────────────────────────────────────────┐
│ Dashboard                                                          │
├────────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ Mail safety chain — STATUS: ⚠ Attention needed                │ │
│  │  Mirror: 1 of 4 mailboxes lagging (luca@example.com — 2h)     │ │
│  │  Repository: rustfs-s3 unreachable since 10:30                │ │
│  │  Snapshots: last for 'andrea' 6 h ago — within retention      │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  4 mailboxes · 87,322 messages · 12 GB local · 4.3 GB off-site     │
│                                                                    │
│  Needs Attention                                                   │
│   ⚠ luca@example.com — mirror failed (auth error) — 2 h ago        │
│   ⚠ rustfs-s3 repository — unreachable since 10:30                 │
│                                                                    │
│  Recent Activity                                                   │
│   ✓ andrea@gmail.com mirrored — 12 new — 5 min ago                 │
│   ✓ andrea@gmail.com snapshotted — 6 h ago                         │
│   ✓ marco@example.com mirrored — 0 new — 1 h ago                   │
│   ✗ luca@example.com mirror failed — 2 h ago                       │
└────────────────────────────────────────────────────────────────────┘
```

Key changes:
- **Chain widget at top** — universal navigation + status
- **Mail safety chain card** — the dashboard's headline replaces the 6 stat cards. Stats are a single sentence below.
- **Off-site storage stat** is shown alongside local storage.
- **Activity feed distinguishes mirror events from snapshot events** — the verbs are different.
- **"Errors: 1"** stat card is gone — replaced by "Needs Attention" structured items that say WHAT to fix.

---

## 4. Account detail page

### CURRENT (truncated)

```
┌──────────────────────────────────────────────────────────────────────┐
│ ‹ andrea@gmail.com                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ [Hero: ● syncing — 3 of 12 folders done]                             │
│                                                                      │
│ Info                                                                 │
│   Provider: gmail                                                    │
│   IMAP: imap.gmail.com:993                                           │
│   Auth: oauth2                                                       │
│   Store: default-store                                               │
│   Maildir: /data/mailboxes/{uuid}                                    │
│                                                                      │
│ ▼ Mailbox Stats                                                      │
│ ▶ Ownership & Visibility                                             │
│ ▶ Edit Account                                                       │
│ ▶ Migrate Store                                                      │
│ ▶ Offsite Backup                            ← BURIED, NEVER OPENED   │
│ ─── danger zone ───                                                  │
│ ▶ Delete Account                                                     │
│                                                                      │
│ Sync History                                                         │
│   ✓ 5 min ago — 12 messages                                          │
└──────────────────────────────────────────────────────────────────────┘
```

The "Offsite Backup" section is at position 5 of 6 — collapsed by default, between "Migrate Store" and the danger zone. Most users never click it.

### PROPOSED (Proposal A — "Repository", Vorta-inspired tab order)

```
┌──────────────────────────────────────────────────────────────────────┐
│ ‹ andrea@gmail.com                                                   │
├──────────────────────────────────────────────────────────────────────┤
│ [Per-account chain widget]                                           │
│  ●Source       ●Mirror        ●Repository    ●Snapshots              │
│   imap.gmail   ✓ 5 min ago    rustfs-s3 ✓    17 (oldest 30d)         │
│                                                                      │
│ ▼ Mirror (this is where most users live)                             │
│   Live status, last sync, schedule, errors. The current "hero".      │
│                                                                      │
│ ▼ Off-site backup → Repository                ← PROMOTED, OPEN BY    │
│   Repository: rustfs-s3 (last back-up 6 h ago)   DEFAULT WHEN        │
│   Schedule: 0 2 * * * (daily 02:00)              CONFIGURED          │
│   Retention: standard (30d / 12w / 6m)                               │
│   17 snapshots — newest 6 h ago — oldest 30 d                        │
│   [Back up now]  [Edit policy]                                       │
│   ▶ Snapshots (17)   — expandable list, restore actions              │
│                                                                      │
│ ▶ Source connection                                                  │
│   Provider, IMAP host, auth, OAuth re-link. Read-only by default.   │
│                                                                      │
│ ▶ Storage volume                                                     │
│   Where the mirror lives on disk. Migrate to a different volume.     │
│                                                                      │
│ ▶ Ownership & sharing                                                │
│   Owners, group visibility.                                          │
│                                                                      │
│ ─── danger zone ───                                                  │
│ ▶ Delete mailbox                                                     │
│                                                                      │
│ Activity log                                                         │
│   ✓ Mirrored 5 min ago — 12 messages                                 │
│   ✓ Snapshotted 6 h ago — repo rustfs-s3 — 23 MB                     │
└──────────────────────────────────────────────────────────────────────┘
```

Key changes:
- **Per-account chain widget at top** — local context.
- **Section order follows the chain**: Mirror → Off-site → Source → Storage → Sharing → Danger.
- **Off-site is the SECOND section, expanded by default if configured**, instead of buried at position 5.
- **"Storage volume"** replaces "Migrate Store" — cleaner separation between the data and where it lives.
- **Activity log** unifies sync history + backup history with verb discipline.

---

## 5. Admin / Backup → "Repositories"

### CURRENT

```
┌──────────────────────────────────────────────────────────────────────┐
│ ☁ Backup Destinations                                                │
├──────────────────────────────────────────────────────────────────────┤
│ Manage offsite backup destinations for account maildir data. Each   │
│ destination is a restic repository backed by S3 or a local path.    │
│                                                                      │
│ ⊕ Add Destination                                                    │
│  ┌──────────────┐                                                    │
│  │ Type: [S3 ▼] │                                                    │
│  │ Name: ___    │                                                    │
│  │ ...long form ...                                                  │
│  └──────────────┘                                                    │
│                                                                      │
│ ┌──────────┬─────────┬──────────┬──────────┬──────────┐              │
│ │ Name     │ Type    │ Endpoint │ Accounts │ Actions  │              │
│ ├──────────┼─────────┼──────────┼──────────┼──────────┤              │
│ │ rustfs   │ S3      │ s3://... │ 1        │ ⋯        │              │
│ │ local    │ local   │ /backup  │ 0        │ ⋯        │              │
│ └──────────┴─────────┴──────────┴──────────┴──────────┘              │
└──────────────────────────────────────────────────────────────────────┘
```

### PROPOSED (Proposal A)

```
┌──────────────────────────────────────────────────────────────────────┐
│ 🏛️ Repositories                                                      │
├──────────────────────────────────────────────────────────────────────┤
│ Off-site storage targets where mailbox snapshots are kept. Each     │
│ repository is encrypted independently (restic). Mailboxes pick a    │
│ repository in their backup policy.                                  │
│                                                                      │
│ ┌─────────┬──────┬───────────┬────────┬──────────┬─────┬─────────┐  │
│ │ Name    │ Type │ Endpoint  │ Mboxes │ Snapshots│ Size│ Health  │  │
│ ├─────────┼──────┼───────────┼────────┼──────────┼─────┼─────────┤  │
│ │ rustfs  │ S3   │ s3://...  │   3    │   17     │ 4.3G│ ✓ OK    │  │
│ │ local   │ local│ /backup   │   0    │   0      │  -  │ ⚠ empty │  │
│ └─────────┴──────┴───────────┴────────┴──────────┴─────┴─────────┘  │
│                                                                      │
│ + Add a repository    (wizard — Kopia-style: Type → Connect → Test) │
└──────────────────────────────────────────────────────────────────────┘
```

Key changes:
- Title → "Repositories" (Proposal A) / "Depositi" (Proposal C) / "Vaults" (Proposal B)
- **Health column** — surfaces unreachable / TLS-failing repos.
- **Snapshots and size columns** — the status quartet from competitor research.
- **"Mboxes" column** replaces "Accounts" — small rename from concept change.
- **Wizard for adding** instead of long inline form — Kopia pattern.

---

## 6. /restore page (the most overloaded)

Today's /restore mixes IMAP-to-IMAP restore (a maintenance flow) with the implicit understanding that it's where you "restore from backup". The two are different. The proposal **splits the page** by the actual flow.

### PROPOSED — entry chooser

```
┌──────────────────────────────────────────────────────────────────────┐
│ Restore mail                                                         │
├──────────────────────────────────────────────────────────────────────┤
│ What do you want to do?                                              │
│                                                                      │
│  [ 📸 Recover from a snapshot ]                                       │
│      Bring back mail from an off-site snapshot into a new mailbox.   │
│      Use after data loss.                                            │
│                                                                      │
│  [ 🔄 Move mail between mailboxes ]                                   │
│      Copy or move messages from one IMAP server to another.          │
│      Use to consolidate or migrate accounts.                         │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

The first option uses the verb **Recover** (depot-side). The second uses **Restore / Move** (mailbox-side). Two flows, two entry points, no overlap.

The current /restore form moves under "Move mail between mailboxes" with no rewording (it works as-is).
The "Recover from a snapshot" path replaces the buried per-account snapshot list — promoted to a top-level workflow.

---

## 7. Account form (/accounts/new) — wizard split

### CURRENT
One long form: Provider, Name, Email, Host, Port, TLS, Auth type, Credentials, Auth mechs, advanced fields. The user makes 10+ decisions on one screen.

### PROPOSED (Kopia-inspired wizard)

```
Step 1: Pick your provider
   ( ) Gmail    ( ) Outlook    ( ) Yahoo    ( ) iCloud    ( ) Other IMAP

Step 2: Sign in
   [If OAuth provider] → "Sign in with Google" button → OAuth flow
   [If IMAP] → email + app password

Step 3: Test the connection
   ✓ Connected
   ✓ Found 12 folders
   ✓ Approx. 4.3 GB of mail to mirror
   [Looks good — start mirroring]

Step 4: First mirror running
   ⏳ This may take a while. We'll email you when it's done.
   [View progress]
```

Key changes:
- **One decision per screen** (Kopia gold standard).
- **imapsync's "preview before commit" pattern** — the test step shows folders + size estimate.
- **Step 4 explicitly says it's still running** — addresses the "is it done?" anxiety.

---

## 8. Per-account status pill (compact)

### CURRENT

Status pills today: "syncing", "first-sync", "error", "idle", "paused", "sign-in-needed", "migrating". One pill per account, on the accounts list.

### PROPOSED (two pills per row)

```
┌────────────────────┬──────────────────────────────────┬─────────────┐
│ Mailbox            │ Mirror              · Repository │ Actions     │
├────────────────────┼──────────────────────────────────┼─────────────┤
│ andrea@gmail.com   │ ✓ 5 min ago         · ✓ 6 h ago  │ ⋯           │
│ marco@example.com  │ ✓ 1 h ago           · — none     │ ⋯           │
│ luca@example.com   │ ✗ failed 2 h ago    · ✓ 1 d ago  │ ⋯           │
└────────────────────┴──────────────────────────────────┴─────────────┘
```

Two-column status: "Mirror" pill + "Repository" pill. Each one independently colored. Users see at a glance which stage is healthy and which is not.

---

## 9. Notification microcopy

| Today | Proposal A (EN) | Proposal C (IT) |
|---|---|---|
| "Backup configuration saved" | "Backup policy saved for {mailbox}" | "Profilo di deposito salvato per {casella}" |
| "Backup started" | "Snapshot started — saving to {repo}" | "Snapshot avviato — salvataggio su {deposito}" |
| "Backup failed" | "Mirror failed for {mailbox}: {reason}" or "Snapshot failed for {mailbox}: {reason}" | "Backup locale fallito per {casella}" / "Snapshot fallito per {casella}" |
| "No backup configured" | "Off-site backup not yet set up" | "Backup off-site non ancora configurato" |
| "Snapshot restored as 'Backup X (date)'" | "Recovered into 'Recovered X (date)'. The mailbox is suspended — review and enable when ready." | "Recuperato in 'X recuperata (data)'. Casella sospesa — verifica e abilita." |

The notifications are where the lexicon discipline pays off most — every flash message becomes specific.

---

## What's NOT in these mockups (deliberately deferred)

- A full /backup admin overhaul beyond the title rename and the columns.
- The wizard for "Add a repository" — sketched at high level only.
- Role-specific dashboards (admin vs user variant).
- Per-org reporting view (P3 stretch goal).

These belong in a follow-on design pass after the lexicon and IA decisions land.
