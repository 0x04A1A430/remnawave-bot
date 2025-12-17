from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Security

from app.services.support_settings_service import SupportSettingsService

from ..dependencies import require_api_token

router = APIRouter()


@router.get("/nalogo_receipts_enabled")
async def get_nalogo_receipts_enabled(
    _: Any = Security(require_api_token),
) -> bool:
    return SupportSettingsService.is_nalogo_receipts_enabled()


@router.put("/nalogo_receipts_enabled")
async def set_nalogo_receipts_enabled(
    enabled: bool,
    _: Any = Security(require_api_token),
) -> bool:
    return SupportSettingsService.set_nalogo_receipts_enabled(enabled)
