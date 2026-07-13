#!/usr/bin/env python3
"""Sync vendored frontend assets with the versions pinned in vendor.json.

Downloads each file from jsDelivr (npm mirror) at the pinned version and
writes it into src/mailfallback/static/vendor/. With --check, nothing is
written: the script exits non-zero if any on-disk file differs from the
pinned version (drift between manifest and vendored files).

Stdlib only, so it runs in CI and locally without installing the project:

    python3 scripts/sync_vendor.py          # download + overwrite
    python3 scripts/sync_vendor.py --check  # verify, no writes
"""

# ruff: noqa: T201, S310  # CLI prints to stdout; URLs are https-only from a constant

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / "src/mailfallback/static/vendor"
MANIFEST = VENDOR_DIR / "vendor.json"
CDN = "https://cdn.jsdelivr.net/npm/{package}@{version}/{path}"


def fetch(package: str, version: str, path: str) -> bytes:
    url = CDN.format(package=package, version=version, path=path)
    request = urllib.request.Request(url, headers={"User-Agent": "mailfallback-vendor-sync"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    drifted: list[str] = []

    for package, spec in manifest["packages"].items():
        version = spec["version"]
        for remote_path, local_name in spec["files"].items():
            expected = fetch(package, version, remote_path)
            target = VENDOR_DIR / local_name
            current = target.read_bytes() if target.exists() else b""
            if hashlib.sha256(current).digest() == hashlib.sha256(expected).digest():
                print(f"ok       {local_name} ({package}@{version})")
                continue
            if args.check:
                drifted.append(f"{local_name} != {package}@{version} {remote_path}")
                print(f"DRIFT    {local_name} ({package}@{version})")
            else:
                target.write_bytes(expected)
                print(f"updated  {local_name} ({package}@{version}, {len(expected)} bytes)")

    if drifted:
        print(
            "\nVendored files out of sync with vendor.json — "
            "run `python3 scripts/sync_vendor.py` and commit the result:",
            file=sys.stderr,
        )
        for line in drifted:
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
