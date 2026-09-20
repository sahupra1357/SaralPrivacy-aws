"""core: schemas, pgcrypto, ops.uuid_generate_v7(), app.login_attempts

Revision ID: 0001
Revises:
Create Date: 2026-09-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("create schema if not exists ops")
    op.execute("create schema if not exists app")
    op.execute("create extension if not exists pgcrypto")
    # UUIDv7 (time-ordered) — copied verbatim from _backup/supabase/migrations/0001_initial_schema.sql
    op.execute(
        """
        create or replace function ops.uuid_generate_v7() returns uuid
        language sql volatile parallel safe as $$
          select encode(
            set_bit(set_bit(
              overlay(uuid_send(gen_random_uuid())
                placing substring(int8send((extract(epoch from clock_timestamp())*1000)::bigint) from 3)
                from 1 for 6),
              52, 1), 53, 1), 'hex')::uuid
        $$;
        """
    )
    # Shared rate limiter (core/ratelimit.py). Lives here because every module uses it.
    op.create_table(
        "login_attempts",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        schema="app",
    )


def downgrade() -> None:
    op.drop_table("login_attempts", schema="app")
    op.execute("drop function if exists ops.uuid_generate_v7()")
