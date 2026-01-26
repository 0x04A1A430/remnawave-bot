"""
Unified notification delivery service for all user types.

This service handles notification delivery through appropriate channels:
- Telegram Bot for users with telegram_id
- Email + WebSocket for email-only users
"""

import asyncio
import logging
from enum import Enum
from typing import Any

from aiogram import Bot

from app.config import settings
from app.database.models import User


logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Types of notifications that can be sent to users."""

    # Balance notifications
    BALANCE_TOPUP = 'balance_topup'
    BALANCE_CHANGE = 'balance_change'
    BALANCE_LOW = 'balance_low'

    # Subscription notifications
    SUBSCRIPTION_ACTIVATED = 'subscription_activated'
    SUBSCRIPTION_EXPIRING = 'subscription_expiring'
    SUBSCRIPTION_EXPIRED = 'subscription_expired'
    SUBSCRIPTION_RENEWED = 'subscription_renewed'

    # Autopay notifications
    AUTOPAY_SUCCESS = 'autopay_success'
    AUTOPAY_FAILED = 'autopay_failed'
    AUTOPAY_INSUFFICIENT_FUNDS = 'autopay_insufficient_funds'

    # Daily subscription notifications
    DAILY_DEBIT = 'daily_debit'
    DAILY_INSUFFICIENT_FUNDS = 'daily_insufficient_funds'
    TRAFFIC_RESET = 'traffic_reset'

    # Account notifications
    BAN_NOTIFICATION = 'ban_notification'
    UNBAN_NOTIFICATION = 'unban_notification'
    WARNING_NOTIFICATION = 'warning_notification'

    # Referral notifications
    REFERRAL_BONUS = 'referral_bonus'
    REFERRAL_REGISTERED = 'referral_registered'

    # Auth emails
    EMAIL_VERIFICATION = 'email_verification'
    PASSWORD_RESET = 'password_reset'

    # Other
    BROADCAST = 'broadcast'
    PAYMENT_RECEIVED = 'payment_received'


class NotificationDeliveryService:
    """
    Service for delivering notifications to users through appropriate channels.

    For Telegram users: sends via Telegram Bot
    For email-only users: sends via Email and WebSocket (if connected)
    """

    def __init__(self):
        self._email_service = None
        self._email_templates = None
        self._ws_manager = None

    @property
    def email_service(self):
        """Lazy load email service."""
        if self._email_service is None:
            from app.cabinet.services.email_service import email_service

            self._email_service = email_service
        return self._email_service

    @property
    def email_templates(self):
        """Lazy load email templates."""
        if self._email_templates is None:
            from app.cabinet.services.email_templates import EmailNotificationTemplates

            self._email_templates = EmailNotificationTemplates()
        return self._email_templates

    @property
    def ws_manager(self):
        """Lazy load WebSocket manager."""
        if self._ws_manager is None:
            from app.cabinet.routes.websocket import cabinet_ws_manager

            self._ws_manager = cabinet_ws_manager
        return self._ws_manager

    async def send_notification(
        self,
        user: User,
        notification_type: NotificationType,
        context: dict[str, Any],
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """
        Send notification to user through appropriate channel.

        Args:
            user: User to notify
            notification_type: Type of notification
            context: Context data for message formatting
            bot: Telegram bot instance (required for Telegram users)
            telegram_message: Pre-formatted Telegram message (optional)
            telegram_markup: Telegram keyboard markup (optional)

        Returns:
            True if notification was sent successfully through at least one channel
        """
        if user.telegram_id:
            # User has Telegram - send via bot
            return await self._send_telegram_notification(
                user=user,
                notification_type=notification_type,
                context=context,
                bot=bot,
                message=telegram_message,
                markup=telegram_markup,
            )
        if user.email and user.email_verified:
            # Email-only user - send via email and WebSocket
            results = await asyncio.gather(
                self._send_email_notification(user, notification_type, context),
                self._send_websocket_notification(user, notification_type, context),
                return_exceptions=True,
            )

            email_sent = results[0] is True
            ws_sent = results[1] is True

            if email_sent or ws_sent:
                logger.info(
                    'Уведомление %s отправлено email-пользователю %s (email=%s, ws=%s)',
                    notification_type.value,
                    user.id,
                    email_sent,
                    ws_sent,
                )
                return True
            logger.warning(
                'Не удалось отправить уведомление %s email-пользователю %s',
                notification_type.value,
                user.id,
            )
            return False
        logger.debug(
            'Пользователь %s не имеет telegram_id или verified email, пропускаем уведомление',
            user.id,
        )
        return False

    async def _send_telegram_notification(
        self,
        user: User,
        notification_type: NotificationType,
        context: dict[str, Any],
        bot: Bot | None,
        message: str | None,
        markup: Any | None,
    ) -> bool:
        """Send notification via Telegram bot."""
        if not bot:
            logger.warning(
                'Bot instance not provided for Telegram notification to user %s',
                user.telegram_id,
            )
            return False

        if not message:
            logger.warning(
                'No Telegram message provided for notification %s to user %s',
                notification_type.value,
                user.telegram_id,
            )
            return False

        try:
            from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

            await asyncio.wait_for(
                bot.send_message(
                    chat_id=user.telegram_id,
                    text=message,
                    reply_markup=markup,
                    parse_mode='HTML',
                ),
                timeout=15.0,
            )
            return True

        except TimeoutError:
            logger.warning(
                'Timeout при отправке Telegram уведомления пользователю %s',
                user.telegram_id,
            )
            return False

        except TelegramForbiddenError:
            logger.warning(
                'Telegram user %s заблокировал бота',
                user.telegram_id,
            )
            return False

        except TelegramBadRequest as e:
            logger.warning(
                'Ошибка отправки Telegram уведомления пользователю %s: %s',
                user.telegram_id,
                e,
            )
            return False

        except Exception as e:
            logger.error(
                'Неожиданная ошибка при отправке Telegram уведомления: %s',
                e,
            )
            return False

    async def _send_email_notification(
        self,
        user: User,
        notification_type: NotificationType,
        context: dict[str, Any],
    ) -> bool:
        """Send notification via email."""
        if not self.email_service.is_configured():
            logger.debug('SMTP не настроен, пропускаем email уведомление')
            return False

        if not user.email or not user.email_verified:
            logger.debug('У пользователя %s нет подтверждённого email', user.id)
            return False

        try:
            # Get email template (check DB override first, then fall back to hardcoded)
            language = user.language or 'ru'

            # Try DB override
            template = None
            try:
                from app.cabinet.services.email_template_overrides import get_template_override

                override = await get_template_override(notification_type.value, language)
                if override:
                    # Wrap custom body in base template
                    full_html = self.email_templates._get_base_template(override['body_html'], language)
                    template = {
                        'subject': override['subject'],
                        'body_html': full_html,
                    }
            except Exception as e:
                logger.debug('Не удалось проверить override шаблона: %s', e)

            if not template:
                template = self.email_templates.get_template(notification_type, language, context)

            if not template:
                logger.warning(
                    'Не найден email шаблон для %s',
                    notification_type.value,
                )
                return False

            # Send email
            success = self.email_service.send_email(
                to_email=user.email,
                subject=template['subject'],
                body_html=template['body_html'],
                body_text=template.get('body_text'),
            )

            if success:
                logger.info(
                    'Email уведомление %s отправлено пользователю %s (%s)',
                    notification_type.value,
                    user.id,
                    user.email,
                )

            return success

        except Exception as e:
            logger.error(
                'Ошибка отправки email уведомления пользователю %s: %s',
                user.id,
                e,
            )
            return False

    async def _send_websocket_notification(
        self,
        user: User,
        notification_type: NotificationType,
        context: dict[str, Any],
    ) -> bool:
        """Send notification via WebSocket to cabinet."""
        try:
            message = {
                'type': f'notification.{notification_type.value}',
                **context,
            }

            await self.ws_manager.send_to_user(user.id, message)
            return True

        except Exception as e:
            logger.debug(
                'WebSocket уведомление не отправлено пользователю %s: %s',
                user.id,
                e,
            )
            return False

    # ============================================================================
    # Convenience methods for common notification types
    # ============================================================================

    async def notify_balance_topup(
        self,
        user: User,
        amount_kopeks: int,
        new_balance_kopeks: int,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about balance top-up."""
        context = {
            'amount_kopeks': amount_kopeks,
            'amount_rubles': amount_kopeks / 100,
            'new_balance_kopeks': new_balance_kopeks,
            'new_balance_rubles': new_balance_kopeks / 100,
            'formatted_amount': settings.format_price(amount_kopeks),
            'formatted_balance': settings.format_price(new_balance_kopeks),
        }

        return await self.send_notification(
            user=user,
            notification_type=NotificationType.BALANCE_TOPUP,
            context=context,
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_subscription_expiring(
        self,
        user: User,
        days_left: int,
        expires_at: Any,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about expiring subscription."""
        context = {
            'days_left': days_left,
            'expires_at': str(expires_at),
        }

        return await self.send_notification(
            user=user,
            notification_type=NotificationType.SUBSCRIPTION_EXPIRING,
            context=context,
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_subscription_expired(
        self,
        user: User,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about expired subscription."""
        return await self.send_notification(
            user=user,
            notification_type=NotificationType.SUBSCRIPTION_EXPIRED,
            context={},
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_autopay_success(
        self,
        user: User,
        amount_kopeks: int,
        new_expires_at: Any,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about successful autopay."""
        context = {
            'amount_kopeks': amount_kopeks,
            'amount_rubles': amount_kopeks / 100,
            'formatted_amount': settings.format_price(amount_kopeks),
            'new_expires_at': str(new_expires_at),
        }

        return await self.send_notification(
            user=user,
            notification_type=NotificationType.AUTOPAY_SUCCESS,
            context=context,
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_autopay_failed(
        self,
        user: User,
        reason: str,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about failed autopay."""
        context = {
            'reason': reason,
        }

        return await self.send_notification(
            user=user,
            notification_type=NotificationType.AUTOPAY_FAILED,
            context=context,
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_ban(
        self,
        user: User,
        reason: str | None = None,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about account ban."""
        context = {
            'reason': reason or 'Нарушение правил использования',
        }

        return await self.send_notification(
            user=user,
            notification_type=NotificationType.BAN_NOTIFICATION,
            context=context,
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_unban(
        self,
        user: User,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about account unban."""
        return await self.send_notification(
            user=user,
            notification_type=NotificationType.UNBAN_NOTIFICATION,
            context={},
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_referral_bonus(
        self,
        user: User,
        bonus_kopeks: int,
        referral_name: str,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about referral bonus."""
        context = {
            'bonus_kopeks': bonus_kopeks,
            'bonus_rubles': bonus_kopeks / 100,
            'formatted_bonus': settings.format_price(bonus_kopeks),
            'referral_name': referral_name,
        }

        return await self.send_notification(
            user=user,
            notification_type=NotificationType.REFERRAL_BONUS,
            context=context,
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )

    async def notify_daily_debit(
        self,
        user: User,
        amount_kopeks: int,
        new_balance_kopeks: int,
        bot: Bot | None = None,
        telegram_message: str | None = None,
        telegram_markup: Any | None = None,
    ) -> bool:
        """Notify user about daily subscription debit."""
        context = {
            'amount_kopeks': amount_kopeks,
            'amount_rubles': amount_kopeks / 100,
            'formatted_amount': settings.format_price(amount_kopeks),
            'new_balance_kopeks': new_balance_kopeks,
            'new_balance_rubles': new_balance_kopeks / 100,
            'formatted_balance': settings.format_price(new_balance_kopeks),
        }

        return await self.send_notification(
            user=user,
            notification_type=NotificationType.DAILY_DEBIT,
            context=context,
            bot=bot,
            telegram_message=telegram_message,
            telegram_markup=telegram_markup,
        )


# Singleton instance
notification_delivery_service = NotificationDeliveryService()
