"""Queries for the Notice Pack module. No business logic, no HTTP concepts."""

from sqlmodel import Session

from app.models.notices import NoticeCapture, NoticeEvent


def create_capture(
    session: Session,
    *,
    email: str,
    name: str,
    business_name: str,
    sector: str,
    readiness_score: int,
    export_type: str,
    source: str,
    consent: bool,
    ip_address: str,
    city: str,
    country: str,
    created_at_attr: str,
) -> NoticeCapture:
    row = NoticeCapture(
        email=email,
        name=name,
        business_name=business_name,
        sector=sector,
        readiness_score=readiness_score,
        export_type=export_type,
        source=source,
        consent=consent,
        ip_address=ip_address,
        city=city,
        country=country,
        created_at_attr=created_at_attr,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def create_event(
    session: Session,
    *,
    name: str,
    session_id: str,
    payload: str,
    created_at_attr: str,
) -> NoticeEvent:
    row = NoticeEvent(
        name=name,
        session_id=session_id,
        payload=payload,
        created_at_attr=created_at_attr,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
