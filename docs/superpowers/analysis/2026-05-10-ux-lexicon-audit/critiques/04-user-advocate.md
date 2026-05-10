# User advocate critique

> Voice: I am sitting next to Luca (P4) and Marco (P2). I watch them try to use MFB for the first time. Then I write down what they said out loud and what they didn't. The product team reads this and changes the words.

---

## Bottom line

- The product **calls a connection a "backup"**, then **calls a backup a "backup destination"**, then **calls a restore target a "destination account"**, and the user has to do the lexical disambiguation MFB refused to do for them. P4/P5 will not survive this.
- The **first user-facing string in the empty state** ("Add your first email backup") is the most important sentence in the product, and it's wrong. It pre-installs a mental model that takes the rest of the journey to undo.
- There is **a single trust moment** in the journey — the first time mail appears in Roundcube — and the product does nothing to celebrate or contextualize it. Land that moment harder and half the lexicon problems become survivable.

---

## "First hour as Luca" (P4 transcript)

I'm Luca. I had Gmail since 2007. Two weeks ago a Reddit thread said "Google can lock you out without warning, here's how to back up your archive." I bought a Synology, found MailFallBack, and tonight I'm running `docker compose up -d`. It's 22:00. I have wine.

The compose finishes. I go to `localhost:8000`. Login. `admin` / `changeme`. **[FAIL]** *Default credentials are in the README, not surfaced here; I had to alt-tab.* The dashboard loads.

It says: **Accounts: 0. Messages: 0. Storage: 0 B. Errors: 0.** And below that, in muted text: **"No accounts yet. Add your first email backup."** **[FAIL]** *I don't want to add a backup. I want to add my email account. The word "backup" makes me think this is the thing I came here to do — so OK, I click.*

I land on /accounts/new. The header says **"Add an email account"** and below: *"We'll back up your messages here. You can read them anytime, even if your provider is down."* OK — that sentence is good. Wait, but then why did the previous screen call it a backup? **[FAIL]** *Two pages, two different framings. The dashboard taught me "add a backup", this page corrected me to "add an account". Whose vocabulary do I trust?*

Email. Password. *"For Gmail, iCloud, Yahoo, and most providers, use an app password, not your normal one."* OK. I tab over to Google, generate one, paste it. Nickname: "Personal Gmail". I hit "Add Account".

The page reloads. There's my account. Status pill says **"first-sync"**. **[FAIL]** *Hyphenated lowercase machine-state, not words for me. Is that good? Bad? Am I waiting?* I refresh. Counter goes up. Numbers move. I open the account detail page.

The hero says **"First backup in progress…"** **[FAIL]** *Wait — this IS the backup? Then what was that "Offsite Backup" section I saw scrolling down?* Below: *"This may take a while — first backups download every message in your account."* **[FAIL]** *"First backups" plural. There are multiple? Are they running in parallel?*

I wait. Forty minutes. Counter hits 47,000 messages. Status flips to **"idle"**. **[FAIL]** *Idle sounds like idle, like nothing's happening. I want it to say "ready" or "done" or "you're safe now".*

I click **Webmail** in the sidebar. Roundcube loads. I log in with the same credentials. **And there's my mail. All of it. On my Synology.** I scroll back to 2008. There's the email from my dad about the car. He died in 2019. I have it on a hard drive in my house now.

That's the moment. **That's when I trust this product.** Nothing in MFB acknowledges this moment. **[FAIL]** *No banner, no "You now have a local copy of 47,000 messages — here's what that means". I figured it out by scrolling.*

I go back to the account page. There's a section called **"Offsite Backup"** collapsed at the bottom. **[FAIL]** *Offsite. Off — site. So the thing I just did was on-site backup? But the dashboard called THAT a backup too. Am I backed up or not? If the disk in this Synology dies tonight, what happens?*

I expand it. *"No offsite backup configured. Add a destination first."* link to /admin/backup. **[FAIL]** *"Destination". I just learned "destination" means S3 bucket. Five minutes from now I'll click /restore and "destination" will mean a different thing entirely.*

I go to admin. Add an S3 bucket I made on Backblaze. Come back. Click **"Backup Now"**. **[FAIL]** *Backup of what? The thing I already backed up? Is this making a backup of the backup?*

It runs. Snapshot count: 1. **[FAIL]** *"Snapshot" is the first word in this whole journey that means exactly one thing.*

It's 23:40. I close the laptop. I think I'm safe. **[FAIL]** *I think I'm safe. I'm not sure.*

---

## "First hour as Marco" (P2 transcript)

I'm Marco. My mother is 71. She's had `nome.cognome@libero.it` since the second Berlusconi government. Last month Libero went down for a week and she called me sobbing because she couldn't read messages from her sister in Argentina. I'm setting up MFB on the Proxmox box in the cantina so this never happens again.

I run the compose on a Saturday morning. I already know what `mbsync` and `restic` are; I just want the GUI for mum to use eventually. Login. Dashboard.

**"No accounts yet. Add your first email backup."** **[FAIL]** *I'm here for backup AND for offline read access. Calling the connection a "backup" undersells the second half. Mum won't care about backup; she'll care that the email opens when Libero is down.*

I add her IMAP account. Libero. App password (had to walk her through it on WhatsApp last week, that was a whole afternoon). The form is fine. **"Server settings"** is collapsed under a `<details>`. Good — I don't want her seeing IMAP ports if she ever logs in here.

I add the store. Wait, I don't add the store, there's already a default. But the "Stores" admin page exists. **[FAIL]** *"Store" in Italian retail context means "negozio". I have to mentally translate "store" → "where the maildir lives on disk". Mum would be lost. Even my brother who's a backend dev at a fintech would ask "store of what?".*

Sync starts. I'm watching the log scroll. 12,000 messages. Counter climbs. Hero says **"First backup in progress…"**. **[FAIL]** *I'm a sysadmin and even I had to think for two seconds whether this is mbsync or restic talking. The word "backup" in MFB is doing both jobs and I have to read context every single time.*

Done. I click into the account detail. There's a section **"Offsite Backup"** way at the bottom under a `<details>`. **[FAIL]** *Buried. If I weren't already looking for it, I'd close this tab thinking I'm done.* I expand it. *"No offsite backup configured. Add a destination first."* I go to /admin/backup, configure my Hetzner Storage Box. Come back. Run a snapshot. Good.

Now I want to test recovery. I go to /restore. **[FAIL]** *"Restore" in the sidebar is a separate flow. So the "restore from snapshot" I just looked at on the account page is NOT what /restore does? I have to click between two pages to do recovery and they share zero vocabulary.*

The /restore page has dropdowns: "Source account", "Destination account". **[FAIL]** *"Destination account" — the same word I just used five minutes ago to mean "S3 bucket". My brain has to stack-pop and re-bind "destination" to a different referent.*

I close the laptop. I tell myself I'll write a one-page printout for mum. **[FAIL]** *I wouldn't show this UI to a 71-year-old. The model is fine; the words betray it.*

I'd recommend MFB to friends. But I'd warn them: "ignore that word `backup` — they use it for two different things, you'll work it out".

---

## The five labels P4 most needs to read correctly

If P4 misreads any of these five, they either lose data or believe they are safe when they aren't.

| # | Where it appears | Current text | What it should say (so a non-technical user cannot misread) |
|---|---|---|---|
| 1 | `dashboard.html:90` empty state | "No accounts yet. Add your first email backup." | **"No mailboxes connected yet. Connect a mailbox to start saving a copy."** — kills "backup" at the door; uses "mailbox" (which Roundcube users already know) and "copy" (which everyone knows). |
| 2 | `account_detail.html:310` collapsed section | "Offsite Backup" | **"Off-site copy (in case this server dies)"** — names the failure mode. The parenthetical does more teaching than three docs pages. |
| 3 | `sync_panel.html:40-43` first action button | "Start first backup" | **"Start first download"** — the action is downloading mail to local disk. "Backup" can stay as the *outcome word* once we own the verb. |
| 4 | `sync_panel.html:222` after sync completes | hero state "idle" + "Last successful backup: X ago" | **"Up to date — your local copy matches your provider as of X."** — replaces machine-state "idle" with human reassurance; says explicitly *local copy*, *your provider*, so the user can distinguish from off-site. |
| 5 | flash message after restore (`ui_backup.py:411`) | "Snapshot restored as 'Backup X (date)'" | **"Restore complete — a recovered mailbox is waiting for you to activate it. Until you activate it, no mail is being delivered to it. [Activate now]"** — current wording invites the user to assume the restore is done; the suspended state is invisible until they try to log in. This is the single highest data-trust risk in the product. |

The pattern: every label should answer *what is this, why does it matter to me, what do I do next.* Five words is plenty.

---

## The sentence I want on the empty Dashboard

Three sentences. Replaces the entire empty-state block.

> **MailFallBack keeps a copy of your mailboxes on this server, so you can read them even when your provider is down or your account is locked.**
>
> **You haven't connected a mailbox yet — that's the first step.**
>
> **[ Connect a mailbox ]** ← big primary button

Breakdown:
- Sentence 1 (**what**): names the value — local copy, read-when-provider-is-down. The exact fear that brought P4 here.
- Sentence 2 (**next**): one action, no ambiguity. The word "first" creates a series; the user knows there's more to do later (off-site) without being overwhelmed now.
- Button (**reassurance**): "Connect" not "Add" not "Backup". You connect a mailbox — the verb a Gmail user already uses for IMAP setup on their phone.

Optional fourth line, smaller, after the button: *"Already have an off-site bucket (S3, Backblaze)? You can hook one up after your first mailbox is connected."* — Marco-mode escape hatch.

---

## Words that don't work for P4/P5

| Current word | Why it loses them | Suggested replacement |
|---|---|---|
| **backup** (for local mbsync) | They came here for backup. If you call the connection step "backup", they think they're done after step one. | **copy / local copy / download** |
| **backup** (for off-site restic) | Conflicts with the above. Two referents one word = no referent. | **off-site copy / cloud copy / snapshot vault** |
| **destination** (for restic depot) | Word means nothing concrete. Bucket? Folder? Address? | **storage location / bucket / cloud target** |
| **destination account** (in /restore) | Same word, different concept, three clicks away. | **target mailbox** |
| **store** (admin) | English-as-second-language users read "negozio". Even native speakers need a beat. | **disk location / mail folder root** |
| **maildir** | Unix jargon. P4 has never seen this word. | hide entirely from non-admin views, or **mail folder format (Maildir)** for admins |
| **first-sync** (status pill) | Hyphenated machine state, no verb. | **Downloading for the first time** |
| **idle** (status pill) | Sounds like the system gave up. | **Up to date** |
| **Offsite Backup** (section header) | Where Luca learns the on-site/off-site distinction one minute too late. | **Off-site copy (disaster recovery)** |
| **Backup Destinations** (admin page title) | Sounds like a list of *backup runs*, not a list of *places*. | **Cloud storage targets** or **Off-site repositories** |
| **Snapshot** | Actually fine — Luca learned it in context. | **keep** |
| **AccountBackup** (model) | Ambiguous: artifact or policy? | **OffsiteBackupPolicy** (or similar) — model-layer rename, see Phase 5 |
| **migrating** | Means "moving disk" but reads "moving to a new email provider". | **Moving files** |
| **Maildir migration** | Reads as a country crossing. | **Move to new disk location** |
| **Last sync attempted** (vs last successful) | Currently no distinction in UI. | distinguish: **Last update** vs **Last successful update** |

---

## Words that DO work

These are P4/P5-readable already. Phase 5 should not touch them.

- **Account** — universal. Everyone has accounts.
- **Mailbox** — Roundcube users already know it. Maps to IMAP folder in their head.
- **Webmail** — instantly recognised. Don't rebrand.
- **Password** / **App password** (with the link to Google's docs) — the "app password" tooltip is one of the genuinely good UX moments in the current product.
- **Dashboard** — universal, expected.
- **Sync now / Sync All** — the verb works as long as it stops competing with "backup" for the same referent.
- **Snapshot** — Luca learned it in context and it stuck. Bright spot in the lexicon.
- **Connect / Connection** (when it appears) — natural verb for IMAP setup.
- **Errors** stat card — direct, factual. Don't soften it.
- **Up to date / Out of date** (if introduced) — much better than "idle/syncing/error" pills.

---

## The trust moment

There is exactly one moment when P4 stops being skeptical and starts being grateful: **the first time they open Roundcube and see their old mail rendered locally**. It's the only point in the journey where the product proves its central claim with their own eyes.

Today, MFB does nothing to mark this moment. Roundcube is just another sidebar link. The user discovers the proof by clicking around.

**Proposal — make it land harder:**

1. **Auto-trigger a "first sync complete" celebration card on the dashboard** the first time a sync finishes successfully. Single big card:
   > **You now have a local copy of 47,238 messages from Personal Gmail.**
   > These messages are saved on this server and you can read them at any time, even if Gmail is down or your account is locked.
   > **[ Open in webmail → ]**   **[ Set up off-site copy → ]**

2. **The celebration card teaches the next step**: off-site backup is framed not as a separate feature but as "the second half of being safe". Two CTAs of equal weight; the user picks.

3. **Persist the card until acknowledged** so Marco can show it to mum a week later.

4. **Repeat at a smaller scale** every time a *new* account completes its first sync.

Why this works: the user's own data is the most persuasive UI element in the product. We're not asking them to trust copy or icons — we're showing them their own life, locally. Currently the moment happens but the product is silent. Make it speak.

---

## Things the current-state doc gets wrong from user angle

1. **"What's working today" lists 'sidebar IA is intuitive once you know the model'.** The phrase "once you know the model" is the bug. P5 has zero model. The IA is intuitive only to someone who has already finished reading the docs — i.e., to Andrea. For P4/P5 it isn't intuitive; it's discoverable through trial and error. The doc should not call this a strength; it's a tolerable weakness.

2. **"Snapshot is clean" is true for admins but invisible to the user who needs it most.** Snapshot is the cleanest term in the lexicon and P4 doesn't see it until they've already navigated three pages and configured an off-site destination. The doc treats snapshot as a "preserve" — agreed — but the recommendation should also be "expose snapshot as a first-class concept earlier". Hide the word "destination" behind "snapshot vault" or "snapshot location" so the clean term carries the unclean one.

3. **The honesty audit lists "Backup configured" as a problem but understates the scope.** "Backup configured" means an `AccountBackup` row exists. The doc says: *"no proof of success."* True, but the deeper failure is that the badge **gives the user a false sense of safety equivalent to having a working off-site backup**. For P4 this is the closest the product comes to lying. The badge should not exist until at least one snapshot has been verified — or it should read **"Configured (no successful backup yet)"** in two lines.

---

*Word count: ~2,050.*
