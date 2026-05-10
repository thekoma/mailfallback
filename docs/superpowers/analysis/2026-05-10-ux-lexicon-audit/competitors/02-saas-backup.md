# Competitor: SaaS email backup

How the **commercial mainstream** of cloud-to-cloud SaaS backup vendors (Microsoft 365 / Google Workspace) names the four MFB stages: Source -> Local backup -> Remote depot -> Snapshots. This is the vocabulary MFB's prospective users have most likely already been trained on.

## Vendors covered

- **Backupify** by Datto / Kaseya — also sold as *Datto SaaS Protection*. Targets MSPs and SMBs.
- **Spanning Backup** by Kaseya/Unitrends — M365, Google Workspace, Salesforce. Strong SMB / mid-market following.
- **AvePoint Cloud Backup for Microsoft 365** — enterprise-leaning, big on configurable storage residency.
- **Afi.ai** — newer, "AI-driven" entrant, M365 + Google Workspace. Very docs-forward.
- **SkyKick Cloud Backup** — partner/MSP-channel only, M365 only, marketed as "set-and-forget".

## Lexicon comparison table

| Concept | Backupify (Datto) | Spanning | AvePoint | Afi | SkyKick | Industry consensus |
| --- | --- | --- | --- | --- | --- | --- |
| **Source mailbox / origin** | "user", "mailbox", "Exchange Online" — no abstract noun for the source. The bound entity is the M365 *tenant*. | "user", named per workload ("Exchange Online", "OneDrive for Business") | "Exchange Online mailbox", "workload" | **"data source"** (= tenant) — explicit and reused throughout onboarding | "user", "mailbox", "site" | Vendors do *not* use the word "source". They use the workload name (mailbox / user / tenant). |
| **Local copy / cache** | None — vendor cloud is the only copy. | None — pure cloud-to-cloud. | None in the standard SKU; **BYOS** lets you point storage at your own bucket but that is still "the backup", not a "local cache". | None. | None. | **The concept does not exist.** SaaS vendors collapse "local" and "remote" into one tier. This is a divergence from MFB. |
| **Storage location (vendor cloud, S3, Azure)** | "Datto's private cloud", "geo-redundant storage" | "AWS data centers" / "backup storage (hosted in the Amazon Web Services cloud)" | **"Bring Your Own Storage" (BYOS)** + **"Bring Your Own Key" (BYOK)**; otherwise "AvePoint's Azure storage" | "Afi backup storage region" — user picks a **region**, not a provider | "SkyKick Cloud" (opaque, single tier, "stored outside of Office 365") | Either an opaque vendor cloud OR a **region selector**. AvePoint's *BYOS* is the only place where the customer's own bucket is a first-class concept. |
| **Snapshot / point-in-time** | **"point-in-time backup"** + **"snapshot"** used interchangeably. Restore UI = **"Snapshot Selector"** with a calendar. | **"point-in-time snapshot"**, restore UI surfaces **"Point-In-Time Restore"** | **"recovery point"** (in ransomware context); otherwise "backup" | **"snapshot"** + **"recovery point"** ("Auto-labelling of recovery points to define the last clean snapshot") | **"snapshot"** ("up to six snapshots daily") | **Snapshot is universal.** "Point-in-time" is the second most common modifier. "Recovery point" is rising but secondary. |
| **Backup frequency** | "3x daily backups" / "3 daily, point-in-time backups" | "Daily, Automated Backup" + "Customizable, On-Demand Backup" | "backups run up to four times a day, every day" | "1-24x daily frequency" / "high-frequency snapshots" | "Six snapshots daily" / "set-and-forget" | Cadence is sold as a **count per day**, never as a cron expression. |
| **Retention policy** | "Automated, Unlimited Cloud Retention" / "Infinite Cloud Retention (ICR)" | "Unlimited Backup Retention" / "unrestricted retention policy" | implicit unlimited; configurable **policy** | **"unlimited retention"** / **"unlimited versioning"** | "Unlimited retention" | The marketing word is **"unlimited retention"**. The technical word is **"retention policy"**. Customers see the marketing word first. |
| **Restore** | **"Granular Restore"**, "one-click restore", "Restore To" (destination), **"Export"** (Zip download) | **"Point-In-Time Restore"**, **"Granular, Search-Based Restore"**, **"End User Self-Service Restore"**, **"cross-user restore"** | **"ReCenter"** (their restore portal), **"Granular restore"**, **"Out-of-place restore"** | **"Flexible Restore"**, "in-place / non-destructive restore", "instant offline export" | "one-click restore", "lightning-fast search" | **"Restore"** is universal as the verb. **"Granular"** is universal as the modifier. **"Export"** is the universal escape hatch (download a Zip). |

## Mental models they teach

The five vendors teach roughly the same model, with small dialect variations:

> **"Your data is in the cloud (M365 / Google). We make a copy in our cloud. You can roll back to any point in time, or export a Zip."**

There is no concept of "local" anywhere. Everything is cloud-to-cloud, which means MFB's Source -> Local backup -> Remote depot -> Snapshots is a *richer* topology than what these tools describe. Specifically:

- **Backupify / Datto** frames it as *protection of SaaS data*. From the help docs: *"A snapshot includes all of the user, site, or team data at the time the backup was created."* The user is taught that snapshots are atomic, time-stamped, and selected from a calendar.
- **Spanning** uses the *purpose-built* framing — *"Purpose-Built to Simplify Microsoft 365 Backup and Recovery"* — and emphasizes that a snapshot is a *version* you restore from. The mental model is **snapshot = version**.
- **AvePoint** is the most enterprise-flavored. They explicitly teach two storage tiers: *"unlimited 256-bit encrypted storage, or in your storage of choice when you Bring Your Own Storage"*. This is the closest analog to MFB's "remote depot is yours, vendor doesn't see it".
- **Afi** teaches *fidelity*: "100% fidelity backup including Google Directory, Classroom, Drive document IDs". The mental model is **the backup is a faithful clone**, and snapshots are dated revisions of that clone. They are the only vendor that says **"data source"** plainly in onboarding.
- **SkyKick** sells the mental model **as the absence of a mental model**: *"set-and-forget"*, six snapshots/day, unlimited retention, one-click restore. The user is not invited to think about storage at all.

## Onboarding patterns

All five vendors share a fundamentally identical wizard, with small naming differences:

1. **Sign in / create account** (vendor account)
2. **Connect tenant / authorize** — OAuth/admin-consent flow against M365 or Google Workspace. Backupify and Spanning both call this "*Authorize*". Afi calls it "*Add a data source*".
3. **Pick a region / storage option** — only Afi and AvePoint surface this explicitly. Backupify, Spanning, SkyKick hide it.
4. **Initial discovery / sync** — Afi names this step verbatim: *"Wait for the initial Microsoft 365 resource discovery"*. Backupify calls it the *first backup*. SkyKick and Spanning gloss over it.
5. **You're protected** — final state, all vendors converge on a dashboard showing per-user / per-mailbox status.

Notable patterns:

- **Per-tenant, not per-mailbox.** All five SaaS vendors onboard at the **tenant** level (one OAuth grant covers everyone). They then *auto-discover* mailboxes. This is fundamentally different from MFB, which is per-account.
- **No empty state to handle.** Because tenants come pre-populated with users, there is never a "you have no accounts yet" state — the wizard finishes, discovery runs, the table fills up.
- **Wizards always end on a status table**, never on a settings page. The first thing a user sees post-onboarding is *"is the backup running?"*, not *"configure your retention"*.

## Pricing-page word choices

These are the most distilled marketing phrases — the language each vendor bets is most legible to a buyer.

**Backupify / Datto:**
- "3x daily backups"
- "Infinite Cloud Retention"
- "Granular Restore"
- "one-click restore"
- "Predictable pricing"
- "single pane of glass"
- "Straightforward Billing"

**Spanning:**
- "Daily, Automated Backup"
- "Customizable, On-Demand Backup"
- "Unlimited Backup Retention"
- "Point-In-Time Restore"
- "End User Self-Service Restore"
- "Granular, Search-Based Restore"
- "Purpose-Built to Simplify Microsoft 365 Backup"
- "99.9% uptime SLA"

**AvePoint:**
- "Bring Your Own Storage"
- "Bring Your Own Key"
- "Multi-geo support"
- "ReCenter" (restore portal name)
- "Out-of-place restore"
- "Granular restore"
- "Self-Service"

**Afi:**
- "Most Complete Google Workspace Backup"
- "high-frequency snapshots"
- "unlimited retention"
- "100% fidelity"
- "Flexible point-in-time restore"
- "Immutable backup storage"
- "Intelligent Ransomware Protection"
- "Storage Locations"
- "Instant Offline Export"

**SkyKick:**
- "Unlimited backup for a fixed cost"
- "Six snapshots daily"
- "Unlimited retention"
- "lightning-fast search"
- "one-click restore"
- "set-and-forget"
- "automatically discovers new users, sites and files"

## Implications for MFB

1. **"Snapshot" is the safe word.** All five SaaS vendors converge on **snapshot** for the per-point-in-time restore unit, often modified as **"point-in-time"** ("point-in-time backup", "point-in-time snapshot", "point-in-time restore"). MFB should match this. "Recovery point" is a credible second choice but only if MFB wants to lean into the ransomware/DR framing (Afi, AvePoint).
2. **MFB's "local backup" tier has no SaaS analog.** None of the five vendors expose the idea of a local cache or a working copy. Their storage tier is opaque. This means MFB cannot copy SaaS vocabulary for the Source -> Local stage — there is none. MFB likely needs to either (a) borrow from on-prem backup tools (Veeam, Restic, BorgBackup), or (b) coin its own term and explain it. **Recommendation: "local mirror" or "live mirror"** maps better than "backup" for that tier, because users will read "backup" as the SaaS-style end-state.
3. **"Remote depot" should align with industry's "storage location" / "region".** Afi's *"Storage Locations"* and AvePoint's *BYOS* are the closest mental models. MFB should consider naming the concept **"Backup Destination"** (which it already uses) or **"Storage Location"** — both are recognizable. Avoid coining anything novel here.
4. **Granular + Search + Export is the universal restore trinity.** Every vendor offers these three. MFB's restore UX should aim for the same legibility: granular item selection, full-text search, and a Zip/EML export escape hatch. The verb is always **"Restore"**, the modifier is always **"Granular"**, and the escape hatch is always **"Export"**.
5. **Retention is sold as "unlimited", configured as "policy".** The marketing word every user has internalized is **"unlimited retention"**. MFB doesn't need to match the marketing claim, but it must surface a clear **"Retention policy"** field (cron-style or human-readable like "keep 7 daily, 4 weekly, 12 monthly" — which is restic's existing vocabulary, and a defensible bridge from MFB's restic backend to user-facing copy).

## References

- Backupify: <https://www.backupify.com/> ; <https://www.datto.com/products/saas-protection/microsoft-365-backup/> ; <https://saasprotection.datto.com/help/M365/Content/Recovering_and_restoring_files/03_Recovering_and_restoring_data.htm> ; <https://datto2.my.site.com/s/article/KB370000000096>
- Spanning: <https://www.spanning.com/products/microsoft-365-backup/> ; <https://www.spanning.com/products/google-workspace-backup/> ; <https://help.spanning.kaseya.com/help/Content/K-Span-365/365-restore.htm>
- AvePoint: <https://www.avepoint.com/products/cloud-backup/microsoft-365-backup> ; <https://catalog.byappdirect.com/en-US/apps/448790/avepoint-cloud-backup-for-microsoft-365/features> ; <https://adoption.microsoft.com/en-us/microsoft-365-backup/avepoint/>
- Afi: <https://afi.ai/google-workspace-backup> ; <https://afi.ai/docs/o365/first-steps/onboarding/> ; <https://afi.ai/docs/gw/backup-and-recovery/overview/>
- SkyKick: <https://clouddirect.net/knowledge-base/KB0012385/skykick-cloud-backup-overview> ; <https://sg.cloud.im/content/docs/SKyKick-CloudBackup.pdf> ; <https://support.bemopro.com/hc/en-us/articles/360060058533-SkyKick-Cloud-Backup-for-Exchange-Online-OneDrive-Teams-SharePoint-Office-365-Groups-and-Planner>
