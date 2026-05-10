# Competitor landscape — synthesis

Five parallel competitor passes were run. Each lives in `competitors/`. This document synthesizes what they collectively tell us about the lexicon, the IA, and where MFB sits in the market.

| File | Pass focus |
|---|---|
| [`competitors/01-mailstore-family.md`](competitors/01-mailstore-family.md) | MailStore Home, MailStore Server, mail-archiver |
| [`competitors/02-saas-backup.md`](competitors/02-saas-backup.md) | Backupify, Spanning, AvePoint, Afi, SkyKick |
| [`competitors/03-oss-sync.md`](competitors/03-oss-sync.md) | isync/mbsync, OfflineIMAP, imapsync, getmail, MailPiler, Cyrus archive |
| [`competitors/04-backup-ux.md`](competitors/04-backup-ux.md) | restic, Vorta/Borg, Duplicati, Kopia, Time Machine, Synology Hyper Backup |
| [`competitors/05-nas-homelab.md`](competitors/05-nas-homelab.md) | Synology MailPlus, QNAP, Mailcow, NethServer/Piler, Proton/Tutanota Bridge, Thunderbird |

---

## Five takeaways that change the audit

### 1. "Snapshot" is the consensus word for a point-in-time backup unit

Across SaaS (Backupify, Spanning, AvePoint, Afi, SkyKick) **and** backup tooling (restic, Kopia, Vorta, Synology Hyper Backup), the artifact is a **snapshot**. "Recovery point" appears as a secondary marketing term. Borg is the legacy holdout with "Archive", and Vorta's docs already reconcile this as "Archives (or snapshots)". Duplicati confusingly uses "Backup", "Snapshot", and "Version" interchangeably — and pays a documented support cost for it.

**Implication for MFB:** keep "snapshot". It's already aligned. Anything else loses.

### 2. "Repository" is the consensus word for the place snapshots live

restic, Kopia, Vorta, Borg, Synology Hyper Backup, and the backup tooling space generally call it a **repository** (sometimes "repo"). Duplicati uses "Destination/Backend" and gets confusion in its forums for it.

**Implication for MFB:** "Backup Destination" is Duplicati-flavoured and should change. "Repository", "Repo", "Depot" are stronger. "Vault" is also viable in the consumer space.

### 3. SaaS has no analog for MFB's local-mirror tier

All five SaaS vendors (Backupify et al.) collapse local + remote into one opaque cloud tier. There is no industry word for "the local copy mbsync keeps". MFB has to coin its own. The strongest candidates from competitive evidence:

- **"Local mirror"** (echoes OfflineIMAP's `localrepository` / `remoterepository`)
- **"Live copy"** (consumer-friendly)
- **"Fallback mailbox"** (echoes MFB's own product name)
- **"Mailbox"** (collapsing the abstraction)

Avoid "archive" — that word has eDiscovery weight and means cold/policy-bound storage in NAS/Piler-land.

### 4. AvePoint's "BYOS" and Afi's "Storage Locations" validate the depot concept

The only SaaS vendor that exposes customer-owned storage as a first-class concept is AvePoint with **BYOS (Bring Your Own Storage)**. Afi has **Storage Locations** as a secondary concept (region selector). MFB's depot model is unusual for SaaS but native for self-hosted / NAS.

**Implication:** MFB's market position is "self-hosted, with the depot under your control". This is a feature, not a bug — but it has to be sold as one. Lexicon should reinforce it.

### 5. MFB is leaking mbsync vocabulary into the GUI and shouldn't

`partials/sync_settings_fields.html` exposes `Far`, `Near`, `Patterns` (with `!` and `%` syntax), `Expunge`, `SubFolders Verbatim`, `PipelineDepth`, `DisableExtension`. None of these appear in any other tool's user-facing surface. Even mbsync's 1.4 Master/Slave→Far/Near rename made the terms *more* abstract, not more user-friendly.

**Implication:** these belong in `mbsync_config.py` as implementation detail. The GUI should expose **outcomes** (sync direction, what to keep, schedule) — not config keywords.

---

## Repositioning candidate from the NAS/Piler axis

The NAS world (Synology, QNAP, Piler) draws **four** distinct lines that English merges:

| NAS world | Definition | MFB analog today |
|---|---|---|
| **Snapshot** | Atomic point-in-time on a single volume | restic snapshot |
| **Backup** | Versioned copy on a different system | restic depot + snapshots |
| **Replication** | Continuous sync of a volume to another system | mbsync (?) — but no, mbsync is more of a *mirror* |
| **Archive** | Cold, policy-bound, often legal | nothing in MFB |

The MFB local maildir is closest to **Replication** (continuous mirror with intentional retention of deleted items). But "replication" is a heavy word.

**Critical recommendation from competitor pass 5:** MFB should NOT call itself an *archive* (loses to Piler / MailStore on compliance) and should NOT call itself a *backup* product (loses to Hyper-Backup-class products on flexibility). The honest framing is **"mail mirror with fallback access"** — disaster-recovery vocabulary, not compliance vocabulary. **The product name "MailFallBack" already encodes this**; the GUI should reinforce it.

---

## Wizard / IA patterns worth borrowing

### From Kopia (gold standard)

One decision per screen: storage type → details + password → connect → source → policy. Step N+1 never shows until step N validates. **MFB should mirror this for the "first depot" setup.**

### From Vorta

Tab order = mental model: **Repository · Sources · Schedule · Snapshots · Misc**. This sequence teaches the user the chain. **MFB's account-detail page could adopt the same sequence as section ordering.**

### From OfflineIMAP

`localrepository` / `remoterepository` is the right semantic pair: it says **what each side is**, not where in topology. Validates "local" / "remote" as the primary axis.

### From getmail

A 4-section grammar: `[retriever] / [destination] / [options] / [filter]`. Maps cleanly to MFB's account-form fields if we group them. Today, MFB's `/accounts/new` is one long unstructured form.

### From imapsync ("preview before commit")

The `--just*` family shows what would happen before committing. MFB's single "Test connection" button could be a multi-rung ladder: **login OK → folders OK → size estimate → ready**.

### From MailPiler

The discipline of refusing "backup" entirely. MailPiler calls itself an "email archive" and never wavers. MFB should pick one identity and hold the line.

---

## Vocabulary cross-reference (compressed)

| Concept | restic | Kopia | Vorta/Borg | Duplicati | Synology | SaaS (consensus) | OSS sync | Today's MFB |
|---|---|---|---|---|---|---|---|---|
| **Source data** | source | source | sources | source | source | mailbox / tenant | local/remote repository | account / IMAP host |
| **Local copy of mailbox** | — | — | — | — | — | — (collapsed) | localrepository | "Maildir" / "store" |
| **Remote storage location** | repository | repository | repository | destination/backend | hyper backup destination | storage location / BYOS | — | "Backup Destination" |
| **Point-in-time unit** | snapshot | snapshot | archive (= snapshot) | backup/snapshot/version | snapshot | snapshot / point-in-time / recovery point | — | snapshot |
| **Schedule** | — (cron) | policy | schedule | schedule | retention rule | policy | cron | schedule (cron) |
| **Retention** | forget --keep-* | retention | retention | retention | retention rule | retention policy | — | retention preset |
| **Restore** | restore | restore | extract | restore | restore | restore (granular / search / export) | — | restore |

**Where MFB diverges from consensus:**
- "Backup Destination" → should be "Repository" or "Depot" or "Vault"
- "Maildir" / "Store" / overloaded "Backup" → should be "Mirror" / "Local copy" / "Fallback mailbox"
- The mbsync config terms in the GUI form → should be hidden

**Where MFB matches consensus:**
- snapshot (unanimous)
- restore (unanimous)
- schedule (unanimous)
- retention preset (matches restic policy semantics)

---

## Status / dashboard patterns

The "Status quartet" appears across Vorta, Duplicati, Kopia, and Synology Hyper Backup, validated as a trust-builder by Home Assistant 2025.1's release notes:

1. **Last <thing> X ago**
2. **Next <thing> in Y**
3. **Size triplet**: logical / on-disk / deduplicated (Borg's pattern; restic exposes the same)
4. **Snapshot count**

MFB exposes (1) and (4) per account in the Offsite Backup section. (2) and the dedup metric are missing. **Adding the quartet would be a high-leverage trust signal.**

---

## Restore patterns

MFB chose: **restore creates a new suspended account named "Backup {name} ({date})"**. This is unusual but has precedent:

- **Kopia** — read-only mount of a snapshot.
- **Synology** — restore to a different shared folder by default.
- **Time Machine** — restore individual files in place.
- **MailStore** — restore to original folder OR new folder OR export to PST/EML.

The MFB pattern is **closer to Synology's "restore-to-a-different-target"** than to consumer products' in-place restore. It's safe (no data overwritten) but the user has no idea what to do next. The lexicon should reflect this safety property and provide explicit next-step guidance.

---

## What this synthesis tells the next phases

- **Lexicon (Phase 5):** Three proposals must be drafted: (a) align with restic/Kopia ("Repository"), (b) align with consumer/SaaS ("Vault" / "Storage Location"), (c) Italian-flavoured ("Deposito"). Each gets an IT/EN table.
- **Mockups (Phase 6):** Account-detail and admin-backup screens should adopt Vorta's section ordering and Kopia's wizard discipline. Add the status quartet. Add a chain widget.
- **Strategic options (Phase 7):** The market-positioning question (mirror vs. archive vs. backup) is a strategic choice, not just lexicon. Frame it as such.
- **Recommendation (Phase 8):** Respect the product's name ("MailFallBack") — the lexicon should reinforce the "fallback mail copy" identity, not chase MailStore on compliance ground or Backupify on cloud ground.
