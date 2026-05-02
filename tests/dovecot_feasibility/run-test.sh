#!/usr/bin/env bash
# run-test.sh -- Dovecot Lua + HTTP Feasibility Test
#
# Validates that Dovecot 2.4's Lua userdb can:
#   1. Make HTTP calls to an external API
#   2. Parse JSON responses
#   3. Return dynamic namespace extra-fields
#
# Usage: ./run-test.sh
# Exit codes: 0 = success, 1 = failure

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE="docker compose"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}PASS${NC}: $1"; }
fail() { echo -e "${RED}FAIL${NC}: $1"; }
info() { echo -e "${YELLOW}INFO${NC}: $1"; }

cleanup() {
    info "Cleaning up..."
    $COMPOSE down -v --remove-orphans 2>/dev/null || true
}
trap cleanup EXIT

# ------------------------------------------------------------------
# Start the stack
# ------------------------------------------------------------------
info "Starting test stack..."
$COMPOSE up -d --build --wait 2>&1

echo ""
info "=== Dovecot logs ==="
$COMPOSE logs dovecot 2>&1 | tail -40
echo ""

# ------------------------------------------------------------------
# Test 1: auth test (passdb)
# ------------------------------------------------------------------
info "Test 1: doveadm auth test (passdb)"
AUTH_TEST=$($COMPOSE exec -T dovecot /dovecot/bin/doveadm auth test -x service=imap testuser testpass 2>&1) || true
echo "$AUTH_TEST"

if echo "$AUTH_TEST" | grep -qi "succeeded\|ok"; then
    pass "auth test succeeded"
else
    fail "auth test failed"
    info "Full Dovecot logs:"
    $COMPOSE logs dovecot 2>&1
    exit 1
fi

echo ""

# ------------------------------------------------------------------
# Test 2: doveadm user (userdb lookup)
#
# NOTE: "doveadm auth lookup" does NOT trigger userdb in Dovecot 2.4.
# Use "doveadm user" instead, which calls auth_userdb_lookup().
# ------------------------------------------------------------------
info "Test 2: doveadm user (userdb lookup via Lua + HTTP)"
USER_LOOKUP=$($COMPOSE exec -T dovecot /dovecot/bin/doveadm user -x service=imap testuser 2>&1) || true
echo "$USER_LOOKUP"

echo ""

# ------------------------------------------------------------------
# Test 3: Check for namespace fields in lookup output
# ------------------------------------------------------------------
info "Test 3: Checking for userdb extra-fields"

TESTS_PASSED=0
TESTS_TOTAL=0

check_field() {
    local field="$1"
    local expected="$2"
    TESTS_TOTAL=$((TESTS_TOTAL + 1))

    if echo "$USER_LOOKUP" | grep -q "$field"; then
        if [ -n "$expected" ]; then
            if echo "$USER_LOOKUP" | grep "$field" | grep -q "$expected"; then
                pass "Field '$field' contains '$expected'"
                TESTS_PASSED=$((TESTS_PASSED + 1))
            else
                fail "Field '$field' found but does not contain '$expected'"
                echo "  Actual: $(echo "$USER_LOOKUP" | grep "$field")"
            fi
        else
            pass "Field '$field' present"
            TESTS_PASSED=$((TESTS_PASSED + 1))
        fi
    else
        fail "Field '$field' not found in lookup output"
    fi
}

# Core userdb fields
check_field "uid" "1000"
check_field "gid" "1000"
check_field "home" "/data/mailboxes/.dovecot-home/testuser"

# Namespace creation field (space-separated list of namespace names)
check_field "namespace" "acc_1 acc_2"

# Namespace extra-fields -- Dovecot 2.4 format
# Uses separate fields: mail_driver, mail_path, mailbox_list_layout
# (not the old "location = maildir:/path:LAYOUT=fs" syntax)
check_field "namespace/acc_1/mail_driver" "maildir"
check_field "namespace/acc_1/mail_path" "/data/mailboxes/test-uuid-1"
check_field "namespace/acc_1/mailbox_list_layout" "fs"
check_field "namespace/acc_1/prefix" ""
check_field "namespace/acc_1/inbox" "yes"
check_field "namespace/acc_1/separator" "/"
check_field "namespace/acc_2/mail_driver" "maildir"
check_field "namespace/acc_2/mail_path" "/data/mailboxes/test-uuid-2"
check_field "namespace/acc_2/mailbox_list_layout" "fs"
check_field "namespace/acc_2/prefix" "Second Account/"
check_field "namespace/acc_2/inbox" "no"
check_field "namespace/acc_2/separator" "/"

echo ""

# ------------------------------------------------------------------
# Test 4: Check mock API received the request
# ------------------------------------------------------------------
info "Test 4: Mock API logs"
MOCK_LOGS=$($COMPOSE logs mock-api 2>&1)
echo "$MOCK_LOGS" | tail -10

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if echo "$MOCK_LOGS" | grep -q "GET /api/internal/dovecot/userdb/testuser"; then
    pass "Mock API received userdb lookup request"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    fail "Mock API did not receive expected request"
fi

echo ""

# ------------------------------------------------------------------
# Test 5: Unknown user returns USERDB_RESULT_USER_UNKNOWN
# ------------------------------------------------------------------
info "Test 5: Unknown user lookup"
UNKNOWN_LOOKUP=$($COMPOSE exec -T dovecot /dovecot/bin/doveadm user -x service=imap unknownuser 2>&1) || true
echo "$UNKNOWN_LOOKUP"

TESTS_TOTAL=$((TESTS_TOTAL + 1))
if echo "$UNKNOWN_LOOKUP" | grep -qi "unknown\|doesn't exist\|no userdb"; then
    pass "Unknown user correctly rejected"
    TESTS_PASSED=$((TESTS_PASSED + 1))
else
    fail "Unknown user was not rejected as expected"
fi

echo ""

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
if [ "$TESTS_PASSED" -eq "$TESTS_TOTAL" ]; then
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  ALL $TESTS_TOTAL TESTS PASSED${NC}"
    echo -e "${GREEN}  Dovecot Lua + HTTP: FEASIBLE${NC}"
    echo -e "${GREEN}========================================${NC}"
    exit 0
else
    echo ""
    echo -e "${RED}========================================${NC}"
    echo -e "${RED}  $TESTS_PASSED / $TESTS_TOTAL TESTS PASSED${NC}"
    echo -e "${RED}  Review output above for failures${NC}"
    echo -e "${RED}========================================${NC}"
    exit 1
fi
