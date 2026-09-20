from sqlmodel import Session, select

from app.crud import notices as crud
from app.models.notices import NoticeCapture, NoticeEvent


def test_create_capture_persists_every_column(session: Session) -> None:
    row = crud.create_capture(
        session,
        email="owner@acme.in",
        name="Owner",
        business_name="Acme",
        sector="d2c",
        readiness_score=55,
        export_type="pack",
        source="notice-generator",
        consent=True,
        ip_address="1.2.3.4",
        city="Pune",
        country="IN",
        created_at_attr="2026-09-18T06:30:00.000Z",
    )
    stored = session.exec(select(NoticeCapture).where(NoticeCapture.id == row.id)).one()
    assert stored.email == "owner@acme.in"
    assert stored.readiness_score == 55
    assert stored.consent is True
    assert stored.city == "Pune"
    # The app-side timestamp lands in the renamed column; created_at is the row's own.
    assert stored.created_at_attr == "2026-09-18T06:30:00.000Z"
    assert stored.created_at is not None
    assert stored.legacy_id is None


def test_create_event_persists_name_session_and_payload(session: Session) -> None:
    row = crud.create_event(
        session,
        name="notice_score_calculated",
        session_id="sid-9",
        payload='{"sector":"ca","score":40}',
        created_at_attr="2026-09-18T06:30:00.000Z",
    )
    stored = session.exec(select(NoticeEvent).where(NoticeEvent.id == row.id)).one()
    assert stored.name == "notice_score_calculated"
    assert stored.session_id == "sid-9"
    assert stored.payload == '{"sector":"ca","score":40}'
    assert stored.created_at_attr == "2026-09-18T06:30:00.000Z"
