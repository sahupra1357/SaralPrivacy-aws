"""Shared fixtures (write-tests skill).

- `session`: real Postgres (scripts/test.sh points POSTGRES_DB at *_test and migrates it),
  each test inside a transaction that is rolled back.
- `client`: TestClient with get_session overridden.
- `mock_email`, `mock_storage`, `mock_llm`, `mock_pdf`, `mock_revalidate`: autouse
  recorders so no test ever touches the network.
- `admin_headers` / `blogger_headers`: provided by the auth module's conftest plugin
  once it exists (app/tests/auth/conftest.py registers them via pytest_plugins).
"""

from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.db import engine, get_session
from app.main import app

# Auth fixtures (admin_headers, blogger_headers, ...) shared by every module.
pytest_plugins = ["app.tests.auth.fixtures"]


@pytest.fixture
def session() -> Generator[Session, None, None]:
    """One outer transaction per test, rolled back at the end.

    `join_transaction_mode="create_savepoint"` (SQLAlchemy 2.x) turns every commit()
    and begin_nested() in the code under test into savepoints inside that outer
    transaction, so production code can commit freely and nothing persists.
    """
    connection = engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield sess
    finally:
        sess.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client(session: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_session, None)


@dataclass
class Recorder:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def last(self) -> dict[str, Any]:
        assert self.calls, "expected at least one call"
        return self.calls[-1]


@pytest.fixture(autouse=True)
def mock_email(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    from app.services import email

    rec = Recorder()

    def fake_send(to: Any, subject: str, html: str, **kw: Any) -> email.SentEmail:
        recipients = [to] if isinstance(to, str) else list(to)
        rec.calls.append({"to": recipients, "subject": subject, "html": html, **kw})
        return email.SentEmail(message_id=f"test-{len(rec.calls)}", to=recipients, subject=subject)

    monkeypatch.setattr(email, "send", fake_send)
    return rec


@pytest.fixture(autouse=True)
def mock_storage(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    from app.services import storage

    rec = Recorder()
    store: dict[str, bytes] = {}

    def put(key: str, data: bytes, content_type: str, **kw: Any) -> str:
        store[key] = data
        rec.calls.append(
            {"op": "put", "key": key, "content_type": content_type, "size": len(data), **kw}
        )
        return storage.get_url(key)

    monkeypatch.setattr(storage, "put", put)
    monkeypatch.setattr(storage, "exists", lambda key: key in store)
    monkeypatch.setattr(storage, "delete", lambda key: store.pop(key, None))
    monkeypatch.setattr(storage, "get_bytes", lambda key: store[key])
    monkeypatch.setattr(
        storage, "presigned_download_url", lambda key, **kw: f"https://signed.test/{key}"
    )
    return rec


@pytest.fixture(autouse=True)
def mock_llm(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    from app.services import llm

    rec = Recorder()
    responses: dict[str, Any] = {"complete": "MOCKED COMPLETION", "stream": ["MOCKED ", "STREAM"]}

    def complete(system: str, messages: list[dict[str, Any]], **kw: Any) -> str:
        rec.calls.append({"op": "complete", "system": system, "messages": messages, **kw})
        return str(responses["complete"])

    def stream(system: str, messages: list[dict[str, Any]], **kw: Any) -> Iterator[str]:
        rec.calls.append({"op": "stream", "system": system, "messages": messages, **kw})
        yield from responses["stream"]

    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(llm, "stream", stream)
    rec.responses = responses
    return rec


@pytest.fixture(autouse=True)
def mock_pdf(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    from app.services import pdf

    rec = Recorder()

    def render_html(html: str, **kw: Any) -> bytes:
        rec.calls.append({"html": html, **kw})
        return b"%PDF-1.4\n%mock\n"

    monkeypatch.setattr(pdf, "render_html", render_html)
    return rec


@pytest.fixture(autouse=True)
def mock_revalidate(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    from app.services import revalidate

    rec = Recorder()
    monkeypatch.setattr(revalidate, "tag", lambda name: rec.calls.append({"tag": name}) or True)
    monkeypatch.setattr(revalidate, "path", lambda p: rec.calls.append({"path": p}) or True)
    return rec
