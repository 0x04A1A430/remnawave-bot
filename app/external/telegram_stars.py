import logging
from typing import Optional, Dict, Any
from aiogram import Bot
from aiogram.types import LabeledPrice, InlineKeyboardMarkup, InlineKeyboardButton

from app.config import settings

logger = logging.getLogger(__name__)


class TelegramStarsService:
    
    def __init__(self, bot: Bot):
        self.bot = bot
    
    @staticmethod
    def calculate_stars_from_rubles(rubles: float) -> int:
        return settings.rubles_to_stars(rubles)
    
    @staticmethod
    def calculate_rubles_from_stars(stars: int) -> float:
        return settings.stars_to_rubles(stars)
    
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
            amount_rubles = amount_kopeks / 100
            stars_amount = self.calculate_stars_from_rubles(amount_rubles)
            
            invoice_link = await self.bot.create_invoice_link(
                title=title,
                description=description,
                payload=payload,
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label=title, amount=stars_amount)],
                start_parameter=start_parameter
            )
            
            logger.info(
                f"Создан Stars invoice на {stars_amount} звезд (~{amount_rubles:.2f}₽) "
                f"для {chat_id}, курс: {settings.get_stars_rate():.2f}₽/⭐"
            )
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
            amount_rubles = amount_kopeks / 100
            stars_amount = self.calculate_stars_from_rubles(amount_rubles)
            
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
            
            logger.info(
                f"Отправлен Stars invoice {message.message_id} на {stars_amount} звезд "
                f"(~{amount_rubles:.2f}₽), курс: {settings.get_stars_rate():.2f}₽/⭐"
            )
            return {
                "message_id": message.message_id,
                "stars_amount": stars_amount,
                "rubles_amount": amount_rubles,
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
            logger.info(f"Ответ на pre_checkout_query: ok={ok}")
            return True
        except Exception as e:
            logger.error(f"Ошибка ответа на pre_checkout_query: {e}")
            return False
