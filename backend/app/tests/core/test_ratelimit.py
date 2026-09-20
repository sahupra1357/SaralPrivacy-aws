from freezegun import freeze_time
from sqlmodel import Session

from app.core.ratelimit import hit


def test_allows_up_to_limit_then_blocks_with_retry_after(session: Session) -> None:
    with freeze_time("2026-09-18 10:00:00"):
        for _ in range(3):
            assert hit(session, "t:1.1.1.1", 3, 60).ok
        blocked = hit(session, "t:1.1.1.1", 3, 60)
    assert not blocked.ok
    assert 1 <= blocked.retry_after <= 61


def test_window_resets_after_expiry(session: Session) -> None:
    with freeze_time("2026-09-18 10:00:00"):
        for _ in range(4):
            hit(session, "t:2.2.2.2", 3, 60)
        assert not hit(session, "t:2.2.2.2", 3, 60).ok
    with freeze_time("2026-09-18 10:01:01"):
        assert hit(session, "t:2.2.2.2", 3, 60).ok


def test_keys_are_independent(session: Session) -> None:
    for _ in range(5):
        hit(session, "a:x", 2, 60)
    assert hit(session, "b:x", 2, 60).ok
