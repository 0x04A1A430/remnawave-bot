import logging
import hashlib
import hmac
from typing import Optional, Dict, Any
from datetime import datetime
from aiogram import Bot
from aiogram.types import LabeledPrice
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.yookassa_service import YooKassaService
from app.external.telegram_stars import TelegramStarsService
from app.database.crud.yookassa import create_yookassa_payment, link_yookassa_payment_to_transaction
from app.database.crud.transaction import create_transaction
from app.database.crud.user import add_user_balance, get_user_by_id
from app.database.models import TransactionType, PaymentMethod

logger = logging.getLogger(__name__)


class PaymentService:
    
    def __init__(self, bot: Optional[Bot] = None):
        self.bot = bot
        self.yookassa_service = YooKassaService() if settings.is_yookassa_enabled() else None
        self.stars_service = TelegramStarsService(bot) if bot else None
    
    async def create_stars_invoice(
        self,
        amount_kopeks: int,
        description: str,
        payload: Optional[str] = None
    ) -> str:
        
        if not self.bot or not self.stars_service:
            raise ValueError("Bot instance required for Stars payments")
        
        try:
            amount_rubles = amount_kopeks / 100
            stars_amount = TelegramStarsService.calculate_stars_from_rubles(amount_rubles)
            
            invoice_link = await self.bot.create_invoice_link(
                title="Пополнение баланса VPN",
                description=f"{description} (≈{stars_amount} ⭐)",
                payload=payload or f"balance_topup_{amount_kopeks}",
                provider_token="", 
                currency="XTR", 
                prices=[LabeledPrice(label="Пополнение", amount=stars_amount)]
            )
            
            logger.info(f"Создан Stars invoice на {stars_amount} звезд (~{amount_rubles:.2f}₽)")
            return invoice_link
            
        except Exception as e:
            logger.error(f"Ошибка создания Stars invoice: {e}")
            raise
    
    async def process_stars_payment(
        self,
        db: AsyncSession,
        user_id: int,
        stars_amount: int,
        payload: str,
        telegram_payment_charge_id: str
    ) -> bool:
        try:
            rubles_amount = TelegramStarsService.calculate_rubles_from_stars(stars_amount)
            amount_kopeks = int(rubles_amount * 100)
            
            transaction = await create_transaction(
                db=db,
                user_id=user_id,
                type=TransactionType.DEPOSIT,
                amount_kopeks=amount_kopeks,
                description=f"Пополнение через Telegram Stars ({stars_amount} ⭐)",
                payment_method=PaymentMethod.TELEGRAM_STARS,
                external_id=telegram_payment_charge_id,
                is_completed=True
            )
            
            user = await get_user_by_id(db, user_id)
            if user:
                old_balance = user.balance_kopeks
                
                user.balance_kopeks += amount_kopeks
                user.updated_at = datetime.utcnow()
                
                await db.commit()
                await db.refresh(user)
                
                logger.info(f"💰 Баланс пользователя {user.telegram_id} изменен: {old_balance} → {user.balance_kopeks} (изменение: +{amount_kopeks})")
                
                description_for_referral = f"Пополнение Stars: {rubles_amount:.2f}₽ ({stars_amount} ⭐)"
                logger.info(f"🔍 Проверка реферальной логики для описания: '{description_for_referral}'")
                
                if any(word in description_for_referral.lower() for word in ["пополнение", "stars", "yookassa", "topup"]) and not any(word in description_for_referral.lower() for word in ["комиссия", "бонус"]):
                    logger.info(f"🔞 Вызов process_referral_topup для пользователя {user_id}")
                    try:
                        from app.services.referral_service import process_referral_topup
                        await process_referral_topup(db, user_id, amount_kopeks, self.bot)
                    except Exception as e:
                        logger.error(f"Ошибка обработки реферального пополнения: {e}")
                else:
                    logger.info(f"❌ Описание '{description_for_referral}' не подходит для реферальной логики")
                
                if self.bot:
                    try:
                        from app.services.admin_notification_service import AdminNotificationService
                        notification_service = AdminNotificationService(self.bot)
                        await notification_service.send_balance_topup_notification(
                            user, transaction, old_balance
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления о пополнении Stars: {e}")
                
                if self.bot:
                    try:
                        await self.bot.send_message(
                            user.telegram_id,
                            f"✅ <b>Пополнение успешно!</b>\n\n"
                            f"⭐ Звезд: {stars_amount}\n"
                            f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                            f"🦊 Способ: Telegram Stars\n"
                            f"🆔 Транзакция: {telegram_payment_charge_id[:8]}...\n\n"
                            f"Баланс пополнен автоматически!",
                            parse_mode="HTML"
                        )
                        logger.info(f"✅ Отправлено уведомление пользователю {user.telegram_id} о пополнении на {rubles_amount:.2f}₽")
                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления о пополнении Stars: {e}")
                
                logger.info(
                    f"✅ Обработан Stars платеж: пользователь {user_id}, "
                    f"{stars_amount} звезд → {rubles_amount:.2f}₽"
                )
                return True
            else:
                logger.error(f"Пользователь с ID {user_id} не найден при обработке Stars платежа")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка обработки Stars платежа: {e}", exc_info=True)
            return False
    
    async def create_yookassa_payment(
        self,
        db: AsyncSession,
        user_id: int,
        amount_kopeks: int,
        description: str,
        receipt_email: Optional[str] = None,
        receipt_phone: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        
        if not self.yookassa_service:
            logger.error("YooKassa сервис не инициализирован")
            return None
        
        try:
            amount_rubles = amount_kopeks / 100
            
            payment_metadata = metadata or {}
            payment_metadata.update({
                "user_id": str(user_id),
                "amount_kopeks": str(amount_kopeks),
                "type": "balance_topup"
            })
            
            yookassa_response = await self.yookassa_service.create_payment(
                amount=amount_rubles,
                currency="RUB",
                description=description,
                metadata=payment_metadata,
                receipt_email=receipt_email,
                receipt_phone=receipt_phone
            )
            
            if not yookassa_response or yookassa_response.get("error"):
                logger.error(f"Ошибка создания платежа YooKassa: {yookassa_response}")
                return None
            
            yookassa_created_at = None
            if yookassa_response.get("created_at"):
                try:
                    dt_with_tz = datetime.fromisoformat(
                        yookassa_response["created_at"].replace('Z', '+00:00')
                    )
                    yookassa_created_at = dt_with_tz.replace(tzinfo=None)
                except Exception as e:
                    logger.warning(f"Не удалось парсить created_at: {e}")
                    yookassa_created_at = None
            
            local_payment = await create_yookassa_payment(
                db=db,
                user_id=user_id,
                yookassa_payment_id=yookassa_response["id"],
                amount_kopeks=amount_kopeks,
                currency="RUB",
                description=description,
                status=yookassa_response["status"],
                confirmation_url=yookassa_response.get("confirmation_url"),
                metadata_json=payment_metadata,
                payment_method_type=None, 
                yookassa_created_at=yookassa_created_at, 
                test_mode=yookassa_response.get("test_mode", False)
            )
            
            logger.info(f"Создан платеж YooKassa {yookassa_response['id']} на {amount_rubles}₽ для пользователя {user_id}")
            
            return {
                "local_payment_id": local_payment.id,
                "yookassa_payment_id": yookassa_response["id"],
                "confirmation_url": yookassa_response.get("confirmation_url"),
                "amount_kopeks": amount_kopeks,
                "amount_rubles": amount_rubles,
                "status": yookassa_response["status"],
                "created_at": local_payment.created_at
            }
            
        except Exception as e:
            logger.error(f"Ошибка создания платежа YooKassa: {e}")
            return None
    
    async def process_yookassa_webhook(self, db: AsyncSession, webhook_data: dict) -> bool:
        try:
            from app.database.crud.yookassa import (
                get_yookassa_payment_by_id, 
                update_yookassa_payment_status,
                link_yookassa_payment_to_transaction
            )
            from app.database.crud.transaction import create_transaction
            from app.database.models import TransactionType, PaymentMethod
            
            payment_object = webhook_data.get("object", {})
            yookassa_payment_id = payment_object.get("id")
            status = payment_object.get("status")
            paid = payment_object.get("paid", False)
            
            if not yookassa_payment_id:
                logger.error("Webhook без ID платежа")
                return False
            
            payment = await get_yookassa_payment_by_id(db, yookassa_payment_id)
            if not payment:
                logger.error(f"Платеж не найден в БД: {yookassa_payment_id}")
                return False
            
            captured_at = None
            if status == "succeeded":
                captured_at = datetime.utcnow() 
            
            updated_payment = await update_yookassa_payment_status(
                db, 
                yookassa_payment_id, 
                status, 
                is_paid=paid,
                is_captured=(status == "succeeded"),
                captured_at=captured_at,
                payment_method_type=payment_object.get("payment_method", {}).get("type")
            )
            
            if status == "succeeded" and paid and not updated_payment.transaction_id:
                transaction = await create_transaction(
                    db,
                    user_id=updated_payment.user_id,
                    type=TransactionType.DEPOSIT, 
                    amount_kopeks=updated_payment.amount_kopeks,
                    description=f"Пополнение через YooKassa ({yookassa_payment_id[:8]}...)",
                    payment_method=PaymentMethod.YOOKASSA,
                    external_id=yookassa_payment_id,
                    is_completed=True
                )
                
                await link_yookassa_payment_to_transaction(
                    db, yookassa_payment_id, transaction.id
                )
                
                user = await get_user_by_id(db, updated_payment.user_id)
                if user:
                    old_balance = user.balance_kopeks
                    
                    user.balance_kopeks += updated_payment.amount_kopeks
                    user.updated_at = datetime.utcnow()
                    
                    await db.commit()
                    await db.refresh(user)
                    
                    try:
                        from app.services.referral_service import process_referral_topup
                        await process_referral_topup(db, user.id, updated_payment.amount_kopeks, self.bot)
                    except Exception as e:
                        logger.error(f"Ошибка обработки реферального пополнения YooKassa: {e}")
                    
                    if self.bot:
                        try:
                            from app.services.admin_notification_service import AdminNotificationService
                            notification_service = AdminNotificationService(self.bot)
                            await notification_service.send_balance_topup_notification(
                                user, transaction, old_balance
                            )
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления о пополнении YooKassa: {e}")
                    
                    if self.bot:
                        try:
                            await self.bot.send_message(
                                user.telegram_id,
                                f"✅ <b>Пополнение успешно!</b>\n\n"
                                f"💰 Сумма: {settings.format_price(updated_payment.amount_kopeks)}\n"
                                f"🦊 Способ: Банковская карта\n"
                                f"🆔 Транзакция: {yookassa_payment_id[:8]}...\n\n"
                                f"Баланс пополнен автоматически!",
                                parse_mode="HTML"
                            )
                            logger.info(f"✅ Отправлено уведомление пользователю {user.telegram_id} о пополнении на {updated_payment.amount_kopeks/100:.2f}₽")
                        except Exception as e:
                            logger.error(f"Ошибка отправки уведомления о пополнении: {e}")
                else:
                    logger.error(f"Пользователь с ID {updated_payment.user_id} не найден при пополнении баланса")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки YooKassa webhook: {e}", exc_info=True)
            return False
    
    async def _process_successful_yookassa_payment(
        self,
        db: AsyncSession,
        payment: "YooKassaPayment"
    ) -> bool:
        
        try:
            transaction = await create_transaction(
                db=db,
                user_id=payment.user_id,
                transaction_type=TransactionType.DEPOSIT,
                amount_kopeks=payment.amount_kopeks,
                description=f"Пополнение через YooKassa: {payment.description}",
                payment_method=PaymentMethod.YOOKASSA,
                external_id=payment.yookassa_payment_id,
                is_completed=True
            )
            
            await link_yookassa_payment_to_transaction(
                db=db,
                yookassa_payment_id=payment.yookassa_payment_id,
                transaction_id=transaction.id
            )
            
            user = await get_user_by_id(db, payment.user_id)
            if user:
                await add_user_balance(db, user, payment.amount_kopeks, f"Пополнение YooKassa: {payment.amount_kopeks/100:.2f}₽")
            
            logger.info(f"Успешно обработан платеж YooKassa {payment.yookassa_payment_id}: "
                       f"пользователь {payment.user_id} получил {payment.amount_kopeks/100}₽")
            
            if self.bot and user:
                try:
                    await self._send_payment_success_notification(
                        user.telegram_id, 
                        payment.amount_kopeks
                    )
                except Exception as e:
                    logger.error(f"Ошибка отправки уведомления о платеже: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки успешного платежа YooKassa {payment.yookassa_payment_id}: {e}")
            return False
    
    async def _send_payment_success_notification(
        self,
        telegram_id: int,
        amount_kopeks: int
    ) -> None:
        
        if not self.bot:
            return
        
        try:
            message = (f"✅ <b>Платеж успешно завершен!</b>\n\n"
                      f"💰 Сумма: {settings.format_price(amount_kopeks)}\n"
                      f"💳 Способ: Банковская карта (YooKassa)\n\n"
                      f"Средства зачислены на ваш баланс!")
            
            await self.bot.send_message(
                chat_id=telegram_id,
                text=message,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {telegram_id}: {e}")
    
    async def create_tribute_payment(
        self,
        amount_kopeks: int,
        user_id: int,
        description: str
    ) -> str:
        
        if not settings.TRIBUTE_ENABLED:
            raise ValueError("Tribute payments are disabled")
        
        try:
            payment_data = {
                "amount": amount_kopeks,
                "currency": "RUB",
                "description": description,
                "user_id": user_id,
                "callback_url": f"{settings.WEBHOOK_URL}/tribute/callback"
            }
            
            payment_url = f"https://tribute.ru/pay?amount={amount_kopeks}&user={user_id}"
            
            logger.info(f"Создан Tribute платеж на {amount_kopeks/100}₽ для пользователя {user_id}")
            return payment_url
            
        except Exception as e:
            logger.error(f"Ошибка создания Tribute платежа: {e}")
            raise
    
    def verify_tribute_webhook(
        self,
        data: dict,
        signature: str
    ) -> bool:
        
        if not settings.TRIBUTE_WEBHOOK_SECRET:
            return False
        
        try:
            message = str(data).encode()
            expected_signature = hmac.new(
                settings.TRIBUTE_WEBHOOK_SECRET.encode(),
                message,
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            logger.error(f"Ошибка проверки Tribute webhook: {e}")
            return False
    
    async def process_successful_payment(
        self,
        payment_id: str,
        amount_kopeks: int,
        user_id: int,
        payment_method: str
    ) -> bool:
        
        try:
            logger.info(f"Обработан успешный платеж: {payment_id}, {amount_kopeks/100}₽, {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки платежа: {e}")
            return False
