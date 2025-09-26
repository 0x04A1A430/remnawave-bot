from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import settings
from app.services.version_service import version_service

from ..dependencies import require_api_token

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check(_: object = Depends(require_api_token)) -> dict[str, object]:
    return {
        "status": "ok",
        "api_version": settings.WEB_API_VERSION,
        "bot_version": version_service.current_version,
        "features": {
            "monitoring": settings.MONITORING_INTERVAL > 0,
            "maintenance": True,
            "reporting": True,
            "webhooks": bool(settings.WEBHOOK_URL),
        },
    }
