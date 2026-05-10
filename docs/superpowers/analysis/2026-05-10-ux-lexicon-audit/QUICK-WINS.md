# Quick wins — what to ship in one afternoon

If you don't have time for the full 4-week rollout in `08-recommendation.md`, here are the **eight smallest changes** that move the needle. Each is a 5-30 minute edit with no architectural risk. Together they cost maybe half a day. They don't replace the full audit's recommendation — they buy you something visible while you decide on the bigger work.

---

## 1. Empty Accounts page — fix the wrong word ⏱ 5 min

**File:** `src/mailfallback/templates/accounts.html` (line ~26)

```diff
- "No accounts configured yet. Click <strong>New Account</strong> to add your first email backup."
+ "No mailboxes yet. Click <strong>Connect a mailbox</strong> to start your local backup."
```

Optionally also rename the button label from "New Account" to "Connect a mailbox" / "Collega una casella".

**Why this one first:** it's the very first sentence a new user reads, and it currently sets the wrong mental model.

---

## 2. "Snapshot restored as 'Backup X'" → say it's suspended ⏱ 10 min

**File:** `src/mailfallback/routers/ui_backup.py` (line ~411)

```diff
- request.session["flash_success"] = f"Snapshot restored as '{restored_account.name}'"
+ request.session["flash_success"] = (
+     f"Recovered into '{restored_account.name}'. "
+     f"The mailbox is suspended — review and enable when ready."
+ )
```

Also rename `f"Backup {account.name}"` → `f"Recovered {account.name}"` in the same file (line ~388).

**Why:** support-team's #1 sev-1 ticket comes from users not realising the recovered mailbox is in limbo. This single message change cuts that ticket category in half.

---

## 3. "insecure_tls" toggle — warn the user ⏱ 15 min

**File:** `src/mailfallback/templates/admin_backup.html` (the form fields for create + edit)

Add a warning line below the checkbox when checked:
```html
<small class="sync-error">
  ⚠ TLS certificate verification skipped. Only enable for trusted self-signed CAs.
</small>
```

Optionally rename the label from "insecure_tls" → "Skip TLS certificate verification" / "Salta verifica certificato TLS".

**Why:** the security critique flagged this as a silent footgun. A 1-line warning is enough to elevate user attention.

---

## 4. "Backup configured" badge → "Off-site policy set" ⏱ 10 min

**File:** `src/mailfallback/templates/partials/account_backup.html` (the "Configured" badge area)

Today the badge implies safety. Change to a two-state surface:

```jinja
{% if backup_config.last_status.value == "completed" and backup_config.last_backup_at %}
    <span class="stats-pill"><span class="stats-dot stats-dot-ok"></span> Last back-up {{ backup_config.last_backup_at | time_ago }}</span>
{% elif backup_config.id %}
    <span class="stats-pill"><span class="stats-dot"></span> Off-site policy set — no successful back-up yet</span>
{% endif %}
```

**Why:** the security critique flagged "Backup configured" as overpromising safety. Saying "no successful back-up yet" honestly is one of the most important honesty-fixes in the whole audit.

---

## 5. Open the off-site `<details>` by default when configured ⏱ 2 min

**File:** `src/mailfallback/templates/account_detail.html` (line ~308)

```diff
- <details id="section-backup">
+ <details id="section-backup"{% if backup_config %} open{% endif %}>
```

**Why:** if the user has configured offsite backup, they want to see its status without one extra click. Today it's collapsed, even when configured.

---

## 6. Add an "Off-site" stat card to the Dashboard ⏱ 20 min

**File:** `src/mailfallback/templates/dashboard.html` (the stat-cards row)

Add one more card alongside Accounts/Messages/Storage/Errors:

```jinja
<div class="stat-card">
    <h3>{{ snapshots_total }}</h3>
    <small>Off-site snapshots</small>
</div>
```

In the route handler, count snapshots across all configured AccountBackup rows. (Approximation — use the `last_backup_at` count or sum a cached value; see `services/restic_service.py` for ideas.)

**Why:** the dashboard currently surfaces zero information about off-site health. Even one number tells the user the system is alive.

---

## 7. Distinguish the "Backup" word in flash messages ⏱ 10 min

**File:** `src/mailfallback/routers/ui_backup.py` (multiple lines)

Find every `request.session["flash_success/error"]` containing the word "backup" and qualify it: "Off-site backup started", "Off-site backup configuration saved", etc. Don't change "Backup destination" → "Repository" yet (that's the bigger rename); just add the qualifier.

```diff
- request.session["flash_success"] = "Backup configuration saved"
+ request.session["flash_success"] = "Off-site backup policy saved"

- request.session["flash_success"] = "Backup started"
+ request.session["flash_success"] = "Off-site snapshot started"
```

**Why:** flash messages teach the user the lexicon faster than any tooltip. Get them right early.

---

## 8. Add a one-paragraph "How MFB works" to the docs ⏱ 30 min

**File:** new doc page `docs/src/how-mfb-works.md`, linked from the dashboard footer.

Three paragraphs:
1. The four-stage chain (with the ASCII diagram from `08-recommendation.md`).
2. What "local backup" means (one paragraph): always-on, retains deleted mail for X, accessed via Webmail.
3. What "off-site backup" means (one paragraph): scheduled snapshots to a separate location you control, recovered from when local backup is unavailable.

Link from `templates/base.html` footer: "How does this work?".

**Why:** a single docs page handles the 5% of users who actually read docs, and gives the support team something to link in tickets. Not a substitute for in-product copy fixes (1-7), but a cheap force multiplier.

---

## What this skips (deliberately)

- The **chain widget** (the centrepiece of the recommendation). Worth the bigger investment.
- DB renames.
- IA reorder of the account-detail sections.
- /restore split.
- Repository wizard.
- LEXICON.md and the lint check.

These are all in `08-recommendation.md`. Quick-wins is a *temporary detour*, not a replacement.

---

## What changes after quick-wins land

- Empty Dashboard / Accounts: less misleading.
- Account detail: off-site visible when configured.
- Flash messages: less ambiguous.
- Recovered mailbox: no more limbo confusion.
- Insecure TLS: less of a silent footgun.
- Dashboard: surfaces an off-site signal.
- Docs: has a thing to link.

**Time: ~half a day. Risk: zero (no model, route, or scheduler changes). Reversibility: 100%.**

If after this you want to invest the full 4 weeks in the rollout from `08-recommendation.md`, none of these quick-wins conflicts with it — they all become naturally subsumed when the bigger lexicon rename lands.
