"""Handler for inline gift deep-links and activation.

Flow:
1. Admin sends gift via inline query.
2. Recipient clicks "Активировать" → /start bs_<gift_code>
3. Bot shows preview of what changes.
4. On confirm: applies gift, decrements counter, updates button text.
   When activated_count >= max_activations → mark is_activated=True, button → ✓.

DB encoding for subscription params:
  days=NULL          → no change
  days=N             → add N days
  traffic_limit_gb=NULL  → no change
  traffic_limit_gb=-1    → set unlimited (0 in remnawave)
  traffic_limit_gb=N     → set to N GB
  device_limit=NULL  → no change
  device_limit=N     → set to N devices
"""

from datetime import UTC, datetime, timedelta

import structlog
from aiogram import Bot, Dispatcher, F, types
from aiogram.types import InaccessibleMessage
from sqlalchemy import select

from app.config import settings
from app.database.crud.subscription import (
    create_paid_subscription,
    extend_subscription,
    get_subscription_by_user_id,
)
from app.database.crud.user import get_user_by_telegram_id
from app.database.database import AsyncSessionLocal
from app.database.models import InlineGiftSubscription, SubscriptionStatus
from app.localization.loader import DEFAULT_LANGUAGE
from app.localization.texts import get_texts
from app.services.subscription_service import SubscriptionService
from app.utils.button_emoji import make_button


logger = structlog.get_logger(__name__)

_FOREVER_DAYS = (2099 - 2025) * 365


def _days_label(days: int, texts) -> str:
    if days >= _FOREVER_DAYS:
        return texts.t('INLINE_GIFT_LABEL_FOREVER', 'Навсегда')
    if days % 10 == 1 and days % 100 != 11:
        return texts.t('INLINE_GIFT_DAYS_ONE', '{n} день').format(n=days)
    if days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        return texts.t('INLINE_GIFT_DAYS_FEW', '{n} дня').format(n=days)
    return texts.t('INLINE_GIFT_DAYS_MANY', '{n} дней').format(n=days)


def _fmt_traffic(gb: int, texts) -> str:
    if gb <= 0 or gb == -1:
        return texts.t('INLINE_GIFT_TRAFFIC_UNLIMITED', 'Безлимит')
    return texts.t('INLINE_GIFT_TRAFFIC_GB', '{gb} ГБ').format(gb=gb)


def _build_info_text(
    gift_days,
    gift_traffic_gb,
    gift_devices,
    texts,
    existing_sub=None,
    gift_type: str = 'subscription',
    balance_amount_kopeks: int = 0,
    temp_traffic_gb: int = 0,
    temp_traffic_days: int = 30,
    discount_percent: int = 0,
    reset_traffic: bool = False,
) -> str:
    """Build activation preview. NULL values = no change, not shown."""
    lines = []

    if gift_type == 'balance':
        rub = balance_amount_kopeks // 100
        body = texts.t('INLINE_GIFT_BALANCE_BODY', '+{rub} ₽ на баланс').format(rub=rub)
        return (
            f'{texts.t("INLINE_GIFT_EMOJI_GIFT", "<tg-emoji emoji-id='6032937473162614352'>🎁</tg-emoji>")} '
            f'<b>{texts.t("INLINE_GIFT_BALANCE_HEADER", "Пополнение баланса")}</b>\n\n'
            f'<blockquote>{body}</blockquote>\n\n'
            f'{texts.t("INLINE_GIFT_CONFIRM_PROMPT", "Хотите активировать?")}'
        )

    if gift_type == 'temp_traffic':
        sign = '+' if temp_traffic_gb >= 0 else ''
        body = texts.t('INLINE_GIFT_TEMP_TRAFFIC_BODY', '{sign}{gb} ГБ трафика (на {days} дн.)').format(
            sign=sign, gb=temp_traffic_gb, days=temp_traffic_days or 30
        )
        return (
            f'{texts.t("INLINE_GIFT_EMOJI_GIFT", "<tg-emoji emoji-id='6032937473162614352'>🎁</tg-emoji>")} '
            f'<b>{texts.t("INLINE_GIFT_TITLE", "Подарочная подписка")}</b>\n\n'
            f'<blockquote>{body}</blockquote>\n\n'
            f'{texts.t("INLINE_GIFT_CONFIRM_PROMPT", "Хотите активировать?")}'
        )

    if gift_type == 'reset':
        body = texts.t('INLINE_GIFT_RESET_BODY', 'Сброс использованного трафика')
        return (
            f'{texts.t("INLINE_GIFT_EMOJI_GIFT", "<tg-emoji emoji-id='6032937473162614352'>🎁</tg-emoji>")} '
            f'<b>{texts.t("INLINE_GIFT_TITLE", "Подарочная подписка")}</b>\n\n'
            f'<blockquote>{body}</blockquote>\n\n'
            f'{texts.t("INLINE_GIFT_CONFIRM_PROMPT", "Хотите активировать?")}'
        )

    if gift_days is not None or gift_traffic_gb is not None or gift_devices is not None:
        if existing_sub is None:
            if gift_days is not None:
                lines.append(
                    f'{texts.t("INLINE_GIFT_EMOJI_CALENDAR", "<tg-emoji emoji-id='5967412305338568701'>📅</tg-emoji>")} '
                    f'{texts.t("INLINE_GIFT_LABEL_DURATION", "Срок")}: <b>{_days_label(gift_days, texts)}</b>'
                )
            if gift_traffic_gb is not None:
                lines.append(
                    f'{texts.t("INLINE_GIFT_EMOJI_TRAFFIC", "<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji>")} '
                    f'{texts.t("INLINE_GIFT_LABEL_TRAFFIC", "Трафик")}: <b>{_fmt_traffic(gift_traffic_gb, texts)}</b>'
                )
            if gift_devices is not None:
                lines.append(
                    f'{texts.t("INLINE_GIFT_EMOJI_DEVICES", "<tg-emoji emoji-id='5877318502947229960'>💻</tg-emoji>")} '
                    f'{texts.t("INLINE_GIFT_LABEL_DEVICES", "Устройств")}: <b>{gift_devices}</b>'
                )
        else:
            cur_days = max(0, existing_sub.days_left) if hasattr(existing_sub, 'days_left') else 0
            cur_traffic = existing_sub.traffic_limit_gb or 0
            cur_devices = existing_sub.device_limit or 1

            if gift_days is not None:
                new_days = cur_days + gift_days
                forever = gift_days >= _FOREVER_DAYS or new_days >= _FOREVER_DAYS
                if forever:
                    lines.append(
                        f'{texts.t("INLINE_GIFT_EMOJI_CALENDAR", "<tg-emoji emoji-id='5967412305338568701'>📅</tg-emoji>")} '
                        f'{texts.t("INLINE_GIFT_LABEL_DURATION", "Срок")}: <b>{_days_label(cur_days, texts)}</b> → '
                        f'<b>{texts.t("INLINE_GIFT_LABEL_FOREVER", "Навсегда")}</b>'
                    )
                else:
                    lines.append(
                        f'{texts.t("INLINE_GIFT_EMOJI_CALENDAR", "<tg-emoji emoji-id='5967412305338568701'>📅</tg-emoji>")} '
                        f'{texts.t("INLINE_GIFT_LABEL_DURATION", "Срок")}: <b>{_days_label(cur_days, texts)}</b> '
                        f'+{gift_days}{texts.t("INLINE_GIFT_DAYS_SUFFIX", " дн.")} → <b>{_days_label(new_days, texts)}</b>'
                    )

            if gift_traffic_gb is not None:
                if gift_traffic_gb == -1:
                    if cur_traffic != 0:
                        lines.append(
                            f'{texts.t("INLINE_GIFT_EMOJI_TRAFFIC", "<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji>")} '
                            f'{texts.t("INLINE_GIFT_LABEL_TRAFFIC", "Трафик")}: <b>{_fmt_traffic(cur_traffic, texts)}</b> → '
                            f'<b>{texts.t("INLINE_GIFT_TRAFFIC_UNLIMITED", "Безлимит")}</b>'
                        )
                elif cur_traffic != 0:
                    delta = gift_traffic_gb - cur_traffic
                    sign = '+' if delta >= 0 else ''
                    lines.append(
                        f'{texts.t("INLINE_GIFT_EMOJI_TRAFFIC", "<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji>")} '
                        f'{texts.t("INLINE_GIFT_LABEL_TRAFFIC", "Трафик")}: <b>{_fmt_traffic(cur_traffic, texts)}</b> '
                        f'{sign}{delta} → <b>{_fmt_traffic(gift_traffic_gb, texts)}</b>'
                    )

            if gift_devices is not None:
                new_dev = max(cur_devices, gift_devices)
                if new_dev != cur_devices:
                    lines.append(
                        f'{texts.t("INLINE_GIFT_EMOJI_DEVICES", "<tg-emoji emoji-id='5877318502947229960'>💻</tg-emoji>")} '
                        f'{texts.t("INLINE_GIFT_LABEL_DEVICES", "Устройств")}: <b>{cur_devices}</b> +{new_dev - cur_devices} → <b>{new_dev}</b>'
                    )
                else:
                    lines.append(
                        f'{texts.t("INLINE_GIFT_EMOJI_DEVICES", "<tg-emoji emoji-id='5877318502947229960'>💻</tg-emoji>")} '
                        f'{texts.t("INLINE_GIFT_LABEL_DEVICES", "Устройств")}: <b>{cur_devices}</b>'
                    )

    if gift_type == 'combo':
        if discount_percent:
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_GIFT", "<tg-emoji emoji-id='6032937473162614352'>🎁</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_DISCOUNT", "Скидка")}: <b>{discount_percent}%</b>'
            )
        if balance_amount_kopeks:
            rub = balance_amount_kopeks // 100
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_GIFT", "<tg-emoji emoji-id='6032937473162614352'>🎁</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_BALANCE", "Баланс")}: <b>+{rub} ₽</b>'
            )
        if temp_traffic_gb:
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_TRAFFIC", "<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_TEMP_TRAFFIC", "Временный трафик")}: '
                f'<b>+{temp_traffic_gb} ГБ (на {temp_traffic_days or 30} дн.)</b>'
            )
        if reset_traffic:
            lines.append(
                f'{texts.t("INLINE_GIFT_EMOJI_TRAFFIC", "<tg-emoji emoji-id='5931472654660800739'>📊</tg-emoji>")} '
                f'{texts.t("INLINE_GIFT_LABEL_RESET", "Сброс трафика")}: '
                f'<b>{texts.t("INLINE_GIFT_RESET_BODY", "Сброс использованного трафика")}</b>'
            )

    body = '\n'.join(lines) if lines else '—'
    return (
        f'{texts.t("INLINE_GIFT_EMOJI_GIFT", "<tg-emoji emoji-id='6032937473162614352'>🎁</tg-emoji>")} '
        f'<b>{texts.t("INLINE_GIFT_TITLE", "Подарочная подписка")}</b>\n\n'
        f'<blockquote>{body}</blockquote>\n\n'
        f'{texts.t("INLINE_GIFT_CONFIRM_PROMPT", "Хотите активировать?")}'
    )


async def _reset_panel_traffic(subscription, user) -> None:
    from app.services.remnawave_service import RemnaWaveService

    remnawave_uuid = getattr(subscription, 'remnawave_uuid', None) or getattr(user, 'remnawave_uuid', None)
    if not remnawave_uuid:
        return
    try:
        remnawave_service = RemnaWaveService()
        async with remnawave_service.get_api_client() as api:
            await api.reset_user_traffic(
                remnawave_uuid,
                user_id=subscription.remnawave_id
                if settings.is_multi_tariff_enabled() and subscription
                else user.remnawave_id,
            )
    except Exception as e:
        logger.warning('Не удалось сбросить трафик RemnaWave (подарок)', error=e)


async def _apply_gift_panel_update(db, subscription, user) -> None:
    """Apply the gifted subscription to the panel, finishing grace when open.

    A gift that extends/activates the subscription is a real recovery, so any
    open grace session must be completed as PAID (not left for reconciliation,
    which would otherwise see a mismatched overlay and REVOKE -> disable).
    ``update_panel_user_grace_safe`` routes through
    ``apply_recovered_grace_update_locked`` exactly for this case.
    """
    from app.external.remnawave_api import UserStatus
    from app.services.grace_access_runtime import (
        get_open_grace_overlay,
        update_panel_user_grace_safe,
    )
    from app.services.subscription_service import (
        SubscriptionService as _Svc,
        get_traffic_reset_strategy,
    )
    from app.utils.subscription_utils import resolve_hwid_device_limit_for_payload

    def _as_utc(value):
        from datetime import UTC as _UTC

        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=_UTC)
        return value.astimezone(_UTC)

    service = _Svc()
    remnawave_uuid = getattr(subscription, 'remnawave_uuid', None) or getattr(user, 'remnawave_uuid', None)
    if not remnawave_uuid:
        # Без панельной привязки (уже удалён/не создан) — только локальная выдача.
        logger.warning('Gift panel update skipped: no remnawave identity', subscription_id=subscription.id)
        return

    async with service.get_api_client() as api:
        use_user_id = settings.REMNAWAVE_USE_USER_ID
        if settings.is_multi_tariff_enabled():
            remnawave_id = getattr(subscription, 'remnawave_id', None)
        else:
            remnawave_id = getattr(user, 'remnawave_id', None)
        if use_user_id and remnawave_id is None:
            remnawave_id = await service._resolve_remnawave_id_by_telegram(db, subscription, user)
        update_kwargs = dict(
            uuid=remnawave_uuid,
            status=UserStatus.ACTIVE,
            expire_at=_as_utc(subscription.end_date),
            traffic_limit_bytes=max(0, int(subscription.traffic_limit_gb or 0)) * 1024**3,
            traffic_limit_strategy=get_traffic_reset_strategy(subscription.tariff),
            telegram_id=user.telegram_id,
            email=user.email,
            description=settings.format_remnawave_user_description(
                full_name=user.full_name,
                username=user.username,
                telegram_id=user.telegram_id,
                email=user.email,
                user_id=user.id,
            ),
        )
        if subscription.connected_squads:
            update_kwargs['active_internal_squads'] = subscription.connected_squads
        hwid_limit = resolve_hwid_device_limit_for_payload(subscription)
        if hwid_limit is not None:
            update_kwargs['hwid_device_limit'] = hwid_limit
        if getattr(subscription.tariff, 'external_squad_uuid', None) is not None:
            update_kwargs['external_squad_uuid'] = subscription.tariff.external_squad_uuid
        if remnawave_id is not None:
            update_kwargs['user_id'] = remnawave_id

        try:
            if await get_open_grace_overlay(db, subscription.id) is not None:
                # Открытая grace-сессия: закоммитим локальные изменения (статус
                # ACTIVE + новый end_date), чтобы отдельная guard-сессия в
                # update_panel_user_grace_safe увидела восстановленную подписку
                # и завершила grace как PAID.
                await db.commit()
                updated = await update_panel_user_grace_safe(api, subscription.id, **update_kwargs)
            else:
                updated = await api.update_user(**update_kwargs)
                await db.commit()
        except Exception as exc:
            logger.warning(
                'Gift panel update failed (gift already applied locally)',
                subscription_id=subscription.id,
                error=str(exc),
            )
            updated = None
        if updated is not None:
            subscription.subscription_url = updated.subscription_url
            subscription.subscription_crypto_link = updated.happ_crypto_link


def _check_recipient(gift: InlineGiftSubscription, telegram_id: int, username: str) -> bool:
    if gift.recipient_telegram_id == 0:
        raw = gift.inline_message_id or ''
        if not raw.startswith('u:') and not raw.startswith('tid:'):
            return True
        if raw.startswith('tid:'):
            try:
                return telegram_id == int(raw[4:])
            except ValueError:
                return False
        if raw.startswith('u:'):
            return username.lower() == raw[2:].lower()
        return True
    result = telegram_id == gift.recipient_telegram_id
    if not result:
        logger.warning(
            'RECIPIENT MISMATCH',
            gift_code=gift.gift_code,
            recipient_telegram_id=gift.recipient_telegram_id,
            caller_telegram_id=telegram_id,
            caller_username=username,
            inline_message_id=gift.inline_message_id,
        )
    return result


async def handle_gift_deeplink(message: types.Message, gift_code: str, state=None) -> bool:
    telegram_id = message.from_user.id
    username = (message.from_user.username or '').lower()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code)
        )
        gift = result.scalars().first()

        if not gift:
            return False

        user = await get_user_by_telegram_id(session, telegram_id)
        language = user.language if user else DEFAULT_LANGUAGE
        texts = get_texts(language)

        max_act = getattr(gift, 'max_activations', 1) or 1
        activated = getattr(gift, 'activated_count', 0) or 0

        if gift.is_activated or activated >= max_act:
            await message.answer(texts.t('INLINE_GIFT_ALREADY_ACTIVATED', 'Этот подарок уже был активирован.'))
            return True

        # Unregistered user — save gift code and let start.py run registration first
        if not user:
            if state:
                await state.update_data(pending_inline_gift_code=gift_code)
            return False

        if not _check_recipient(gift, telegram_id, username):
            await message.answer(
                texts.t(
                    'INLINE_GIFT_WRONG_RECIPIENT',
                    'Этот подарок предназначен другому пользователю.',
                )
            )
            return True

        if gift.recipient_telegram_id == 0 and max_act == 1:
            gift.recipient_telegram_id = telegram_id
            await session.commit()
            await session.refresh(gift)

        existing_sub = await get_subscription_by_user_id(session, user.id)

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
                    callback_data=f'igift_activate:{gift_code}',
                ),
                types.InlineKeyboardButton(
                    text=texts.t('INLINE_GIFT_CANCEL_BUTTON', 'Отменить'),
                    callback_data=f'igift_cancel:{gift_code}',
                ),
            ]
        ]
    )

    temp_gb = gift.temp_traffic_gb
    if temp_gb is None and gift.gift_type == 'temp_traffic':
        temp_gb = gift.traffic_limit_gb
    text_out = _build_info_text(
        gift.days,
        gift.traffic_limit_gb,
        gift.device_limit,
        texts,
        existing_sub,
        gift_type=gift.gift_type or 'subscription',
        balance_amount_kopeks=gift.balance_amount_kopeks or 0,
        temp_traffic_gb=temp_gb or 0,
        temp_traffic_days=gift.temp_traffic_days or 30,
        discount_percent=gift.discount_percent or 0,
        reset_traffic=bool(gift.reset_traffic),
    )
    await message.answer(text_out, reply_markup=keyboard, parse_mode='HTML')
    return True


async def handle_activate_callback(callback: types.CallbackQuery) -> None:
    if isinstance(callback.message, InaccessibleMessage):
        texts = get_texts(DEFAULT_LANGUAGE)
        await callback.answer(
            texts.t('INLINE_GIFT_EXPIRED_MESSAGE', 'Сообщение устарело.'),
            show_alert=True,
        )
        return

    gift_code = callback.data.split(':', 1)[1]
    telegram_id = callback.from_user.id
    username = (callback.from_user.username or '').lower()

    await callback.answer()

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code))
        gift = result.scalars().first()

        user = await get_user_by_telegram_id(db, telegram_id)
        language = user.language if user else DEFAULT_LANGUAGE
        texts = get_texts(language)

        if not gift:
            await callback.message.edit_text(texts.t('INLINE_GIFT_NOT_FOUND', 'Подарок не найден.'))
            return

        max_act = getattr(gift, 'max_activations', 1) or 1
        activated = getattr(gift, 'activated_count', 0) or 0

        if gift.is_activated or activated >= max_act:
            await callback.message.edit_text(
                texts.t('INLINE_GIFT_ALREADY_ACTIVATED', 'Этот подарок уже был активирован.')
            )
            return

        if not _check_recipient(gift, telegram_id, username):
            await callback.message.edit_text(
                texts.t(
                    'INLINE_GIFT_WRONG_RECIPIENT',
                    'Этот подарок предназначен другому пользователю.',
                ),
                parse_mode='HTML',
            )
            return

        if not user:
            await callback.message.edit_text(
                texts.t(
                    'INLINE_GIFT_NOT_REGISTERED',
                    'Вы не зарегистрированы. Напишите /start.',
                )
            )
            return

        await callback.message.edit_text(texts.t('INLINE_GIFT_ACTIVATING', '⏳ Активируем подарок...'))

        gift_type = getattr(gift, 'gift_type', 'subscription') or 'subscription'

        try:
            if gift_type == 'discount':
                import secrets as _secrets

                from app.database.crud.promocode import create_promocode
                from app.database.models import PromoCodeType

                pct = gift.discount_percent or 0
                promo_code_str = _secrets.token_hex(4).upper()
                await create_promocode(
                    db,
                    code=promo_code_str,
                    type=PromoCodeType.DISCOUNT,
                    balance_bonus_kopeks=pct,
                    max_uses=1,
                    created_by=gift.sender_user_id,
                )
                gift.activated_count = activated + 1
                gift.is_activated = True
                gift.activated_at = datetime.now(UTC)
                gift.activated_by_user_id = user.id
                inline_msg_id = gift.inline_message_id
                await db.commit()

                success_text = texts.t(
                    'INLINE_GIFT_DISCOUNT_SUCCESS',
                    '<b>Скидка активирована!</b>\n\n<blockquote>Скидка {pct}% — промокод: <code>{code}</code></blockquote>',
                ).format(pct=pct, code=promo_code_str)
                back_kb = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            make_button(
                                text=texts.t('MAIN_MENU_BUTTON', '← Главное меню'),
                                callback_data='back_to_menu',
                             style='danger')
                        ]
                    ]
                )
                await callback.message.edit_text(success_text, parse_mode='HTML', reply_markup=back_kb)
                await _update_inline_button(
                    callback.bot,
                    inline_msg_id,
                    texts.t(
                        'INLINE_GIFT_ACTIVATED_BUTTON',
                        'Активировано',
                    ),
                    0,
                    1,
                )
                return

            if gift_type == 'balance':
                from app.database.crud.user import add_user_balance

                kopeks = gift.balance_amount_kopeks or 0
                rub = kopeks // 100
                await add_user_balance(db, user, kopeks, description='Подарок от администратора')
                gift.activated_count = activated + 1
                gift.is_activated = True
                gift.activated_at = datetime.now(UTC)
                gift.activated_by_user_id = user.id
                inline_msg_id = gift.inline_message_id
                await db.commit()

                success_text = texts.t(
                    'INLINE_GIFT_BALANCE_SUCCESS',
                    '<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>Баланс пополнен!</b>\n\n<blockquote>+{rub} ₽ добавлено на ваш счёт</blockquote>',
                ).format(rub=rub)
                back_kb = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            make_button(
                                text=texts.t('MAIN_MENU_BUTTON', '← Главное меню'),
                                callback_data='back_to_menu',
                             style='danger')
                        ]
                    ]
                )
                await callback.message.edit_text(success_text, parse_mode='HTML', reply_markup=back_kb)
                await _update_inline_button(
                    callback.bot,
                    inline_msg_id,
                    texts.t(
                        'INLINE_GIFT_ACTIVATED_BUTTON',
                        'Активировано',
                    ),
                    0,
                    1,
                )
                return

            if gift_type == 'temp_traffic':
                from app.database.crud.subscription import _lock_subscription_row
                from app.database.models import TrafficPurchase

                gb = gift.temp_traffic_gb or 0
                if not gb:
                    gb = gift.traffic_limit_gb or 0
                temp_days = gift.temp_traffic_days or 30
                existing_sub = await get_subscription_by_user_id(db, user.id)

                if not existing_sub:
                    await callback.message.edit_text(
                        texts.t(
                            'INLINE_GIFT_NO_SUBSCRIPTION',
                            'У вас нет активной подписки для добавления трафика.',
                        )
                    )
                    return

                await _lock_subscription_row(db, existing_sub)

                new_expires_at = datetime.now(UTC) + timedelta(days=temp_days)
                new_purchase = TrafficPurchase(
                    subscription_id=existing_sub.id,
                    traffic_gb=gb,
                    expires_at=new_expires_at,
                )
                db.add(new_purchase)

                current_purchased = getattr(existing_sub, 'purchased_traffic_gb', 0) or 0
                existing_sub.purchased_traffic_gb = current_purchased + gb
                existing_sub.updated_at = datetime.now(UTC)

                gift.activated_count = activated + 1
                gift.is_activated = True
                gift.activated_at = datetime.now(UTC)
                gift.activated_by_user_id = user.id
                inline_msg_id = gift.inline_message_id
                await db.commit()

                sign = '+' if gb >= 0 else ''
                success_text = texts.t(
                    'INLINE_GIFT_TEMP_TRAFFIC_SUCCESS',
                    '<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>Временный трафик активирован!</b>\n\n<blockquote>{sign}{gb} ГБ трафика (на {days} дней)</blockquote>',
                ).format(sign=sign, gb=gb, days=temp_days)
                back_kb = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            make_button(
                                text=texts.t('MAIN_MENU_BUTTON', '← Главное меню'),
                                callback_data='back_to_menu',
                             style='danger')
                        ]
                    ]
                )
                await callback.message.edit_text(success_text, parse_mode='HTML', reply_markup=back_kb)
                await _update_inline_button(
                    callback.bot,
                    inline_msg_id,
                    texts.t(
                        'INLINE_GIFT_ACTIVATED_BUTTON',
                        'Активировано',
                    ),
                    0,
                    1,
                )
                return

            if gift_type == 'reset':
                existing_sub = await get_subscription_by_user_id(db, user.id)
                if not existing_sub:
                    await callback.message.edit_text(
                        texts.t(
                            'INLINE_GIFT_NO_SUBSCRIPTION',
                            'У вас нет активной подписки для сброса трафика.',
                        )
                    )
                    return

                existing_sub.traffic_used_gb = 0.0
                existing_sub.updated_at = datetime.now(UTC)

                gift.activated_count = activated + 1
                gift.is_activated = True
                gift.activated_at = datetime.now(UTC)
                gift.activated_by_user_id = user.id
                inline_msg_id = gift.inline_message_id
                await db.commit()

                await _reset_panel_traffic(existing_sub, user)

                success_text = texts.t(
                    'INLINE_GIFT_RESET_SUCCESS',
                    '<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>Трафик сброшен!</b>\n\n<blockquote>Использованный трафик обнулён</blockquote>',
                )
                back_kb = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            make_button(
                                text=texts.t('MAIN_MENU_BUTTON', '← Главное меню'),
                                callback_data='back_to_menu',
                             style='danger')
                        ]
                    ]
                )
                await callback.message.edit_text(success_text, parse_mode='HTML', reply_markup=back_kb)
                await _update_inline_button(
                    callback.bot,
                    inline_msg_id,
                    texts.t(
                        'INLINE_GIFT_ACTIVATED_BUTTON',
                        'Активировано',
                    ),
                    0,
                    1,
                )
                return

            if gift_type == 'combo':
                import secrets as _secrets

                from app.database.crud.promocode import create_promocode
                from app.database.crud.server_squad import get_available_server_squads
                from app.database.crud.subscription import _lock_subscription_row
                from app.database.crud.user import add_user_balance
                from app.database.models import PromoCodeType, TrafficPurchase

                buffer: list[str] = []
                inline_msg_id = gift.inline_message_id
                subscription = None

                # --- subscription component ---
                has_sub = gift.days is not None or gift.traffic_limit_gb is not None or gift.device_limit is not None
                if has_sub:
                    available = await get_available_server_squads(db)
                    squads: list[str] = [available[0].squad_uuid] if available else []
                    existing_sub = await get_subscription_by_user_id(db, user.id)
                    subscription_service = SubscriptionService()
                    gift_days = gift.days
                    gift_traffic = gift.traffic_limit_gb
                    gift_devices = gift.device_limit

                    if existing_sub:
                        if gift_traffic is None:
                            new_traffic = None
                        elif gift_traffic == -1:
                            new_traffic = 0
                        elif gift_traffic != existing_sub.traffic_limit_gb:
                            new_traffic = gift_traffic
                        else:
                            new_traffic = None

                        if gift_devices is None:
                            new_devices = None
                        else:
                            cur_dev = existing_sub.device_limit or 1
                            new_devices = max(cur_dev, gift_devices)
                            if new_devices == cur_dev:
                                new_devices = None

                        if gift_days is not None and gift_days >= _FOREVER_DAYS:
                            existing_sub.end_date = datetime.now(UTC).replace(year=2099)
                            subscription = await extend_subscription(
                                db=db,
                                subscription=existing_sub,
                                days=0,
                                traffic_limit_gb=new_traffic,
                                device_limit=new_devices,
                                connected_squads=squads if squads else None,
                                commit=False,
                            )
                        else:
                            subscription = await extend_subscription(
                                db=db,
                                subscription=existing_sub,
                                days=gift_days or 0,
                                traffic_limit_gb=new_traffic,
                                device_limit=new_devices,
                                connected_squads=squads if squads else None,
                                commit=False,
                            )
                        # Подарок — реальная выдача/продление: выводим из grace.
                        if subscription.status != SubscriptionStatus.ACTIVE.value:
                            subscription.status = SubscriptionStatus.ACTIVE.value
                            subscription.updated_at = datetime.now(UTC)
                        await _apply_gift_panel_update(db, subscription, user)
                    else:
                        duration_days = gift_days or 0
                        forever_end_date = None
                        if duration_days >= _FOREVER_DAYS:
                            forever_end_date = datetime.now(UTC).replace(year=2099)
                            duration_days = 1
                        traffic_for_new = (
                            0
                            if gift_traffic == -1
                            else (settings.DEFAULT_TRAFFIC_LIMIT_GB if gift_traffic is None else gift_traffic)
                        )
                        devices_for_new = gift_devices if gift_devices is not None else settings.DEFAULT_DEVICE_LIMIT
                        subscription = await create_paid_subscription(
                            db=db,
                            user_id=user.id,
                            duration_days=duration_days,
                            traffic_limit_gb=traffic_for_new,
                            device_limit=devices_for_new,
                            connected_squads=squads,
                            update_server_counters=True,
                        )
                        if forever_end_date:
                            subscription.end_date = forever_end_date

                    sub_parts = []
                    if gift_days is not None:
                        if gift_days >= _FOREVER_DAYS:
                            sub_parts.append(texts.t('INLINE_GIFT_LABEL_FOREVER', 'Навсегда'))
                        else:
                            sub_parts.append(f'+{_days_label(gift_days, texts)}')
                    if gift_traffic is not None:
                        sub_parts.append(_fmt_traffic(gift_traffic, texts))
                    if gift_devices is not None:
                        sub_parts.append(f'{gift_devices} уст.')
                    if sub_parts:
                        buffer.append(
                            texts.t(
                                'INLINE_GIFT_COMBO_SUB',
                                'подписка: {changes}',
                            ).format(changes=', '.join(sub_parts))
                        )

                # --- discount component ---
                if gift.discount_percent:
                    pct = gift.discount_percent
                    promo_code_str = _secrets.token_hex(4).upper()
                    await create_promocode(
                        db,
                        code=promo_code_str,
                        type=PromoCodeType.DISCOUNT,
                        balance_bonus_kopeks=pct,
                        max_uses=1,
                        created_by=gift.sender_user_id,
                    )
                    buffer.append(
                        texts.t(
                            'INLINE_GIFT_COMBO_DISCOUNT',
                            'скидка {pct}% — промокод <code>{code}</code>',
                        ).format(pct=pct, code=promo_code_str)
                    )

                # --- balance component ---
                if gift.balance_amount_kopeks:
                    kopeks = gift.balance_amount_kopeks
                    await add_user_balance(db, user, kopeks, description='Подарок от администратора')
                    buffer.append(texts.t('INLINE_GIFT_COMBO_BALANCE', '+{rub} ₽ на баланс').format(rub=kopeks // 100))

                # --- temp-traffic component ---
                if gift.temp_traffic_gb:
                    gb = gift.temp_traffic_gb
                    coincident_sub = await get_subscription_by_user_id(db, user.id)
                    if not coincident_sub:
                        await callback.message.edit_text(
                            texts.t(
                                'INLINE_GIFT_NO_SUBSCRIPTION',
                                'У вас нет активной подписки для добавления трафика.',
                            )
                        )
                        return
                    await _lock_subscription_row(db, coincident_sub)
                    temp_days = gift.temp_traffic_days or 30
                    new_expires_at = datetime.now(UTC) + timedelta(days=temp_days)
                    new_purchase = TrafficPurchase(
                        subscription_id=coincident_sub.id,
                        traffic_gb=gb,
                        expires_at=new_expires_at,
                    )
                    db.add(new_purchase)
                    coincident_sub.purchased_traffic_gb = (getattr(coincident_sub, 'purchased_traffic_gb', 0) or 0) + gb
                    coincident_sub.updated_at = datetime.now(UTC)
                    buffer.append(
                        texts.t(
                            'INLINE_GIFT_COMBO_TEMP',
                            '+{gb} ГБ трафика (на {days} дн.)',
                        ).format(gb=gb, days=temp_days)
                    )

                reset_sub = None
                # --- reset-traffic component ---
                if gift.reset_traffic:
                    reset_sub = subscription
                    if reset_sub is None:
                        reset_sub = await get_subscription_by_user_id(db, user.id)
                    if not reset_sub:
                        await callback.message.edit_text(
                            texts.t(
                                'INLINE_GIFT_NO_SUBSCRIPTION',
                                'У вас нет активной подписки для сброса трафика.',
                            )
                        )
                        return
                    reset_sub.traffic_used_gb = 0.0
                    reset_sub.updated_at = datetime.now(UTC)
                    buffer.append(
                        texts.t(
                            'INLINE_GIFT_COMBO_RESET',
                            'сброс трафика',
                        )
                    )

                new_activated = activated + 1
                gift.activated_count = new_activated
                if new_activated >= max_act:
                    gift.is_activated = True
                gift.activated_at = datetime.now(UTC)
                gift.activated_by_user_id = user.id
                if subscription is not None:
                    gift.subscription_id = subscription.id
                    await db.refresh(subscription)
                await db.commit()

                if reset_sub is not None:
                    await _reset_panel_traffic(reset_sub, user)

                changes = ', '.join(buffer)
                success_text = texts.t(
                    'INLINE_GIFT_SUCCESS_CHANGES',
                    '<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>Подарок активирован!</b>\n\n<blockquote>{changes}</blockquote>',
                ).format(changes=changes)
                back_kb = types.InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            make_button(
                                text=texts.t('MAIN_MENU_BUTTON', '← Главное меню'),
                                callback_data='back_to_menu',
                             style='danger')
                        ]
                    ]
                )
                await callback.message.edit_text(success_text, parse_mode='HTML', reply_markup=back_kb)
                fully_used = new_activated >= max_act
                button_text = (
                    texts.t('INLINE_GIFT_ACTIVATED_BUTTON', 'Активировано')
                    if fully_used
                    else texts.t('INLINE_GIFT_ACTIVATE_BUTTON_N', 'Активировать (осталось: {n})').format(
                        n=max_act - new_activated
                    )
                )
                await _update_inline_button(
                    callback.bot, inline_msg_id, button_text, max_act - new_activated, max_act, gift_code=gift_code
                )
                return

            # Subscription
            from app.database.crud.server_squad import get_available_server_squads

            available = await get_available_server_squads(db)
            squads: list[str] = [available[0].squad_uuid] if available else []

            existing_sub = await get_subscription_by_user_id(db, user.id)
            subscription_service = SubscriptionService()

            gift_days = gift.days  # None = no change
            gift_traffic = gift.traffic_limit_gb  # None = no change, -1 = unlimited, N = set N
            gift_devices = gift.device_limit  # None = no change

            if existing_sub:
                # traffic
                if gift_traffic is None:
                    new_traffic = None
                elif gift_traffic == -1:
                    new_traffic = 0
                elif gift_traffic != existing_sub.traffic_limit_gb:
                    new_traffic = gift_traffic
                else:
                    new_traffic = None

                # devices
                if gift_devices is None:
                    new_devices = None
                else:
                    cur_dev = existing_sub.device_limit or 1
                    new_devices = max(cur_dev, gift_devices)
                    if new_devices == cur_dev:
                        new_devices = None

                # days
                if gift_days is not None and gift_days >= _FOREVER_DAYS:
                    existing_sub.end_date = datetime.now(UTC).replace(year=2099)
                    subscription = await extend_subscription(
                        db=db,
                        subscription=existing_sub,
                        days=0,
                        traffic_limit_gb=new_traffic,
                        device_limit=new_devices,
                        connected_squads=squads if squads else None,
                        commit=False,
                    )
                else:
                    subscription = await extend_subscription(
                        db=db,
                        subscription=existing_sub,
                        days=gift_days or 0,
                        traffic_limit_gb=new_traffic,
                        device_limit=new_devices,
                        connected_squads=squads if squads else None,
                        commit=False,
                    )
                # Подарок — это реальная выдача/продление подписки: переводим в
                # активную, чтобы она вышла из grace. `extend_subscription` меняет
                # статус на ACTIVE только при days > 0; подарок «Навсегда» идёт
                # с days=0, поэтому статус фиксируем здесь явно.
                if subscription.status != SubscriptionStatus.ACTIVE.value:
                    subscription.status = SubscriptionStatus.ACTIVE.value
                    subscription.updated_at = datetime.now(UTC)
                await _apply_gift_panel_update(db, subscription, user)
            else:
                duration_days = gift_days or 0
                forever_end_date = None
                if duration_days >= _FOREVER_DAYS:
                    forever_end_date = datetime.now(UTC).replace(year=2099)
                    duration_days = 1

                traffic_for_new = (
                    0
                    if gift_traffic == -1
                    else (settings.DEFAULT_TRAFFIC_LIMIT_GB if gift_traffic is None else gift_traffic)
                )
                devices_for_new = gift_devices if gift_devices is not None else settings.DEFAULT_DEVICE_LIMIT

                subscription = await create_paid_subscription(
                    db=db,
                    user_id=user.id,
                    duration_days=duration_days,
                    traffic_limit_gb=traffic_for_new,
                    device_limit=devices_for_new,
                    connected_squads=squads,
                    update_server_counters=True,
                )
                if forever_end_date:
                    subscription.end_date = forever_end_date
                await subscription_service.create_remnawave_user(db, subscription)

            await db.refresh(subscription)

            new_activated = activated + 1
            remaining = max_act - new_activated
            gift.activated_count = new_activated
            if new_activated >= max_act:
                gift.is_activated = True
            gift.activated_at = datetime.now(UTC)
            gift.activated_by_user_id = user.id
            gift.subscription_id = subscription.id
            inline_msg_id = gift.inline_message_id
            await db.commit()

        except Exception as exc:
            logger.exception('Failed to activate inline gift', gift_code=gift_code, error=str(exc))
            await callback.message.edit_text(
                texts.t(
                    'INLINE_GIFT_ACTIVATION_ERROR',
                    'Произошла ошибка. Попробуйте позже.',
                )
            )
            return

        parts = []
        if gift_days is not None:
            parts.append(f'+{_days_label(gift_days, texts)}')
        if gift_traffic is not None:
            parts.append(_fmt_traffic(gift_traffic, texts))
        if gift_devices is not None:
            parts.append(f'{gift_devices} уст.')

        changes = ', '.join(parts)
        if changes:
            success_text = texts.t(
                'INLINE_GIFT_SUCCESS_CHANGES',
                '<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>Подарок активирован!</b>\n\n<blockquote>{changes}</blockquote>',
            ).format(changes=changes)
        else:
            success_text = texts.t(
                'INLINE_GIFT_SUCCESS',
                '<tg-emoji emoji-id="5825794181183836432">✔️</tg-emoji> <b>Подарок активирован!</b>\n\nПодписка обновлена.',
            )

        back_kb = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    make_button(
                        text=texts.t('MAIN_MENU_BUTTON', '← Главное меню'),
                        callback_data='back_to_menu',
                     style='danger')
                ]
            ]
        )
        await callback.message.edit_text(success_text, parse_mode='HTML', reply_markup=back_kb)

        fully_used = new_activated >= max_act
        button_text = (
            texts.t('INLINE_GIFT_ACTIVATED_BUTTON', 'Активировано')
            if fully_used
            else texts.t('INLINE_GIFT_ACTIVATE_BUTTON_N', 'Активировать (осталось: {n})').format(n=remaining)
        )
        await _update_inline_button(
            callback.bot,
            inline_msg_id,
            button_text,
            remaining,
            max_act,
            gift_code=gift_code,
        )


async def _update_inline_button(
    bot: Bot,
    inline_msg_id: str | None,
    text: str,
    remaining: int,
    max_act: int,
    gift_code: str = '',
) -> None:
    raw = inline_msg_id or ''
    if not raw or raw.startswith('u:') or raw.startswith('tid:'):
        return
    try:
        if remaining > 0 and gift_code:
            bot_username = settings.BOT_USERNAME or ''
            deep_link = f'https://t.me/{bot_username}?start=bs_{gift_code}'
            kb = types.InlineKeyboardMarkup(inline_keyboard=[[make_button(text=text, url=deep_link)]])
        else:
            kb = types.InlineKeyboardMarkup(inline_keyboard=[[make_button(text=text, callback_data='igift_noop')]])
        await bot.edit_message_reply_markup(inline_message_id=raw, reply_markup=kb)
    except Exception as e:
        logger.warning('Could not update inline message button', error=str(e))


async def handle_cancel_callback(callback: types.CallbackQuery) -> None:
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return
    await callback.answer()
    texts = get_texts(DEFAULT_LANGUAGE)
    gift_code = callback.data.split(':', 1)[1]

    inline_msg_id = None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code))
        gift = result.scalars().first()
        if gift:
            inline_msg_id = gift.inline_message_id

    await callback.message.edit_text(texts.t('INLINE_GIFT_CANCELLED', 'Активация отменена.'))
    await _update_inline_button(
        callback.bot,
        inline_msg_id,
        texts.t('INLINE_GIFT_CANCELLED_BUTTON', 'Отменено'),
        0,
        1,
    )


async def handle_noop_callback(callback: types.CallbackQuery) -> None:
    texts = get_texts(DEFAULT_LANGUAGE)
    await callback.answer(
        texts.t('INLINE_GIFT_ALREADY_ACTIVATED_ALERT', 'Подарок уже активирован.'),
        show_alert=True,
    )


async def show_pending_inline_gift(
    message: types.Message,
    gift_code: str,
    telegram_id: int | None = None,
    username: str | None = None,
) -> None:
    """Called after new user registration when they arrived via bs_ link."""
    if telegram_id is None:
        telegram_id = message.from_user.id
        username = (message.from_user.username or '').lower()
    else:
        username = (username or '').lower()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(InlineGiftSubscription).where(InlineGiftSubscription.gift_code == gift_code)
        )
        gift = result.scalars().first()

        if not gift:
            return

        user = await get_user_by_telegram_id(session, telegram_id)
        language = user.language if user else DEFAULT_LANGUAGE
        texts = get_texts(language)

        max_act = getattr(gift, 'max_activations', 1) or 1
        activated = getattr(gift, 'activated_count', 0) or 0

        if gift.is_activated or activated >= max_act:
            await message.answer(texts.t('INLINE_GIFT_ALREADY_ACTIVATED', 'Этот подарок уже был активирован.'))
            return

        if not _check_recipient(gift, telegram_id, username):
            await message.answer(
                texts.t(
                    'INLINE_GIFT_WRONG_RECIPIENT',
                    'Этот подарок предназначен другому пользователю.',
                )
            )
            return

        if gift.recipient_telegram_id == 0 and max_act == 1:
            gift.recipient_telegram_id = telegram_id
            await session.commit()
            await session.refresh(gift)

        existing_sub = await get_subscription_by_user_id(session, user.id) if user else None

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text=texts.t('INLINE_GIFT_ACTIVATE_BUTTON', 'Активировать'),
                    callback_data=f'igift_activate:{gift_code}',
                ),
                types.InlineKeyboardButton(
                    text=texts.t('INLINE_GIFT_CANCEL_BUTTON', 'Отменить'),
                    callback_data=f'igift_cancel:{gift_code}',
                ),
            ]
        ]
    )

    temp_gb = gift.temp_traffic_gb
    if temp_gb is None and gift.gift_type == 'temp_traffic':
        temp_gb = gift.traffic_limit_gb
    text_out = _build_info_text(
        gift.days,
        gift.traffic_limit_gb,
        gift.device_limit,
        texts,
        existing_sub,
        gift_type=gift.gift_type or 'subscription',
        balance_amount_kopeks=gift.balance_amount_kopeks or 0,
        temp_traffic_gb=temp_gb or 0,
        temp_traffic_days=gift.temp_traffic_days or 30,
        discount_percent=gift.discount_percent or 0,
        reset_traffic=bool(gift.reset_traffic),
    )
    await message.answer(text_out, reply_markup=keyboard, parse_mode='HTML')


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(handle_activate_callback, F.data.startswith('igift_activate:'))
    dp.callback_query.register(handle_cancel_callback, F.data.startswith('igift_cancel:'))
    dp.callback_query.register(handle_noop_callback, F.data == 'igift_noop')
