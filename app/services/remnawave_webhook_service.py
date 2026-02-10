"""
Service for processing incoming RemnaWave backend webhooks.

Handles user-scope events: subscription expiration, enable/disable,
traffic limits, expiration warnings, and more.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.subscription import (
    deactivate_subscription,
    expire_subscription,
    get_subscription_by_user_id,
    reactivate_subscription,
    update_subscription_usage,
)
from app.database.crud.user import get_user_by_remnawave_uuid, get_user_by_telegram_id
from app.database.models import Subscription, SubscriptionStatus, User
from app.localization.texts import get_texts
from app.utils.miniapp_buttons import build_miniapp_or_callback_button


logger = logging.getLogger(__name__)


class RemnaWaveWebhookService:
    """Processes incoming webhooks from RemnaWave backend."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._handlers: dict[str, Any] = {
            'user.expired': self._handle_user_expired,
            'user.disabled': self._handle_user_disabled,
            'user.enabled': self._handle_user_enabled,
            'user.limited': self._handle_user_limited,
            'user.traffic_reset': self._handle_user_traffic_reset,
            'user.modified': self._handle_user_modified,
            'user.deleted': self._handle_user_deleted,
            'user.revoked': self._handle_user_revoked,
            'user.created': self._handle_user_created,
            'user.expires_in_72_hours': self._handle_expires_in_72h,
            'user.expires_in_48_hours': self._handle_expires_in_48h,
            'user.expires_in_24_hours': self._handle_expires_in_24h,
            'user.expired_24_hours_ago': self._handle_expired_24h_ago,
            'user.first_connected': self._handle_first_connected,
            'user.bandwidth_usage_threshold_reached': self._handle_bandwidth_threshold,
        }

    async def process_event(self, db: AsyncSession, event_name: str, data: dict) -> bool:
        """Route event to the appropriate handler.

        Returns True if the event was processed, False if skipped/unknown.
        """
        handler = self._handlers.get(event_name)
        if not handler:
            logger.debug('Unhandled RemnaWave webhook event: %s', event_name)
            return False

        user, subscription = await self._resolve_user_and_subscription(db, data)
        if not user:
            logger.warning(
                'RemnaWave webhook: user not found for event %s, data telegramId=%s uuid=%s',
                event_name,
                data.get('telegramId'),
                data.get('uuid'),
            )
            return False

        try:
            await handler(db, user, subscription, data)
            return True
        except Exception:
            logger.exception('Error processing RemnaWave webhook event %s for user %s', event_name, user.id)
            try:
                await db.rollback()
            except Exception:
                logger.debug('Rollback after webhook handler error also failed')
            return False

    # ------------------------------------------------------------------
    # User resolution
    # ------------------------------------------------------------------

    async def _resolve_user_and_subscription(
        self, db: AsyncSession, data: dict
    ) -> tuple[User | None, Subscription | None]:
        """Find bot user by telegramId or uuid from webhook payload."""
        user: User | None = None

        telegram_id = data.get('telegramId')
        if telegram_id:
            try:
                user = await get_user_by_telegram_id(db, int(telegram_id))
            except (ValueError, TypeError):
                pass

        if not user:
            uuid = data.get('uuid')
            if uuid:
                user = await get_user_by_remnawave_uuid(db, uuid)

        if not user:
            return None, None

        subscription = await get_subscription_by_user_id(db, user.id)
        return user, subscription

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid_url(value: str) -> bool:
        """Basic URL validation to prevent stored XSS via crafted URLs."""
        if not value or len(value) > 2048:
            return False
        return bool(re.match(r'^https?://', value))

    def _get_renew_keyboard(self, user: User) -> InlineKeyboardMarkup:
        texts = get_texts(user.language)
        button_text = texts.get('WEBHOOK_RENEW_BUTTON', 'Renew subscription')
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [build_miniapp_or_callback_button(text=button_text, callback_data='subscription_extend')],
            ]
        )

    async def _notify_user(
        self,
        user: User,
        text_key: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        format_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Send a notification to user via Telegram."""
        if not user.telegram_id:
            return

        texts = get_texts(user.language)
        message = texts.get(text_key)
        if not message:
            logger.warning('Missing locale key %s for language %s', text_key, user.language)
            return

        if format_kwargs:
            try:
                message = message.format(**format_kwargs)
            except (KeyError, IndexError):
                logger.warning('Failed to format message %s with kwargs %s', text_key, format_kwargs)
                return

        try:
            await self.bot.send_message(
                chat_id=user.telegram_id,
                text=message,
                reply_markup=reply_markup,
                parse_mode='HTML',
            )
        except TelegramForbiddenError:
            logger.warning('User %s blocked the bot, cannot send webhook notification', user.telegram_id)
        except TelegramBadRequest as exc:
            logger.warning('Failed to send webhook notification to %s: %s', user.telegram_id, exc)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def _handle_user_expired(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription and subscription.status != SubscriptionStatus.EXPIRED.value:
            await expire_subscription(db, subscription)
            logger.info('Webhook: subscription %s expired for user %s', subscription.id, user.id)

        await self._notify_user(user, 'WEBHOOK_SUB_EXPIRED', reply_markup=self._get_renew_keyboard(user))

    async def _handle_user_disabled(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription and subscription.status != SubscriptionStatus.DISABLED.value:
            await deactivate_subscription(db, subscription)
            logger.info('Webhook: subscription %s disabled for user %s', subscription.id, user.id)

        await self._notify_user(user, 'WEBHOOK_SUB_DISABLED')

    async def _handle_user_enabled(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription and subscription.status == SubscriptionStatus.DISABLED.value:
            await reactivate_subscription(db, subscription)
            logger.info('Webhook: subscription %s re-enabled for user %s', subscription.id, user.id)

        await self._notify_user(user, 'WEBHOOK_SUB_ENABLED')

    async def _handle_user_limited(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription and subscription.status == SubscriptionStatus.ACTIVE.value:
            await deactivate_subscription(db, subscription)
            logger.info('Webhook: subscription %s limited (traffic) for user %s', subscription.id, user.id)

        await self._notify_user(user, 'WEBHOOK_SUB_LIMITED')

    async def _handle_user_traffic_reset(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription:
            await update_subscription_usage(db, subscription, 0.0)
            # Re-enable if was disabled due to traffic limit
            if subscription.status == SubscriptionStatus.DISABLED.value:
                await reactivate_subscription(db, subscription)
            logger.info('Webhook: traffic reset for subscription %s, user %s', subscription.id, user.id)

        await self._notify_user(user, 'WEBHOOK_SUB_TRAFFIC_RESET')

    async def _handle_user_modified(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        """Sync subscription fields from webhook payload without notifying user."""
        if not subscription:
            return

        changed = False

        # Sync traffic limit
        traffic_limit_bytes = data.get('trafficLimitBytes')
        if traffic_limit_bytes is not None:
            try:
                new_limit_gb = int(traffic_limit_bytes) // (1024**3)
                if subscription.traffic_limit_gb != new_limit_gb:
                    subscription.traffic_limit_gb = new_limit_gb
                    changed = True
            except (ValueError, TypeError):
                pass

        # Sync used traffic
        used_traffic_bytes = data.get('usedTrafficBytes')
        if used_traffic_bytes is not None:
            try:
                new_used_gb = round(int(used_traffic_bytes) / (1024**3), 2)
                subscription.traffic_used_gb = new_used_gb
                changed = True
            except (ValueError, TypeError):
                pass

        # Sync expire date
        expire_at = data.get('expireAt')
        if expire_at:
            try:
                parsed_dt = datetime.fromisoformat(expire_at.replace('Z', '+00:00'))
                new_end_date = parsed_dt.astimezone(UTC).replace(tzinfo=None)
                if subscription.end_date != new_end_date:
                    subscription.end_date = new_end_date
                    changed = True
            except (ValueError, TypeError):
                pass

        # Sync subscription URL (validate to prevent stored XSS)
        subscription_url = data.get('subscriptionUrl')
        if (
            subscription_url
            and self._is_valid_url(subscription_url)
            and subscription.subscription_url != subscription_url
        ):
            subscription.subscription_url = subscription_url
            changed = True

        if changed:
            subscription.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(subscription)
            logger.info('Webhook: subscription %s modified (synced from panel) for user %s', subscription.id, user.id)

    async def _handle_user_deleted(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription and subscription.status != SubscriptionStatus.EXPIRED.value:
            await expire_subscription(db, subscription)
            logger.info(
                'Webhook: subscription %s marked expired (user deleted in panel) for user %s', subscription.id, user.id
            )

        # Clear remnawave linkage
        if user.remnawave_uuid:
            user.remnawave_uuid = None
            await db.commit()

        await self._notify_user(user, 'WEBHOOK_SUB_DELETED')

    async def _handle_user_revoked(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription:
            new_url = data.get('subscriptionUrl')
            new_crypto_link = data.get('subscriptionCryptoLink')
            changed = False

            if new_url and self._is_valid_url(new_url) and subscription.subscription_url != new_url:
                subscription.subscription_url = new_url
                changed = True
            if (
                new_crypto_link
                and self._is_valid_url(new_crypto_link)
                and subscription.subscription_crypto_link != new_crypto_link
            ):
                subscription.subscription_crypto_link = new_crypto_link
                changed = True

            if changed:
                subscription.updated_at = datetime.utcnow()
                await db.commit()
                await db.refresh(subscription)
                logger.info(
                    'Webhook: subscription %s credentials revoked/updated for user %s', subscription.id, user.id
                )

        await self._notify_user(user, 'WEBHOOK_SUB_REVOKED')

    async def _handle_user_created(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        logger.info('Webhook: user %s created externally in panel (uuid=%s)', user.id, data.get('uuid'))

    async def _handle_expires_in_72h(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        await self._notify_user(user, 'WEBHOOK_SUB_EXPIRES_72H', reply_markup=self._get_renew_keyboard(user))

    async def _handle_expires_in_48h(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        await self._notify_user(user, 'WEBHOOK_SUB_EXPIRES_48H', reply_markup=self._get_renew_keyboard(user))

    async def _handle_expires_in_24h(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        await self._notify_user(user, 'WEBHOOK_SUB_EXPIRES_24H', reply_markup=self._get_renew_keyboard(user))

    async def _handle_expired_24h_ago(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        await self._notify_user(user, 'WEBHOOK_SUB_EXPIRED_24H_AGO', reply_markup=self._get_renew_keyboard(user))

    async def _handle_first_connected(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        logger.info('Webhook: user %s first VPN connection', user.id)
        await self._notify_user(user, 'WEBHOOK_SUB_FIRST_CONNECTED')

    async def _handle_bandwidth_threshold(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        # Extract threshold percentage from meta or data
        percent = data.get('thresholdPercent') or data.get('threshold', '')
        if not percent:
            # Try to extract from meta
            meta = data.get('meta', {})
            if isinstance(meta, dict):
                percent = meta.get('thresholdPercent', '80')

        # Sanitize to numeric value only (prevent format string injection)
        percent_str = re.sub(r'[^\d.]', '', str(percent)) or '80'

        await self._notify_user(
            user,
            'WEBHOOK_SUB_BANDWIDTH_THRESHOLD',
            format_kwargs={'percent': percent_str},
        )
