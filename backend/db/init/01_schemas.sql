-- Runs once on a fresh Postgres volume (docker-entrypoint-initdb.d). Alembic owns
-- everything else; this only guarantees the schemas exist before `alembic upgrade`.
create schema if not exists ops;
create schema if not exists app;
create extension if not exists pgcrypto;
