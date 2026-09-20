"""GET /api/v1/chat/health — index size and Pinecone reachability."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.services import retrieval

URL = "/api/v1/chat/health"


def test_health_reports_the_index_without_pinecone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "")
    res = client.get(URL)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["chunks"] > 300
    assert data["pinecone"] == {
        "configured": False,
        "index": "saralprivacy-setu",
        "records": None,
        "reachable": False,
    }


def test_health_reports_pinecone_records_when_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "pc-test-key-123456")
    monkeypatch.setattr(retrieval, "pinecone_stats", lambda: {"vectorCount": 406})
    data = client.get(URL).json()
    assert data["pinecone"]["configured"] is True
    assert data["pinecone"]["records"] == 406
    assert data["pinecone"]["reachable"] is True


def test_health_reports_an_unreachable_pinecone(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retrieval.settings, "PINECONE_API_KEY", "pc-test-key-123456")
    monkeypatch.setattr(retrieval, "pinecone_stats", lambda: None)
    data = client.get(URL).json()
    assert data["pinecone"]["reachable"] is False
    assert data["pinecone"]["records"] is None


def test_health_returns_500_when_the_index_cannot_load(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("chat-index.json missing")

    monkeypatch.setattr(retrieval, "load_index", broken)
    res = client.get(URL)
    assert res.status_code == 500
    assert res.json() == {"ok": False, "error": "chat-index.json missing"}
