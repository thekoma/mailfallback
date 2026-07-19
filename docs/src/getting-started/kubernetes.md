# Kubernetes

MailFallBack ships an official Helm chart, published as an OCI artifact on GHCR.
The chart is the supported way to run MFB on Kubernetes — it renders the app,
Dovecot, Roundcube and Tika, wires their Services and storage together, and
encodes the single-writer and uid-1000 constraints that a hand-built manifest
set has to rediscover the hard way.

```bash
helm install mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  --version 2026.07.4 -n mailfallback --create-namespace -f values.yaml
```

The chart version **equals** the app version and both follow CalVer
(`YYYY.MM.INC`). There is no separate chart-version axis: to run app
`2026.07.4` you install chart `2026.07.4`.

!!! warning "Always pass `--version`"
    Version-less tag discovery does **not** work with the CalVer scheme. Helm
    cannot semver-match a tag like `2026.07.4` (the leading zero and the shape
    are not valid semver ranges), so `helm pull`/`helm show`/`helm install`
    without `--version` fails to resolve a chart. Always pin an explicit
    released version.

The full values reference lives with the chart:
[charts/mailfallback on GitHub](https://github.com/thekoma/mailfallback/tree/main/charts/mailfallback).

## Step-by-step deployment

### 1. Prerequisites

- **Kubernetes ≥ 1.28** and **Helm ≥ 3.14** (OCI registry support is on by
  default in modern Helm).
- An **external PostgreSQL** reachable from the cluster. The chart ships no
  database — PostgreSQL is the only supported backend and must exist before
  install. The app and Roundcube share the same database (Roundcube tables use
  the `rc_` prefix).
- An **RWX-capable `StorageClass`** (NFS, CephFS, Longhorn RWX, …) whose storage
  root is writable by **uid 1000**. The chart sets **no** `fsGroup` on purpose,
  so it does not rely on the kubelet to chown the volume — see
  [Storage and uid 1000](#storage-and-uid-1000).

### 2. Create the namespace

```bash
kubectl create namespace mailfallback
```

You can skip this and pass `--create-namespace` to `helm install` instead.

### 3. Create the PostgreSQL role and database

Create an empty database and a login role. The app image runs Alembic
migrations on first boot, so you do not create the schema yourself — an empty
database is enough.

```sql
CREATE ROLE mailfallback WITH LOGIN PASSWORD '<DB_PASSWORD>';
CREATE DATABASE mailfallback OWNER mailfallback;
```

On a CloudNativePG cluster you can let the operator bootstrap it instead
(`bootstrap.initdb.database: mailfallback`, `owner: mailfallback`).

### 4. Choose the secret mode

The chart needs two sets of secret material: one for the app **and** Dovecot
(they share the same Secret), and one for Roundcube. Pick one mode.

=== "existingSecrets (default)"

    You create the two Secrets yourself and the chart references them by name.
    The chart **never renders secret material** in this mode.

    ```bash
    kubectl -n mailfallback create secret generic mailfallback-env \
      --from-literal=MAILFALLBACK_DATABASE_URL='postgresql+psycopg://mailfallback:<DB_PASSWORD>@<db-host>:5432/mailfallback' \
      --from-literal=MAILFALLBACK_DB_HOST='<db-host>' \
      --from-literal=MAILFALLBACK_DB_PORT='5432' \
      --from-literal=MAILFALLBACK_DB_NAME='mailfallback' \
      --from-literal=MAILFALLBACK_DB_USER='mailfallback' \
      --from-literal=MAILFALLBACK_DB_PASSWORD='<DB_PASSWORD>' \
      --from-literal=MAILFALLBACK_SECRET_KEY='<fernet-key>' \
      --from-literal=MAILFALLBACK_SESSION_SECRET='<random-string>' \
      --from-literal=MAILFALLBACK_DOVECOT_API_KEY='<random-string>' \
      --from-literal=DOVEADM_PASSWORD='<same-as-MAILFALLBACK_DOVECOT_API_KEY>'
    ```

    `DOVEADM_PASSWORD` **must** hold the same value as
    `MAILFALLBACK_DOVECOT_API_KEY` — the app authenticates to the doveadm HTTP
    API with it. Add the Roundcube Secret (`roundcube-env`) too when
    `webmail.enabled` — in `existingSecrets` mode the chart derives **nothing**,
    so it needs the full `ROUNDCUBEMAIL_*` set (including `ROUNDCUBEMAIL_DEFAULT_PORT`,
    `ROUNDCUBEMAIL_DES_KEY` and the DB keys); see the
    [chart README](https://github.com/thekoma/mailfallback/tree/main/charts/mailfallback#1-create-the-two-secrets)
    for the complete list. Its `ROUNDCUBEMAIL_DEFAULT_HOST` must be
    `<release>-dovecot` (for release `mailfallback`, that is `mailfallback-dovecot`).

=== "inlineSecrets"

    The chart renders the Secrets from values instead. This is intended for a
    vault-webhook workflow, where the values hold vault *pointers*
    (`${vault:secret/data/x#key}`) that a mutating webhook resolves after Helm
    renders the Secret. Enabling a mode automatically switches its `envFrom` to
    the chart-managed Secret.

    ```yaml
    inlineSecrets:
      app:
        enabled: true
        values:
          MAILFALLBACK_DATABASE_URL: "postgresql+psycopg://mailfallback:<DB_PASSWORD>@<db-host>:5432/mailfallback"
          MAILFALLBACK_SECRET_KEY: "<fernet-key>"
          MAILFALLBACK_SESSION_SECRET: "<random-string>"
          MAILFALLBACK_DOVECOT_API_KEY: "<random-string>"
      roundcube:
        enabled: true
        values:
          ROUNDCUBEMAIL_DES_KEY: "<24-char-random-string>"
    ```

    !!! danger "Plaintext warning"
        Without a vault webhook or SOPS, inline values land in git,
        `helm get values` output, and the Helm release Secret. Use
        `existingSecrets` if that is not acceptable.

    Three keys are **derived** when omitted, so you only supply what a vault
    actually holds — and setting the key yourself always wins:

    | Derived key | Filled in from |
    | --- | --- |
    | `DOVEADM_PASSWORD` (app) | a copy of `MAILFALLBACK_DOVECOT_API_KEY` (a vault pointer survives the copy intact) |
    | `ROUNDCUBEMAIL_DEFAULT_HOST` (roundcube) | the in-cluster Dovecot Service name `<release>-dovecot` |
    | `ROUNDCUBEMAIL_DEFAULT_PORT` (roundcube) | the plain IMAP port `31143` |

### 5. Write a minimal `values.yaml`

For the default `existingSecrets` mode:

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

### 6. Install the chart

```bash
helm install mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  --version 2026.07.4 -n mailfallback -f values.yaml
```

### 7. Verify first boot

On a fresh install the app first generates the Dovecot and Roundcube config
files. The dovecot and webmail pods have init containers that wait (~30s) for
those files, so they sit in `Init` until the app has written them — this is
expected, not a failure.

```bash
kubectl -n mailfallback get pods -w         # wait for all pods to reach Running
kubectl -n mailfallback port-forward svc/mailfallback 8000:8000
curl http://localhost:8000/healthz          # -> {"status":"ok", ...}
```

Then open the UI (via `hostname`/route, or the port-forward above) and log in
with `admin` / `changeme` — you are forced to change the password on first
login.

### 8. Expose the service

Choose the built-in Gateway API support or bring your own Ingress.

- **Gateway API (built in).** Set `route.enabled=true` and point at an existing
  Gateway. The chart creates an `HTTPRoute` for the app
  (`hostname` → Service `mailfallback:8000`) and, when `webmail.enabled`, one
  for webmail (`webmail.hostname` → Service `<release>-webmail:80`). If your
  gateway enforces a gateway-level external-auth filter, set
  `route.noSsoPolicy=true` to emit Envoy Gateway `SecurityPolicy` objects that
  opt both routes out of it (MFB has native OIDC + local auth, Roundcube has
  native OAuth).
- **Bring your own Ingress.** Leave `route.enabled=false` and route the UI host
  to Service `mailfallback` port `8000` and the webmail host to Service
  `<release>-webmail` port `80`.

By default Dovecot is reachable only in-cluster on the plain IMAP port `31143`.
To expose TLS IMAPS on `31993` externally, set `imaps.enabled=true` and provide
a `kubernetes.io/tls` Secret via `imaps.existingTlsSecret` (or let cert-manager
create one with `imaps.certificate.enabled=true`).

For UI/webmail SSO redirect URIs, see [SSO / OIDC](../admin-guide/sso.md).

### 9. Upgrade and rollback

To upgrade, bump the pinned version (chart version == app version). The app runs
migrations automatically on rollout, so a rollout applies any schema changes —
check the GitHub release notes for migration callouts first.

```bash
helm upgrade mailfallback oci://ghcr.io/thekoma/charts/mailfallback \
  --version <YYYY.MM.INC> -n mailfallback -f values.yaml

helm rollback mailfallback -n mailfallback     # revert a bad upgrade
```

## What the chart renders

- **Four controllers** — the app (`mailfallback`), `dovecot`, and — when their
  toggles are on — `webmail` (Roundcube, `webmail.enabled`, default true) and
  `tika` (`tika.enabled`, default true). The app and dovecot controllers use the
  `Recreate` update strategy: only one mbsync writer and one Dovecot index owner
  may touch the shared maildirs at a time, so the old pod must be gone before the
  new one starts.
- **Services.** The app Service is force-named `mailfallback` on port `8000` —
  the Dovecot Lua userdb hardcodes `http://mailfallback:8000`, so this name is a
  contract, not a default. The `dovecot` Service exposes IMAP `31143`, doveadm
  `8080` and metrics `9900`; the webmail Service exposes `80`; the tika Service
  exposes `9998`. An extra `dovecot-imaps` Service (`31993`) appears only when
  `imaps.enabled`.
- **Optional resources, default off.** The cert-manager `Certificate`
  (`imaps.certificate.enabled`), the Gateway API `HTTPRoute`s (`route.enabled`),
  and the Envoy Gateway `SecurityPolicy` pair (`route.noSsoPolicy`) are all
  disabled by default.
- **Storage.** The chart manages PVCs for the maildirs, the Dovecot `conf.d`
  tree and (when webmail is enabled) the Roundcube config, plus an optional
  second maildir (`storage.maildirs2.enabled`). Each can instead reuse a
  pre-created PVC via its `existingClaim` — point every volume at an
  `existingClaim` and the chart renders no PVCs of its own.

## Key concepts

These carry over from a hand-rolled deployment; the chart applies them for you,
but they explain *why* it is shaped the way it is.

### Storage and uid 1000

The maildir and config volumes are `ReadWriteMany` so the app and dovecot pods
can share them. The chart deliberately sets **no** `fsGroup`: on root-squashed
NFS the kubelet's `applyFSGroup` chown fails and pods never start. Instead,
everything — including the init containers — runs as **uid 1000** end to end, so
file ownership stays consistent across containers that read and write the same
maildir. Ensure the storage root is writable by uid 1000 before install.

### Config-generation init wait

MFB is the control plane for Dovecot and Roundcube config: on boot it runs
migrations, then writes the Dovecot `conf.d` files and the Roundcube
`custom.php` into the shared config volumes. The dovecot and webmail pods each
run a `wait-config` init container that blocks until its awaited file exists, so
those pods stay in `Init` on a first boot until the app has generated the
config. This is why the app and dovecot cannot simply start in parallel.

## Container images

The chart pins every image; override tags under the matching values key. Verify
against `charts/mailfallback/values.yaml` for the version you install.

| Component | Image | Values key |
|-----------|-------|------------|
| MFB app | `ghcr.io/thekoma/mailfallback:<CalVer>` (empty tag = chart `appVersion`) | `image.*` |
| Dovecot | `dovecot/dovecot:2.4.4` | `dovecot.image.*` |
| Roundcube | `roundcube/roundcubemail:1.7.1-apache` | `webmail.image.*` |
| Tika | `apache/tika:3.3.1.0-full` | `tika.image.*` |
| Init (wait-config) | `docker.io/library/busybox:1.37` | `initImage.*` |

PostgreSQL is **not** part of the chart — bring your own (see the prerequisites).

## See also

- [Full values reference](https://github.com/thekoma/mailfallback/tree/main/charts/mailfallback)
  — every `values.yaml` key with defaults and descriptions.
- [SSO / OIDC](../admin-guide/sso.md) — redirect URIs and IdP setup for the UI
  and webmail.
