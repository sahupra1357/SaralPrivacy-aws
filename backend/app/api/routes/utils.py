from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import SessionDep

router = APIRouter(prefix="/utils", tags=["utils"])


@router.get("/health-check/")
def health_check() -> dict[str, bool]:
    return {"ok": True}


@router.get("/health-check/db")
def health_check_db(session: SessionDep) -> dict[str, bool]:
    session.execute(text("select 1"))
    return {"ok": True, "db": True}
