import logging
from typing import Optional, Dict, Any
from aiogram import Bot
from aiogram.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings

logger = logging.getLogger(__name__)


class TelegramStarsService:
    
    def __init__(self, bot: Bot):
        self.bot = bot
    
    async def create_invoice(
        self,
        chat_id: int,
        title: str,
        description: str,
        amount_kopeks: int,
        payload: str,
        start_parameter: Optional[str] = None
    ) -> Optional[str]:
        try:
            stars_amount = max(1, amount_kopeks // 100)
            
            invoice_link = await self.bot.create_invoice_link(
                title=title,
                description=description,
                payload=payload,
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=title, amount=stars_amount)],
                start_parameter=start_parameter
            )
            
            logger.info(f"Создан Stars invoice на {stars_amount} звезд для {chat_id}")
            return invoice_link
            
        except Exception as e:
            logger.error(f"Ошибка создания Stars invoice: {e}")
            return None
    
    async def send_invoice(
        self,
        chat_id: int,
        title: str,
        description: str,
        amount_kopeks: int,
        payload: str,
        keyboard: Optional[InlineKeyboardMarkup] = None
    ) -> Optional[Dict[str, Any]]:
        try:
            stars_amount = max(1, amount_kopeks // 100)
            
            message = await self.bot.send_invoice(
                chat_id=chat_id,
                title=title,
                description=description,
                payload=payload,
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=title, amount=stars_amount)],
                reply_markup=keyboard
            )
            
            logger.info(f"Отправлен Stars invoice {message.message_id} на {stars_amount} звезд")
            return {
                "message_id": message.message_id,
                "stars_amount": stars_amount,
                "payload": payload
            }
            
        except Exception as e:
            logger.error(f"Ошибка отправки Stars invoice: {e}")
            return None
    
    async def answer_pre_checkout_query(
        self,
        pre_checkout_query_id: str,
        ok: bool = True,
        error_message: Optional[str] = None
    ) -> bool:
        try:
            await self.bot.answer_pre_checkout_query(
                pre_checkout_query_id=pre_checkout_query_id,
                ok=ok,
                error_message=error_message
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка ответа на pre_checkout_query: {e}")
            return False
    
    def calculate_stars_amount(self, rubles: float) -> int:
        return max(1, int(rubles))
    
    def calculate_rubles_from_stars(self, stars: int) -> float:
        return float(stars)