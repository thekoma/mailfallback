# Monitoring and alerts

MFB exposes recorded backup state at `/metrics`. **Prometheus scrapes metrics and evaluates alert rules; Alertmanager groups and delivers alerts.** Installing these optional resources does not install Prometheus, Alertmanager or a notification receiver.

See [metric names and semantics](settings.md#backup-gauges), including unknown timestamps, disabled policies and the lifetime-policy history limitation. These alerts do not prove that a newly selected repository contains usable snapshots.

## Kubernetes with Prometheus Operator

Monitoring is disabled by default. Install the `ServiceMonitor` and `PrometheusRule` CRDs through your monitoring stack first, then enable:

```yaml
metrics:
  serviceMonitor:
    enabled: true
    labels:
      release: monitoring
  prometheusRule:
    enabled: true
    labels:
      release: monitoring
```

Set the labels to match your Prometheus instance's `serviceMonitorSelector` and `ruleSelector`; also configure its namespace selectors to include MFB's namespace. The chart reuses its pinned common library to render the ServiceMonitor and adds six Prometheus alert rules. No Alertmanager routes are changed automatically.

The ServiceMonitor selects the app's HTTP Service, scrapes `/metrics` every 60 seconds and references `MAILFALLBACK_METRICS_API_KEY` in the app environment Secret. Set that key to enable authenticated scraping. The chart derives the Secret name from `existingSecrets.app` or the enabled `inlineSecrets.app` configuration. The Secret must be in the ServiceMonitor namespace, and Prometheus Operator must be allowed to read it. Do not put a plaintext token in Helm values merely to enable monitoring.

If using a separate Secret containing the same token:

```yaml
metrics:
  serviceMonitor:
    secretName: mfb-monitoring-token
    secretKey: token
    interval: 60s
    scrapeTimeout: 10s
```

The scrape adds `mfb_deployment=<namespace>/<release>` to every sample, including `up`. Rules select this exact deployment and join on deployment, job, instance and policy/repository identity. This prevents alerts from joining an enabled policy in one instance with a failed policy in another.

You can enable the rule resource independently of the ServiceMonitor if another scraper supplies that same deployment label. Otherwise the rules match no data. Do not enable this wrapper and a second common-library monitor for the same endpoint unless duplicate scraping is intentional.

## Alert thresholds

Defaults assume daily backups:

| Helm value under `metrics.prometheusRule` | Default | Purpose |
| --- | --- | --- |
| `maxAgeSeconds` | `93600` (26 hours) | Age threshold for last success/start |
| `staleFor` | `1h` | Policy stale-success condition must persist |
| `noRunFor` | `26h` | No recent policy run start must persist |
| `configStaleFor` | `26h` | Config stale-success condition must persist |
| `failedFor` | `5m` | Failed status must persist |
| `scrapeFor` | `5m` | Scrape failure must persist |

The six rules cover policy stale success, missing recent starts, policy failure, configuration stale success, configuration failure and unavailable scraping. All backup rules filter out disabled records. Accounts without a policy do not produce per-policy alerts.

`for` adds delay **after** an age threshold is crossed; for example a known last start becomes alerting after roughly 52 hours with the defaults. Timestamp zero means unknown, so on a normal current clock it crosses the age threshold immediately and `for` supplies the initial grace. Adjust these delays for initial backups, weekly schedules and operational needs. Scrape outages can reset pending-alert timing. A target removed from discovery can disappear from `up`; monitor discovery separately.

## Existing Prometheus and Alertmanager (Docker or other deployment)

The repository's [`examples/monitoring/`](https://github.com/thekoma/mailfallback/tree/main/examples/monitoring) contains:

- `prometheus.yml`: scrape, rule-file and Alertmanager connection blocks to merge into an existing Prometheus configuration.
- `mailfallback-rules.yml`: the same six rules as Helm defaults, scoped to `mfb_deployment="mailfallback/mailfallback"`.
- `alertmanager.yml`: an example webhook receiver and grouping policy. Replace the example URL and merge the route into your existing routing tree; do not overwrite unrelated routes.
- `helm-values.yaml`: an opt-in Helm example.
- `rules.test.yml`: executable Prometheus rule tests.

Mount the metrics token as `/etc/prometheus/secrets/mfb-metrics-token` (readable only by Prometheus), the rule file as `/etc/prometheus/mailfallback-rules.yml`, and adjust the target address. `mailfallback:8000` and `alertmanager:9093` require shared container networking or equivalent DNS. Use TLS when scraping across an untrusted network. Change the deployment label in both scrape configuration and rules when choosing another identifier.

Neither example is automatically activated by the application's Docker Compose file. They are opt-in integrations with an existing monitoring stack, not an additional stack bundled with MFB.

## Validate before enabling notifications

```sh
promtool check rules examples/monitoring/mailfallback-rules.yml
promtool test rules examples/monitoring/rules.test.yml
# After mounting the token and rules at their configured paths:
promtool check config /etc/prometheus/prometheus.yml
amtool check-config /etc/alertmanager/alertmanager.yml
```

For Kubernetes, render your actual values with `helm template`, inspect the Secret reference and selectors, and verify both resources are selected by Prometheus. Then check the target is UP, query a `mailfallback_backup_*` series, and check the rule evaluation page. In Alertmanager, verify the route with a test alert before relying on external delivery. Rendering and rule-unit tests do not prove cluster discovery or delivery to your receiver.
