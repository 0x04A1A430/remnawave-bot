"""Admin routes for managing partners in cabinet."""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AdvertisingCampaign,
    PartnerApplication,
    PartnerStatus,
    ReferralEarning,
    User,
)
from app.services.partner_application_service import partner_application_service
from app.services.partner_stats_service import PartnerStatsService

from ..dependencies import get_cabinet_db, get_current_admin_user
from ..schemas.partners import (
    AdminApproveRequest,
    AdminPartnerApplicationItem,
    AdminPartnerApplicationsResponse,
    AdminPartnerDetailResponse,
    AdminPartnerItem,
    AdminPartnerListResponse,
    AdminRejectRequest,
    AdminUpdateCommissionRequest,
    CampaignSummary,
)


logger = structlog.get_logger(__name__)

router = APIRouter(prefix='/admin/partners', tags=['Cabinet Admin Partners'])


# ==================== Applications (static paths first) ====================


@router.get('/applications', response_model=AdminPartnerApplicationsResponse)
async def list_applications(
    application_status: str | None = Query(None, alias='status'),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """List partner applications."""
    applications, total = await partner_application_service.get_all_applications(
        db, status=application_status, limit=limit, offset=offset
    )

    # Batch-fetch users to avoid N+1
    user_ids = list({app.user_id for app in applications})
    if user_ids:
        users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
        users_map = {u.id: u for u in users_result.scalars().all()}
    else:
        users_map = {}

    items = []
    for app in applications:
        user = users_map.get(app.user_id)
        items.append(
            AdminPartnerApplicationItem(
                id=app.id,
                user_id=app.user_id,
                username=user.username if user else None,
                first_name=user.first_name if user else None,
                telegram_id=user.telegram_id if user else None,
                company_name=app.company_name,
                website_url=app.website_url,
                telegram_channel=app.telegram_channel,
                description=app.description,
                expected_monthly_referrals=app.expected_monthly_referrals,
                status=app.status,
                admin_comment=app.admin_comment,
                approved_commission_percent=app.approved_commission_percent,
                created_at=app.created_at,
                processed_at=app.processed_at,
            )
        )

    return AdminPartnerApplicationsResponse(items=items, total=total)


@router.post('/applications/{application_id}/approve')
async def approve_application(
    application_id: int,
    request: AdminApproveRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Approve a partner application."""
    success, error = await partner_application_service.approve_application(
        db,
        application_id=application_id,
        admin_id=admin.id,
        commission_percent=request.commission_percent,
        comment=request.comment,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    # Notify user about approval
    try:
        from aiogram import Bot

        from app.config import settings
        from app.services.notification_delivery_service import notification_delivery_service

        if settings.BOT_TOKEN:
            application = await db.get(PartnerApplication, application_id)
            user = await db.get(User, application.user_id) if application else None
            if user:
                comment_text = f'\n{request.comment}' if request.comment else ''
                tg_message = (
                    f'✅ Ваша заявка на партнёрство одобрена!\nКомиссия: {request.commission_percent}%{comment_text}'
                )
                bot = Bot(token=settings.BOT_TOKEN)
                try:
                    await notification_delivery_service.notify_partner_approved(
                        user=user,
                        commission_percent=request.commission_percent,
                        comment=request.comment,
                        bot=bot,
                        telegram_message=tg_message,
                    )
                finally:
                    await bot.session.close()
    except Exception as e:
        logger.error('Failed to send partner approval notification', error=e)

    return {'success': True}


@router.post('/applications/{application_id}/reject')
async def reject_application(
    application_id: int,
    request: AdminRejectRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Reject a partner application."""
    success, error = await partner_application_service.reject_application(
        db,
        application_id=application_id,
        admin_id=admin.id,
        comment=request.comment,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    # Notify user about rejection
    try:
        from aiogram import Bot

        from app.config import settings
        from app.services.notification_delivery_service import notification_delivery_service

        if settings.BOT_TOKEN:
            application = await db.get(PartnerApplication, application_id)
            user = await db.get(User, application.user_id) if application else None
            if user:
                comment_text = f'\nПричина: {request.comment}' if request.comment else ''
                tg_message = f'❌ Ваша заявка на партнёрство отклонена.{comment_text}'
                bot = Bot(token=settings.BOT_TOKEN)
                try:
                    await notification_delivery_service.notify_partner_rejected(
                        user=user,
                        comment=request.comment,
                        bot=bot,
                        telegram_message=tg_message,
                    )
                finally:
                    await bot.session.close()
    except Exception as e:
        logger.error('Failed to send partner rejection notification', error=e)

    return {'success': True}


# ==================== Stats (static paths) ====================


@router.get('/stats')
async def get_partner_stats(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get overall partner statistics."""
    total_partners = await db.execute(
        select(func.count()).select_from(User).where(User.partner_status == PartnerStatus.APPROVED.value)
    )
    pending_apps = await db.execute(
        select(func.count())
        .select_from(PartnerApplication)
        .where(PartnerApplication.status == PartnerStatus.PENDING.value)
    )
    total_referrals = await db.execute(select(func.count()).select_from(User).where(User.referred_by_id.isnot(None)))
    total_earnings = await db.execute(select(func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0)))

    return {
        'total_partners': total_partners.scalar() or 0,
        'pending_applications': pending_apps.scalar() or 0,
        'total_referrals': total_referrals.scalar() or 0,
        'total_earnings_kopeks': total_earnings.scalar() or 0,
    }


# ==================== Partners list ====================


@router.get('', response_model=AdminPartnerListResponse)
async def list_partners(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """List approved partners."""
    count_result = await db.execute(
        select(func.count()).select_from(User).where(User.partner_status == PartnerStatus.APPROVED.value)
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(User)
        .where(User.partner_status == PartnerStatus.APPROVED.value)
        .order_by(desc(User.created_at))
        .offset(offset)
        .limit(limit)
    )
    partners = result.scalars().all()

    # Batch-fetch earnings and referral counts to avoid N+1
    partner_ids = [u.id for u in partners]
    earnings_map: dict[int, int] = {}
    referral_count_map: dict[int, int] = {}

    if partner_ids:
        earnings_result = await db.execute(
            select(ReferralEarning.user_id, func.coalesce(func.sum(ReferralEarning.amount_kopeks), 0))
            .where(ReferralEarning.user_id.in_(partner_ids))
            .group_by(ReferralEarning.user_id)
        )
        earnings_map = {row[0]: int(row[1]) for row in earnings_result.all()}

        referral_result = await db.execute(
            select(User.referred_by_id, func.count())
            .where(User.referred_by_id.in_(partner_ids))
            .group_by(User.referred_by_id)
        )
        referral_count_map = {row[0]: row[1] for row in referral_result.all()}

    items = []
    for user in partners:
        items.append(
            AdminPartnerItem(
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                telegram_id=user.telegram_id,
                commission_percent=user.referral_commission_percent,
                total_referrals=referral_count_map.get(user.id, 0),
                total_earnings_kopeks=earnings_map.get(user.id, 0),
                balance_kopeks=user.balance_kopeks,
                partner_status=user.partner_status,
                created_at=user.created_at,
            )
        )

    return AdminPartnerListResponse(items=items, total=total)


# ==================== Partner detail (parametric paths last) ====================


@router.get('/{user_id}', response_model=AdminPartnerDetailResponse)
async def get_partner_detail(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get detailed partner info."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Пользователь не найден',
        )

    stats = await PartnerStatsService.get_referrer_detailed_stats(db, user_id)

    # Get assigned campaigns
    campaigns_result = await db.execute(
        select(AdvertisingCampaign).where(AdvertisingCampaign.partner_user_id == user_id)
    )
    campaigns = campaigns_result.scalars().all()
    campaign_list = [
        CampaignSummary(
            id=c.id,
            name=c.name,
            start_parameter=c.start_parameter,
            is_active=c.is_active,
        )
        for c in campaigns
    ]

    summary = stats['summary']
    earnings = stats['earnings']

    return AdminPartnerDetailResponse(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        telegram_id=user.telegram_id,
        commission_percent=user.referral_commission_percent,
        partner_status=user.partner_status,
        balance_kopeks=user.balance_kopeks,
        total_referrals=summary['total_referrals'],
        paid_referrals=summary['paid_referrals'],
        active_referrals=summary['active_referrals'],
        earnings_all_time=earnings['all_time_kopeks'],
        earnings_today=earnings['today_kopeks'],
        earnings_week=earnings['week_kopeks'],
        earnings_month=earnings['month_kopeks'],
        conversion_to_paid=summary['conversion_to_paid_percent'],
        campaigns=campaign_list,
        created_at=user.created_at,
    )


@router.patch('/{user_id}/commission')
async def update_commission(
    user_id: int,
    request: AdminUpdateCommissionRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update partner commission percent."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Пользователь не найден',
        )

    if user.partner_status != PartnerStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Пользователь не является партнёром',
        )

    old_commission = user.referral_commission_percent
    user.referral_commission_percent = request.commission_percent
    await db.commit()

    logger.info(
        'Комиссия партнёра обновлена',
        user_id=user_id,
        old_commission=old_commission,
        new_commission=request.commission_percent,
        admin_id=admin.id,
    )

    return {'success': True, 'commission_percent': request.commission_percent}


@router.post('/{user_id}/revoke')
async def revoke_partner(
    user_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Revoke partner status."""
    success, error = await partner_application_service.revoke_partner(db, user_id=user_id, admin_id=admin.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error,
        )

    return {'success': True}


@router.post('/{user_id}/campaigns/{campaign_id}/assign')
async def assign_campaign(
    user_id: int,
    campaign_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Assign a campaign to a partner."""
    campaign = await db.get(AdvertisingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Кампания не найдена',
        )

    user = await db.get(User, user_id)
    if not user or user.partner_status != PartnerStatus.APPROVED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Пользователь не является партнёром',
        )

    campaign.partner_user_id = user_id
    await db.commit()

    return {'success': True}


@router.post('/{user_id}/campaigns/{campaign_id}/unassign')
async def unassign_campaign(
    user_id: int,
    campaign_id: int,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Unassign a campaign from a partner."""
    campaign = await db.get(AdvertisingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='Кампания не найдена',
        )

    if campaign.partner_user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Кампания не привязана к этому партнёру',
        )

    campaign.partner_user_id = None
    await db.commit()

    return {'success': True}
