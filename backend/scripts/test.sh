#!/usr/bin/env bash
# Runs the backend suite against a dedicated test database. Called by `make test`.
set -euo pipefail
export POSTGRES_DB="${POSTGRES_DB:-saralprivacy}_test"

# 1. Create the test database (connecting to the server's default `postgres` db).
python - <<'PY'
import time
import psycopg
from app.core.config import settings
url = settings.SQLALCHEMY_DATABASE_URI.unicode_string().replace("postgresql+psycopg", "postgresql")
server = url.rsplit("/", 1)[0] + "/postgres"
for attempt in range(60):
    try:
        with psycopg.connect(server, autocommit=True) as c:
            if not c.execute("select 1 from pg_database where datname=%s", (settings.POSTGRES_DB,)).fetchone():
                c.execute(f'create database "{settings.POSTGRES_DB}"')
                print("created", settings.POSTGRES_DB)
        break
    except psycopg.OperationalError:
        time.sleep(1)
else:
    raise SystemExit("database server not reachable")
with psycopg.connect(url, autocommit=True) as c:
    c.execute("create schema if not exists ops")
    c.execute("create schema if not exists app")
    c.execute("create extension if not exists pgcrypto")
PY

# 2. Migrate it to head, then run the suite with coverage.
alembic upgrade head
coverage run --source=app -m pytest "${@:-app/tests}"
coverage report --skip-empty | tail -3
