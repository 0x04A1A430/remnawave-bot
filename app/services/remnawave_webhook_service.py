"""
Service for processing incoming RemnaWave backend webhooks.

Handles all webhook scopes: user, user_hwid_devices, node, service, crm.
User events update subscription state and notify the user.
Admin events (node, service, crm) send alerts to the admin notification chat.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import delete
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from app.config import settings
from app.database.crud.subscription import (
    deactivate_subscription,
    decrement_subscription_server_counts,
    expire_subscription,
    get_subscription_by_user_id,
    reactivate_subscription,
    update_subscription_usage,
)
from app.database.crud.user import get_user_by_id, get_user_by_remnawave_uuid, get_user_by_telegram_id
from app.database.models import Subscription, SubscriptionServer, SubscriptionStatus, User
from app.localization.texts import get_texts
from app.services.admin_notification_service import AdminNotificationService
from app.services.notification_delivery_service import NotificationType, notification_delivery_service
from app.utils.miniapp_buttons import build_miniapp_or_callback_button


logger = logging.getLogger(__name__)


# Mapping from locale text_key to NotificationType for unified delivery
_TEXT_KEY_TO_NOTIFICATION_TYPE: dict[str, NotificationType] = {
    'WEBHOOK_SUB_EXPIRED': NotificationType.WEBHOOK_SUB_EXPIRED,
    'WEBHOOK_SUB_DISABLED': NotificationType.WEBHOOK_SUB_DISABLED,
    'WEBHOOK_SUB_ENABLED': NotificationType.WEBHOOK_SUB_ENABLED,
    'WEBHOOK_SUB_LIMITED': NotificationType.WEBHOOK_SUB_LIMITED,
    'WEBHOOK_SUB_TRAFFIC_RESET': NotificationType.WEBHOOK_SUB_TRAFFIC_RESET,
    'WEBHOOK_SUB_DELETED': NotificationType.WEBHOOK_SUB_DELETED,
    'WEBHOOK_SUB_REVOKED': NotificationType.WEBHOOK_SUB_REVOKED,
    'WEBHOOK_SUB_EXPIRES_72H': NotificationType.WEBHOOK_SUB_EXPIRING,
    'WEBHOOK_SUB_EXPIRES_48H': NotificationType.WEBHOOK_SUB_EXPIRING,
    'WEBHOOK_SUB_EXPIRES_24H': NotificationType.WEBHOOK_SUB_EXPIRING,
    'WEBHOOK_SUB_EXPIRED_24H_AGO': NotificationType.WEBHOOK_SUB_EXPIRED,
    'WEBHOOK_SUB_FIRST_CONNECTED': NotificationType.WEBHOOK_SUB_FIRST_CONNECTED,
    'WEBHOOK_SUB_BANDWIDTH_THRESHOLD': NotificationType.WEBHOOK_SUB_BANDWIDTH_THRESHOLD,
    'WEBHOOK_USER_NOT_CONNECTED': NotificationType.WEBHOOK_USER_NOT_CONNECTED,
    'WEBHOOK_DEVICE_ADDED': NotificationType.WEBHOOK_DEVICE_ADDED,
    'WEBHOOK_DEVICE_DELETED': NotificationType.WEBHOOK_DEVICE_DELETED,
}

# Mapping from locale text_key to the Settings toggle that controls it
_TEXT_KEY_TO_SETTING: dict[str, str] = {
    'WEBHOOK_SUB_EXPIRED': 'WEBHOOK_NOTIFY_SUB_EXPIRED',
    'WEBHOOK_SUB_DISABLED': 'WEBHOOK_NOTIFY_SUB_STATUS',
    'WEBHOOK_SUB_ENABLED': 'WEBHOOK_NOTIFY_SUB_STATUS',
    'WEBHOOK_SUB_LIMITED': 'WEBHOOK_NOTIFY_SUB_LIMITED',
    'WEBHOOK_SUB_TRAFFIC_RESET': 'WEBHOOK_NOTIFY_TRAFFIC_RESET',
    'WEBHOOK_SUB_DELETED': 'WEBHOOK_NOTIFY_SUB_DELETED',
    'WEBHOOK_SUB_REVOKED': 'WEBHOOK_NOTIFY_SUB_REVOKED',
    'WEBHOOK_SUB_EXPIRES_72H': 'WEBHOOK_NOTIFY_SUB_EXPIRING',
    'WEBHOOK_SUB_EXPIRES_48H': 'WEBHOOK_NOTIFY_SUB_EXPIRING',
    'WEBHOOK_SUB_EXPIRES_24H': 'WEBHOOK_NOTIFY_SUB_EXPIRING',
    'WEBHOOK_SUB_EXPIRED_24H_AGO': 'WEBHOOK_NOTIFY_SUB_EXPIRED',
    'WEBHOOK_SUB_FIRST_CONNECTED': 'WEBHOOK_NOTIFY_FIRST_CONNECTED',
    'WEBHOOK_SUB_BANDWIDTH_THRESHOLD': 'WEBHOOK_NOTIFY_BANDWIDTH_THRESHOLD',
    'WEBHOOK_USER_NOT_CONNECTED': 'WEBHOOK_NOTIFY_NOT_CONNECTED',
    'WEBHOOK_DEVICE_ADDED': 'WEBHOOK_NOTIFY_DEVICES',
    'WEBHOOK_DEVICE_DELETED': 'WEBHOOK_NOTIFY_DEVICES',
}

# Admin event display names for notification messages
_ADMIN_NODE_EVENTS: dict[str, str] = {
    'node.created': '🟢 Нода создана',
    'node.modified': '🔧 Нода изменена',
    'node.disabled': '🔴 Нода отключена',
    'node.enabled': '🟢 Нода включена',
    'node.deleted': '🗑️ Нода удалена',
    'node.connection_lost': '🚨 Потеряно соединение с нодой',
    'node.connection_restored': '✅ Соединение с нодой восстановлено',
    'node.traffic_notify': '📊 Уведомление о трафике ноды',
}

_ADMIN_SERVICE_EVENTS: dict[str, str] = {
    'service.panel_started': '🚀 Панель RemnaWave запущена',
    'service.login_attempt_failed': '🔐 Неудачная попытка входа в панель',
    'service.login_attempt_success': '🔓 Успешный вход в панель',
    'service.subpage_config_changed': '📄 Конфиг страницы подписки изменён',
}

_ADMIN_CRM_EVENTS: dict[str, str] = {
    'crm.infra_billing_node_payment_in_7_days': '💳 Оплата ноды через 7 дней',
    'crm.infra_billing_node_payment_in_48hrs': '💳 Оплата ноды через 48 часов',
    'crm.infra_billing_node_payment_in_24hrs': '⚠️ Оплата ноды через 24 часа',
    'crm.infra_billing_node_payment_due_today': '🔴 Оплата ноды сегодня',
    'crm.infra_billing_node_payment_overdue_24hrs': '❗ Просрочка оплаты ноды: 24 часа',
    'crm.infra_billing_node_payment_overdue_48hrs': '❗ Просрочка оплаты ноды: 48 часов',
    'crm.infra_billing_node_payment_overdue_7_days': '🚨 Просрочка оплаты ноды: 7 дней',
}

_ADMIN_ERROR_EVENTS: dict[str, str] = {
    'errors.bandwidth_usage_threshold_reached_max_notifications': '⚠️ Достигнут лимит уведомлений о трафике',
}


class RemnaWaveWebhookService:
    """Processes incoming webhooks from RemnaWave backend."""

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._admin_service = AdminNotificationService(bot)

        # User-scoped handlers: require user resolution
        self._user_handlers: dict[str, Any] = {
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
            'user.not_connected': self._handle_user_not_connected,
            'user_hwid_devices.added': self._handle_device_added,
            'user_hwid_devices.deleted': self._handle_device_deleted,
        }

        # Admin-scoped handlers: no user resolution, notify admin chat
        self._admin_handlers: dict[str, str] = {
            **_ADMIN_NODE_EVENTS,
            **_ADMIN_SERVICE_EVENTS,
            **_ADMIN_CRM_EVENTS,
            **_ADMIN_ERROR_EVENTS,
        }

    def is_admin_event(self, event_name: str) -> bool:
        """Check if the event is admin-scoped (no DB session needed)."""
        return event_name in self._admin_handlers

    async def process_event(self, db: AsyncSession | None, event_name: str, data: dict) -> bool:
        """Route event to the appropriate handler.

        Returns True if the event was processed, False if skipped/unknown.
        db may be None for admin events that don't require database access.
        """
        # Check admin-scoped handlers (no DB needed)
        if event_name in self._admin_handlers:
            return await self._process_admin_event(event_name, data)

        # Check user-scoped handlers (require DB session)
        user_handler = self._user_handlers.get(event_name)
        if user_handler:
            if db is None:
                logger.error('RemnaWave webhook: DB session required for user event %s', event_name)
                return False
            return await self._process_user_event(db, event_name, data, user_handler)

        logger.debug('Unhandled RemnaWave webhook event: %s', event_name)
        return False

    async def _process_user_event(self, db: AsyncSession, event_name: str, data: dict, handler: Any) -> bool:
        """Resolve user and execute user-scoped handler."""
        user, subscription = await self._resolve_user_and_subscription(db, data)
        if not user:
            logger.warning(
                'RemnaWave webhook: user not found for event %s, data telegramId=%s uuid=%s',
                event_name,
                data.get('telegramId'),
                data.get('uuid'),
            )
            return False

        user_id = user.id
        try:
            await handler(db, user, subscription, data)
            return True
        except (StaleDataError, PendingRollbackError):
            logger.warning(
                'RemnaWave webhook %s: entity already deleted for user %s (concurrent deletion)',
                event_name,
                user_id,
            )
            try:
                await db.rollback()
            except Exception:
                pass
            return True
        except Exception:
            logger.exception('Error processing RemnaWave webhook event %s for user %s', event_name, user_id)
            try:
                await db.rollback()
            except Exception:
                logger.debug('Rollback after webhook handler error also failed')
            return False

    async def _process_admin_event(self, event_name: str, data: dict) -> bool:
        """Format and send admin notification for infrastructure events."""
        if not self._admin_service.is_enabled:
            logger.debug('Admin notifications disabled, skipping event %s', event_name)
            return True

        title = self._admin_handlers.get(event_name, event_name)

        # Build message from event data (escape all untrusted values to prevent HTML injection)
        lines = [f'<b>{title}</b>']

        # Extract common fields
        name = html.escape(data.get('name') or data.get('nodeName') or data.get('username') or '')
        if name:
            lines.append(f'Имя: <code>{name}</code>')

        address = html.escape(data.get('address') or data.get('ip') or '')
        if address:
            lines.append(f'Адрес: <code>{address}</code>')

        port = data.get('port')
        if port:
            lines.append(f'Порт: <code>{html.escape(str(port))}</code>')

        version = html.escape(data.get('version') or data.get('panelVersion') or '')
        if version:
            lines.append(f'Версия: <code>{version}</code>')

        # CRM billing fields
        amount = html.escape(str(data.get('amount') or data.get('price') or ''))
        if amount:
            lines.append(f'Сумма: <code>{amount}</code>')

        due_date = html.escape(data.get('dueDate') or data.get('paymentDate') or '')
        if due_date:
            lines.append(f'Дата: <code>{due_date}</code>')

        # Login attempt fields
        ip_addr = html.escape(data.get('ipAddress') or data.get('ip') or '')
        if ip_addr and not address:
            lines.append(f'IP: <code>{ip_addr}</code>')

        message = html.escape(data.get('message') or '')
        if message:
            lines.append(f'Сообщение: {message}')

        # Subpage config fields
        subpage = data.get('subpageConfig')
        if isinstance(subpage, dict):
            action = subpage.get('action', '')
            action_labels = {'CREATED': 'Создан', 'UPDATED': 'Обновлён', 'DELETED': 'Удалён'}
            lines.append(f'Действие: {action_labels.get(action, html.escape(str(action)))}')
            sub_uuid = subpage.get('uuid', '')
            if sub_uuid:
                lines.append(f'UUID: <code>{html.escape(str(sub_uuid))}</code>')

        try:
            await self._admin_service.send_webhook_notification('\n'.join(lines))
            return True
        except Exception:
            logger.exception('Failed to send admin notification for event %s', event_name)
            return False

    # ------------------------------------------------------------------
    # User resolution
    # ------------------------------------------------------------------

    async def _resolve_user_and_subscription(
        self, db: AsyncSession, data: dict
    ) -> tuple[User | None, Subscription | None]:
        """Find bot user by telegramId or uuid from webhook payload.

        Handles both user-scope events (top-level telegramId/uuid) and
        device-scope events (userUuid, or nested user.telegramId/user.uuid).
        """
        user: User | None = None

        # Try top-level telegramId first
        telegram_id = data.get('telegramId')
        if telegram_id:
            try:
                user = await get_user_by_telegram_id(db, int(telegram_id))
            except (ValueError, TypeError):
                pass

        # Try top-level uuid
        if not user:
            uuid = data.get('uuid') or data.get('userUuid')
            if uuid:
                user = await get_user_by_remnawave_uuid(db, uuid)

        # Try nested user object (e.g. user_hwid_devices events)
        if not user:
            nested_user = data.get('user')
            if isinstance(nested_user, dict):
                nested_tid = nested_user.get('telegramId')
                if nested_tid:
                    try:
                        user = await get_user_by_telegram_id(db, int(nested_tid))
                    except (ValueError, TypeError):
                        pass
                if not user:
                    nested_uuid = nested_user.get('uuid')
                    if nested_uuid:
                        user = await get_user_by_remnawave_uuid(db, nested_uuid)

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

    @staticmethod
    def _is_valid_link(value: str) -> bool:
        """Validate URL or deep link (happ://, vless://, ss://, etc.)."""
        if not value or len(value) > 4096:
            return False
        return bool(re.match(r'^[a-zA-Z][a-zA-Z0-9+\-.]*://', value))

    def _get_renew_keyboard(self, user: User) -> InlineKeyboardMarkup:
        texts = get_texts(user.language)
        button_text = texts.get('WEBHOOK_RENEW_BUTTON', 'Renew subscription')
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [build_miniapp_or_callback_button(text=button_text, callback_data='subscription_extend')],
            ]
        )

    def _get_subscription_keyboard(self, user: User) -> InlineKeyboardMarkup:
        texts = get_texts(user.language)
        button_text = texts.get('MY_SUBSCRIPTION_BUTTON', 'My subscription')
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [build_miniapp_or_callback_button(text=button_text, callback_data='subscription')],
            ]
        )

    def _get_connect_keyboard(self, user: User) -> InlineKeyboardMarkup:
        texts = get_texts(user.language)
        button_text = texts.get('CONNECT_BUTTON', 'Connect')
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [build_miniapp_or_callback_button(text=button_text, callback_data='subscription_connect')],
            ]
        )

    def _get_traffic_keyboard(self, user: User) -> InlineKeyboardMarkup:
        texts = get_texts(user.language)
        buy_text = texts.get('BUY_TRAFFIC_BUTTON', 'Buy traffic')
        sub_text = texts.get('MY_SUBSCRIPTION_BUTTON', 'My subscription')
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [build_miniapp_or_callback_button(text=buy_text, callback_data='buy_traffic')],
                [build_miniapp_or_callback_button(text=sub_text, callback_data='subscription')],
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
        """Send a notification to user via appropriate channel.

        Telegram users receive a bot message; email-only users receive
        an email and/or WebSocket notification through the unified
        notification delivery service.

        Respects WEBHOOK_NOTIFY_USER_ENABLED master toggle and
        per-event toggles from Settings.
        """
        if not settings.WEBHOOK_NOTIFY_USER_ENABLED:
            logger.debug('Webhook user notifications disabled globally, skipping %s', text_key)
            return

        setting_key = _TEXT_KEY_TO_SETTING.get(text_key)
        if setting_key and not getattr(settings, setting_key, True):
            logger.debug('Webhook notification %s disabled via %s', text_key, setting_key)
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

        # Append "Close" button to every webhook notification keyboard
        close_text = texts.get('WEBHOOK_CLOSE_BUTTON', '✖️ Закрыть')
        close_row = [InlineKeyboardButton(text=close_text, callback_data='webhook:close')]
        if reply_markup:
            reply_markup = InlineKeyboardMarkup(
                inline_keyboard=[*reply_markup.inline_keyboard, close_row],
            )
        else:
            reply_markup = InlineKeyboardMarkup(inline_keyboard=[close_row])

        notification_type = _TEXT_KEY_TO_NOTIFICATION_TYPE.get(text_key)
        if not notification_type:
            logger.warning('No NotificationType mapping for text_key %s', text_key)
            return

        context = {'text_key': text_key, **(format_kwargs or {})}

        try:
            await notification_delivery_service.send_notification(
                user=user,
                notification_type=notification_type,
                context=context,
                bot=self.bot,
                telegram_message=message,
                telegram_markup=reply_markup,
            )
        except Exception:
            logger.exception('Notification delivery failed for user %s, text_key %s', user.id, text_key)

    # ------------------------------------------------------------------
    # Webhook timestamp helper
    # ------------------------------------------------------------------

    @staticmethod
    def _stamp_webhook_update(subscription: Subscription) -> None:
        """Mark subscription as recently updated by webhook to prevent sync overwrite."""
        subscription.last_webhook_update_at = datetime.now(UTC).replace(tzinfo=None)

    # ------------------------------------------------------------------
    # User event handlers
    # ------------------------------------------------------------------

    async def _handle_user_expired(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription:
            self._stamp_webhook_update(subscription)
            if subscription.status != SubscriptionStatus.EXPIRED.value:
                await expire_subscription(db, subscription)
                logger.info('Webhook: subscription %s expired for user %s', subscription.id, user.id)
            else:
                await db.commit()

        await self._notify_user(user, 'WEBHOOK_SUB_EXPIRED', reply_markup=self._get_renew_keyboard(user))

    async def _handle_user_disabled(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription:
            self._stamp_webhook_update(subscription)
            if subscription.status != SubscriptionStatus.DISABLED.value:
                await deactivate_subscription(db, subscription)
                logger.info('Webhook: subscription %s disabled for user %s', subscription.id, user.id)
            else:
                await db.commit()

        await self._notify_user(user, 'WEBHOOK_SUB_DISABLED', reply_markup=self._get_subscription_keyboard(user))

    async def _handle_user_enabled(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription:
            self._stamp_webhook_update(subscription)
            if subscription.status == SubscriptionStatus.DISABLED.value:
                await reactivate_subscription(db, subscription)
                logger.info('Webhook: subscription %s re-enabled for user %s', subscription.id, user.id)
            else:
                await db.commit()

        await self._notify_user(user, 'WEBHOOK_SUB_ENABLED', reply_markup=self._get_connect_keyboard(user))

    async def _handle_user_limited(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription:
            self._stamp_webhook_update(subscription)
            if subscription.status == SubscriptionStatus.ACTIVE.value:
                await deactivate_subscription(db, subscription)
                logger.info('Webhook: subscription %s limited (traffic) for user %s', subscription.id, user.id)
            else:
                await db.commit()

        await self._notify_user(user, 'WEBHOOK_SUB_LIMITED', reply_markup=self._get_traffic_keyboard(user))

    async def _handle_user_traffic_reset(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        if subscription:
            self._stamp_webhook_update(subscription)
            await update_subscription_usage(db, subscription, 0.0)
            # Re-enable if was disabled due to traffic limit
            if subscription.status == SubscriptionStatus.DISABLED.value:
                await reactivate_subscription(db, subscription)
            logger.info('Webhook: traffic reset for subscription %s, user %s', subscription.id, user.id)

        await self._notify_user(user, 'WEBHOOK_SUB_TRAFFIC_RESET', reply_markup=self._get_subscription_keyboard(user))

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

        # Sync status from panel
        panel_status = data.get('status')
        if panel_status:
            now = datetime.now(UTC).replace(tzinfo=None)
            end_date = subscription.end_date
            if panel_status == 'ACTIVE' and end_date and end_date > now:
                if subscription.status != SubscriptionStatus.ACTIVE.value:
                    subscription.status = SubscriptionStatus.ACTIVE.value
                    changed = True
                    logger.info(
                        'Webhook: subscription %s reactivated (%s → active) for user %s',
                        subscription.id,
                        subscription.status,
                        user.id,
                    )
            elif panel_status == 'DISABLED':
                if subscription.status != SubscriptionStatus.DISABLED.value:
                    subscription.status = SubscriptionStatus.DISABLED.value
                    changed = True

        # Sync subscription URL (validate to prevent stored XSS)
        subscription_url = data.get('subscriptionUrl')
        if (
            subscription_url
            and self._is_valid_url(subscription_url)
            and subscription.subscription_url != subscription_url
        ):
            subscription.subscription_url = subscription_url
            changed = True

        # Always stamp to protect from sync overwrite, even if no fields changed
        self._stamp_webhook_update(subscription)
        if changed:
            subscription.updated_at = datetime.now(UTC).replace(tzinfo=None)
            logger.info('Webhook: subscription %s modified (synced from panel) for user %s', subscription.id, user.id)
        await db.commit()

    async def _handle_user_deleted(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        user_id = user.id
        sub_id = subscription.id if subscription else None

        if subscription:
            self._stamp_webhook_update(subscription)

            # Decrement server counters BEFORE clearing connected_squads
            await decrement_subscription_server_counts(db, subscription)

            # Re-fetch after potential rollback inside decrement_subscription_server_counts
            try:
                await db.refresh(subscription)
            except Exception:
                # Subscription was cascade-deleted, re-fetch user and skip subscription updates
                logger.warning(
                    'Webhook: subscription %s already deleted for user %s, skipping subscription cleanup',
                    sub_id,
                    user_id,
                )
                subscription = None
                try:
                    await db.rollback()
                except Exception:
                    pass

                try:
                    user = await get_user_by_id(db, user_id)
                except Exception:
                    logger.error('Webhook: user %s not found after rollback', user_id)
                    return
                if not user:
                    logger.error('Webhook: user %s not found after rollback', user_id)
                    return

        if subscription:
            if subscription.status != SubscriptionStatus.EXPIRED.value:
                subscription.status = SubscriptionStatus.EXPIRED.value
                logger.info(
                    'Webhook: subscription %s marked expired (user deleted in panel) for user %s',
                    sub_id,
                    user_id,
                )

            # Clear subscription data — panel user no longer exists
            subscription.subscription_url = None
            subscription.subscription_crypto_link = None
            subscription.remnawave_short_uuid = None
            subscription.connected_squads = None
            subscription.updated_at = datetime.now(UTC).replace(tzinfo=None)

            # Remove SubscriptionServer link rows
            await db.execute(delete(SubscriptionServer).where(SubscriptionServer.subscription_id == sub_id))

        # Clear remnawave linkage
        if user.remnawave_uuid:
            user.remnawave_uuid = None

        await db.commit()

        await self._notify_user(user, 'WEBHOOK_SUB_DELETED', reply_markup=self._get_renew_keyboard(user))

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
                and self._is_valid_link(new_crypto_link)
                and subscription.subscription_crypto_link != new_crypto_link
            ):
                subscription.subscription_crypto_link = new_crypto_link
                changed = True

            # Always stamp to protect from sync overwrite
            self._stamp_webhook_update(subscription)
            if changed:
                subscription.updated_at = datetime.now(UTC).replace(tzinfo=None)
                logger.info(
                    'Webhook: subscription %s credentials revoked/updated for user %s', subscription.id, user.id
                )
            await db.commit()

        await self._notify_user(user, 'WEBHOOK_SUB_REVOKED', reply_markup=self._get_connect_keyboard(user))

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
        await self._notify_user(user, 'WEBHOOK_SUB_FIRST_CONNECTED', reply_markup=self._get_subscription_keyboard(user))

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
            reply_markup=self._get_traffic_keyboard(user),
            format_kwargs={'percent': percent_str},
        )

    async def _handle_user_not_connected(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        logger.info('Webhook: user %s has not connected to VPN', user.id)
        await self._notify_user(user, 'WEBHOOK_USER_NOT_CONNECTED', reply_markup=self._get_connect_keyboard(user))

    # ------------------------------------------------------------------
    # Device event handlers (user_hwid_devices scope)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_device_name(data: dict) -> str:
        """Extract device name from webhook payload.

        RemnaWave sends device info in data['hwidUserDevice'] nested object.
        Builds a composite name: "tag (platform)" or just "platform" or hwid short.
        """
        device_obj = data.get('hwidUserDevice')
        if not isinstance(device_obj, dict):
            # Fallback: top-level fields
            raw = data.get('deviceName') or data.get('tag') or data.get('hwid') or ''
            return html.escape(str(raw)) if raw else ''

        tag = (device_obj.get('tag') or device_obj.get('deviceName') or device_obj.get('name') or '').strip()
        platform = (device_obj.get('platform') or '').strip()
        hwid = (device_obj.get('hwid') or '').strip()

        if tag and platform:
            return html.escape(f'{tag} ({platform})')
        if tag:
            return html.escape(tag)
        if platform and hwid:
            # Show platform + short hwid suffix for identification
            hwid_short = hwid[:8] if len(hwid) > 8 else hwid
            return html.escape(f'{platform} ({hwid_short})')
        if platform:
            return html.escape(platform)
        if hwid:
            hwid_short = hwid[:12] if len(hwid) > 12 else hwid
            return html.escape(hwid_short)
        return ''

    async def _handle_device_added(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        device_name = self._extract_device_name(data)
        logger.info('Webhook: device added for user %s: %s', user.id, device_name or '(empty)')
        await self._notify_user(
            user,
            'WEBHOOK_DEVICE_ADDED',
            reply_markup=self._get_subscription_keyboard(user),
            format_kwargs={'device': device_name or '—'},
        )

    async def _handle_device_deleted(
        self, db: AsyncSession, user: User, subscription: Subscription | None, data: dict
    ) -> None:
        device_name = self._extract_device_name(data)
        logger.info('Webhook: device deleted for user %s: %s', user.id, device_name or '(empty)')
        await self._notify_user(
            user,
            'WEBHOOK_DEVICE_DELETED',
            reply_markup=self._get_subscription_keyboard(user),
            format_kwargs={'device': device_name or '—'},
        )
