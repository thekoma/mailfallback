# Security critique

## Bottom line

- **Lexicon is a control.** "Backup" currently means both a plaintext local mirror and an encrypted remote depot. A user reading "Backed up 5 min ago" who assumes the host is now expendable has been actively misled into accepting a single-disk failure mode. This is not UX with security flavour; it is a security control failure expressed as copy.
- **"Configured" is the most dangerous word in the current UI.** A `BackupDestination` row plus an enabled `AccountBackup` produces a green "Backup configured" badge regardless of whether one byte reached the depot. Unreachable endpoint, wrong password, expired key — all leave the badge cheerful. A badge that lies during steady state is worse than no badge at all: it suppresses operator alarm.
- **Encryption claims are scattered.** restic encrypts the depot with a per-destination password (Fernet-stored in DB); mbsync writes the local mirror cleartext; source IMAP TLS is per-account; depot TLS has an `insecure_tls` opt-out. No screen tells an operator the end-to-end story, and audit-log gaps mean the toggles that determine posture leave no immutable trail.

## Trust boundaries in the four-stage chain

```
+--------------+   TLS+auth    +----------------+   fs perms     +-----------------+   TLS+auth     +-----------------+
|  Source IMAP | ============> |  Local Maildir | ============>  |  Restic Depot   | ============>  |  Snapshots (in  |
|  (Gmail,     |               |  (mbsync write |                |  (S3 bucket /   |                |   depot, addr-  |
|   M365, etc) |               |   plaintext)   |                |   local path)   |                |   essable by ID)|
+--------------+               +----------------+                +-----------------+                +-----------------+
       |                                |                                 |                                  |
   TB-1: provider               TB-2: container/                  TB-3: object store /              TB-4: snapshot identity
   credential leaves            host filesystem                   third-party operator              (immutable until forget)
   provider boundary            shared with Dovecot,
   under our control            Roundcube, restic
                                (read), restore (read)

Threat profile per zone:
  Source        : provider-managed, credentialled, TLS-by-default; we are the client
  Local Maildir : OUR HOST. Cleartext. Anyone who reads /data/mailboxes reads everyone's mail.
  Depot         : shared-tenant (S3) or operator-controlled (local). Encrypted by restic.
  Snapshots     : encrypted+integrity-checked, but pruning/forget is destructive and reversible only if the depot is.
```

The four boundaries have radically different threat profiles. The current UI flattens them all into one word ("backup"), which encourages operators to apply one mental model — usually the most reassuring one — to all four zones.

## Where the lexicon affects security

1. **"Local backup" / "Backed up X ago" implies offsite redundancy.** A user who reads this may never configure restic; one disk loss then takes everything. **Copy:** call it "Local mirror" everywhere; reserve "Backup" for the depot. Empty state: "Mirrored locally. **No offsite backup configured** — a host failure would lose this mailbox."
2. **"Backup configured" reads as an operational guarantee.** It actually means "row exists in DB". **Copy:** distinguish "Backup scheduled" (config exists) from "Backup verified" (last run completed, snapshot count > 0, last seen ≤ N days). The badge should track verified, not configured.
3. **"Encrypted" — used unevenly.** The depot flow mentions a "Repository password" but does not say the *local mirror is plaintext*. A user configuring restic may assume both stages are encrypted. **Copy:** label the local stage "Mirror (plaintext on host)", the depot stage "Depot (encrypted with repository password)".
4. **"Insecure TLS" toggle is buried.** The label sits next to the S3 fields with no warning. An operator clicking this opts into MITM. **Copy:** rename to "Disable TLS certificate verification (lab use only)"; warn that snapshots may be intercepted; audit-log the flip; show a permanent yellow badge on the destination card while it is on.
5. **"Test destination" success means initialised, not secure.** `test_destination()` calls `restic init`: it proves the password works today. It does not prove the depot is private to this MFB, that credentials are least-privilege, or that the bucket has versioning. **Copy:** rename "Initialise and test"; show a checklist of what was and was not verified.
6. **"Restore" hides two flows with different blast radii.** IMAP-to-IMAP writes to a *third-party provider* with user-supplied credentials; snapshot restore writes to the *local store* and auto-suspends. The first can leak to a wrong account on typo; the second is contained. **Copy:** "Re-upload to IMAP" vs. "Restore from snapshot".
7. **"Snapshots: 5" implies five copies.** Restic deduplicates: depot loss takes all five. **Copy:** "5 snapshots in 1 depot. Configure a second depot to survive depot loss."
8. **"Destination" sounds passive.** A user deleting one may not realise the S3 access key dies with it. **Copy:** before delete, "This permanently removes the S3 access key. The depot remains in S3 and will be unreachable until credentials are re-entered."

## Per-stage threat enumeration

### Stage 1 — Source IMAP

- **Attacks:** credential theft (OAuth refresh token or app-password, Fernet-encrypted in DB, decrypted into memory each sync); MITM if `tls_type` is misconfigured; provider-side compromise.
- **Protected today:** Fernet-at-rest, TLS toggle per account, OAuth refresh narrows the credential window.
- **Implied but not delivered:** the per-account `tls_type` has no visible "TLS posture" badge. A user could create an account with `STARTTLS` against a downgrading server and never know. Recent commits fixed an OAuth authorization bypass; the UI does not show which accounts pre-date the fix.

### Stage 2 — Local Maildir

- **Attacks:** any in-container process reading `/data/mailboxes` (Dovecot, restic, restore worker do so by design; anything that lands code execution does so trivially); volume-snapshot exfiltration; host-level backup tools sweeping the volume; disk forensic recovery.
- **Protected today:** single UID (1000/vmail) across containers, rootless container, named-volume permissions inherited from the image. The 87-issue security cluster closed RCE and IDOR paths into this stage.
- **Implied but not delivered:** "backup" copy implies durability. The local mirror is one ext4 corruption away from rebuild-from-source — *if* source IMAP still has the messages. Many MFB users configure this precisely because they fear source loss. The UI does not say the mirror is volatile.

### Stage 3 — Restic depot

- **Attacks:** bucket misconfiguration (public read, no encryption-in-transit policy, no MFA-delete); leaked S3 keys; depot-side ransomware (attacker with delete on the bucket runs `restic forget --prune` or just `s3 rm`); password compromise yields full read.
- **Protected today:** restic native encryption, per-account repo path, Fernet-at-rest for the password, `insecure_tls` defaults to false.
- **Implied but not delivered:** no recommendation that S3 keys be scoped to PutObject/GetObject on a single prefix. The destination form accepts root-equivalent credentials silently. The UI does not show which IAM permissions the supplied key actually has, nor warn that retention requires Delete.

### Stage 4 — Snapshots

- **Attacks:** retention misconfig or operator error deletes all snapshots; restore-time replay (re-restoring an old snapshot resurrects expunged messages without explaining why the inbox grew).
- **Protected today:** restic authenticates each tree; restore lands in `.offsite-restore/` and is fronted by an auto-suspended account.
- **Implied but not delivered:** the success flash "Snapshot restored as 'Backup X (date)'" suggests a live account. It is suspended pending manual activation — a *security-positive* default that the copy fights against. Operators may force-enable, fail, and disable the suspension without understanding it was intentional.

## Encryption surfaces

| Surface                       | Encrypted?                       | Key                            | Who controls the key                                         |
|-------------------------------|----------------------------------|--------------------------------|--------------------------------------------------------------|
| Source IMAP creds in DB       | Yes (Fernet)                     | Derived from `secret_key`      | MFB operator (env var `MAILFALLBACK_SECRET_KEY`)             |
| OAuth2 refresh token in DB    | Yes (Fernet)                     | Same                           | Same                                                         |
| Source IMAP wire              | TLS (per-account `tls_type`)     | Per-provider                   | Provider                                                     |
| Local Maildir on disk         | **No**                           | n/a                            | n/a                                                          |
| Dovecot SQL passwords         | bcrypt for app users             | n/a                            | MFB                                                          |
| Restic depot at rest          | Yes (AES-256, restic native)     | restic password (per-dest)     | MFB operator; password is Fernet-at-rest in DB               |
| S3 access key in DB           | Yes (Fernet)                     | Same `secret_key`              | MFB operator                                                 |
| S3 wire                       | TLS, **opt-out via `insecure_tls`** | n/a                          | Operator can downgrade silently                              |
| Backup logs (`/data/logs/sync`) | No                             | n/a                            | n/a; may contain sender/subject hints from mbsync output     |

The single `MAILFALLBACK_SECRET_KEY` is the master credential: with it, everything in the DB decrypts, including the restic password, which then unlocks the depot. The UI never communicates this. **Recommendation:** the System page should display a "Crypto posture" panel: "All credentials are encrypted with a single application key (`MAILFALLBACK_SECRET_KEY`). Anyone with this key and a copy of the database can decrypt every credential and every backup. Rotate periodically; back up separately from the database."

The legacy KDF fallback in `decrypt_credentials()` is correct and defensive, but the warning log is the only signal. **Recommendation:** count legacy decryptions and surface "N credentials still using legacy KDF — re-save to upgrade" on the admin dashboard.

## "What if I'm breached" scenarios

- **MFB DB compromised (read).** Attacker gets: bcrypt password hashes (offline crack possible), Fernet-encrypted source IMAP creds, Fernet-encrypted OAuth refresh tokens, Fernet-encrypted S3 keys, Fernet-encrypted restic passwords. **All** of these are decryptable if `MAILFALLBACK_SECRET_KEY` is also obtained (it lives in the env / docker secret — usually on the same host as the DB). Net effect: every mailbox, plus depot read, plus depot delete. **Operator story today:** there is none. There is no playbook surfaced in the UI. **Recommendation:** an "Incident response" admin page that lists every credential class, where it lives, what to rotate, and in what order.
- **Local store volume compromised.** Attacker reads cleartext mail for every account on that store. Restic depot is unaffected (different password). Source IMAP credentials are not on this volume. **Story today:** none — there is no per-store risk indicator and no documentation of what the volume contains.
- **S3 bucket compromised, read-only.** Attacker has all snapshots' bytes. Without the restic password they cannot decrypt. With the password (e.g., from MFB DB compromise) they have full mail history including expunged messages. **Story today:** depot password rotation flow does not exist in the UI.
- **S3 bucket compromised, read+delete.** Attacker can `restic forget --prune` the entire history. There is no off-by-default protection (bucket versioning, MFA-delete, S3 Object Lock are not surfaced). **Story today:** none.
- **Restic password compromised.** Attacker who also has S3 read can decrypt. Without S3 access, nothing happens. **Story today:** rotating the restic password effectively requires rebuilding the depot. The UI does not say this.
- **Single-account compromise leaking other accounts.** Per-account restic repos under separate paths in the same bucket are isolated *only by path prefix*; if the bucket-wide IAM key has access to the whole prefix, a compromise of the key from MFB equals compromise of all per-account repos. **Story today:** the per-account restic repo design is opaque to the user, who may believe per-account encryption means per-account isolation.

## Compliance-adjacent vocabulary

These words have legal weight; do not use them unless MFB earns them.

- **"Archive"** — in eDiscovery, an archive implies tamper-evident preservation, retention enforcement, and chain-of-custody. MFB does not enforce any of these. **Recommendation:** do not call any feature an "archive". The current 5 occurrences (icon, page title in restore) should become "Restore from snapshot" / "Snapshot library".
- **"Retention"** — already used in `RetentionPreset`. This is acceptable because restic genuinely enforces it, but the UI should distinguish "snapshot retention" (depot side) from any future "message retention" (mailbox side). They are not the same.
- **"Encryption at rest"** — accurate for the restic depot and the DB credential columns. **Not** accurate for the local Maildir. Use this phrase only when describing the depot.
- **"Audit log"** — already used. The model exists. The current implementation logs *user actions*; it does not log all security-relevant events (see next section). **Recommendation:** rename the page "Activity log" until coverage is comprehensive, or document the gaps inline so the operator does not believe they have audit-grade evidence.
- **"Immutable"** — do not use. Restic snapshots are immutable until forgotten by anyone with the password.

## Audit log gaps relevant to backup chain

Events that a security-aware operator expects to find in `audit_logs` but currently will not:

1. **Restic password changed on a destination.** This invalidates the entire depot and is one of the highest-impact admin actions. Should log: who, when, destination ID, old-password-hash for proof-of-prior-state.
2. **`insecure_tls` toggled on a destination.** Silent downgrade of egress security; must be flagged.
3. **Destination connectivity test failed N times consecutively.** Today the test result is shown ephemerally; a streak of failures is invisible historically.
4. **Snapshot retention pruned X snapshots.** `apply_retention()` issues `restic forget --prune` and returns; the count of removed snapshots is not persisted. An operator investigating "where did my March snapshots go?" has no answer.
5. **Restic restore initiated.** Restoring a snapshot is a sensitive operation (re-creates suspended account, brings back potentially deleted mail). Should log: who, source snapshot ID, target store, target account.
6. **OAuth refresh token used.** Or at least: refresh failures should be auditable separately from sync errors, because a refresh failure may indicate provider-side compromise notification.
7. **`MAILFALLBACK_SECRET_KEY` mismatch detected.** When legacy KDF fallback fires (credentials encrypted under a previous key), this should produce a high-priority audit row, not a debug log line.
8. **Backup destination credentials viewed/exported.** If a future config-export endpoint includes destination secrets (it should not, but if it does), the export must be audited.

## Things the current-state doc gets wrong

1. **The "Snapshot count" honesty audit understates the issue.** Saying "5 snapshots" is not just imprecise about restic semantics; it is *security misleading*, because deduplication means five snapshots are not five copies. A second depot is the only thing that survives depot loss. The honesty audit treats this as a labelling issue; it is a redundancy-mental-model issue.
2. **The doc lists "encrypted" only by inference and does not name the master key.** The four-stage chain in `01-current-state.md` mentions restic encrypts, but does not call out that `MAILFALLBACK_SECRET_KEY` unlocks every credential class in the DB. From a threat-model viewpoint the master key is the most important sentence in the document, and it is not there.
3. **The "Restic offsite backup" maturity row says 12 commits, in-flight, not yet production-validated. That is correct, but the security-relevant follow-up is missing: a feature that handles credentials, encryption, and depot mutation should not ship to "stable" without an explicit security-review checkpoint. The audit_log gaps above are blockers, not polish.**
