import logging
import hashlib
import hmac
from typing import Optional
from aiogram import Bot
from aiogram.types import LabeledPrice

from app.config import settings

logger = logging.getLogger(__name__)


class PaymentService:
    
    def __init__(self, bot: Optional[Bot] = None):
        self.bot = bot
    
    async def create_stars_invoice(
        self,
        amount_kopeks: int,
        description: str,
        payload: Optional[str] = None
    ) -> str:
        
        if not self.bot:
            raise ValueError("Bot instance required for Stars payments")
        
        try:
            stars_amount = max(1, amount_kopeks // 100)
            
            invoice_link = await self.bot.create_invoice_link(
                title="Пополнение баланса VPN",
                description=description,
                payload=payload or f"balance_topup_{amount_kopeks}",
                provider_token="", 
                currency="XTR", 
                prices=[LabeledPrice(label="Пополнение", amount=stars_amount)]
            )
            
            logger.info(f"Создан Stars invoice на {stars_amount} звезд")
            return invoice_link
            
        except Exception as e:
            logger.error(f"Ошибка создания Stars invoice: {e}")
            raise
    
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
            # Здесь должна быть логика обработки платежа
            # Например, пополнение баланса пользователя
            
            logger.info(f"Обработан успешный платеж: {payment_id}, {amount_kopeks/100}₽, {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обработки платежа: {e}")
            return False