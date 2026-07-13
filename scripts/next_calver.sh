#!/usr/bin/env bash
# Compute the next CalVer (YYYY.MM.INC) from git tags.
#   next_calver.sh            -> next release   (e.g. 2026.07.2)
#   next_calver.sh --pre rc   -> next rc for the next release base (e.g. 2026.07.2-rc1)
# CALVER_TODAY=YYYY-MM-DD overrides "today" (tests).
set -euo pipefail

PRE=""
if [ "${1:-}" = "--pre" ]; then
  PRE="${2:?usage: next_calver.sh [--pre rc|beta]}"
  case "$PRE" in rc|beta) ;; *) echo "invalid pre type: $PRE" >&2; exit 1;; esac
fi

TODAY="${CALVER_TODAY:-$(date -u +%Y-%m-%d)}"
YEAR="${TODAY%%-*}"
MONTH="$(echo "$TODAY" | cut -d- -f2)"
YM="${YEAR}.${MONTH}"

# Highest release INC for the current month (release tags only, no prereleases).
LATEST_INC=$(git tag -l "${YM}.*" | grep -E "^${YM}\.[0-9]+$" | awk -F. '{print $3}' | sort -n | tail -1 || true)
if [ -z "$LATEST_INC" ]; then
  NEXT_INC=0
else
  NEXT_INC=$((LATEST_INC + 1))
fi
BASE="${YM}.${NEXT_INC}"

if [ -z "$PRE" ]; then
  echo "$BASE"
  exit 0
fi

LATEST_PRE=$(git tag -l "${BASE}-${PRE}*" | grep -E "^${BASE}-${PRE}[0-9]+$" | sed "s/^${BASE}-${PRE}//" | sort -n | tail -1 || true)
if [ -z "$LATEST_PRE" ]; then
  PRE_NUM=1
else
  PRE_NUM=$((LATEST_PRE + 1))
fi
echo "${BASE}-${PRE}${PRE_NUM}"
