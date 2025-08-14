import logging
import hmac
import hashlib
import json
from typing import Optional

from aiohttp import web
from aiogram import Bot
from sqlalchemy.orm import sessionmaker

from config import Config
from database import Database

logger = logging.getLogger(__name__)

def convert_period_to_months(period: Optional[str]) -> int:
    """Map Tribute subscription period strings to months."""
    if not period:
        return 1

    mapping = {
        "monthly": 1,
        "quarterly": 3,
        "3-month": 3,
        "3months": 3,
        "3-months": 3,
        "q": 3,
        "halfyearly": 6,
        "yearly": 12,
        "annual": 12,
        "y": 12,
    }
    return mapping.get(period.lower(), 1)


class TributeService:
    def __init__(
        self,
        bot: Bot,
        config: Config,
        db: Database,
    ):
        self.bot = bot
        self.config = config
        self.db = db

    async def handle_webhook(self, raw_body: bytes, signature_header: Optional[str]) -> web.Response:
        def ok(data: Optional[dict] = None) -> web.Response:
            payload = {"status": "ok"}
            if data:
                payload.update(data)
            return web.json_response(payload, status=200)

        def ignored(reason: str) -> web.Response:
            return web.json_response({"status": "ignored", "reason": reason}, status=200)

        def bad_request(reason: str) -> web.Response:
            return web.json_response({"status": "error", "reason": reason}, status=400)

        if hasattr(self.config, 'TRIBUTE_API_KEY') and self.config.TRIBUTE_API_KEY:
            if not signature_header:
                return web.json_response({"status": "error", "reason": "no_signature"}, status=403)
            expected_sig = hmac.new(self.config.TRIBUTE_API_KEY.encode(), raw_body,
                                    hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected_sig, signature_header):
                return web.json_response({"status": "error", "reason": "invalid_signature"}, status=403)

        try:
            payload = json.loads(raw_body.decode())
        except Exception:
            return bad_request("invalid_json")

        logging.info(
            "Tribute webhook data: %s",
            json.dumps(payload, ensure_ascii=False),
        )

        event_name = payload.get("name")
        data = payload.get("payload", {})

        user_id = data.get("telegram_user_id")
        if not user_id:
            return ignored("missing_telegram_user_id")

        amount_value = data.get("amount", 0)
        currency = data.get("currency", "RUB").upper()
        amount_float = round(amount_value / 100.0, 2) 

        if event_name == "new_donation": 
            await self._handle_new_donation(user_id, amount_float, currency, data)
        elif event_name == "cancelled_subscription":
            await self._handle_cancellation(user_id)
    
        return ok({"event": event_name or "unknown"})

    async def _handle_new_donation(self, user_id: int, amount: float, currency: str, data: dict):
        try:
            if not user_id:
                logger.warning(f"No telegram_user_id in webhook data")
                return
        
            async with self.db.session_factory() as session:
                payment = await self.db.create_payment(
                    user_id=int(user_id),
                    amount=amount,
                    payment_type='tribute',
                    description=f'Пополнение через Tribute: {amount} {currency}',
                    status='completed'
                )
            
                await self.db.add_balance(int(user_id), amount)
            
                try:
                    success_msg = (
                        f"✅ **Платеж через Tribute получен!**\n\n"
                        f"💰 Сумма: {amount} {currency}\n"
                        f"🎉 Средства зачислены на баланс!\n\n"
                        f"💳 Ваш текущий баланс можно посмотреть в главном меню."
                    )
                
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="💰 Мой баланс", callback_data="balance")],
                        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
                    ])
                
                    await self.bot.send_message(
                        int(user_id),
                        success_msg,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Failed to send Tribute payment success message to user {user_id}: {e}")

                await session.commit()
                logger.info(f"Successfully processed Tribute donation: {amount} {currency} for user {user_id}")
                
        except Exception as e:
            logger.error(f"Error handling tribute donation: {e}")

    async def _handle_cancellation(self, user_id: int):
        try:
            cancellation_msg = (
                "🚨 Ваш платеж Tribute был отменен.\n\n"
                "Если это произошло по ошибке, обратитесь в поддержку."
            )
            
            await self.bot.send_message(
                int(user_id),
                cancellation_msg,
                parse_mode="HTML"
            )
            
            logger.info(f"Tribute subscription cancelled for user {user_id}")
                    
        except Exception as e:
            logger.error(f"Error handling tribute cancellation for user {user_id}: {e}")


async def tribute_webhook_route(request: web.Request):
    tribute_service: TributeService = request.app['tribute_service']
    raw_body = await request.read()
    signature_header = request.headers.get('trbt-signature')
    return await tribute_service.handle_webhook(raw_body, signature_header)
