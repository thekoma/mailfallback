#!/usr/bin/env python3
"""Render-assert the mailfallback chart invariants (the journal's lessons)."""
# ruff: noqa: T201, S607  # CI script prints its verdict; `helm` resolved from PATH

import subprocess
import sys

import yaml

CHART = "charts/mailfallback"


def render(fixture):
    out = subprocess.run(
        [
            "helm",
            "template",
            "mailfallback",
            CHART,
            "-n",
            "mailfallback",
            "-f",
            f"{CHART}/ci/{fixture}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def by(docs, kind):
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == kind}


failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


NO_SECRET_FIXTURES = ("minimal-values.yaml", "full-values.yaml", "existing-claims-values.yaml")

for fixture in (*NO_SECRET_FIXTURES, "inline-secrets-values.yaml"):
    docs = render(fixture)
    deps, svcs = by(docs, "Deployment"), by(docs, "Service")
    # 1. service name constraint (Lua)
    check("mailfallback" in svcs, f"{fixture}: Service 'mailfallback' missing")
    check(
        svcs["mailfallback"]["spec"]["ports"][0]["port"] == 8000, f"{fixture}: app svc port != 8000"
    )
    # selector must match the app deployment labels
    dep = deps["mailfallback"]
    sel = svcs["mailfallback"]["spec"]["selector"]
    lbl = dep["spec"]["template"]["metadata"]["labels"]
    check(all(lbl.get(k) == v for k, v in sel.items()), f"{fixture}: app svc selector mismatch")
    # 2. no fsGroup anywhere
    for name, d in deps.items():
        sc = d["spec"]["template"]["spec"].get("securityContext") or {}
        check("fsGroup" not in sc, f"{fixture}: fsGroup set on {name}")
    # 3. proxy headers env on app
    app_env = {
        e["name"]: e.get("value")
        for e in dep["spec"]["template"]["spec"]["containers"][0].get("env", [])
    }
    check(app_env.get("FORWARDED_ALLOW_IPS") == "*", f"{fixture}: FORWARDED_ALLOW_IPS missing")
    # 5. strategies
    check(dep["spec"]["strategy"]["type"] == "Recreate", f"{fixture}: app strategy != Recreate")
    check(
        deps["mailfallback-dovecot"]["spec"]["strategy"]["type"] == "Recreate",
        f"{fixture}: dovecot strategy != Recreate",
    )
    # 4. init gates
    dov_inits = deps["mailfallback-dovecot"]["spec"]["template"]["spec"].get("initContainers", [])
    check(
        any("mfb-auth.conf" in " ".join(c.get("command", [])) for c in dov_inits),
        f"{fixture}: dovecot init gate missing",
    )
    # 6. NFS flag follows RWX
    check(
        app_env.get("MAILFALLBACK_DOVECOT_NFS") == "true",
        f"{fixture}: DOVECOT_NFS not true (default values are RWX)",
    )
    # no secret material rendered (defaults / existingSecrets mode)
    if fixture in NO_SECRET_FIXTURES:
        check(not by(docs, "Secret"), f"{fixture}: chart rendered a Secret")

# fixture-specific
docs_min = render("minimal-values.yaml")
check(
    not by(docs_min, "Certificate")
    and not by(docs_min, "HTTPRoute")
    and not by(docs_min, "SecurityPolicy"),
    "minimal: optional objects rendered while off",
)
docs_full = render("full-values.yaml")
for kind, names in (
    ("Certificate", {"mailfallback-imaps"}),
    ("HTTPRoute", {"mailfallback-main", "mailfallback-webmail"}),
    ("SecurityPolicy", {"mailfallback-main-no-sso", "mailfallback-webmail-no-sso"}),
):
    check(
        set(by(docs_full, kind)) == names,
        f"full: {kind} names {set(by(docs_full, kind))} != {names}",
    )
check("mailfallback-dovecot-imaps" in by(docs_full, "Service"), "full: imaps LB service missing")
# 7. TLS env + mount when imaps on
app_env_full = {
    e["name"]: e.get("value")
    for e in by(docs_full, "Deployment")["mailfallback"]["spec"]["template"]["spec"]["containers"][
        0
    ].get("env", [])
}
check(app_env_full.get("MAILFALLBACK_DOVECOT_TLS") == "true", "full: DOVECOT_TLS not set")
dov_full = by(docs_full, "Deployment")["mailfallback-dovecot"]["spec"]["template"]["spec"]
check(
    any(v.get("secret", {}).get("secretName") == "imaps-tls" for v in dov_full.get("volumes", [])),
    "full: TLS secret not mounted",
)
docs_ec = render("existing-claims-values.yaml")
check(not by(docs_ec, "PersistentVolumeClaim"), "existing-claims: chart must not create PVCs")
check(
    len(by(render("minimal-values.yaml"), "PersistentVolumeClaim")) == 3,
    "minimal: expected 3 chart-managed PVCs (maildirs, confd, webmail-conf)",
)

# inline-secrets mode: chart-rendered env Secrets with derivation/override/annotations
docs_inline = render("inline-secrets-values.yaml")
secrets = by(docs_inline, "Secret")
check(
    set(secrets) == {"mailfallback-app-env", "mailfallback-roundcube-env"},
    f"inline: secret names {set(secrets)}",
)
app_sd = secrets.get("mailfallback-app-env", {}).get("stringData", {})
rc_sd = secrets.get("mailfallback-roundcube-env", {}).get("stringData", {})
check(
    app_sd.get("DOVEADM_PASSWORD") == app_sd.get("MAILFALLBACK_DOVECOT_API_KEY"),
    "inline: DOVEADM_PASSWORD not derived from the API key",
)
check(
    rc_sd.get("ROUNDCUBEMAIL_DEFAULT_HOST") == "mailfallback-dovecot",
    "inline: DEFAULT_HOST not derived",
)
check(
    rc_sd.get("ROUNDCUBEMAIL_DEFAULT_PORT") == "31993",
    "inline: explicit DEFAULT_PORT override lost",
)
for name in ("mailfallback-app-env", "mailfallback-roundcube-env"):
    ann = secrets.get(name, {}).get("metadata", {}).get("annotations") or {}
    check(
        ann.get("vaultsync/watch") == "secret/data/mailfallback",
        f"inline: {name} annotations missing",
    )
inline_deps = by(docs_inline, "Deployment")


def envfrom_names(dep):
    return {
        ef["secretRef"]["name"]
        for c in dep["spec"]["template"]["spec"]["containers"]
        for ef in c.get("envFrom", [])
    }


check(
    envfrom_names(inline_deps["mailfallback"]) == {"mailfallback-app-env"},
    "inline: app envFrom wrong",
)
check(
    envfrom_names(inline_deps["mailfallback-dovecot"]) == {"mailfallback-app-env"},
    "inline: dovecot envFrom wrong",
)
check(
    envfrom_names(inline_deps["mailfallback-webmail"]) == {"mailfallback-roundcube-env"},
    "inline: webmail envFrom wrong",
)

if failures:
    print("FAILED:\n" + "\n".join(f" - {f}" for f in failures))
    sys.exit(1)
print("chart render assertions: all OK")
