"""Engine and session factory. Tables are managed by Alembic, never create_all."""

from collections.abc import Generator

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import settings

engine: Engine = create_engine(str(settings.SQLALCHEMY_DATABASE_URI), pool_pre_ping=True)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
