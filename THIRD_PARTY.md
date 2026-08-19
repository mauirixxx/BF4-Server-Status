# Third-Party Software and Services

BF4 Server Watcher is licensed under the MIT License. Third-party software and
services retain their own licenses and terms.

The dependency ranges below match the v2.3.0 `requirements.txt` release bundle. The listed lower bounds are the versions tested for this release; upper bounds prevent unreviewed breaking-version upgrades.

## Python dependencies

### discord.py >=2.7.1,<3
- Purpose: Discord API client.
- License: MIT.
- Upstream: https://github.com/Rapptz/discord.py
- PyPI: https://pypi.org/project/discord.py/

### Requests >=2.34.2,<3
- Purpose: HTTP client used for Keeper, BFLIST, and version-check requests.
- License: Apache-2.0.
- Upstream: https://github.com/psf/requests
- PyPI: https://pypi.org/project/requests/

### python-dotenv >=1.2.3,<2
- Purpose: `.env` loading for deployment-global settings.
- License: BSD-3-Clause.
- Upstream: https://github.com/theskumar/python-dotenv
- PyPI: https://pypi.org/project/python-dotenv/

### SQLAlchemy >=2.0.52,<2.1
- Purpose: Database ORM, SQL abstraction, connection pooling, and PostgreSQL/MySQL/MariaDB portability.
- License: MIT.
- Upstream: https://github.com/sqlalchemy/sqlalchemy
- PyPI: https://pypi.org/project/SQLAlchemy/

### Alembic >=1.19.1,<2
- Purpose: Database schema migrations.
- License: MIT.
- Upstream: https://github.com/sqlalchemy/alembic
- PyPI: https://pypi.org/project/alembic/

### Psycopg >=3.3.4,<4
- Purpose: PostgreSQL database driver.
- Installed as: `psycopg[binary]>=3.3.4,<4`.
- License expression reported by the Psycopg 3.3.4 PyPI metadata: LGPL-3.0-only.
- Upstream: https://github.com/psycopg/psycopg
- PyPI: https://pypi.org/project/psycopg/

### psycopg-binary (via Psycopg `binary` extra)
- Purpose: Precompiled Psycopg optimization/runtime component installed by the `binary` extra.
- License expression reported by the psycopg-binary 3.3.4 PyPI metadata: LGPL-3.0-only.
- Upstream: https://github.com/psycopg/psycopg
- PyPI: https://pypi.org/project/psycopg-binary/

### PyMySQL >=1.2.0,<2
- Purpose: MySQL/MariaDB database driver.
- Installed as: `PyMySQL[rsa]>=1.2.0,<2`.
- License: MIT.
- Upstream: https://github.com/PyMySQL/PyMySQL
- PyPI: https://pypi.org/project/PyMySQL/

### Python 3.12
- Purpose: Runtime and standard library.
- License: Python Software Foundation License Version 2 and other notices
  applicable to components bundled with Python.
- Upstream: https://www.python.org/

## Transitive dependencies

Installing the bounded direct dependencies may install transitive dependencies under their
own licenses. This source release does not distribute a prebuilt Docker image.

Before publishing a prebuilt Docker image or binary distribution, generate and
review an exact dependency/license inventory from that release artifact.

## External services

### Discord
This project communicates with Discord. Users must create/configure their own
Discord application and comply with Discord's applicable terms/developer
policies. Discord is not bundled with this project.

### BFLIST
ServerWatcher queries BFLIST's Battlefield 4 player/server API for PC scoreboard
enrichment. BFLIST is a third-party service and is not bundled with or licensed
by this project. Availability, response formats, access rules, and terms may
change independently.

### Battlefield / Battlelog Keeper endpoint
ServerWatcher queries the Battlelog Keeper snapshot endpoint for Battlefield 4
server status. This project does not represent Keeper as an officially
supported public developer API. Its availability/behavior may change without
notice. Users are responsible for complying with applicable EA terms.

### PostgreSQL / MySQL / MariaDB
ServerWatcher connects to an administrator-provided database service. These
database servers are not bundled with this source release and retain their own
licenses/terms.

## Trademark / affiliation disclaimer

This is an unofficial community project. It is not affiliated with, endorsed
by, or sponsored by Electronic Arts, DICE, Discord, BFLIST, PostgreSQL, MySQL,
or MariaDB. Product, service, and company names belong to their respective
owners.
