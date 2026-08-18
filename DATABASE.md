# Database URL Configuration

BF4 Server Watcher v2 uses SQLAlchemy.

PostgreSQL is the primary deployment target. MySQL and MariaDB are supported through SQLAlchemy/PyMySQL-compatible URLs.

Set the database connection in `.env` as `DATABASE_URL`.

## PostgreSQL

```env
DATABASE_URL=postgresql+psycopg://bf4_serverwatcher:PASSWORD@host.docker.internal:5432/bf4_serverwatcher
```

## MySQL

```env
DATABASE_URL=mysql+pymysql://bf4_serverwatcher:PASSWORD@host.docker.internal:3306/bf4_serverwatcher?charset=utf8mb4
```

## MariaDB

```env
DATABASE_URL=mariadb+pymysql://bf4_serverwatcher:PASSWORD@host.docker.internal:3306/bf4_serverwatcher?charset=utf8mb4
```

`docker-compose.yml` maps `host.docker.internal` to the Docker host so a database running on the host can be reached from the container.

Do not commit real database passwords or connection strings.
