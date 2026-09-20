import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def founder_alert_recipient(monkeypatch: pytest.MonkeyPatch) -> str:
    """ADMIN_EMAIL decides whether the founder alert is sent at all; pin it so the
    capture tests do not depend on the developer's .env."""
    recipient = "founder@saralprivacy.test"
    monkeypatch.setattr(settings, "ADMIN_EMAIL", recipient)
    return recipient
