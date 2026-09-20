"""Seed the first admin if no admin exists. Implemented by the auth module; until then
this is a no-op so prestart works in wave 0."""

import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def _ensure_storage() -> None:
    """Local MinIO: create the bucket + public-read infographics/ policy. No-op on AWS."""
    from app.services import storage  # noqa: PLC0415

    try:
        if storage.ensure_bucket():
            log.info("created storage bucket")
    except Exception:  # noqa: BLE001 — storage being down must not stop the API starting
        log.warning(
            "could not prepare the storage bucket (uploads will fail until it exists)",
            exc_info=True,
        )


def main() -> None:
    _ensure_storage()
    try:
        from app.crud.auth import ensure_first_admin  # noqa: PLC0415
    except ImportError:
        log.info("auth module not integrated yet; skipping first-admin seed")
        return
    from sqlmodel import Session  # noqa: PLC0415

    from app.core.db import engine  # noqa: PLC0415

    with Session(engine) as session:
        ensure_first_admin(session)


if __name__ == "__main__":
    main()
