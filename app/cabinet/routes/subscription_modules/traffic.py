"""Traffic management endpoints.

GET /subscription/traffic-packages
POST /subscription/traffic
PUT /subscription/traffic
POST /subscription/refresh-traffic
POST /subscription/traffic/save-cart
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.tariff import get_tariff_by_id
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import TransactionType, User
from app.services.pricing_engine import pricing_engine
from app.services.remnawave_service import RemnaWaveService
from app.services.subscription_service import SubscriptionService
from app.services.user_cart_service import user_cart_service
from app.utils.cache import RateLimitCache, cache, cache_key

from ...dependencies import get_cabinet_db, get_current_cabinet_user
from ...schemas.subscription import (
    TrafficPackageResponse,
    TrafficPurchaseRequest,
)
from .helpers import _apply_addon_discount


logger = structlog.get_logger(__name__)

router = APIRouter()


@router.get('/traffic-packages', response_model=list[TrafficPackageResponse])
async def get_traffic_packages(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get available traffic packages."""
    from app.database.crud.tariff import get_tariff_by_id
    from app.database.crud.user import get_user_by_id

    fresh_user = await get_user_by_id(db, user.id)
    if not fresh_user or not fresh_user.subscription:
        return []

    # Режим тарифов - берём пакеты из тарифа
    if settings.is_tariffs_mode() and fresh_user.subscription.tariff_id:
        tariff = await get_tariff_by_id(db, fresh_user.subscription.tariff_id)
        if not tariff:
            return []

        # Проверяем, разрешена ли докупка для этого тарифа
        if not getattr(tariff, 'traffic_topup_enabled', False):
            return []

        # Проверяем безлимит
        if tariff.traffic_limit_gb == 0:
            return []

        packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
        result = []

        for gb, price in packages.items():
            if price <= 0:
                continue
            result.append(
                TrafficPackageResponse(
                    gb=gb,
                    price_kopeks=price,
                    price_rubles=price / 100,
                    is_unlimited=False,
                )
            )

        return sorted(result, key=lambda x: x.gb)

    # Classic режим - глобальные настройки
    if not settings.is_traffic_topup_enabled():
        return []

    # Проверяем настройку тарифа пользователя (allow_traffic_topup)
    if fresh_user.subscription.tariff_id:
        tariff = await get_tariff_by_id(db, fresh_user.subscription.tariff_id)
        if tariff and not tariff.allow_traffic_topup:
            return []

    packages = settings.get_traffic_topup_packages()
    result = []

    for pkg in packages:
        if not pkg.get('enabled', True):
            continue
        if pkg['price'] <= 0:
            continue

        result.append(
            TrafficPackageResponse(
                gb=pkg['gb'],
                price_kopeks=pkg['price'],
                price_rubles=pkg['price'] / 100,
                is_unlimited=pkg['gb'] == 0,
            )
        )

    return result


@router.post('/traffic')
async def purchase_traffic(
    request: TrafficPurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Purchase additional traffic."""
    if getattr(user, 'restriction_subscription', False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Subscription purchases are restricted for this account',
        )

    from app.database.crud.subscription import add_subscription_traffic
    from app.database.crud.tariff import get_tariff_by_id
    from app.utils.pricing_utils import calculate_prorated_price

    await db.refresh(user, ['subscriptions'])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No subscription found',
        )

    subscription = user.subscription
    tariff = None
    base_price_kopeks = 0
    is_tariff_mode = settings.is_tariffs_mode() and subscription.tariff_id

    # Режим тарифов
    if is_tariff_mode:
        tariff = await get_tariff_by_id(db, subscription.tariff_id)
        if not tariff:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Tariff not found',
            )

        # Проверяем, разрешена ли докупка
        if not getattr(tariff, 'traffic_topup_enabled', False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Traffic top-up is disabled for this tariff',
            )

        # Проверяем безлимит
        if tariff.traffic_limit_gb == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Cannot add traffic to unlimited subscription',
            )

        # Проверяем лимит докупки
        max_topup_limit = getattr(tariff, 'max_topup_traffic_gb', 0) or 0
        if max_topup_limit > 0:
            current_traffic = subscription.traffic_limit_gb or 0
            new_traffic = current_traffic + request.gb
            if new_traffic > max_topup_limit:
                available_gb = max(0, max_topup_limit - current_traffic)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f'Traffic limit exceeded. Max: {max_topup_limit} GB, available: {available_gb} GB',
                )

        # Получаем цену из тарифа
        packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
        if request.gb not in packages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Traffic package {request.gb}GB is not available',
            )
        base_price_kopeks = packages[request.gb]
        if base_price_kopeks <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Traffic package {request.gb}GB has no price configured',
            )

    else:
        # Classic режим
        if not settings.is_traffic_topup_enabled():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Traffic top-up feature is disabled',
            )

        # Проверяем настройку тарифа (allow_traffic_topup)
        if subscription.tariff_id:
            tariff = await get_tariff_by_id(db, subscription.tariff_id)
            if tariff and not tariff.allow_traffic_topup:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail='Traffic top-up is not available for your tariff',
                )

        # Получаем цену из глобальных настроек
        packages = settings.get_traffic_topup_packages()
        matching_pkg = next((pkg for pkg in packages if pkg['gb'] == request.gb and pkg.get('enabled', True)), None)
        if not matching_pkg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Invalid traffic package',
            )
        base_price_kopeks = matching_pkg['price']
        if base_price_kopeks <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Traffic package has no price configured',
            )

    # На тарифах пакеты трафика покупаются на 1 месяц (30 дней),
    # цена в тарифе уже месячная — не умножаем на оставшиеся месяцы подписки.
    # Пропорциональный расчёт применяем только в классическом режиме.
    if is_tariff_mode:
        prorated_price = base_price_kopeks
        days_charged = 30
    else:
        prorated_price, days_charged = calculate_prorated_price(
            base_price_kopeks,
            subscription.end_date,
        )

    # Lock user row to prevent TOCTOU on promo-offer state
    from app.database.crud.user import lock_user_for_pricing

    user = await lock_user_for_pricing(db, user.id)

    # Apply discount from promo group using proper method
    period_hint_days = days_charged if days_charged > 0 else 30
    discount_result = _apply_addon_discount(user, 'traffic', prorated_price, period_hint_days)
    final_price = discount_result['discounted']
    traffic_discount_percent = discount_result['percent']
    discount_value = discount_result['discount']

    # Ensure minimum price after discount (except for 100% discount)
    if traffic_discount_percent < 100 and final_price > 0:
        final_price = max(100, final_price)

    # Проверяем баланс
    if user.balance_kopeks < final_price:
        missing = final_price - user.balance_kopeks

        # Save cart for auto-purchase after balance top-up
        cart_data = {
            'cart_mode': 'add_traffic',
            'subscription_id': subscription.id,
            'traffic_gb': request.gb,
            'price_kopeks': final_price,
            'base_price_kopeks': prorated_price,
            'discount_percent': traffic_discount_percent,
            'source': 'cabinet',
            'description': f'Докупка {request.gb} ГБ трафика',
        }

        try:
            await user_cart_service.save_user_cart(user.id, cart_data)
            logger.info(
                'Cart saved for traffic purchase (cabinet) user + discount',
                user_id=user.id,
                gb=request.gb,
                traffic_discount_percent=traffic_discount_percent,
            )
        except Exception as e:
            logger.error('Error saving cart for traffic purchase (cabinet)', error=e)

        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                'code': 'insufficient_funds',
                'message': f'Недостаточно средств. Не хватает {settings.format_price(missing)}',
                'missing_amount': missing,
                'cart_saved': True,
                'cart_mode': 'add_traffic',
            },
        )

    # Формируем описание
    if traffic_discount_percent > 0:
        traffic_description = f'Докупка {request.gb} ГБ трафика (скидка {traffic_discount_percent}%)'
    else:
        traffic_description = f'Докупка {request.gb} ГБ трафика'

    # Списываем баланс
    success = await subtract_user_balance(db, user, final_price, traffic_description)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to charge balance',
        )

    # Добавляем трафик (add_subscription_traffic обновляет purchased_traffic_gb, traffic_reset_at и коммитит)
    await add_subscription_traffic(db, subscription, request.gb)

    # Реактивируем подписку если она была DISABLED/EXPIRED (например, после LIMITED/EXPIRED в RemnaWave)
    from app.database.crud.subscription import reactivate_subscription

    await reactivate_subscription(db, subscription)

    # Синхронизируем с RemnaWave
    try:
        subscription_service = SubscriptionService()
        if getattr(user, 'remnawave_uuid', None):
            await subscription_service.update_remnawave_user(db, subscription)
            # Явно включаем пользователя на панели (PATCH может не снять LIMITED-статус)
            if subscription.status == 'active':
                await subscription_service.enable_remnawave_user(user.remnawave_uuid)
        else:
            await subscription_service.create_remnawave_user(db, subscription)
    except Exception as e:
        logger.error('Failed to sync traffic with RemnaWave', error=e)

    # Создаём транзакцию
    await create_transaction(
        db=db,
        user_id=user.id,
        type=TransactionType.SUBSCRIPTION_PAYMENT,
        amount_kopeks=final_price,
        description=traffic_description,
    )

    await db.refresh(user)
    await db.refresh(subscription)

    # Отправляем уведомление админам
    try:
        from aiogram import Bot

        from app.services.admin_notification_service import AdminNotificationService

        if getattr(settings, 'ADMIN_NOTIFICATIONS_ENABLED', False) and settings.BOT_TOKEN:
            bot = Bot(token=settings.BOT_TOKEN)
            try:
                notification_service = AdminNotificationService(bot)
                old_traffic = subscription.traffic_limit_gb - request.gb
                await notification_service.send_subscription_update_notification(
                    db=db,
                    user=user,
                    subscription=subscription,
                    update_type='traffic',
                    old_value=old_traffic,
                    new_value=subscription.traffic_limit_gb,
                    price_paid=final_price,
                )
            finally:
                await bot.session.close()
    except Exception as e:
        logger.error('Failed to send admin notification for traffic purchase', error=e)

    response: dict[str, Any] = {
        'success': True,
        'message': 'Traffic purchased successfully',
        'gb_added': request.gb,
        'new_traffic_limit_gb': subscription.traffic_limit_gb,
        'amount_paid_kopeks': final_price,
        'new_balance_kopeks': user.balance_kopeks,
    }

    if traffic_discount_percent > 0:
        response['discount_percent'] = traffic_discount_percent
        response['discount_kopeks'] = discount_value
        response['base_price_kopeks'] = prorated_price

    return response


@router.post('/traffic/save-cart')
async def save_traffic_cart(
    request: TrafficPurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> dict[str, bool]:
    """Save cart for traffic purchase (for insufficient balance flow)."""

    await db.refresh(user, ['subscriptions'])
    subscription = user.subscription

    if not subscription:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='У вас нет активной подписки',
        )

    if subscription.status not in ['active', 'trial']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Ваша подписка неактивна',
        )

    if subscription.is_trial:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Докупка трафика недоступна на пробном периоде',
        )

    if subscription.traffic_limit_gb == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='У вас уже безлимитный трафик',
        )

    # Get traffic price from tariff or settings
    tariff = None
    base_price_kopeks = 0
    is_tariff_mode = settings.is_tariffs_mode() and subscription.tariff_id

    if is_tariff_mode:
        tariff = await get_tariff_by_id(db, subscription.tariff_id)
        if not tariff:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Тариф не найден',
            )

        if not getattr(tariff, 'traffic_topup_enabled', False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Докупка трафика недоступна на вашем тарифе',
            )

        packages = tariff.get_traffic_topup_packages() if hasattr(tariff, 'get_traffic_topup_packages') else {}
        if request.gb not in packages:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f'Пакет трафика {request.gb} ГБ недоступен',
            )
        base_price_kopeks = packages[request.gb]
    else:
        if not settings.is_traffic_topup_enabled():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Докупка трафика отключена',
            )

        packages = settings.get_traffic_topup_packages()
        matching_pkg = next((pkg for pkg in packages if pkg['gb'] == request.gb and pkg.get('enabled', True)), None)
        if not matching_pkg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Недоступный пакет трафика',
            )
        base_price_kopeks = matching_pkg['price']

    # Calculate prorated price (days-based), then apply discount
    from app.utils.pricing_utils import calculate_prorated_price as _calc_prorated

    now = datetime.now(UTC)
    days_left = max(1, (subscription.end_date - now).days)
    prorated_price, _ = _calc_prorated(
        base_price_kopeks,
        subscription.end_date,
    )
    discount_result = _apply_addon_discount(user, 'traffic', prorated_price, days_left)
    final_price = discount_result['discounted']
    traffic_discount_percent = discount_result['percent']

    # Save cart for auto-purchase after balance top-up
    cart_data = {
        'cart_mode': 'add_traffic',
        'subscription_id': subscription.id,
        'traffic_gb': request.gb,
        'price_kopeks': final_price,
        'base_price_kopeks': base_price_kopeks,
        'discount_percent': traffic_discount_percent,
        'source': 'cabinet',
        'description': f'Докупка {request.gb} ГБ трафика',
    }
    await user_cart_service.save_user_cart(user.id, cart_data)
    logger.info('Cart saved for traffic purchase (cabinet save-cart) user +', user_id=user.id, gb=request.gb)

    return {'success': True, 'cart_saved': True}


# ============ Traffic Switch (Change Traffic Package) ============


@router.put('/traffic')
async def switch_traffic_package(
    request: TrafficPurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
) -> dict[str, Any]:
    """Switch to a different traffic package (change limit)."""
    from app.utils.pricing_utils import calculate_prorated_price

    await db.refresh(user, ['subscriptions'])

    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No subscription found',
        )

    if user.subscription.is_trial:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Traffic management is only available for paid subscriptions',
        )

    current_traffic = user.subscription.traffic_limit_gb or 0
    new_traffic = request.gb

    if current_traffic == new_traffic:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Already on this traffic package',
        )

    # Get available packages
    packages = settings.get_traffic_packages()
    current_pkg = next((p for p in packages if p['gb'] == current_traffic and p.get('enabled', True)), None)
    new_pkg = next((p for p in packages if p['gb'] == new_traffic and p.get('enabled', True)), None)

    if not new_pkg:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Invalid traffic package',
        )

    # Calculate price difference (only charge for upgrade)
    current_price = current_pkg['price'] if current_pkg else 0
    new_price = new_pkg['price']

    if new_price > current_price:
        # Upgrade - charge difference
        price_diff = new_price - current_price

        # Lock user row to prevent TOCTOU on promo-offer state
        from app.database.crud.user import lock_user_for_pricing

        user = await lock_user_for_pricing(db, user.id)

        # Apply promo discount via PricingEngine
        price_diff, _discount_val, traffic_discount_percent = pricing_engine.calculate_traffic_discount(
            price_diff,
            user,
        )

        # Prorated calculation
        final_price, days_charged = calculate_prorated_price(price_diff, user.subscription.end_date)

        if user.balance_kopeks < final_price:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f'Insufficient balance. Need {final_price / 100:.2f} RUB',
            )

        # Charge balance
        description = f'Traffic upgrade from {current_traffic}GB to {new_traffic}GB'
        success = await subtract_user_balance(db, user, final_price, description)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail='Failed to charge balance',
            )

        # Create transaction
        await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=final_price,
            description=description,
        )

        charged = final_price
    else:
        # Downgrade - no charge, no refund
        charged = 0

    # Update subscription — delete TrafficPurchase records before resetting purchased_traffic_gb
    from sqlalchemy import delete as sql_delete

    from app.database.models import TrafficPurchase

    await db.execute(sql_delete(TrafficPurchase).where(TrafficPurchase.subscription_id == user.subscription.id))
    user.subscription.traffic_limit_gb = new_traffic
    user.subscription.purchased_traffic_gb = 0  # Reset purchased traffic on switch
    user.subscription.traffic_reset_at = None  # Reset traffic reset date
    user.subscription.updated_at = datetime.now(UTC)
    await db.commit()

    # Sync with RemnaWave
    try:
        subscription_service = SubscriptionService()
        if getattr(user, 'remnawave_uuid', None):
            await subscription_service.update_remnawave_user(db, user.subscription)
        else:
            await subscription_service.create_remnawave_user(db, user.subscription)
    except Exception as e:
        logger.error('Failed to sync traffic switch with RemnaWave', error=e)

    await db.refresh(user)
    await db.refresh(user.subscription)

    return {
        'success': True,
        'message': f'Traffic changed from {current_traffic}GB to {new_traffic}GB',
        'old_traffic_gb': current_traffic,
        'new_traffic_gb': new_traffic,
        'charged_kopeks': charged,
        'balance_kopeks': user.balance_kopeks,
        'balance_label': settings.format_price(user.balance_kopeks),
    }


# ============ Traffic Refresh ============

# Rate limit: 1 request per 60 seconds per user
TRAFFIC_REFRESH_RATE_LIMIT = 1
TRAFFIC_REFRESH_RATE_WINDOW = 60  # seconds
TRAFFIC_CACHE_TTL = 60  # Cache traffic data for 60 seconds


@router.post('/refresh-traffic')
async def refresh_traffic(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Refresh traffic usage from RemnaWave panel.
    Rate limited to 1 request per 60 seconds.
    """
    if not user.subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='No active subscription',
        )

    # Используем user.id для rate limit и кеша (работает и для email-пользователей)
    user_cache_id = user.id

    # Check rate limit
    is_limited = await RateLimitCache.is_rate_limited(
        user_cache_id,
        'traffic_refresh',
        TRAFFIC_REFRESH_RATE_LIMIT,
        TRAFFIC_REFRESH_RATE_WINDOW,
    )

    if is_limited:
        # Check if we have cached data
        traffic_cache_key = cache_key('traffic', user_cache_id)
        cached_data = await cache.get(traffic_cache_key)

        if cached_data:
            return {
                'success': True,
                'cached': True,
                'rate_limited': True,
                'retry_after_seconds': TRAFFIC_REFRESH_RATE_WINDOW,
                **cached_data,
            }

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f'Rate limited. Try again in {TRAFFIC_REFRESH_RATE_WINDOW} seconds.',
            headers={'Retry-After': str(TRAFFIC_REFRESH_RATE_WINDOW)},
        )

    # Fetch traffic from RemnaWave
    try:
        remnawave_service = RemnaWaveService()

        # Для email-пользователей (без telegram_id) используем UUID
        if user.telegram_id:
            traffic_stats = await remnawave_service.get_user_traffic_stats(user.telegram_id)
        elif user.remnawave_uuid:
            traffic_stats = await remnawave_service.get_user_traffic_stats_by_uuid(user.remnawave_uuid)
        else:
            traffic_stats = None

        if not traffic_stats:
            # Return current database values if RemnaWave unavailable
            traffic_data = {
                'traffic_used_bytes': int((user.subscription.traffic_used_gb or 0) * (1024**3)),
                'traffic_used_gb': round(user.subscription.traffic_used_gb or 0, 2),
                'traffic_limit_bytes': int((user.subscription.traffic_limit_gb or 0) * (1024**3)),
                'traffic_limit_gb': user.subscription.traffic_limit_gb or 0,
                'traffic_used_percent': round(
                    ((user.subscription.traffic_used_gb or 0) / (user.subscription.traffic_limit_gb or 1)) * 100
                    if user.subscription.traffic_limit_gb
                    else 0,
                    1,
                ),
                'is_unlimited': (user.subscription.traffic_limit_gb or 0) == 0,
            }
            return {
                'success': True,
                'cached': False,
                'source': 'database',
                **traffic_data,
            }

        # Update subscription with fresh data
        used_gb = traffic_stats.get('used_traffic_gb', 0)
        if abs((user.subscription.traffic_used_gb or 0) - used_gb) > 0.01:
            user.subscription.traffic_used_gb = used_gb
            user.subscription.updated_at = datetime.now(UTC)
            await db.commit()

        # Calculate percentage
        limit_gb = user.subscription.traffic_limit_gb or 0
        if limit_gb > 0:
            percent = min(100, (used_gb / limit_gb) * 100)
        else:
            percent = 0

        traffic_data = {
            'traffic_used_bytes': traffic_stats.get('used_traffic_bytes', 0),
            'traffic_used_gb': round(used_gb, 2),
            'traffic_limit_bytes': traffic_stats.get('traffic_limit_bytes', 0),
            'traffic_limit_gb': limit_gb,
            'traffic_used_percent': round(percent, 1),
            'is_unlimited': limit_gb == 0,
            'lifetime_used_bytes': traffic_stats.get('lifetime_used_traffic_bytes', 0),
            'lifetime_used_gb': round(traffic_stats.get('lifetime_used_traffic_gb', 0), 2),
        }

        # Cache the result
        traffic_cache_key = cache_key('traffic', user_cache_id)
        await cache.set(traffic_cache_key, traffic_data, TRAFFIC_CACHE_TTL)

        return {
            'success': True,
            'cached': False,
            'source': 'remnawave',
            **traffic_data,
        }

    except Exception as e:
        logger.error('Error refreshing traffic for user', user_id=user.id, error=e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail='Failed to refresh traffic data',
        )
