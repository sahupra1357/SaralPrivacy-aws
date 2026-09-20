"""POST /api/v1/chat/feedback — D2: redacted, failures only."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from app.crud import chat as crud
from app.models.chat import ChatFeedback

URL = "/api/v1/chat/feedback"


def _rows(session: Session) -> list[ChatFeedback]:
    return list(session.exec(select(ChatFeedback)).all())


def test_feedback_rejects_a_malformed_body(client: TestClient) -> None:
    res = client.post(URL, content=b"{", headers={"content-type": "application/json"})
    assert res.status_code == 400
    assert res.json() == {"error": "Invalid JSON body."}


def test_feedback_requires_session_and_turn_ids(client: TestClient) -> None:
    res = client.post(URL, json={"sessionId": "s1"})
    assert res.status_code == 400
    assert res.json() == {"error": "sessionId and turnId are required."}


def test_feedback_rate_limits_by_ip(client: TestClient) -> None:
    for _ in range(20):
        assert (
            client.post(URL, json={"sessionId": "s1", "turnId": "t1", "helpful": True}).status_code
            == 200
        )
    res = client.post(URL, json={"sessionId": "s1", "turnId": "t1", "helpful": True})
    assert res.status_code == 429
    assert res.json() == {"error": "Too many requests."}


def test_a_thumbs_up_stores_the_signal_and_no_question_text(
    client: TestClient, session: Session
) -> None:
    res = client.post(
        URL,
        json={
            "sessionId": "s1",
            "turnId": "t1",
            "helpful": True,
            "pageUrl": "/learn/consent",
            "question": "my pan is ABCDE1234F",
        },
    )
    assert res.status_code == 200
    assert res.json() == {"stored": True}

    rows = _rows(session)
    assert len(rows) == 1
    assert rows[0].helpful is True
    assert rows[0].page_url == "/learn/consent"
    assert rows[0].failure_kind is None
    # No failureKind means the question is not stored at all.
    assert rows[0].redacted_question is None


def test_a_failure_turn_stores_the_question_redacted(client: TestClient, session: Session) -> None:
    res = client.post(
        URL,
        json={
            "sessionId": "s1",
            "turnId": "t2",
            "helpful": False,
            "failureKind": "thumbs_down",
            "question": "call me on 9876543210 about ABCDE1234F",
        },
    )
    assert res.json() == {"stored": True}

    row = _rows(session)[0]
    assert row.failure_kind == "thumbs_down"
    assert row.redacted_question is not None
    assert "9876543210" not in row.redacted_question
    assert "ABCDE1234F" not in row.redacted_question
    assert "[phone]" in row.redacted_question


def test_an_unknown_failure_kind_is_dropped(client: TestClient, session: Session) -> None:
    client.post(
        URL,
        json={"sessionId": "s1", "turnId": "t3", "failureKind": "made_up", "question": "secret"},
    )
    row = _rows(session)[0]
    assert row.failure_kind is None
    assert row.redacted_question is None


def test_long_fields_are_truncated(client: TestClient, session: Session) -> None:
    client.post(
        URL,
        json={
            "sessionId": "s" * 200,
            "turnId": "t" * 200,
            "reason": "r" * 900,
            "pageUrl": "/p" * 400,
        },
    )
    row = _rows(session)[0]
    assert len(row.session_id) == 64
    assert len(row.turn_id) == 64
    assert row.reason is not None and len(row.reason) == 500
    assert row.page_url is not None and len(row.page_url) == 200


def test_a_store_failure_never_breaks_chat_ux(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OperationalError("insert", {}, Exception("table gone"))

    monkeypatch.setattr(crud, "create_feedback", boom)
    res = client.post(URL, json={"sessionId": "s1", "turnId": "t1", "helpful": False})
    assert res.status_code == 200
    assert res.json() == {"stored": False}
