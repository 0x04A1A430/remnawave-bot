"""Promo code routes for cabinet."""

import logging
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.promocode_service import PromoCodeService

from ..dependencies import get_cabinet_db, get_current_cabinet_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/promocode", tags=["Cabinet Promocode"])


class PromocodeActivateRequest(BaseModel):
    """Request to activate a promo code."""
    code: str = Field(..., min_length=1, max_length=50, description="Promo code to activate")


class PromocodeActivateResponse(BaseModel):
    """Response after activating a promo code."""
    success: bool
    message: str
    balance_before: float = 0
    balance_after: float = 0
    bonus_description: str | None = None


@router.post("/activate", response_model=PromocodeActivateResponse)
async def activate_promocode(
    request: PromocodeActivateRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Activate a promo code for the current user."""
    promocode_service = PromoCodeService()

    result = await promocode_service.activate_promocode(
        db=db,
        user_id=user.id,
        code=request.code.strip()
    )

    if result["success"]:
        balance_before_rubles = result.get("balance_before_kopeks", 0) / 100
        balance_after_rubles = result.get("balance_after_kopeks", 0) / 100

        return PromocodeActivateResponse(
            success=True,
            message="Promo code activated successfully",
            balance_before=balance_before_rubles,
            balance_after=balance_after_rubles,
            bonus_description=result.get("description"),
        )

    # Map error codes to messages
    error_messages = {
        "not_found": "Promo code not found",
        "expired": "Promo code has expired",
        "used": "Promo code has been fully used",
        "already_used_by_user": "You have already used this promo code",
        "user_not_found": "User not found",
        "server_error": "Server error occurred",
    }

    error_code = result.get("error", "server_error")
    error_message = error_messages.get(error_code, "Failed to activate promo code")

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=error_message,
    )
