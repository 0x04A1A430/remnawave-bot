"""Service for managing payment method display configurations in cabinet."""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.models import PaymentMethodConfig, PromoGroup


logger = logging.getLogger(__name__)


# ============ Default method definitions ============


# Mapping: method_id -> (default_display_name_func, is_configured_func, default_min, default_max, has_sub_options)
def _get_method_defaults() -> dict:
    """Get default configuration for each payment method based on env vars."""
    return {
        'telegram_stars': {
            'default_display_name': settings.get_telegram_stars_display_name(),
            'is_configured': settings.TELEGRAM_STARS_ENABLED,
            'default_min': 100,
            'default_max': 1000000,
            'available_sub_options': None,
        },
        'tribute': {
            'default_display_name': 'Tribute',
            'is_configured': settings.TRIBUTE_ENABLED and bool(getattr(settings, 'TRIBUTE_DONATE_LINK', '')),
            'default_min': 10000,
            'default_max': 10000000,
            'available_sub_options': None,
        },
        'cryptobot': {
            'default_display_name': settings.get_cryptobot_display_name(),
            'is_configured': settings.is_cryptobot_enabled(),
            'default_min': 1000,
            'default_max': 10000000,
            'available_sub_options': None,
        },
        'heleket': {
            'default_display_name': settings.get_heleket_display_name(),
            'is_configured': settings.is_heleket_enabled(),
            'default_min': 1000,
            'default_max': 10000000,
            'available_sub_options': None,
        },
        'yookassa': {
            'default_display_name': settings.get_yookassa_display_name(),
            'is_configured': settings.is_yookassa_enabled(),
            'default_min': settings.YOOKASSA_MIN_AMOUNT_KOPEKS,
            'default_max': settings.YOOKASSA_MAX_AMOUNT_KOPEKS,
            'available_sub_options': [
                {'id': 'card', 'name': 'Карта'},
                {'id': 'sbp', 'name': 'СБП'},
            ],
        },
        'mulenpay': {
            'default_display_name': settings.get_mulenpay_display_name(),
            'is_configured': settings.is_mulenpay_enabled(),
            'default_min': settings.MULENPAY_MIN_AMOUNT_KOPEKS,
            'default_max': settings.MULENPAY_MAX_AMOUNT_KOPEKS,
            'available_sub_options': None,
        },
        'pal24': {
            'default_display_name': settings.get_pal24_display_name(),
            'is_configured': settings.is_pal24_enabled(),
            'default_min': settings.PAL24_MIN_AMOUNT_KOPEKS,
            'default_max': settings.PAL24_MAX_AMOUNT_KOPEKS,
            'available_sub_options': [
                {'id': 'sbp', 'name': 'СБП'},
                {'id': 'card', 'name': 'Карта'},
            ],
        },
        'platega': {
            'default_display_name': settings.get_platega_display_name(),
            'is_configured': settings.is_platega_enabled(),
            'default_min': settings.PLATEGA_MIN_AMOUNT_KOPEKS,
            'default_max': settings.PLATEGA_MAX_AMOUNT_KOPEKS,
            'available_sub_options': _get_platega_sub_options(),
        },
        'wata': {
            'default_display_name': settings.get_wata_display_name(),
            'is_configured': settings.is_wata_enabled(),
            'default_min': settings.WATA_MIN_AMOUNT_KOPEKS,
            'default_max': settings.WATA_MAX_AMOUNT_KOPEKS,
            'available_sub_options': None,
        },
        'freekassa': {
            'default_display_name': settings.get_freekassa_display_name(),
            'is_configured': settings.is_freekassa_enabled(),
            'default_min': settings.FREEKASSA_MIN_AMOUNT_KOPEKS,
            'default_max': settings.FREEKASSA_MAX_AMOUNT_KOPEKS,
            'available_sub_options': [
                {'id': 'sbp', 'name': 'NSPK СБП'},
                {'id': 'card', 'name': 'Карта'},
            ],
        },
        'cloudpayments': {
            'default_display_name': settings.get_cloudpayments_display_name(),
            'is_configured': settings.is_cloudpayments_enabled(),
            'default_min': settings.CLOUDPAYMENTS_MIN_AMOUNT_KOPEKS,
            'default_max': settings.CLOUDPAYMENTS_MAX_AMOUNT_KOPEKS,
            'available_sub_options': [
                {'id': 'card', 'name': 'Карта'},
                {'id': 'sbp', 'name': 'СБП'},
            ],
        },
    }


def _get_platega_sub_options() -> list[dict] | None:
    """Get available Platega sub-options from config."""
    try:
        active_methods = settings.get_platega_active_methods()
        definitions = settings.get_platega_method_definitions()
        if not active_methods:
            return None
        options = []
        for method_code in active_methods:
            info = definitions.get(method_code, {})
            options.append(
                {
                    'id': str(method_code),
                    'name': info.get('title') or info.get('name') or f'Platega {method_code}',
                }
            )
        return options if options else None
    except Exception:
        return None


# Default order of methods
DEFAULT_METHOD_ORDER = [
    'telegram_stars',
    'tribute',
    'cryptobot',
    'heleket',
    'yookassa',
    'mulenpay',
    'pal24',
    'platega',
    'wata',
    'freekassa',
    'cloudpayments',
]


# ============ Initialization ============


async def ensure_payment_method_configs(db: AsyncSession) -> None:
    """Initialize payment method configs if they don't exist yet.

    Called on startup to seed defaults from env vars.
    """
    count_result = await db.execute(select(func.count()).select_from(PaymentMethodConfig))
    count = count_result.scalar() or 0

    if count > 0:
        return  # Already initialized

    logger.info('Initializing payment method configurations from env vars...')

    defaults = _get_method_defaults()

    for idx, method_id in enumerate(DEFAULT_METHOD_ORDER):
        method_def = defaults.get(method_id, {})
        is_configured = method_def.get('is_configured', False)
        sub_options = None
        available = method_def.get('available_sub_options')
        if available:
            # Enable all sub-options by default
            sub_options = {opt['id']: True for opt in available}

        config = PaymentMethodConfig(
            method_id=method_id,
            sort_order=idx,
            is_enabled=is_configured,
            display_name=None,
            sub_options=sub_options,
            min_amount_kopeks=None,
            max_amount_kopeks=None,
            user_type_filter='all',
            first_topup_filter='any',
            promo_group_filter_mode='all',
        )
        db.add(config)

    await db.commit()
    logger.info(f'Payment method configurations initialized ({len(DEFAULT_METHOD_ORDER)} methods).')


# ============ CRUD ============


async def get_all_configs(db: AsyncSession) -> list[PaymentMethodConfig]:
    """Get all payment method configs ordered by sort_order."""
    result = await db.execute(
        select(PaymentMethodConfig)
        .options(selectinload(PaymentMethodConfig.allowed_promo_groups))
        .order_by(PaymentMethodConfig.sort_order)
    )
    return list(result.scalars().all())


async def get_config_by_method_id(db: AsyncSession, method_id: str) -> PaymentMethodConfig | None:
    """Get a single config by method_id."""
    result = await db.execute(
        select(PaymentMethodConfig)
        .options(selectinload(PaymentMethodConfig.allowed_promo_groups))
        .where(PaymentMethodConfig.method_id == method_id)
    )
    return result.scalar_one_or_none()


async def update_config(
    db: AsyncSession,
    method_id: str,
    data: dict,
    promo_group_ids: list[int] | None = None,
) -> PaymentMethodConfig | None:
    """Update a payment method config."""
    config = await get_config_by_method_id(db, method_id)
    if not config:
        return None

    # Update scalar fields
    updatable_fields = (
        'is_enabled',
        'display_name',
        'sub_options',
        'min_amount_kopeks',
        'max_amount_kopeks',
        'user_type_filter',
        'first_topup_filter',
        'promo_group_filter_mode',
    )
    for key in updatable_fields:
        if key in data:
            setattr(config, key, data[key])

    # Update promo groups M2M if specified
    if promo_group_ids is not None:
        if promo_group_ids:
            result = await db.execute(select(PromoGroup).where(PromoGroup.id.in_(promo_group_ids)))
            groups = list(result.scalars().all())
        else:
            groups = []
        config.allowed_promo_groups = groups

    await db.commit()
    await db.refresh(config, attribute_names=['allowed_promo_groups'])
    return config


async def update_sort_order(db: AsyncSession, ordered_method_ids: list[str]) -> None:
    """Batch update sort order for all methods."""
    for index, method_id in enumerate(ordered_method_ids):
        result = await db.execute(select(PaymentMethodConfig).where(PaymentMethodConfig.method_id == method_id))
        config = result.scalar_one_or_none()
        if config:
            config.sort_order = index

    await db.commit()


async def get_all_promo_groups(db: AsyncSession) -> list[PromoGroup]:
    """Get all promo groups for the filter selector."""
    result = await db.execute(select(PromoGroup).order_by(PromoGroup.priority.desc(), PromoGroup.name))
    return list(result.scalars().all())
