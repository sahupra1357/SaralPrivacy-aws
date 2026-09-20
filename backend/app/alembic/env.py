import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from app.models import *  # noqa: F401,F403  — registers every module's tables

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_url() -> str:
    from app.core.config import settings

    return settings.SQLALCHEMY_DATABASE_URI.unicode_string()


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    # Never autogenerate a DROP for something that exists in the database but has no
    # model: several tables/indexes/constraints are created by hand-written migration
    # code (unmodelled tables, CHECKs, extra indexes). Removals are written by hand.
    if reflected and compare_to is None:
        return False
    # Only manage our two application schemas; ignore extensions and system schemas.
    if type_ == "table":
        return getattr(obj, "schema", None) in {"ops", "app"}
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_schemas=True,
        include_object=include_object,
        version_table_schema="app",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=True,
            include_object=include_object,
            version_table_schema="app",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

_ = os  # keep import for env overrides in future
