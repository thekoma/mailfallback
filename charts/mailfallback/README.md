# MailFallBack Helm chart

Self-hosted email backup service (mbsync + web UI) with read-only Dovecot IMAP
fallback and optional Roundcube webmail. It backs up IMAP mailboxes to a local
Maildir and serves them read-only over IMAP as a fallback.

Source & docs: https://github.com/thekoma/mailfallback

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

## Values

One row per leaf key in `values.yaml`.

| Key | Default | Description |
| --- | --- | --- |
| `image.repository` | `ghcr.io/thekoma/mailfallback` | MFB application image |
| `image.tag` | `""` | Image tag; empty = chart `appVersion` |
| `hostname` | `""` | External hostname of the MFB UI (used by `route.enabled` and NOTES) |
| `existingSecrets.app` | `mailfallback-env` | Secret with env for app + dovecot |
| `existingSecrets.roundcube` | `roundcube-env` | Secret with env for roundcube |
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
