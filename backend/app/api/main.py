from fastapi import APIRouter

from app.api.routes import (
    admin,
    aeo,
    assessments,
    blog,
    briefings,
    chat,
    forms,
    login,
    mfa,
    notices,
    outreach,
    seo,
    users,
    utils,
    webhooks,
)

api_router = APIRouter()
api_router.include_router(utils.router)

# ── Module routers (orchestrator adds one line per integrated module) ────────
api_router.include_router(assessments.router)
api_router.include_router(notices.router)
api_router.include_router(login.router)
api_router.include_router(mfa.router)
api_router.include_router(users.router)
api_router.include_router(forms.router)
api_router.include_router(chat.router)
api_router.include_router(outreach.router)
api_router.include_router(outreach.cron_router)
api_router.include_router(webhooks.router)
api_router.include_router(admin.router)
api_router.include_router(aeo.router)
api_router.include_router(seo.router)
api_router.include_router(briefings.router)
api_router.include_router(blog.router)
