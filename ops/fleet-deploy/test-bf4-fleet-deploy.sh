#!/bin/bash
set -euo pipefail

WRAPPER="${1:-ops/fleet-deploy/bf4-fleet-deploy}"

fail()
{
    echo "FAIL: $*" >&2
    exit 1
}

bash -n "$WRAPPER" || fail "wrapper syntax"
echo "PASS: wrapper syntax"

# The generic deployer must retain a fixed upstream repository and must not
# accept a URL supplied by the SSH caller.
grep -Fq 'REPO=mauirixxx/BF4-Server-Status' "$WRAPPER" || fail "fixed GitHub repository missing"
grep -Fq 'release_metadata()' "$WRAPPER" || fail "release allowlist missing"
grep -Fq 'EXPECTED_SHA' "$WRAPPER" || fail "checksum pinning missing"
grep -Fq 'deploy-v*)' "$WRAPPER" || fail "versioned forced-command dispatcher missing"
grep -Fq 'DENIED: unsupported BF4 fleet deployment operation' "$WRAPPER" || fail "default deny missing"
echo "PASS: static trust-boundary checks"

# Commands that do not need Docker can be exercised directly by setting the
# same environment variable OpenSSH supplies to a forced command.
APPROVED="$(SSH_ORIGINAL_COMMAND=approved-releases bash "$WRAPPER")"
test "$APPROVED" = 'v3.1.1' || fail "unexpected approved release list: $APPROVED"
echo "PASS: approved release list"

set +e
OUT="$(SSH_ORIGINAL_COMMAND=id bash "$WRAPPER" 2>&1)"
RC=$?
set -e
test "$RC" -eq 64 || fail "arbitrary command rc=$RC"
grep -Fq 'DENIED:' <<<"$OUT" || fail "arbitrary command was not denied"
echo "PASS: arbitrary command denied"

set +e
OUT="$(SSH_ORIGINAL_COMMAND='deploy-v3.1.2' bash "$WRAPPER" 2>&1)"
RC=$?
set -e
test "$RC" -eq 64 || fail "unapproved release rc=$RC"
grep -Fq 'is not approved' <<<"$OUT" || fail "unapproved release was not denied by allowlist"
echo "PASS: unapproved release denied"

set +e
OUT="$(SSH_ORIGINAL_COMMAND='deploy-v../../tmp/x' bash "$WRAPPER" 2>&1)"
RC=$?
set -e
test "$RC" -eq 64 || fail "malformed version rc=$RC"
grep -Fq 'invalid BF4 release version' <<<"$OUT" || fail "malformed version was not rejected"
echo "PASS: malformed version denied"

echo "ALL FLEET DEPLOYER BOUNDARY TESTS PASS"
