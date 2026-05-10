#!/usr/bin/env bash
# scripts/lexicon-check.sh — advisory check for bare "Backup" in user-facing copy.
# See LEXICON.md. Exits 0 (warning only); CI logs the warnings but does not fail.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SCOPE_TEMPLATES='src/mailfallback/templates/'
SCOPE_ROUTERS='src/mailfallback/routers/'
ALLOWLIST="scripts/.lexicon-allowlist"

# Allowed qualifiers (case-insensitive): "backup" must be adjacent to one of these to pass.
ALLOWED='(local|off-site|offsite|locale|configuration|destination|policy|policies|deposito|repository|snapshot|completed|failed|started|now|history|profile|tables|worker|service|operations|jobs?|email)'

# Filter `grep -REn` output by matching the regex against the CONTENT only
# (after path:line:). The match must be a STANDALONE WORD — surrounding
# characters cannot be [a-zA-Z_-], which excludes identifiers like
# `backup_config`, `BackupDestination`, `id="backup_dest"`, URL paths
# `/admin/backup/...`, and template includes `partials/account_backup.html`.
# Usage: filter_content_matches WORD
filter_content_matches() {
    local word="$1"
    awk -v word="$word" '
        BEGIN { IGNORECASE = 1 }
        {
            # grep -REn output: path:lineno:content
            i = index($0, ":");
            rest = substr($0, i + 1);
            j = index(rest, ":");
            content = substr(rest, j + 1);
            standalone = "(^|[^a-zA-Z_./-])" word "([^a-zA-Z_/-]|$)";
            if (content ~ standalone) print $0;
        }
    '
}

# Remove rows whose content matches the "backup + qualifier" pattern (in either order).
strip_qualified() {
    awk -v allowed="$ALLOWED" '
        BEGIN { IGNORECASE = 1 }
        {
            i = index($0, ":");
            rest = substr($0, i + 1);
            j = index(rest, ":");
            content = substr(rest, j + 1);
            qualified_after = "[bB]ackup[ \t]+" allowed "([^a-zA-Z]|$)";
            qualified_before = allowed "[ \t]+[bB]ackup([^a-zA-Z]|$)";
            if (content ~ qualified_after || content ~ qualified_before) next;
            print $0;
        }
    '
}

# Templates: any line containing bare "backup" in the content.
TPL_HITS=$(grep -REn '[Bb]ackup' "$SCOPE_TEMPLATES" --include='*.html' 2>/dev/null \
    | filter_content_matches 'backup' \
    | strip_qualified \
    || true)

# Routers: only lines that are flash messages.
ROUTER_HITS=$(grep -REn 'flash_(success|error)' "$SCOPE_ROUTERS" --include='*.py' 2>/dev/null \
    | filter_content_matches 'backup' \
    | strip_qualified \
    || true)

# Apply path:line allowlist.
if [ -f "$ALLOWLIST" ]; then
    while IFS= read -r entry; do
        [ -z "$entry" ] && continue
        case "$entry" in \#*) continue ;; esac
        prefix="${entry%%: *}"
        TPL_HITS=$(echo "$TPL_HITS" | grep -vF "$prefix" || true)
        ROUTER_HITS=$(echo "$ROUTER_HITS" | grep -vF "$prefix" || true)
    done < "$ALLOWLIST"
fi

if [ -n "$TPL_HITS" ] || [ -n "$ROUTER_HITS" ]; then
    echo "::warning::lexicon-check found bare 'backup' usage; see LEXICON.md."
    [ -n "$TPL_HITS" ]    && echo "$TPL_HITS"
    [ -n "$ROUTER_HITS" ] && echo "$ROUTER_HITS"
    echo
    echo "To allowlist a false positive, add 'path:line: <reason>' to $ALLOWLIST."
fi

exit 0
