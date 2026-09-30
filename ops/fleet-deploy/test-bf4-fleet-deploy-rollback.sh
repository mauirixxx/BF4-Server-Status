#!/bin/bash
set -euo pipefail

ROOT="$(mktemp -d /tmp/bf4-fleet-rollback-test.XXXXXX)"
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/bin" "$ROOT/app" "$ROOT/release" "$ROOT/state"
printf 'SECRET=preserve-me\n' > "$ROOT/app/.env"
printf 'OLD_APP\n' > "$ROOT/app/old.txt"
printf 'bf4-server-watcher:3.0.1-hf2\n' > "$ROOT/state/image"
touch "$ROOT/state/running"

cat > "$ROOT/release/Dockerfile" <<'EOF'
FROM scratch
EOF
cat > "$ROOT/release/docker-compose.yml" <<'EOF'
services:
  bf4-worker-agent:
    image: bf4-server-watcher:3.1.1
EOF
cp "$ROOT/release/docker-compose.yml" "$ROOT/release/docker-compose.worker-agent.yml"
printf 'BOT_VERSION = "v3.1.1"\n' > "$ROOT/release/serverwatcher.py"
printf 'NEW_APP\n' > "$ROOT/release/new.txt"
python3 - "$ROOT" <<'PYZIP'
import sys, zipfile
from pathlib import Path
root = Path(sys.argv[1])
with zipfile.ZipFile(root / "BF4_Server_Watcher_v3.1.1.zip", "w") as archive:
    for path in (root / "release").iterdir():
        archive.write(path, path.name)
PYZIP
SHA="$(sha256sum "$ROOT/BF4_Server_Watcher_v3.1.1.zip" | awk '{print $1}')"
ENV_SHA="$(sha256sum "$ROOT/app/.env" | awk '{print $1}')"

sed \
    -e "s|DOCKER=/usr/bin/docker|DOCKER=$ROOT/bin/docker|" \
    -e "s|APP=/opt/bf4-serverwatcher|APP=$ROOT/app|" \
    -e "s|fd779e4423c1275cee0a7552c57ed9a5e5137d321f5600579b9e21157a53c9bf|$SHA|" \
    ops/fleet-deploy/bf4-fleet-deploy > "$ROOT/wrapper"
chmod +x "$ROOT/wrapper"

cat > "$ROOT/bin/curl" <<EOF
#!/bin/bash
set -e
out=''
while ((\$#)); do
    if [[ \$1 == --output ]]; then out=\$2; shift 2; else shift; fi
done
cp '$ROOT/BF4_Server_Watcher_v3.1.1.zip' "\$out"
EOF
chmod +x "$ROOT/bin/curl"
cat > "$ROOT/bin/docker" <<EOF
#!/bin/bash
set -euo pipefail
S='$ROOT/state'
cmd=\${1:-}; shift || true
case "\$cmd" in
inspect)
    fmt=''
    while ((\$#)); do
        if [[ \$1 == --format=* ]]; then fmt=\${1#--format=};
        elif [[ \$1 == --format ]]; then fmt=\$2; shift; fi
        shift
done
    image=\$(cat "\$S/image"); running=false; [[ -f \$S/running ]] && running=true
    case "\$fmt" in
        *'.Config.Image'*) echo "\$image" ;;
        *'.Image'*) echo 'sha256:oldimageid' ;;
        *'.State.Running'*) echo "\$running" ;;
        *) echo "image=\$image status=running running=\$running" ;;
    esac ;;
build) echo 'FAKE BUILD PASS' ;;
stop) rm -f "\$S/running"; echo BF4_ServerWatcher_Agent ;;
start) touch "\$S/running" ;;
rm) rm -f "\$S/running"; echo BF4_ServerWatcher_Agent ;;
tag) echo "\$2" > "\$S/image" ;;
compose)
    if [[ ! -f \$S/new-compose-failed ]]; then
        touch "\$S/new-compose-failed"
        echo 'INJECTED: new compose startup failure' >&2
        exit 1
    fi
    if [[ -f \$S/fail-rollback ]]; then
        echo 'INJECTED: rollback compose failure' >&2
        exit 1
    fi
    echo 'bf4-server-watcher:3.0.1-hf2' > "\$S/image"
    touch "\$S/running"
    echo 'FAKE ROLLBACK COMPOSE PASS' ;;
logs) echo 'fake logs' ;;
*) echo "unexpected fake docker command: \$cmd" >&2; exit 2 ;;
esac
EOF
chmod +x "$ROOT/bin/docker"

if [[ ${TEST_FAIL_ROLLBACK:-0} == 1 ]]; then
    touch "$ROOT/state/fail-rollback"
fi

set +e
OUT="$(PATH="$ROOT/bin:/usr/bin:/bin" SSH_ORIGINAL_COMMAND=deploy-v3.1.1 bash "$ROOT/wrapper" 2>&1)"
RC=$?
set -e
printf '%s\n' "$OUT"

if [[ ${TEST_FAIL_ROLLBACK:-0} == 1 ]]; then
    test "$RC" -eq 90 || { echo "FAIL: expected rollback-escalation rc=90, got $RC" >&2; exit 1; }
    grep -Fq 'ROLLBACK FAILED: operator intervention required' <<<"$OUT"
else
    test "$RC" -eq 43 || { echo "FAIL: expected deployment rc=43, got $RC" >&2; exit 1; }
    grep -Fq 'ROLLBACK PASS: previous worker restored and .env unchanged' <<<"$OUT"
fi
test -f "$ROOT/app/old.txt" || { echo 'FAIL: previous app tree not restored' >&2; exit 1; }
test ! -f "$ROOT/app/new.txt" || { echo 'FAIL: failed release remained installed' >&2; exit 1; }
test "$(sha256sum "$ROOT/app/.env" | awk '{print $1}')" = "$ENV_SHA" || {
    echo 'FAIL: .env changed across rollback' >&2
    exit 1
}
test "$(cat "$ROOT/state/image")" = 'bf4-server-watcher:3.0.1-hf2' || {
    echo 'FAIL: previous image reference not restored' >&2
    exit 1
}
if [[ ${TEST_FAIL_ROLLBACK:-0} == 1 ]]; then
    test ! -f "$ROOT/state/running" || { echo 'FAIL: worker unexpectedly running after rollback restart failure' >&2; exit 1; }
    echo 'PASS: rollback failure escalated rc=90 after restoring app/image/.env'
else
    test -f "$ROOT/state/running" || { echo 'FAIL: previous worker not running' >&2; exit 1; }
    echo 'PASS: injected post-write startup failure restored previous app/image/.env/running worker'
fi
echo 'ALL FLEET DEPLOYER ROLLBACK TESTS PASS'
