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
#   3. The "Removed from Source" container, with the negation missing
#      (3a) or covering only its children (3b) -- this is the bug #244
#      fixes: MFB's container is a local box the far side never has (the
#      far side IS the Source), so mbsync fails trying to open it. The
#      container is built EXACTLY as write_container_readme leaves it,
#      cur/new/tmp and message included: a bare-directory fixture is not
#      a Maildir box, and a fixture that is not a box makes both cases
#      pass while asserting nothing.
#   4. The container AND its children negated -- the actual fix.
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

mk_container_readme() {  # mk_container_readme <near dir> -- what write_container_readme leaves
  # NOT a bare directory. folder_reconcile.write_container_readme gives the
  # container its OWN cur/new/tmp to hold the explanatory message, and that
  # is what makes it a real Maildir box the far side has never heard of --
  # the precise condition #244 is about, and the one a bare-directory
  # fixture cannot see. The filename carries the "container-readme" marker
  # that function uses as its idempotency guard.
  mkbox "$1/Removed from Source"
  printf 'From: MailFallBack <noreply@mailfallback.local>\nSubject: About this folder\n\nFolders in here were removed from the Source and are no longer synced.\n' \
    >"$1/Removed from Source/new/1757894400.container-readme.mfb-test:2,"
}

mk_container() {  # mk_container <near dir> -- container + one quarantined folder
  mk_container_readme "$1"
  mkbox "$1/Removed from Source/Ghost (2026-09-15 1200)"
  echo ghost >"$1/Removed from Source/Ghost (2026-09-15 1200)/new/ghost1.eml"
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
# A failed SETUP skips the rest of the case instead of recording a failure
# and asserting on regardless: asserting against an unsynced fixture reports
# a second, invented failure and hides which one is real.
if [ "$MB_EXIT" -ne 0 ]; then
  fail "case1 initial sync should succeed, exit=$MB_EXIT: $MB_OUT" case1
else
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
if [ "$MB_EXIT" -ne 0 ]; then
  fail "case2 initial sync should succeed, exit=$MB_EXIT: $MB_OUT" case2
else
  rm -rf "$C2/far/DeadBox"
  write_mbsyncrc "$C2/rc" "$C2/far" "$C2/near" '* !"DeadBox"'
  run_mbsync "$C2/rc"
  if [ "$MB_EXIT" -eq 0 ]; then
    pass "exit 0 once DeadBox is negated out of Patterns"
  else
    fail "case2: exit=$MB_EXIT out=[$MB_OUT]" case2
  fi
fi

echo "--- Case 3: container present, negation missing or incomplete ---"
# Two independent fixtures, not one tree reused: mbsync writes .mbsyncstate
# on a failed run too, so 3b would otherwise be measured against 3a's
# leftovers.
echo "--- Case 3a: no negation at all ---"
C3A=$WORK/case3a
mkbox "$C3A/far/INBOX"
echo hello >"$C3A/far/INBOX/new/inbox1.eml"
mkdir -p "$C3A/near"
mk_container "$C3A/near"
write_mbsyncrc "$C3A/rc" "$C3A/far" "$C3A/near" '*'
run_mbsync "$C3A/rc"
if [ "$MB_EXIT" -eq 1 ] && printf '%s' "$MB_OUT" | grep -q 'far side box Removed from Source'; then
  pass "exit 1, 'far side box Removed from Source... cannot be opened'"
else
  fail "case3a: exit=$MB_EXIT out=[$MB_OUT]" case3a
fi

echo "--- Case 3b: children negated, container itself NOT ---"
# The reason cases 3 and 4 exist in this shape. '!"Removed from Source/*"'
# reads as sufficient and shipped as such; it is not. isync's '*' matches
# across the hierarchy, so it covers the dated folders INSIDE the container
# but never the box named exactly "Removed from Source" -- and
# folder_reconcile.write_container_readme makes that box real by giving it
# cur/new/tmp for the explanatory message. The fixture this replaces built
# the container as a BARE DIRECTORY, so this case passed while asserting
# nothing about the shape production actually produces.
C3B=$WORK/case3b
mkbox "$C3B/far/INBOX"
echo hello >"$C3B/far/INBOX/new/inbox1.eml"
mkdir -p "$C3B/near"
mk_container "$C3B/near"
write_mbsyncrc "$C3B/rc" "$C3B/far" "$C3B/near" '* !"Removed from Source/*"'
run_mbsync "$C3B/rc"
if [ "$MB_EXIT" -eq 1 ] \
  && printf '%s' "$MB_OUT" | grep -q 'far side box Removed from Source cannot be opened'; then
  pass "exit 1, 'far side box Removed from Source cannot be opened' -- children-only is not enough"
else
  fail "case3b: exit=$MB_EXIT out=[$MB_OUT]" case3b
fi

echo "--- Case 4: container AND children negated (the actual #244 fix) ---"
C4=$WORK/case4
mkbox "$C4/far/INBOX"
echo hello >"$C4/far/INBOX/new/inbox1.eml"
mkdir -p "$C4/near"
mk_container "$C4/near"
write_mbsyncrc "$C4/rc" "$C4/far" "$C4/near" '* !"Removed from Source" !"Removed from Source/*"'
run_mbsync "$C4/rc"
# Exit 0 is the point, but the container's contents must survive too: a
# negation that worked by emptying the quarantine would be a worse bug than
# the one it fixes.
if [ "$MB_EXIT" -eq 0 ] \
  && [ "$(count_msgs "$C4/near/Removed from Source")" -eq 1 ] \
  && [ "$(count_msgs "$C4/near/Removed from Source/Ghost (2026-09-15 1200)")" -eq 1 ] \
  && [ "$(count_msgs "$C4/near/INBOX")" -eq 1 ]; then
  pass "exit 0 with both negations; README and quarantined folder untouched, INBOX still synced"
else
  fail "case4: exit=$MB_EXIT readme=$(count_msgs "$C4/near/Removed from Source") ghost=$(count_msgs "$C4/near/Removed from Source/Ghost (2026-09-15 1200)") inbox=$(count_msgs "$C4/near/INBOX") out=[$MB_OUT]" case4
fi

echo "--- Case 5: folder reappears at the Source after quarantine ---"
C5=$WORK/case5
mkbox "$C5/far/INBOX"
mkbox "$C5/far/OldFolder"
echo hello >"$C5/far/INBOX/new/inbox1.eml"
echo old >"$C5/far/OldFolder/new/old1.eml"
mkdir -p "$C5/near"
write_mbsyncrc "$C5/rc" "$C5/far" "$C5/near" '* !"Removed from Source" !"Removed from Source/*"'
run_mbsync "$C5/rc"
if [ "$MB_EXIT" -ne 0 ]; then
  fail "case5 initial sync should succeed, exit=$MB_EXIT: $MB_OUT" case5
else
  # Simulate MFB's own folder_reconcile.quarantine_folder(): move the synced
  # box under the container and drop its mbsync journal, exactly as that
  # function does (it only strips ".mbsyncstate"-prefixed files, keeping
  # ".uidvalidity" behind -- matched here for fidelity).
  mk_container_readme "$C5/near"
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
