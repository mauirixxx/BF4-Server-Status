# PR2 Build Manifest

Build: `v3.0.0-pr2` implementation candidate, 2026-08-26.

Static validation performed in the build environment:
- Python AST/`py_compile` across application and Alembic files: PASS.
- Compose YAML parse: PASS.
- PR2 static invariant script: PASS.
- Secret-pattern scan found documentation placeholders only; no live `.env` is included.

Runtime/container/four-site acceptance testing is intentionally **pending** and must follow `V3_PR2_TESTING.md`. The build environment could not install the missing `discord.py`/`psycopg` packages because it has no package-network access, so this bundle must not be described as production-validated until it is built and exercised on the real Docker hosts.
