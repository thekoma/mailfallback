# Kubernetes

MailFallBack ships an **official Helm chart**, published as an OCI artifact on GHCR. This page walks through a fresh cluster install; the [chart README](https://github.com/thekoma/mailfallback/tree/main/charts/mailfallback) is the full reference (complete values table, inline-secrets/vault workflow, derivations).

```bash
helm install mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  --version 2026.07.4 -n mailfallback --create-namespace \
  -f values.yaml
```

!!! warning "Always pin `--version`"
    The chart version **equals** the app version (CalVer, `YYYY.MM.INC`). Version-less tag discovery does **not** work — Helm cannot semver-match `2026.07.x`, so omitting `--version` fails to resolve a chart. Pick a version from the [GitHub releases](https://github.com/thekoma/mailfallback/releases).

## Prerequisites

- **Kubernetes ≥ 1.28** and **Helm ≥ 3.14** (OCI registry support is on by default)
- An **external PostgreSQL** reachable from the cluster — the chart ships no database
- An **RWX-capable `StorageClass`** (NFS, CephFS, …) whose storage root is writable by **uid 1000**

### External PostgreSQL

PostgreSQL is the only supported backend and must exist before install. The app and Roundcube share the same database (Roundcube tables use the `rc_` prefix). Create an empty database and a login role — the app runs Alembic migrations on first boot, so you never create the schema yourself.

CloudNativePG example:

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: mailfallback-db
  namespace: mailfallback
spec:
  instances: 3
  storage:
    size: 10Gi
  bootstrap:
    initdb:
      database: mailfallback
      owner: mailfallback
```

Or, on any existing PostgreSQL server:

```sql
CREATE ROLE mailfallback WITH LOGIN PASSWORD '<DB_PASSWORD>';
CREATE DATABASE mailfallback OWNER mailfallback;
```

### RWX storage

The Maildir and config volumes default to `ReadWriteMany` so the app and Dovecot pods can share them. Provide an RWX-capable `StorageClass` via `storage.*.storageClass`, or pre-create the PVCs and pass them through `storage.*.existingClaim`.

!!! note "Root-squash / fsGroup"
    The chart deliberately sets **no** `fsGroup`: on root-squashed NFS the kubelet's `applyFSGroup` chown fails and pods never start. Instead everything runs as uid **1000** end to end — make sure the storage root is writable by uid 1000 before installing.

## Secrets

The chart supports two modes:

- **`existingSecrets` (recommended, default)** — you create the two Secrets with `kubectl` and the chart references them by name. The chart never renders secret material.
- **`inlineSecrets`** — the chart renders the Secrets from values, designed for a vault-webhook workflow where values hold vault *pointers*, not plaintext. See [Inline secrets in the chart README](https://github.com/thekoma/mailfallback/tree/main/charts/mailfallback#inline-secrets).

App + Dovecot Secret (default name `mailfallback-env`):

```bash
kubectl -n mailfallback create secret generic mailfallback-env \
  --from-literal=MAILFALLBACK_DATABASE_URL='postgresql+psycopg://mailfallback:<DB_PASSWORD>@mailfallback-db-rw:5432/mailfallback' \
  --from-literal=MAILFALLBACK_DB_HOST='mailfallback-db-rw' \
  --from-literal=MAILFALLBACK_DB_PORT='5432' \
  --from-literal=MAILFALLBACK_DB_NAME='mailfallback' \
  --from-literal=MAILFALLBACK_DB_USER='mailfallback' \
  --from-literal=MAILFALLBACK_DB_PASSWORD='<DB_PASSWORD>' \
  --from-literal=MAILFALLBACK_SECRET_KEY='<fernet-key>' \
  --from-literal=MAILFALLBACK_SESSION_SECRET='<random-string>' \
  --from-literal=MAILFALLBACK_DOVECOT_API_KEY='<random-string>' \
  --from-literal=DOVEADM_PASSWORD='<same-as-MAILFALLBACK_DOVECOT_API_KEY>'
```

Generate the Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and the session/API secrets with `openssl rand -hex 32`. `DOVEADM_PASSWORD` **must** hold the same value as `MAILFALLBACK_DOVECOT_API_KEY`.

Roundcube Secret (default name `roundcube-env`, needed when `webmail.enabled`):

```bash
kubectl -n mailfallback create secret generic roundcube-env \
  --from-literal=ROUNDCUBEMAIL_DEFAULT_HOST='mailfallback-dovecot' \
  --from-literal=ROUNDCUBEMAIL_DEFAULT_PORT='31143' \
  --from-literal=ROUNDCUBEMAIL_DB_TYPE='pgsql' \
  --from-literal=ROUNDCUBEMAIL_DB_HOST='mailfallback-db-rw' \
  --from-literal=ROUNDCUBEMAIL_DB_PORT='5432' \
  --from-literal=ROUNDCUBEMAIL_DB_NAME='mailfallback' \
  --from-literal=ROUNDCUBEMAIL_DB_USER='mailfallback' \
  --from-literal=ROUNDCUBEMAIL_DB_PASSWORD='<DB_PASSWORD>' \
  --from-literal=ROUNDCUBEMAIL_SKIN='elastic' \
  --from-literal=ROUNDCUBEMAIL_PLUGINS='archive,zipdownload,subscriptions_option' \
  --from-literal=ROUNDCUBEMAIL_DES_KEY='<24-char-random-string>'
```

`ROUNDCUBEMAIL_DEFAULT_HOST` **must** be `<release>-dovecot` (for release name `mailfallback` that is `mailfallback-dovecot`) — Roundcube connects to Dovecot in-cluster over the plain IMAP port `31143`.

Optional keys for the app Secret (add only when you use the feature): `MAILFALLBACK_OIDC_*` (UI SSO), `MAILFALLBACK_WEBMAIL_OAUTH_*` (Roundcube OAuth bridge), `MAILFALLBACK_GOOGLE_*` / `MAILFALLBACK_MICROSOFT_*` (provider OAuth2 for account backup), `MAILFALLBACK_METRICS_API_KEY` (protects `/metrics`).

## Minimal values.yaml

```yaml
hostname: mail.example.com
webmail:
  enabled: true
  hostname: webmail.example.com
existingSecrets:
  app: mailfallback-env
  roundcube: roundcube-env
storage:
  maildirs:
    size: 50Gi
    storageClass: nfs-rwx
route:
  enabled: true
  gateway:
    name: eg
    namespace: envoy-gateway-system
```

See the [full values table](https://github.com/thekoma/mailfallback/tree/main/charts/mailfallback#values) for everything else (second maildir volume, resources, images, init image, …).

## Verify first boot

On a fresh install the app first generates the Dovecot and Roundcube config files; the dovecot and webmail pods have init containers that wait (~30s) for those files, so they sit in `Init` until the app has written them.

```bash
kubectl -n mailfallback get pods -w        # wait for all pods to reach Running
kubectl -n mailfallback port-forward svc/mailfallback 8000:8000
curl http://localhost:8000/healthz         # -> {"status":"ok","version":"..."}
```

Then open the UI (via `hostname`/route, or the port-forward above), log in with `admin` / `changeme1234!` (you are forced to change the password on first login), and add your first account.

## Exposure

### Gateway API (built in)

Set `route.enabled=true` and point at an existing Gateway. The chart creates an `HTTPRoute` for the app (`hostname` → Service `mailfallback:8000`) and, when `webmail.enabled`, one for webmail (`webmail.hostname` → Service `<release>-webmail:80`).

If your gateway enforces a gateway-level external-auth filter, set `route.noSsoPolicy=true` to emit Envoy Gateway `SecurityPolicy` objects that opt both routes out of it (MFB has native OIDC + local auth; Roundcube has native OAuth).

### Bring your own Ingress

Leave `route.enabled=false` and create your own Ingress. Route the UI host to Service `mailfallback` port `8000` and the webmail host to Service `<release>-webmail` port `80`.

## IMAPS

By default Dovecot is reachable only in-cluster on the plain IMAP port `31143`. To expose TLS IMAPS on `31993` externally, set `imaps.enabled=true` and provide a `kubernetes.io/tls` Secret via `imaps.existingTlsSecret`, or let cert-manager create it:

```yaml
imaps:
  enabled: true
  existingTlsSecret: mailfallback-imaps-tls
  certificate:
    enabled: true
    secretName: mailfallback-imaps-tls
    issuerRef:
      name: letsencrypt
      kind: ClusterIssuer
    dnsNames:
      - imap.example.com
  service:
    type: LoadBalancer
    loadBalancerIP: 203.0.113.10
```

!!! warning "Cert renewal requires a Dovecot restart"
    Dovecot loads the certificate at startup and does **not** reload it automatically. After cert-manager renews the certificate, restart the Dovecot pod: `kubectl -n mailfallback rollout restart deploy/<release>-dovecot`.

## SSO

Configure two independent providers in your IdP (examples use Authentik), with keys in the app Secret:

- **MFB UI (OIDC)** — `MAILFALLBACK_OIDC_*`, redirect URI `https://<hostname>/auth/oidc/callback`
- **Roundcube webmail (OAuth)** — `MAILFALLBACK_WEBMAIL_OAUTH_*`, redirect URI `https://<webmail.hostname>/index.php/login/oauth`

## Monitoring

Dovecot exposes Prometheus metrics on port 9900. MFB exposes metrics at `/metrics` (protected by `MAILFALLBACK_METRICS_API_KEY`). Create ServiceMonitor resources if using the Prometheus Operator.

## Upgrading

Bump the pinned chart version (chart version == app version):

```bash
helm upgrade mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  -n mailfallback --version <YYYY.MM.INC> -f values.yaml
```

Migrations run automatically on rollout; check the [release notes](https://github.com/thekoma/mailfallback/releases) for migration callouts first. To roll back a bad upgrade:

```bash
helm rollback mailfallback -n mailfallback
```

## Deploying without the chart

If you prefer your own manifests, the key constraints are:

- **Shared maildir storage** — both the MFB container (writes via mbsync) and Dovecot (reads via IMAP) need the same data: use an RWX PVC across pods, or run Dovecot as a sidecar in the MFB pod with a single-node PVC.
- **UID consistency** — all containers run as uid/gid **1000**; set `runAsUser: 1000` / `runAsGroup: 1000` in the pod security context (avoid `fsGroup` on root-squashed NFS).
- **Config volumes** — MFB generates the Dovecot and Roundcube configuration at boot; share `/confs/dovecot` and `/confs/webmail` with the consuming containers (Dovecot mounts its config at `/etc/dovecot/conf.d`) and gate their startup on the files existing.
- **Probes** — liveness `GET /healthz`, readiness `GET /readyz`, both on port 8000.
- **Ports** — app `8000` (HTTP), Dovecot `31143` (IMAP) / `31993` (IMAPS) / `9900` (metrics) / `8080` (doveadm HTTP API, in-cluster only).

The [chart templates](https://github.com/thekoma/mailfallback/tree/main/charts/mailfallback/templates) are the reference implementation for all of the above.
