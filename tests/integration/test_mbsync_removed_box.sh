#!/usr/bin/env bash
# Pins isync 1.5.1's behaviour with the folders MFB's removed-folder design
# (#244) depends on, against the REAL mbsync binary shipped in the product
# image. No pytest can check these: every claim below is a claim about
# isync, not about MFB's Python, so a future isync upgrade that changes
# behaviour must fail this script loudly instead of silently reintroducing
# the bug.
#
# Six cases, run inside a single long-lived container (cheap `docker exec`
# calls instead of a fresh container per case):
#   1. A folder gone from the Source, still matched by Patterns.
#   2. The same folder, negated out of Patterns.
#   3. The "Removed from Source" container itself, matched by Patterns --
#      this is the bug #244 fixes: without the negation MFB appends to
#      every channel's Patterns, mbsync tries to open the container on
#      the far side, which never has it (the far side IS the Source).
#   4. The container, negated -- the actual fix.
#   5. A folder that was quarantined and then reappeared at the Source.
#   6. A nested folder, to record exactly what SubFolders Verbatim +
#      LAYOUT=fs write to disk (see CLAUDE.md's Testing section for why
#      this matters: folder_reconcile compares this on-disk spelling
#      against the Source's raw IMAP folder names).
#
# Gotchas that cost real time while first measuring this (see
# .superpowers/sdd/2026-09-15-upstream-removed-folders/task-11-report.md):
#  - The fixture MUST be built with files/dirs created INSIDE the
#    container. A bind mount of a macOS temp dir is not shared by Docker
#    Desktop and silently mounts an empty directory -- the test then
#    passes while asserting nothing.
#  - isync's config parser rejects a file whose sections aren't separated
#    by a blank line; a compacted heredoc that strips blank lines
#    produces an error that looks nothing like a config problem.
#  - `$?` must be captured immediately after mbsync runs, before its
#    output is piped anywhere -- a pipeline's `$?` is the last command's.
#  - Maildir renames files on delivery, so assertions count files in
#    cur/+new/ rather than checking for one specific filename.
#
# Usage: bash tests/integration/test_mbsync_removed_box.sh
# Requires: docker, with the image below already pulled (no network access
# beyond that is used). Exits non-zero and names the failing case(s) if
# any assertion fails; always cleans up its container.

set -uo pipefail

IMAGE="${MFB_TEST_IMAGE:-ghcr.io/thekoma/mailfallback:2026.09.0-rc1}"
CONTAINER="mfb-removed-box-test-$$"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "== Starting $CONTAINER from $IMAGE =="
if ! docker run -d --name "$CONTAINER" --entrypoint sh "$IMAGE" -c 'sleep 3600' >/dev/null; then
  echo "FAIL: could not start container from $IMAGE (is it pulled locally?)" >&2
  exit 1
fi

# Wait for the container to actually be reachable before the first exec.
ready=0
for _ in $(seq 1 20); do
  if docker exec "$CONTAINER" true >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
if [ "$ready" -ne 1 ]; then
  echo "FAIL: container $CONTAINER never became ready" >&2
  exit 1
fi

# The inner script does all the fixture-building, mbsync runs and
# assertions INSIDE the container's own filesystem (first gotcha above).
# It is written to a real file and `docker cp`'d in -- not piped through
# `docker exec sh -c "..."` -- so isync's blank-line-between-sections
# requirement survives verbatim instead of being collapsed by an extra
# layer of shell quoting.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"; cleanup' EXIT
INNER="$TMPDIR/run.sh"
cat > "$INNER" <<'INNER_EOF'
#!/bin/sh
set -u
WORK=/tmp/mfb-removed-box-fixtures
rm -rf "$WORK"
mkdir -p "$WORK"

FAIL=0
FAILED=""

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAIL=1; FAILED="$FAILED $2"; }

mkbox() {  # mkbox <path> -- an empty Maildir box (cur/new/tmp)
  mkdir -p "$1/cur" "$1/new" "$1/tmp"
}

count_msgs() {  # count_msgs <box path> -- files in cur/+new/, never a filename
  find "$1/cur" "$1/new" -type f 2>/dev/null | wc -l | tr -d ' '
}

# far/near are both plain Maildirs, so the whole script runs offline; the
# point is isync's box-open/Patterns logic, which doesn't care whether the
# "Source" is local or IMAP. Mirrors mbsync_config.generate_mbsyncrc's own
# defaults (Sync Pull, Create Near, Expunge None) for fidelity.
write_mbsyncrc() {  # write_mbsyncrc <rcfile> <far dir> <near dir> <patterns>
  cat > "$1" <<EOF
MaildirStore far
Path $2/
Inbox $2/INBOX
SubFolders Verbatim

MaildirStore near
Path $3/
Inbox $3/INBOX
SubFolders Verbatim

Channel test
Far :far:
Near :near:
Patterns $4
Sync Pull
Create Near
Expunge None
SyncState *
EOF
}

run_mbsync() {  # run_mbsync <rcfile> -- sets MB_EXIT and MB_OUT
  MB_OUT="$(mbsync -c "$1" test 2>&1)"
  MB_EXIT=$?   # captured immediately: no pipe stands between this and mbsync
}

echo "--- Case 1: dead box still included in Patterns ---"
C1=$WORK/case1
mkbox "$C1/far/INBOX"
mkbox "$C1/far/DeadBox"
echo hello >"$C1/far/INBOX/new/inbox1.eml"
echo bye >"$C1/far/DeadBox/new/dead1.eml"
mkdir -p "$C1/near"
write_mbsyncrc "$C1/rc" "$C1/far" "$C1/near" '*'
run_mbsync "$C1/rc"
[ "$MB_EXIT" -eq 0 ] || fail "case1 initial sync should succeed, exit=$MB_EXIT: $MB_OUT" case1
# DeadBox vanishes from the Source; INBOX gets a new message so we can
# prove the OTHER folder still syncs despite DeadBox failing to open.
rm -rf "$C1/far/DeadBox"
echo again >"$C1/far/INBOX/new/inbox2.eml"
run_mbsync "$C1/rc"
if [ "$MB_EXIT" -eq 1 ] \
  && printf '%s' "$MB_OUT" | grep -q 'cannot be opened anymore' \
  && [ "$(count_msgs "$C1/near/INBOX")" -eq 2 ]; then
  pass "exit 1, 'cannot be opened anymore', INBOX still synced (now 2 messages)"
else
  fail "case1: exit=$MB_EXIT inbox_msgs=$(count_msgs "$C1/near/INBOX") out=[$MB_OUT]" case1
fi

echo "--- Case 2: dead box negated out of Patterns ---"
C2=$WORK/case2
mkbox "$C2/far/INBOX"
mkbox "$C2/far/DeadBox"
echo hello >"$C2/far/INBOX/new/inbox1.eml"
echo bye >"$C2/far/DeadBox/new/dead1.eml"
mkdir -p "$C2/near"
write_mbsyncrc "$C2/rc" "$C2/far" "$C2/near" '*'
run_mbsync "$C2/rc"
[ "$MB_EXIT" -eq 0 ] || fail "case2 initial sync should succeed, exit=$MB_EXIT: $MB_OUT" case2
rm -rf "$C2/far/DeadBox"
write_mbsyncrc "$C2/rc" "$C2/far" "$C2/near" '* !"DeadBox"'
run_mbsync "$C2/rc"
if [ "$MB_EXIT" -eq 0 ]; then
  pass "exit 0 once DeadBox is negated out of Patterns"
else
  fail "case2: exit=$MB_EXIT out=[$MB_OUT]" case2
fi

echo "--- Case 3: 'Removed from Source' container present, NOT negated ---"
C3=$WORK/case3
mkbox "$C3/far/INBOX"
echo hello >"$C3/far/INBOX/new/inbox1.eml"
mkdir -p "$C3/near"
mkbox "$C3/near/Removed from Source/Ghost (2026-09-15 1200)"
write_mbsyncrc "$C3/rc" "$C3/far" "$C3/near" '*'
run_mbsync "$C3/rc"
if [ "$MB_EXIT" -eq 1 ] && printf '%s' "$MB_OUT" | grep -q 'far side box Removed from Source/.* cannot be opened'; then
  pass "exit 1, 'far side box Removed from Source/... cannot be opened'"
else
  fail "case3: exit=$MB_EXIT out=[$MB_OUT]" case3
fi

echo "--- Case 4: container negated (the actual #244 fix) ---"
C4=$WORK/case4
mkbox "$C4/far/INBOX"
echo hello >"$C4/far/INBOX/new/inbox1.eml"
mkdir -p "$C4/near"
mkbox "$C4/near/Removed from Source/Ghost (2026-09-15 1200)"
write_mbsyncrc "$C4/rc" "$C4/far" "$C4/near" '* !"Removed from Source/*"'
run_mbsync "$C4/rc"
if [ "$MB_EXIT" -eq 0 ]; then
  pass "exit 0 once the container is negated out of Patterns"
else
  fail "case4: exit=$MB_EXIT out=[$MB_OUT]" case4
fi

echo "--- Case 5: folder reappears at the Source after quarantine ---"
C5=$WORK/case5
mkbox "$C5/far/INBOX"
mkbox "$C5/far/OldFolder"
echo hello >"$C5/far/INBOX/new/inbox1.eml"
echo old >"$C5/far/OldFolder/new/old1.eml"
mkdir -p "$C5/near"
write_mbsyncrc "$C5/rc" "$C5/far" "$C5/near" '* !"Removed from Source/*"'
run_mbsync "$C5/rc"
[ "$MB_EXIT" -eq 0 ] || fail "case5 initial sync should succeed, exit=$MB_EXIT: $MB_OUT" case5
# Simulate MFB's own folder_reconcile.quarantine_folder(): move the synced
# box under the container and drop its mbsync journal, exactly as that
# function does (it only strips ".mbsyncstate"-prefixed files, keeping
# ".uidvalidity" behind -- matched here for fidelity).
mkdir -p "$C5/near/Removed from Source"
mv "$C5/near/OldFolder" "$C5/near/Removed from Source/OldFolder (2026-09-15 1200)"
rm -f "$C5/near/Removed from Source/OldFolder (2026-09-15 1200)/.mbsyncstate"
QUAR_BEFORE="$(md5sum "$C5/near/Removed from Source/OldFolder (2026-09-15 1200)/new/"* 2>/dev/null)"
rm -rf "$C5/far/OldFolder"
# ... and now it comes back at the Source, with different content.
mkbox "$C5/far/OldFolder"
echo fresh >"$C5/far/OldFolder/new/fresh1.eml"
run_mbsync "$C5/rc"
QUAR_AFTER="$(md5sum "$C5/near/Removed from Source/OldFolder (2026-09-15 1200)/new/"* 2>/dev/null)"
if [ "$MB_EXIT" -eq 0 ] \
  && [ -d "$C5/near/OldFolder" ] \
  && [ "$(count_msgs "$C5/near/OldFolder")" -eq 1 ] \
  && [ "$QUAR_BEFORE" = "$QUAR_AFTER" ]; then
  pass "exit 0, fresh OldFolder synced (1 message), quarantined copy byte-identical"
else
  fail "case5: exit=$MB_EXIT fresh_msgs=$(count_msgs "$C5/near/OldFolder" 2>/dev/null) quarantine_changed=$([ "$QUAR_BEFORE" = "$QUAR_AFTER" ] && echo no || echo YES) out=[$MB_OUT]" case5
fi

echo "--- Case 6: nested-folder disk layout (SubFolders Verbatim) ---"
echo "    (see CLAUDE.md/task-11 report: folder_reconcile compares this"
echo "     spelling against the Source's raw IMAP folder names)"
C6=$WORK/case6
mkbox "$C6/far/INBOX"
mkbox "$C6/far/Parent"
mkbox "$C6/far/Parent/Child"
echo p >"$C6/far/Parent/new/p1.eml"
echo c >"$C6/far/Parent/Child/new/c1.eml"
mkdir -p "$C6/near"
write_mbsyncrc "$C6/rc" "$C6/far" "$C6/near" '*'
run_mbsync "$C6/rc"
echo "  disk layout under near/ after syncing Parent/Child:"
find "$C6/near" | sed 's/^/    /'
# The exact relative-path spelling, computed the same way
# folder_reconcile.local_synced_folders derives it (os.walk + relpath from
# the account root): a "/"-joined path, never a flat "Parent.Child"-style
# name. This is what _list_upstream_folders' delimiter normalisation
# (sync_worker.py) must produce from a "."-delimiter Source's LIST for the
# two sides to ever match -- pinning it here so a future isync change that
# altered this spelling would fail loudly instead of silently
# reintroducing #244 on any dot-delimiter (self-hosted Dovecot/Courier)
# Source.
NESTED_REL=$(find "$C6/near" -mindepth 1 -type d -name Child | sed "s|^$C6/near/||")
if [ "$MB_EXIT" -eq 0 ] \
  && [ -d "$C6/near/Parent" ] \
  && [ -d "$C6/near/Parent/Child" ] \
  && [ "$(count_msgs "$C6/near/Parent")" -eq 1 ] \
  && [ "$(count_msgs "$C6/near/Parent/Child")" -eq 1 ] \
  && [ "$NESTED_REL" = "Parent/Child" ]; then
  pass "nested folder becomes a real nested directory Parent/Child/ (with its own cur/new/tmp), on-disk spelling pinned as '$NESTED_REL', not a flat 'Parent.Child'-style name"
else
  fail "case6: exit=$MB_EXIT on_disk_spelling=[$NESTED_REL] (see disk layout printed above)" case6
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "ALL 6 CASES PASSED"
else
  echo "FAILED CASES:$FAILED"
fi
exit "$FAIL"
INNER_EOF

docker cp "$INNER" "$CONTAINER:/tmp/mfb-removed-box-run.sh" >/dev/null
docker exec "$CONTAINER" sh /tmp/mfb-removed-box-run.sh
RESULT=$?

exit "$RESULT"
