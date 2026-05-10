# Security critique

## TL;DR

- **The word "backup" launders a security claim.** Calling the local mbsync mirror a "backup" implies durability and tamper-resistance that the local Maildir does not provide (single disk, plaintext, same trust domain as the host). The lexicon decision is also a threat-model decision: rename the local stage and you stop overclaiming.
- **"Encrypted" must be scoped per stage.** TLS-in-transit (source IMAP, S3) is not encryption-at-rest. Restic depots are E2E encrypted; the local store is not. UI must never use the bare word "encrypted" without a stage qualifier.
- **"Backup configured" / "destination configured" today mean a row exists in the DB, not that the data is reachable, restorable, or fresh.** Every safety word in the GUI currently denotes config presence, not operational truth.

## Trust boundaries

```
[Source IMAP]  ===TLS===>  [MFB host]                    | trust domain A: provider
                              |                          | (their auth, their crypto)
                              |  mbsync (plaintext on disk)
                              v
                          [Local Maildir on MailStore]   | trust domain B: MFB host
                              |                          | (host root = full read)
                              |  restic (chunked, AES-256-CTR + Poly1305)
                              v
                       [Restic depot: S3 or local path]  | trust domain C: object store
                              |                          | (bucket creds in MFB DB)
                              |  restic snapshots, deduped
                              v
                          [Snapshots]                    | trust domain D: time-shifted
                                                         | (only restic key opens them)

   ^                          ^                          ^
   |                          |                          |
 provider breach          host breach              bucket breach
```

Boundaries: A↔B = network; B↔(C,D) = restic password (the *only* thing separating an S3 read leak from plaintext mail).

## Lexicon-security tensions table

| Current word/copy | What it implies | What is actually true | Suggested fix |
|---|---|---|---|
| "Backup" (for local mbsync mirror) | Durable, recoverable, separated from prod | Single-disk Maildir on the same host, plaintext, no versioning | Reserve "backup" for restic only; call local stage "mirror" or "local copy" |
| "Backup configured" badge (per-account) | A backup exists and is restorable | An `AccountBackup` row exists; restic may have never run, key may be wrong, bucket may be gone | "Scheduled — last verified <date>" or "Configured, never run" |
| "Encrypted" (bare) | At-rest E2E protection | Means TLS for source/S3, restic-key for depot, **nothing** for local Maildir | Always qualify: "in transit", "at rest (depot)", "key required to restore" |
| "Last sync: 5 min ago" | Recent successful capture | Last *attempt*; could be a failure followed by silence | "Last successful sync" vs "Last attempt" — split the labels |
| "Snapshot restored" success toast | Mailbox is back online and serving | Restored as a *suspended* account pending manual activation | "Snapshot staged — review and activate" |
| "Insecure TLS" toggle on destination | Optional looseness | Disables certificate verification — full MITM exposure of bucket credentials and depot URL | Rename "Disable TLS verification (testing only)" + red warning |
| "Backup destination" | A safe place data goes | A target the MFB host has *write* credentials for — same machine compromise = depot compromise | "Backup target (write-access from this host)" |

## Per-stage threat enumeration

| Stage | Top attack | MFB protects today | UI overclaim risk |
|---|---|---|---|
| Source IMAP | Stolen OAuth refresh token / app password | Fernet-encrypted secrets in DB; OAuth refresh handled server-side | "OAuth connected" hides the token-rot risk |
| Local store (Maildir) | Host root or volume snapshot exfil | None — files are plaintext on disk; relies on host hardening | "Backup" implies isolation that doesn't exist |
| Restic depot (S3 / local) | Bucket credential leak from MFB DB; ransomware delete | Restic password gates *content*; no object-lock or append-only enforcement in MFB | "Destination configured" implies durability/immutability |
| Snapshots | Silent corruption; restic key loss | `restic check` not run by MFB; key custody is implicit (passphrase in DB) | "Snapshots: 14" implies they are restorable — never verified |

## Encryption surfaces

| Surface | Encrypted? | Key | Custodian |
|---|---|---|---|
| Source IMAP fetch | TLS in transit only | Provider TLS cert | Provider |
| Stored IMAP credentials (DB) | At rest, app-level | Fernet key (env var) | MFB operator |
| Local Maildir on MailStore | **No** | — | Host filesystem perms only |
| mbsync → restic handoff | N/A (local file read) | — | Host |
| Restic depot contents | **Yes** (AES-256 + Poly1305) | Restic repo password | MFB DB (Fernet-wrapped) |
| Restic depot transport (S3) | TLS (toggleable off) | S3 endpoint cert | Bucket provider |
| S3 bucket credentials | At rest, app-level | Fernet key | MFB operator |

Single point of failure: the **Fernet key** (env var) decrypts both IMAP creds and restic passwords. Lose-or-leak it once = lose everything.

## "What if I'm breached" — one scenario per line

- **MFB DB compromised** — attacker gets ciphertexts of IMAP creds, S3 creds, restic passwords. Without the Fernet env-key they are opaque; with it (often co-located on the same host) they get the keys to source mailboxes *and* the depot. Operator story: **N** — no documented key-rotation flow, no split-custody guidance.
- **Local store volume compromised** — full plaintext mail of every account, including any in-flight mbsync state. No "evidence of access" log. Operator story: **N**.
- **S3 bucket compromised, read-only** — attacker gets restic chunks; useless without the repo password. Strong case for the depot model. Operator story: **Y** (if password not also leaked).
- **S3 bucket compromised, write/delete** — attacker can wipe or tamper snapshots. MFB does not configure object-lock, versioning, or an append-only role. Operator story: **N**.
- **Restic password compromised** — full plaintext history of every account ever backed up to that depot, even after rotation (old chunks remain decryptable). Operator story: **N** — UI offers no rotate-and-rewrap workflow.

## Compliance vocabulary warnings

- **"Archive"** carries WORM / retention-policy connotations (SEC 17a-4, GDPR Art. 30). MFB has neither. Avoid in product copy unless we ship immutability.
- **"Retention"** — current presets (light/standard/full) are restic *prune* policies, not legal retention. Don't say "Retention: 90 days" without "(snapshots pruned after, not legal hold)".
- **"Encryption at rest"** — true only for the restic depot. Saying it about the *service* is materially false because the local mirror is plaintext.
- **"Audit log"** — implies tamper-evidence. The current `AuditLog` table is mutable by anyone with DB access; call it "Activity log" until we sign or append-only it.

## Audit log gaps

- **Restic password / Fernet key access or rotation** — no event today; rotations would be invisible.
- **Restore-from-snapshot operations** — who restored which account from which snapshot to where (suspended account creation is silent).
- **Backup destination credential changes** — S3 keys swapped, endpoint changed, "insecure TLS" toggled — none logged with before/after.
- **Failed authentication to source IMAP after credential change** — relevant for detecting upstream account takeover.
- **Dovecot session metadata** — IMAP login from a new IP, app-password use vs OIDC, never surfaced in MFB's audit view.

## Things current-state doc gets wrong

- It frames "Backup configured" as a UX honesty issue ("no proof of success"). It's a **security** issue: the badge is the operator's only at-a-glance assurance and currently can be green for an account whose depot has been unreachable for weeks.
- It lists `audit log` under "what's working today (worth preserving)". The table exists and is populated, but it is not append-only, not signed, not surfaced for compliance personas, and is missing the events listed above. Calling it "solid" overstates the maturity for any security-minded reviewer.
