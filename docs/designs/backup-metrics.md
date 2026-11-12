# Backup metrics (#308)

## Scope and decisions

Expose recorded mailbox-policy and configuration-backup state through the existing bearer-key-protected `/metrics` endpoint. No new API scopes, agent/MCP changes, UI changes, restic calls on scrape, notification behavior changes or sync counters. Follow-up release scope adds optional monitoring resources and examples, as requested.

Policy metric labels are `policy_id` and `account_id` UUIDs, deliberately without display names or destination labels. These are lifetime-policy records: changing destination preserves history, so these metrics do not verify the newly configured repository. Configuration metrics use `repository_id`. This avoids adding state-reset semantics to backup configuration or attributing old snapshots to a new destination.

Unknown timestamps emit zero, not a fabricated success. Disabled policies and configuration backups remain observable with enabled gauges. Accounts without policies have no policy metrics. Alert examples suppress disabled records and use `for` to give unknown history a grace period. Prometheus scrape failure is separate (`up`).

A nullable `Repository.last_config_backup_success_at` records only successful completion. Migration 030 seeds it only for records with status `ok`; a previous failed attempt cannot reveal historical success. The column participates in the existing encrypted config export/import because `backup_destinations` is already exported. No new environment variables. The optional chart monitor uses the existing metrics API key.

New gauges use a scrape-local CollectorRegistry. Bulk-select policies, repositories, and latest completed jobs (window rank per policy); query count is independent of policy count. Existing sync metric names and semantics remain unchanged. No background task is added: the existing config backup runner writes success in its existing commit. Failures preserve it, including after restart.

## Alternatives

Scrape-time storage verification adds latency, credentials and failure coupling. An independent exporter adds another deployment. Process-local counters do not retain historical success across restart. Reuse the persisted records instead, accepting their limits: cached snapshot counts, unknown upgrade history, and lifetime-policy rather than current-destination provenance.

## Optional monitoring deployment

Following the requested release extension, add opt-in `metrics.serviceMonitor` and `metrics.prometheusRule`. Reuse common 5.3.0's ServiceMonitor renderer, reference the app Secret, and stamp a namespace/release `mfb_deployment` target label. Rules select that deployment and join instance/job/entity identity. Defaults emit neither CRD resource. Existing Prometheus Operator and Alertmanager remain prerequisites; no receivers or cluster installations are mutated. Standalone examples mirror chart default rules; render assertions catch drift. Promtool tests cover delay, disabled policies, cross-instance identity and unknown success.

## Verification and task ledger

- [x] Failing endpoint tests: metric values, duplicate names/UUID identity, disabled/unknown/no-policy states, latest completed duration, deletion between scrapes, bounded backup queries, authentication.
- [x] Failing config runner/export and migration tests: success then failure preserves timestamp; backfill only `ok`; downgrade removes column.
- [x] Migration/model, writer, collector and route implementation.
- [x] Focused tests, full repository gates, live authenticated scrape and migration on PostgreSQL container.
- [x] Operator docs: gauge semantics, auth, lifecycle alerts and destination-history limitation.

SQLite covers window-query behavior and migration logic. PostgreSQL migration 030 and authenticated curl scrape passed; a transaction with populated policy/job/config fixtures verified window-query duration and config success, then rolled back. Full tests: 1,811 passed with both `-n auto` and `-n 4`; ruff and pre-commit passed, including explicit checks of new untracked files. Authlib reports an existing deprecation warning. Local startup required assigning `/confs/dovecot` to UID/GID 1000; no volumes were removed. No UI/browser or isync changes are involved. Preserve existing working-tree changes, and do not stage or commit.
