from pathlib import Path
import ast

root = Path(__file__).resolve().parent

python_files = (
    list(root.glob("*.py"))
    + list((root / "alembic" / "versions").glob("*.py"))
)

# Basic Python syntax validation.
for path in python_files:
    ast.parse(path.read_text(), filename=str(path))

required = {
    "serverwatcher.py": [
        "v3.0.0-pr3",
        "PRIMARY_OPERATOR_DISCORD_USER_ID",
        "operator_event_loop",
        "operator_group",
        "operator_destinations",
    ],
    "operator_notifications.py": [
        "bootstrap_primary_operator",
        "ensure_delivery_rows",
        "mark_delivery_failure",
        "cluster_status_snapshot",
    ],
    "alembic/versions/0012_v3_0_0_operator_notifications.py": [
        "cluster_operator_destinations",
        "cluster_operator_event_deliveries",
        'revision="0012_v3_0_0_operator_notify"',
    ],
    "docker-compose.yml": [
        "3.0.0-pr3",
        "stop_grace_period: 30s",
    ],
    "docker-compose.worker-agent.yml": [
        "3.0.0-pr3",
        "stop_grace_period: 30s",
    ],
}

for name, needles in required.items():
    text = (root / name).read_text()
    for needle in needles:
        if needle not in text:
            raise SystemExit(
                f"FAIL {name}: missing {needle!r}"
            )

# Alembic stores the current revision in alembic_version.version_num,
# which is VARCHAR(32) in this project. Reject migration IDs that are
# too long before they ever reach a live database.
revision_ids = {}

for path in sorted((root / "alembic" / "versions").glob("*.py")):
    tree = ast.parse(path.read_text(), filename=str(path))
    revision = None

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        for target in node.targets:
            if (
                isinstance(target, ast.Name)
                and target.id == "revision"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                revision = node.value.value

    if revision is None:
        continue

    if len(revision) > 32:
        raise SystemExit(
            f"FAIL {path.name}: Alembic revision "
            f"{revision!r} is {len(revision)} characters; "
            "maximum is 32"
        )

    if revision in revision_ids:
        raise SystemExit(
            f"FAIL duplicate Alembic revision {revision!r}: "
            f"{revision_ids[revision]} and {path.name}"
        )

    revision_ids[revision] = path.name

print(
    f"PASS: parsed {len(python_files)} Python files, "
    f"validated {len(revision_ids)} Alembic revisions, "
    "and PR3 static invariants"
)
