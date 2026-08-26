from pathlib import Path
import ast, sys
root=Path(__file__).resolve().parent
py=list(root.glob('*.py'))+list((root/'alembic/versions').glob('*.py'))
for p in py:
    ast.parse(p.read_text(), filename=str(p))
required={
 'serverwatcher.py':['v3.0.0-pr2','_fresh_discord_client_and_tree','operator_event_loop','monitor_loop'],
 'discord_leader.py':['discord:leader','DEFAULT_LEASE_TTL_SECONDS = 30','DEFAULT_LEASE_RENEW_SECONDS = 10'],
 'migrate_with_lock.py':['pg_advisory_lock','schema verification failed'],
 'docker-compose.yml':['stop_grace_period: 30s'],
 'docker-compose.worker-agent.yml':['stop_grace_period: 30s'],
}
for name, needles in required.items():
    text=(root/name).read_text()
    for needle in needles:
        if needle not in text: raise SystemExit(f'FAIL {name}: missing {needle!r}')
print(f'PASS: parsed {len(py)} Python files and PR2 static invariants')
