"""User-facing partner application routes for cabinet."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.services.partner_application_service import partner_application_service

from ..dependencies import get_cabinet_db, get_current_cabinet_user
from ..schemas.partners import (
    PartnerApplicationInfo,
    PartnerApplicationRequest,
    PartnerStatusResponse,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/referral/partner', tags=['Cabinet Partner'])


@router.get('/status', response_model=PartnerStatusResponse)
async def get_partner_status(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get partner status and latest application for current user."""
    latest_app = await partner_application_service.get_latest_application(db, user.id)

    app_info = None
    if latest_app:
        app_info = PartnerApplicationInfo(
            id=latest_app.id,
            status=latest_app.status,
            company_name=latest_app.company_name,
            website_url=latest_app.website_url,
            telegram_channel=latest_app.telegram_channel,
            description=latest_app.description,
            expected_monthly_referrals=latest_app.expected_monthly_referrals,
            admin_comment=latest_app.admin_comment,
            approved_commission_percent=latest_app.approved_commission_percent,
            created_at=latest_app.created_at,
            processed_at=latest_app.processed_at,
        )

    commission = user.referral_commission_percent
    if commission is None and user.is_partner:
        commission = settings.REFERRAL_COMMISSION_PERCENT

    return PartnerStatusResponse(
        partner_status=user.partner_status,
        commission_percent=commission,
        latest_application=app_info,
    )


@router.post('/apply', response_model=PartnerApplicationInfo)
async def apply_for_partner(
    request: PartnerApplicationRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Submit partner application."""
    application, error = await partner_application_service.submit_application(
        db,
        user_id=user.id,
        company_name=request.company_name,
        website_url=request.website_url,
        telegram_channel=request.telegram_channel,
        description=request.description,
        expected_monthly_referrals=request.expected_monthly_referrals,
    )

    if not application:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    return PartnerApplicationInfo(
        id=application.id,
        status=application.status,
        company_name=application.company_name,
        website_url=application.website_url,
        telegram_channel=application.telegram_channel,
        description=application.description,
        expected_monthly_referrals=application.expected_monthly_referrals,
        admin_comment=application.admin_comment,
        approved_commission_percent=application.approved_commission_percent,
        created_at=application.created_at,
        processed_at=application.processed_at,
    )
