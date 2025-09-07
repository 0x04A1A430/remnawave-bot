import logging
from typing import Optional, Dict, Any
from datetime import datetime
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User, Subscription, Transaction
from app.database.crud.user import get_user_by_id

logger = logging.getLogger(__name__)


class AdminNotificationService:
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.chat_id = getattr(settings, 'ADMIN_NOTIFICATIONS_CHAT_ID', None)
        self.topic_id = getattr(settings, 'ADMIN_NOTIFICATIONS_TOPIC_ID', None)
        self.enabled = getattr(settings, 'ADMIN_NOTIFICATIONS_ENABLED', False)
    
    async def _get_referrer_info(self, db: AsyncSession, referred_by_id: Optional[int]) -> str:
        if not referred_by_id:
            return "Нет"
        
        try:
            referrer = await get_user_by_id(db, referred_by_id)
            if not referrer:
                return f"ID {referred_by_id} (не найден)"
            
            if referrer.username:
                return f"@{referrer.username} (ID: {referred_by_id})"
            else:
                return f"ID {referrer.telegram_id}"
                
        except Exception as e:
            logger.error(f"Ошибка получения данных рефера {referred_by_id}: {e}")
            return f"ID {referred_by_id}"
    
    async def send_trial_activation_notification(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            user_status = "🆕 Новый" if not user.has_had_paid_subscription else "🔄 Существующий"
            referrer_info = await self._get_referrer_info(db, user.referred_by_id)
            
            message = f"""🎯 <b>АКТИВАЦИЯ ТРИАЛА</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}
👥 <b>Статус:</b> {user_status}

⏰ <b>Параметры триала:</b>
📅 Период: {settings.TRIAL_DURATION_DAYS} дней
📊 Трафик: {settings.TRIAL_TRAFFIC_LIMIT_GB} ГБ
📱 Устройства: {settings.TRIAL_DEVICE_LIMIT}
🌐 Сервер: {subscription.connected_squads[0] if subscription.connected_squads else 'По умолчанию'}

📆 <b>Действует до:</b> {subscription.end_date.strftime('%d.%m.%Y %H:%M')}
🔗 <b>Реферер:</b> {referrer_info}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о триале: {e}")
            return False
    
    async def send_subscription_purchase_notification(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription,
        transaction: Transaction,
        period_days: int,
        was_trial_conversion: bool = False
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            event_type = "🔄 КОНВЕРСИЯ ИЗ ТРИАЛА" if was_trial_conversion else "💎 ПОКУПКА ПОДПИСКИ"
            
            if was_trial_conversion:
                user_status = "🎯 Конверсия из триала"
            elif user.has_had_paid_subscription:
                user_status = "🔄 Продление/Обновление"
            else:
                user_status = "🆕 Первая покупка"
            
            servers_info = await self._get_servers_info(subscription.connected_squads)
            payment_method = self._get_payment_method_display(transaction.payment_method)
            referrer_info = await self._get_referrer_info(db, user.referred_by_id)
            
            message = f"""💎 <b>{event_type}</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}
👥 <b>Статус:</b> {user_status}

💰 <b>Платеж:</b>
💵 Сумма: {settings.format_price(transaction.amount_kopeks)}
💳 Способ: {payment_method}
🆔 ID транзакции: {transaction.id}

📱 <b>Параметры подписки:</b>
📅 Период: {period_days} дней
📊 Трафик: {self._format_traffic(subscription.traffic_limit_gb)}
📱 Устройства: {subscription.device_limit}
🌐 Серверы: {servers_info}

📆 <b>Действует до:</b> {subscription.end_date.strftime('%d.%m.%Y %H:%M')}
💰 <b>Баланс после покупки:</b> {settings.format_price(user.balance_kopeks)}
🔗 <b>Реферер:</b> {referrer_info}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о покупке: {e}")
            return False
    
    async def send_balance_topup_notification(
        self,
        db: AsyncSession,
        user: User,
        transaction: Transaction,
        old_balance: int
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            topup_status = "🆕 Первое пополнение" if not user.has_made_first_topup else "🔄 Пополнение"
            payment_method = self._get_payment_method_display(transaction.payment_method)
            balance_change = user.balance_kopeks - old_balance
            referrer_info = await self._get_referrer_info(db, user.referred_by_id)
            
            message = f"""💰 <b>ПОПОЛНЕНИЕ БАЛАНСА</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}
💳 <b>Статус:</b> {topup_status}

💰 <b>Детали пополнения:</b>
💵 Сумма: {settings.format_price(transaction.amount_kopeks)}
💳 Способ: {payment_method}
🆔 ID транзакции: {transaction.id}

💰 <b>Баланс:</b>
📉 Было: {settings.format_price(old_balance)}
📈 Стало: {settings.format_price(user.balance_kopeks)}
➕ Изменение: +{settings.format_price(balance_change)}

🔗 <b>Реферер:</b> {referrer_info}
📱 <b>Подписка:</b> {self._get_subscription_status(user)}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о пополнении: {e}")
            return False
    
    async def send_subscription_extension_notification(
        self,
        db: AsyncSession,
        user: User,
        subscription: Subscription,
        transaction: Transaction,
        extended_days: int,
        old_end_date: datetime
    ) -> bool:
        if not self._is_enabled():
            return False
        
        try:
            payment_method = self._get_payment_method_display(transaction.payment_method)
            servers_info = await self._get_servers_info(subscription.connected_squads)
            
            message = f"""⏰ <b>ПРОДЛЕНИЕ ПОДПИСКИ</b>

👤 <b>Пользователь:</b> {user.full_name}
🆔 <b>Telegram ID:</b> <code>{user.telegram_id}</code>
📱 <b>Username:</b> @{user.username or 'отсутствует'}

💰 <b>Платеж:</b>
💵 Сумма: {settings.format_price(transaction.amount_kopeks)}
💳 Способ: {payment_method}
🆔 ID транзакции: {transaction.id}

📅 <b>Продление:</b>
➕ Добавлено дней: {extended_days}
📆 Было до: {old_end_date.strftime('%d.%m.%Y %H:%M')}
📆 Стало до: {subscription.end_date.strftime('%d.%m.%Y %H:%M')}

📱 <b>Текущие параметры:</b>
📊 Трафик: {self._format_traffic(subscription.traffic_limit_gb)}
📱 Устройства: {subscription.device_limit}
🌐 Серверы: {servers_info}

💰 <b>Баланс после операции:</b> {settings.format_price(user.balance_kopeks)}

⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"""
            
            return await self._send_message(message)
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о продлении: {e}")
            return False
    
    async def _send_message(self, text: str) -> bool:
        if not self.chat_id:
            logger.warning("ADMIN_NOTIFICATIONS_CHAT_ID не настроен")
            return False
        
        try:
            message_kwargs = {
                'chat_id': self.chat_id,
                'text': text,
                'parse_mode': 'HTML',
                'disable_web_page_preview': True
            }
            
            if self.topic_id:
                message_kwargs['message_thread_id'] = self.topic_id
            
            await self.bot.send_message(**message_kwargs)
            logger.info(f"Уведомление отправлено в чат {self.chat_id}")
            return True
            
        except TelegramForbiddenError:
            logger.error(f"Бот не имеет прав для отправки в чат {self.chat_id}")
            return False
        except TelegramBadRequest as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
            return False
        except Exception as e:
            logger.error(f"Неожиданная ошибка при отправке уведомления: {e}")
            return False
    
    def _is_enabled(self) -> bool:
        return self.enabled and bool(self.chat_id)
    
    def _get_payment_method_display(self, payment_method: Optional[str]) -> str:
        method_names = {
            'telegram_stars': '⭐ Telegram Stars',
            'yookassa': '💳 YooKassa (карта)',
            'tribute': '💎 Tribute (карта)',
            'manual': '🛠️ Вручную (админ)',
            'balance': '💰 С баланса'
        }
        
        if not payment_method:
            return '💰 С баланса'
            
        return method_names.get(payment_method, f'💰 С баланса')
    
    def _format_traffic(self, traffic_gb: int) -> str:
        if traffic_gb == 0:
            return "∞ Безлимит"
        return f"{traffic_gb} ГБ"
    
    def _get_subscription_status(self, user: User) -> str:
        if not user.subscription:
            return "❌ Нет подписки"
        
        sub = user.subscription
        if sub.is_trial:
            return f"🎯 Триал (до {sub.end_date.strftime('%d.%m')})"
        elif sub.is_active:
            return f"✅ Активна (до {sub.end_date.strftime('%d.%m')})"
        else:
            return "❌ Неактивна"
    
    async def _get_servers_info(self, squad_uuids: list) -> str:
        if not squad_uuids:
            return "❌ Нет серверов"
        
        try:
            from app.handlers.subscription import get_servers_display_names
            servers_names = await get_servers_display_names(squad_uuids)
            return f"{len(squad_uuids)} шт. ({servers_names})"
        except Exception as e:
            logger.warning(f"Не удалось получить названия серверов: {e}")
            return f"{len(squad_uuids)} шт."
