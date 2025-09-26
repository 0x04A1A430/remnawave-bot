from datetime import datetime

from fastapi import APIRouter

from app.config import settings
from app.webapi.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.WEBAPI_VERSION,
        timestamp=datetime.utcnow(),
    )
