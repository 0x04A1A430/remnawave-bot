import logging
import hashlib
import hmac
import json
from typing import Optional, Dict, Any
from datetime import datetime

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import get_db
from app.database.models import Transaction, TransactionType, PaymentMethod
from app.database.crud.transaction import (
    create_transaction, get_transaction_by_external_id, complete_transaction
)
from app.database.crud.user import get_user_by_telegram_id, add_user_balance
from app.external.tribute import TributeService as TributeAPI

logger = logging.getLogger(__name__)


class TributeService:
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.tribute_api = TributeAPI()
    
    async def create_payment_link(
        self,
        user_id: int,
        amount_kopeks: int,
        description: str = "Пополнение баланса"
    ) -> Optional[str]:
        
        if not settings.TRIBUTE_ENABLED:
            logger.warning("Tribute платежи отключены")
            return None
        
        try:
            payment_url = await self.tribute_api.create_payment_link(
                user_id=user_id,
                amount_kopeks=amount_kopeks,
                description=description
            )
            
            if not payment_url:
                return None
            
            return payment_url
            
        except Exception as e:
            logger.error(f"Ошибка создания Tribute платежа: {e}")
            return None
    
    async def process_webhook(
        self,
        payload: str,
        signature: Optional[str] = None
    ) -> Dict[str, Any]:
        
        if signature and settings.TRIBUTE_WEBHOOK_SECRET:
            if not self.tribute_api.verify_webhook_signature(payload, signature):
                logger.warning("Неверная подпись Tribute webhook")
                return {"status": "error", "reason": "invalid_signature"}
        
        try:
            webhook_data = json.loads(payload)
        except json.JSONDecodeError:
            logger.error("Некорректный JSON в Tribute webhook")
            return {"status": "error", "reason": "invalid_json"}
        
        logger.info(f"Получен Tribute webhook: {json.dumps(webhook_data, ensure_ascii=False)}")
        
        processed_data = await self.tribute_api.process_webhook(webhook_data)
        if not processed_data:
            return {"status": "ignored", "reason": "invalid_data"}
        
        event_type = processed_data.get("event_type", "payment")
        status = processed_data.get("status")
        
        if event_type == "payment" and status == "paid":
            await self._handle_successful_payment(processed_data)
        elif event_type == "payment" and status == "failed":
            await self._handle_failed_payment(processed_data)
        elif event_type == "refund":
            await self._handle_refund(processed_data)
        
        return {"status": "ok", "event": event_type}
    
    async def _handle_successful_payment(self, payment_data: Dict[str, Any]):
        """Обработка успешного платежа - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
        
        try:
            user_id = payment_data["user_id"]
            amount_kopeks = payment_data["amount_kopeks"]
            payment_id = payment_data["payment_id"]
            
            logger.info(f"🔄 Обрабатываем успешный Tribute платеж: user_id={user_id}, amount={amount_kopeks}, payment_id={payment_id}")
            
            async for session in get_db():
                external_id = f"donation_{payment_id}"
                
                logger.info(f"🔍 Ищем существующую транзакцию с external_id: '{external_id}', payment_method: TRIBUTE")
                
                existing_transaction = await get_transaction_by_external_id(
                    session, external_id, PaymentMethod.TRIBUTE
                )
                
                if existing_transaction:
                    logger.info(f"🔍 Найдена существующая транзакция:")
                    logger.info(f"   ID: {existing_transaction.id}")
                    logger.info(f"   External ID: '{existing_transaction.external_id}'")
                    logger.info(f"   Payment Method: {existing_transaction.payment_method}")
                    logger.info(f"   Is Completed: {existing_transaction.is_completed}")
                    logger.info(f"   Amount: {existing_transaction.amount_kopeks} коп")
                    logger.info(f"   Created: {existing_transaction.created_at}")
                    
                    if existing_transaction.is_completed and existing_transaction.amount_kopeks == amount_kopeks:
                        logger.warning(f"❌ Транзакция с donation_request_id {payment_id} и суммой {amount_kopeks} коп уже обработана")
                        
                        user = await get_user_by_telegram_id(session, user_id)
                        if user:
                            logger.info(f"💰 Текущий баланс пользователя {user_id}: {user.balance_kopeks} коп")
                        else:
                            logger.error(f"❌ Пользователь {user_id} не найден при проверке баланса")
                        return
                    elif existing_transaction.is_completed and existing_transaction.amount_kopeks != amount_kopeks:
                        logger.warning(f"⚠️ Найден платеж с тем же ID {payment_id}, но другой суммой:")
                        logger.warning(f"   Существующий: {existing_transaction.amount_kopeks} коп")
                        logger.warning(f"   Новый: {amount_kopeks} коп")
                        logger.warning(f"   Обрабатываем как новый платеж...")
                        
                        external_id = f"donation_{payment_id}_{amount_kopeks}_{int(datetime.utcnow().timestamp())}"
                        logger.info(f"🔧 Создан уникальный external_id: {external_id}")
                    else:
                        logger.info(f"⚠️ Найдена незавершенная транзакция {existing_transaction.id}, завершаем...")
                        await complete_transaction(session, existing_transaction)
                        
                        user = await get_user_by_telegram_id(session, user_id)
                        if user:
                            old_balance = user.balance_kopeks
                            user.balance_kopeks += amount_kopeks
                            await session.commit()
                            logger.info(f"💰 Баланс обновлен при завершении транзакции: {old_balance} -> {user.balance_kopeks} коп")
                            await self._send_success_notification(user_id, amount_kopeks)
                            logger.info(f"✅ Завершена незавершенная транзакция для пользователя {user_id}")
                        return
                else:
                    logger.info(f"✅ Транзакция с external_id '{external_id}' не найдена, создаем новую")
                
                user = await get_user_by_telegram_id(session, user_id)
                if not user:
                    logger.error(f"❌ Пользователь {user_id} не найден")
                    return
                
                logger.info(f"👤 Найден пользователь {user.telegram_id}, текущий баланс: {user.balance_kopeks} коп")
                
                transaction = await create_transaction(
                    db=session,
                    user_id=user.id, 
                    type=TransactionType.DEPOSIT,
                    amount_kopeks=amount_kopeks,
                    description=f"Пополнение через Tribute: {amount_kopeks/100}₽ (ID: {payment_id})",
                    payment_method=PaymentMethod.TRIBUTE,
                    external_id=external_id, 
                    is_completed=True
                )
                
                old_balance = user.balance_kopeks
                user.balance_kopeks += amount_kopeks
                user.updated_at = datetime.utcnow()
                
                await session.commit()
                
                logger.info(f"💰 Баланс пользователя {user_id} обновлен: {old_balance} -> {user.balance_kopeks} коп (+{amount_kopeks})")
                
                await self._send_success_notification(user_id, amount_kopeks)
                
                logger.info(f"✅ Успешно обработан Tribute платеж: {amount_kopeks/100}₽ для пользователя {user_id}")
                break
                
        except Exception as e:
            logger.error(f"❌ Ошибка обработки успешного Tribute платежа: {e}", exc_info=True)
    
    async def _handle_failed_payment(self, payment_data: Dict[str, Any]):
        """Обработка неудачного платежа"""
        
        try:
            user_id = payment_data["user_id"]
            payment_id = payment_data["payment_id"]
            
            async for session in get_db():
                transaction = await get_transaction_by_external_id(
                    session, f"donation_{payment_id}", PaymentMethod.TRIBUTE
                )
                
                if transaction:
                    transaction.description = f"{transaction.description} (платеж отклонен)"
                    await session.commit()
                
                await self._send_failure_notification(user_id)
                
                logger.info(f"Обработан неудачный Tribute платеж для пользователя {user_id}")
                break
                
        except Exception as e:
            logger.error(f"Ошибка обработки неудачного Tribute платежа: {e}")
    
    async def _handle_refund(self, refund_data: Dict[str, Any]):
        """Обработка возврата"""
        
        try:
            user_id = refund_data["user_id"]
            amount_kopeks = refund_data["amount_kopeks"]
            payment_id = refund_data["payment_id"]
            
            async for session in get_db():
                await create_transaction(
                    db=session,
                    user_id=user_id,
                    type=TransactionType.REFUND,
                    amount_kopeks=-amount_kopeks, 
                    description=f"Возврат Tribute платежа {payment_id}",
                    payment_method=PaymentMethod.TRIBUTE,
                    external_id=f"refund_{payment_id}",
                    is_completed=True
                )
                
                user = await get_user_by_telegram_id(session, user_id)
                if user and user.balance_kopeks >= amount_kopeks:
                    user.balance_kopeks -= amount_kopeks
                    await session.commit()
                
                await self._send_refund_notification(user_id, amount_kopeks)
                
                logger.info(f"Обработан возврат Tribute: {amount_kopeks/100}₽ для пользователя {user_id}")
                break
                
        except Exception as e:
            logger.error(f"Ошибка обработки возврата Tribute: {e}")
    
    async def _send_success_notification(self, user_id: int, amount_kopeks: int):
        """Отправка уведомления об успешном платеже"""
        
        try:
            amount_rubles = amount_kopeks / 100
            
            text = (
                f"✅ **Платеж успешно получен!**\n\n"
                f"💰 Сумма: {amount_rubles:.2f} ₽\n"
                f"💳 Способ оплаты: Tribute\n"
                f"🎉 Средства зачислены на баланс!\n\n"
                f"Спасибо за оплату! 🙏"
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Мой баланс", callback_data="menu_balance")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
            ])
            
            await self.bot.send_message(
                user_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об успешном платеже: {e}")
    
    async def _send_failure_notification(self, user_id: int):
        """Отправка уведомления о неудачном платеже"""
        
        try:
            text = (
                "❌ **Платеж не прошел**\n\n"
                "К сожалению, ваш платеж через Tribute был отклонен.\n\n"
                "Возможные причины:\n"
                "• Недостаточно средств на карте\n"
                "• Технические проблемы банка\n"
                "• Превышен лимит операций\n\n"
                "Попробуйте еще раз или обратитесь в поддержку."
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="menu_balance")],
                [InlineKeyboardButton(text="💬 Поддержка", callback_data="menu_support")]
            ])
            
            await self.bot.send_message(
                user_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о неудачном платеже: {e}")
    
    async def _send_refund_notification(self, user_id: int, amount_kopeks: int):
        """Отправка уведомления о возврате"""
        
        try:
            amount_rubles = amount_kopeks / 100
            
            text = (
                f"🔄 **Возврат средств**\n\n"
                f"💰 Сумма возврата: {amount_rubles:.2f} ₽\n"
                f"💳 Способ: Tribute\n\n"
                f"Средства будут возвращены на вашу карту в течение 3-5 рабочих дней.\n\n"
                f"Если у вас есть вопросы, обратитесь в поддержку."
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💬 Поддержка", callback_data="menu_support")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_menu")]
            ])
            
            await self.bot.send_message(
                user_id,
                text,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о возврате: {e}")
    
    async def force_process_payment(
        self, 
        payment_id: str, 
        user_id: int, 
        amount_kopeks: int,
        description: str = "Принудительная обработка Tribute платежа"
    ) -> bool:
        """Принудительная обработка платежа (для отладки)"""
        
        try:
            logger.info(f"🔧 ПРИНУДИТЕЛЬНАЯ ОБРАБОТКА: payment_id={payment_id}, user_id={user_id}, amount={amount_kopeks}")
            
            async for session in get_db():
                user = await get_user_by_telegram_id(session, user_id)
                if not user:
                    logger.error(f"❌ Пользователь {user_id} не найден")
                    return False
                
                external_id = f"force_donation_{payment_id}_{int(datetime.utcnow().timestamp())}"
                
                transaction = await create_transaction(
                    db=session,
                    user_id=user.id,
                    type=TransactionType.DEPOSIT,
                    amount_kopeks=amount_kopeks,
                    description=description,
                    payment_method=PaymentMethod.TRIBUTE,
                    external_id=external_id,
                    is_completed=True
                )
                
                old_balance = user.balance_kopeks
                user.balance_kopeks += amount_kopeks
                user.updated_at = datetime.utcnow()
                
                await session.commit()
                
                logger.info(f"💰 ПРИНУДИТЕЛЬНО обновлен баланс: {old_balance} -> {user.balance_kopeks} коп")
                
                await self._send_success_notification(user_id, amount_kopeks)
                
                logger.info(f"✅ Принудительно обработан платеж {payment_id}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка принудительной обработки: {e}", exc_info=True)
            return False
    
    async def get_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        return await self.tribute_api.get_payment_status(payment_id)
    
    async def create_refund(
        self,
        payment_id: str,
        amount_kopeks: Optional[int] = None,
        reason: str = "Возврат по запросу"
    ) -> Optional[Dict[str, Any]]:
        return await self.tribute_api.refund_payment(payment_id, amount_kopeks, reason)
