# MailFallBack Helm chart

Self-hosted email backup service (mbsync + web UI) with read-only Dovecot IMAP
fallback and optional Roundcube webmail. It backs up IMAP mailboxes to a local
Maildir and serves them read-only over IMAP as a fallback.

Source & docs: https://github.com/thekoma/mailfallback

## Step-by-step deployment

An end-to-end walkthrough for a fresh cluster install. Each step links to the
reference section below for the full detail — this is the connective tissue, not
a replacement for those sections.

### 1. Check prerequisites

- Kubernetes ≥ 1.28 and Helm ≥ 3.14 (OCI registry support is on by default).
- An **external PostgreSQL** reachable from the cluster — the chart ships no
  database. See [Prerequisites → External PostgreSQL](#external-postgresql).
- An **RWX-capable `StorageClass`** (NFS, CephFS, …) whose storage root is
  writable by **uid 1000** (the chart sets no `fsGroup`). See
  [Prerequisites → RWX storage](#rwx-storage).

### 2. Create the namespace

```bash
kubectl create namespace mailfallback
```

(You can skip this and pass `--create-namespace` to `helm install` in step 6.)

### 3. Create the PostgreSQL role and database

MFB and Roundcube share one database (Roundcube tables use the `rc_` prefix).
Create an empty database and a login role — the app runs Alembic migrations on
first boot, so you do not create the schema yourself. Use the CloudNativePG
`Cluster` example or the plain `psql` snippet in
[Prerequisites → External PostgreSQL](#external-postgresql).

### 4. Choose the secret mode

The chart needs two sets of secret material (app + dovecot, and Roundcube).
Pick one mode:

- **Mode A — `existingSecrets` (recommended).** You create the two Secrets with
  `kubectl create secret` and the chart references them by name. Follow
  [Install → 1. Create the two Secrets](#1-create-the-two-secrets) for the exact
  `kubectl` commands and the full key list.
- **Mode B — `inlineSecrets`.** The chart renders the Secrets from values,
  intended for a vault-webhook workflow. See [Inline secrets](#inline-secrets).
  **Plaintext warning:** without a vault webhook or SOPS, inline values land in
  git, `helm get values`, and the Helm release Secret — use Mode A if that is
  not acceptable.

### 5. Write a minimal `values.yaml`

**Mode A** (references the Secrets from step 4):

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

**Mode B** (chart renders the Secrets — see [Inline secrets](#inline-secrets)
for the full key set and vault-pointer syntax):

```yaml
hostname: mail.example.com
webmail:
  enabled: true
  hostname: webmail.example.com
storage:
  maildirs:
    size: 50Gi
    storageClass: nfs-rwx
inlineSecrets:
  app:
    enabled: true
    values:                                   # + the DB_* keys, see Inline secrets
      MAILFALLBACK_DATABASE_URL: "postgresql+psycopg://mailfallback:<DB_PASSWORD>@mailfallback-db-rw:5432/mailfallback"
      MAILFALLBACK_SECRET_KEY: "<fernet-key>"
      MAILFALLBACK_SESSION_SECRET: "<random-string>"
      MAILFALLBACK_DOVECOT_API_KEY: "<random-string>"  # DOVEADM_PASSWORD auto-derived
  roundcube:
    enabled: true
    values:                                   # + the DB_* keys, see Inline secrets
      ROUNDCUBEMAIL_DES_KEY: "<24-char-random-string>"
```

### 6. Install the chart

The chart is an OCI artifact on GHCR. Chart version **equals** the app version
(CalVer), so always pass `--version` explicitly:

```bash
helm install mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  --version <VER> -n mailfallback -f values.yaml
```

Replace `<VER>` with a released version (e.g. `2026.07.4`). Version-less tag
discovery does **not** work with the CalVer scheme — Helm cannot semver-match
`2026.07.x`, so omitting `--version` fails to resolve a chart. Always pin it.

### 7. Verify first boot

On a fresh install the app first generates the Dovecot and Roundcube config
files; the dovecot and webmail pods have init containers that wait (~30s) for
those files, so they sit in `Init` until the app has written them.

```bash
kubectl -n mailfallback get pods -w        # wait for all pods to reach Running
kubectl -n mailfallback port-forward svc/mailfallback 8000:8000
curl http://localhost:8000/healthz         # -> {"status":"ok","version":"..."}
```

Then open the UI (via `hostname`/route, or the port-forward above), log in with
`admin` / `changeme` (you are forced to change the password on first login), and
add your first account.

### 8. Expose the service

Choose Gateway API (built in) or bring your own Ingress — see
[Exposure](#exposure). To reach Dovecot over TLS IMAPS externally, see
[IMAPS](#imaps). For UI/webmail SSO, see [SSO](#sso).

### 9. Upgrade and rollback

To upgrade, bump the pinned chart version (chart version == app version):

```bash
helm upgrade mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  -n mailfallback --version <YYYY.MM.INC> -f values.yaml
```

Migrations run automatically on rollout; check the release notes for migration
callouts first. See [Upgrading](#upgrading). To roll back a bad upgrade:

```bash
helm rollback mailfallback -n mailfallback
```

## Prerequisites

### External PostgreSQL

The chart does **not** ship a database — PostgreSQL is the only supported
backend and must exist before install. The app and Roundcube share the same
database (Roundcube tables use the `rc_` prefix).

CloudNativePG example (`Cluster` CR plus a bootstrap database):

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

The app image runs Alembic migrations automatically on startup, so the schema
is created for you on first boot — you only need an empty database and a role.

### RWX storage

The Maildir and config volumes default to `ReadWriteMany` so the app and
dovecot pods can share them. Provide an RWX-capable `StorageClass` (NFS,
CephFS, etc.) via `storage.*.storageClass`, or pre-create the PVCs and pass
them through `storage.*.existingClaim`.

**Root-squash / fsGroup lesson:** the chart deliberately sets **no** `fsGroup`.
On root-squashed NFS the kubelet's `applyFSGroup` chown fails and pods never
start. Instead, everything runs as uid **1000** end to end. Ensure the storage
root is writable by uid 1000 before install (the dovecot/webmail init
containers also run as uid 1000 and must be able to stat the config tree).

## Install

### 1. Create the two Secrets

The chart **never renders secret material** — it only references pre-existing
Secrets by name (`existingSecrets.app` and `existingSecrets.roundcube`). Create
them yourself. Replace every `CHANGEME` / `<...>` placeholder with a real value.

App + dovecot Secret (default name `mailfallback-env`):

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

`DOVEADM_PASSWORD` **must** hold the same value as `MAILFALLBACK_DOVECOT_API_KEY`
(the app authenticates to the doveadm HTTP API with it). Generate the crypto
material with e.g. `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
for the Fernet key and `openssl rand -hex 32` for the session/API secrets.

Optional keys (add to the same Secret only when you use the feature):
`MAILFALLBACK_OIDC_*` (UI SSO), `MAILFALLBACK_WEBMAIL_OAUTH_*` (Roundcube OAuth
bridge), `MAILFALLBACK_GOOGLE_*` / `MAILFALLBACK_MICROSOFT_*` (provider OAuth2
for account backup), `MAILFALLBACK_METRICS_API_KEY` (protects `/metrics`).

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

`ROUNDCUBEMAIL_DEFAULT_HOST` **must** be `<release>-dovecot` (for release name
`mailfallback` that is `mailfallback-dovecot`) — Roundcube connects to Dovecot
in-cluster over the plain IMAP port `31143`.

### 2. Install the chart

The chart is published as an OCI artifact. Chart version equals the app version
(CalVer), so pin the version you want.

```bash
helm install mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  -n mailfallback --create-namespace \
  -f values.yaml
```

Example `values.yaml` (~20 lines):

```yaml
image:
  tag: ""                      # empty = chart appVersion
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

## Inline secrets

By default the chart references two pre-existing Secrets (see Install above) and
never renders secret material itself. As an alternative, `inlineSecrets` lets
the chart render the Secrets for you from values — one for the app + dovecot
(`<release>-app-env`) and one for Roundcube (`<release>-roundcube-env`). When a
mode is enabled its `envFrom` switches from `existingSecrets.<name>` to the
chart-managed Secret automatically, so you do not touch `existingSecrets`.

This is designed for a vault-webhook workflow (see below), where the `values`
hold vault *pointers* rather than plaintext.

```yaml
inlineSecrets:
  app:
    enabled: true
    annotations:
      vaultsync/watch: "secret/data/mailfallback"
    values:
      MAILFALLBACK_DATABASE_URL: "postgresql+psycopg://mfb:${vault:secret/data/mailfallback#db_password}@db:5432/mfb"
      MAILFALLBACK_DB_PASSWORD: "${vault:secret/data/mailfallback#db_password}"
      MAILFALLBACK_SECRET_KEY: "${vault:secret/data/mailfallback#secret_key}"
      MAILFALLBACK_SESSION_SECRET: "${vault:secret/data/mailfallback#session_secret}"
      MAILFALLBACK_DOVECOT_API_KEY: "${vault:secret/data/mailfallback#api_key}"
      # DOVEADM_PASSWORD auto-derived
```

The `values` map holds the same keys documented under Install — the whole
required set for the app Secret, and the `ROUNDCUBEMAIL_*` set for the roundcube
Secret. Anything you place under `annotations` is copied verbatim onto the
rendered Secret's `metadata.annotations`.

**Plaintext warning:** Without a vault webhook or SOPS, inline values land in git, `helm get values` output and the Helm release Secret. Use `existingSecrets` if that is not acceptable.

### Derivations

Three keys are filled in for you when you omit them, so you only supply the
material a vault actually holds. Supplying the key yourself always wins.

| Key | Derived from | How to override |
| --- | --- | --- |
| `DOVEADM_PASSWORD` (app) | Copy of `MAILFALLBACK_DOVECOT_API_KEY` in the same `values` (string copy — a vault pointer survives intact) | Set `DOVEADM_PASSWORD` explicitly in `inlineSecrets.app.values` |
| `ROUNDCUBEMAIL_DEFAULT_HOST` (roundcube) | The in-cluster dovecot Service name `<release>-dovecot` | Set `ROUNDCUBEMAIL_DEFAULT_HOST` explicitly in `inlineSecrets.roundcube.values` |
| `ROUNDCUBEMAIL_DEFAULT_PORT` (roundcube) | The plain IMAP port `"31143"` | Set `ROUNDCUBEMAIL_DEFAULT_PORT` explicitly in `inlineSecrets.roundcube.values` |

### Vault-webhook pattern

The intended use is a mutating webhook (e.g. Vault Agent Injector, Vault Secrets
Operator, or an in-cluster syncer) that resolves `${vault:...}` pointers into
the live Secret after Helm renders it. The chart's job is only to emit the
Secret shell plus the pointers and annotations; the webhook does the resolution.

Point the webhook at the Secret with `inlineSecrets.<mode>.annotations` — those
annotations land on the rendered Secret, which is what the webhook watches.
Because ArgoCD setups typically `ignoreDifferences` on Secret `data` (so the
webhook-resolved payload is not reverted on every sync), rotation does **not**
flow through a data change. Instead bump a trigger annotation — the
`vaultsync/trigger`-style pattern — so the annotation diff forces a
re-reconcile and the webhook re-pulls the secret:

```yaml
inlineSecrets:
  app:
    enabled: true
    annotations:
      vaultsync/watch: "secret/data/mailfallback"
      vaultsync/trigger: "2026-07-17T10:00:00Z"   # bump to rotate
```

## Values

One row per leaf key in `values.yaml`.

| Key | Default | Description |
| --- | --- | --- |
| `image.repository` | `ghcr.io/thekoma/mailfallback` | MFB application image |
| `image.tag` | `""` | Image tag; empty = chart `appVersion` |
| `hostname` | `""` | External hostname of the MFB UI (used by `route.enabled` and NOTES) |
| `existingSecrets.app` | `mailfallback-env` | Secret with env for app + dovecot |
| `existingSecrets.roundcube` | `roundcube-env` | Secret with env for roundcube |
| `inlineSecrets.app.enabled` | `false` | Render the app+dovecot Secret `<release>-app-env` from values instead of using `existingSecrets.app` |
| `inlineSecrets.app.values` | `{}` | Env keys for the rendered app Secret; `DOVEADM_PASSWORD` auto-derived from `MAILFALLBACK_DOVECOT_API_KEY` when absent |
| `inlineSecrets.app.annotations` | `{}` | Extra `metadata.annotations` on the rendered app Secret (e.g. vault-webhook pointers) |
| `inlineSecrets.roundcube.enabled` | `false` | Render the roundcube Secret `<release>-roundcube-env` from values instead of using `existingSecrets.roundcube` |
| `inlineSecrets.roundcube.values` | `{}` | Env keys for the rendered roundcube Secret; `ROUNDCUBEMAIL_DEFAULT_HOST`/`PORT` auto-derived when absent |
| `inlineSecrets.roundcube.annotations` | `{}` | Extra `metadata.annotations` on the rendered roundcube Secret |
| `dovecot.image.repository` | `dovecot/dovecot` | Dovecot image |
| `dovecot.image.tag` | `2.4.4` | Dovecot image tag |
| `webmail.enabled` | `true` | Deploy Roundcube webmail |
| `webmail.hostname` | `""` | External hostname of webmail (route + `MAILFALLBACK_WEBMAIL_URL`) |
| `webmail.image.repository` | `roundcube/roundcubemail` | Roundcube image |
| `webmail.image.tag` | `1.7.1-apache` | Roundcube image tag |
| `tika.enabled` | `true` | Deploy Apache Tika (content search) |
| `tika.image.repository` | `apache/tika` | Tika image |
| `tika.image.tag` | `3.3.1.0-full` | Tika image tag |
| `initImage.repository` | `docker.io/library/busybox` | Image for the wait-config init containers |
| `initImage.tag` | `"1.37"` | Init image tag |
| `storage.maildirs.size` | `20Gi` | Maildir PVC size |
| `storage.maildirs.storageClass` | `""` | Maildir StorageClass (cluster default if empty) |
| `storage.maildirs.accessModes` | `[ReadWriteMany]` | Maildir access modes (RWX drives `MAILFALLBACK_DOVECOT_NFS`) |
| `storage.maildirs.existingClaim` | `""` | Reuse an existing PVC (takes precedence over size/class) |
| `storage.maildirs2.enabled` | `false` | Mount a second maildir volume at `/data/mailboxes2` |
| `storage.maildirs2.size` | `10Gi` | Second maildir PVC size |
| `storage.maildirs2.storageClass` | `""` | Second maildir StorageClass |
| `storage.maildirs2.accessModes` | `[ReadWriteMany]` | Second maildir access modes |
| `storage.maildirs2.existingClaim` | `""` | Reuse an existing PVC for the second maildir |
| `storage.dovecotConfd.size` | `1Gi` | Dovecot conf.d PVC size |
| `storage.dovecotConfd.storageClass` | `""` | Dovecot conf.d StorageClass |
| `storage.dovecotConfd.accessModes` | `[ReadWriteMany]` | Dovecot conf.d access modes |
| `storage.dovecotConfd.existingClaim` | `""` | Reuse an existing PVC for dovecot conf.d |
| `storage.webmailConf.size` | `1Gi` | Roundcube config PVC size |
| `storage.webmailConf.storageClass` | `""` | Roundcube config StorageClass |
| `storage.webmailConf.accessModes` | `[ReadWriteMany]` | Roundcube config access modes |
| `storage.webmailConf.existingClaim` | `""` | Reuse an existing PVC for roundcube config |
| `imaps.enabled` | `false` | Expose IMAPS (31993) with TLS; sets `MAILFALLBACK_DOVECOT_TLS` and mounts the cert |
| `imaps.existingTlsSecret` | `""` | `kubernetes.io/tls` Secret mounted at `/etc/dovecot/ssl` |
| `imaps.certificate.enabled` | `false` | Create a cert-manager `Certificate` for the IMAPS cert |
| `imaps.certificate.secretName` | `mailfallback-imaps-tls` | Secret the Certificate writes (use as `existingTlsSecret`) |
| `imaps.certificate.issuerRef.name` | `""` | cert-manager issuer name (required when certificate enabled) |
| `imaps.certificate.issuerRef.kind` | `ClusterIssuer` | cert-manager issuer kind |
| `imaps.certificate.dnsNames` | `[]` | DNS names on the IMAPS certificate |
| `imaps.service.type` | `LoadBalancer` | Service type for the IMAPS Service |
| `imaps.service.loadBalancerIP` | `""` | Static LB IP for the IMAPS Service |
| `imaps.service.annotations` | `{}` | Annotations on the IMAPS Service |
| `route.enabled` | `false` | Create Gateway API HTTPRoutes for app + webmail |
| `route.gateway.name` | `""` | Parent Gateway name (required with `route.enabled`) |
| `route.gateway.namespace` | `""` | Parent Gateway namespace |
| `route.noSsoPolicy` | `false` | Emit Envoy Gateway SecurityPolicies opting routes out of gateway extAuth |
| `resources.app.requests` | `{cpu: 100m, memory: 256Mi}` | App container resource requests |
| `resources.app.limits` | `{cpu: "2", memory: 1Gi}` | App container resource limits |
| `resources.dovecot.requests` | `{cpu: 100m, memory: 128Mi}` | Dovecot container resource requests |
| `resources.dovecot.limits` | `{cpu: "1", memory: 1Gi}` | Dovecot container resource limits |
| `resources.webmail.requests` | `{cpu: 50m, memory: 128Mi}` | Webmail container resource requests |
| `resources.webmail.limits` | `{cpu: "1", memory: 512Mi}` | Webmail container resource limits |
| `resources.tika.requests` | `{cpu: 100m, memory: 512Mi}` | Tika container resource requests |
| `resources.tika.limits` | `{cpu: "2", memory: 2Gi}` | Tika container resource limits |

## Exposure

### Gateway API (built in)

Set `route.enabled=true` and point at an existing Gateway. The chart creates an
`HTTPRoute` for the app (`hostname` → Service `mailfallback:8000`) and, when
`webmail.enabled`, one for webmail (`webmail.hostname` → Service
`<release>-webmail:80`):

```yaml
route:
  enabled: true
  gateway:
    name: eg
    namespace: envoy-gateway-system
```

If your gateway enforces a gateway-level external-auth filter, set
`route.noSsoPolicy=true` to emit Envoy Gateway `SecurityPolicy` objects that opt
both routes out of it (MFB has native OIDC + local auth; Roundcube has native
OAuth).

### Bring your own Ingress

Leave `route.enabled=false` and create your own Ingress. Route the UI host to
Service `mailfallback` port `8000` and the webmail host to Service
`<release>-webmail` port `80` (e.g. `mailfallback-webmail:80`).

## IMAPS

By default Dovecot is reachable only in-cluster on the plain IMAP port `31143`.
To expose TLS IMAPS on `31993` externally, set `imaps.enabled=true` and provide
a `kubernetes.io/tls` Secret via `imaps.existingTlsSecret`, or let cert-manager
create it:

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

**Cert-renewal limitation:** Dovecot loads the certificate at startup and does
**not** reload it automatically. After cert-manager renews the certificate you
must restart the dovecot pod (`kubectl -n mailfallback rollout restart
deploy/<release>-dovecot`) for the new cert to take effect.

## SSO

Configure two independent providers in your IdP (examples use Authentik). Set
the matching secret keys and redirect URIs exactly.

**MFB UI (OIDC)** — `MAILFALLBACK_OIDC_*` in `existingSecrets.app`. Authentik
OAuth2/OIDC provider, redirect URI:

```
https://<hostname>/auth/oidc/callback
```

**Roundcube webmail (OAuth)** — `MAILFALLBACK_WEBMAIL_OAUTH_*` in
`existingSecrets.app`. Second Authentik OAuth2/OIDC provider, redirect URI:

```
https://<webmail.hostname>/index.php/login/oauth
```

Replace `<hostname>` and `<webmail.hostname>` with your `hostname` and
`webmail.hostname` values.

## Upgrading

Versioning is CalVer (`YYYY.MM.INC`) and the **chart version equals the app
version** — there is no separate chart-version axis. To upgrade, bump the chart
version you install:

```bash
helm upgrade mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  -n mailfallback --version <YYYY.MM.INC> -f values.yaml
```

The app runs database migrations automatically on startup, so a rollout applies
any schema changes. Check the GitHub release notes for migration callouts before
upgrading.
