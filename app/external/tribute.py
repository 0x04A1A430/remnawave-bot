import logging
import hashlib
import hmac
import json
from typing import Optional, Dict, Any

from app.config import settings

logger = logging.getLogger(__name__)


class TributeService:
    
    def __init__(self):
        self.api_key = settings.TRIBUTE_API_KEY
        self.webhook_secret = settings.TRIBUTE_WEBHOOK_SECRET
        self.donate_link = settings.TRIBUTE_DONATE_LINK
    
    async def create_payment_link(
        self,
        user_id: int,
        amount_kopeks: int = 0,
        description: str = "Пополнение баланса"
    ) -> Optional[str]:
        
        if not settings.TRIBUTE_ENABLED:
            logger.warning("Tribute платежи отключены")
            return None
        
        try:
            
            payment_url = f"{self.donate_link}&user_id={user_id}"
            
            logger.info(f"Создана ссылка Tribute для пользователя {user_id}")
            return payment_url
            
        except Exception as e:
            logger.error(f"Ошибка создания Tribute ссылки: {e}")
            return None
    
    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        
        if not self.webhook_secret:
            logger.warning("Webhook secret не настроен")
            return True 
        
        try:
            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()
            
            return hmac.compare_digest(signature, expected_signature)
            
        except Exception as e:
            logger.error(f"Ошибка проверки подписи webhook: {e}")
            return False
    
    async def process_webhook(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Обрабатывает webhook от Tribute"""
        
        try:
            if payload.get("test_event"):
                logger.info("Получено тестовое событие Tribute")
                return {
                    "event_type": "test",
                    "payment_id": "test_event",
                    "user_id": 0,
                    "amount_kopeks": 0,
                    "status": "test",
                    "external_id": "test"
                }
            
            payment_id = None
            status = None
            amount_kopeks = 0 
            amount_rubles = 0
            telegram_user_id = None
            
            payment_id = payload.get("id") or payload.get("payment_id")
            status = payload.get("status")
            amount_rubles = payload.get("amount", 0)
            telegram_user_id = payload.get("telegram_user_id") or payload.get("user_id")
            
            if amount_rubles:
                amount_kopeks = int(amount_rubles * 100)
            
            if not payment_id and "payload" in payload:
                data = payload["payload"]
                payment_id = data.get("id") or data.get("payment_id")
                status = data.get("status")
                amount_rubles = data.get("amount", 0)
                amount_kopeks = data.get("amount_kopeks", int(amount_rubles * 100))
                telegram_user_id = data.get("telegram_user_id") or data.get("user_id")
            
            if not payment_id and "name" in payload:
                event_name = payload.get("name")
                data = payload.get("payload", {})
                payment_id = str(data.get("donation_request_id", ""))
                
                amount_kopeks = data.get("amount_kopeks", 0)
                if not amount_kopeks:
                    amount_rubles = data.get("amount", 0)
                    amount_kopeks = int(amount_rubles * 100)
                    
                telegram_user_id = data.get("telegram_user_id")
                
                if event_name == "new_donation":
                    status = "paid"
                elif event_name == "cancelled_subscription":
                    status = "cancelled"
                else:
                    status = "unknown"
            
            logger.info(f"Обработка Tribute webhook: payment_id={payment_id}, status={status}, amount_kopeks={amount_kopeks}, user_id={telegram_user_id}")
            
            if not telegram_user_id:
                logger.error("Не найден telegram_user_id в webhook данных")
                return None
            
            return {
                "event_type": "payment",
                "payment_id": payment_id or f"tribute_{telegram_user_id}_{amount_kopeks}",
                "user_id": int(telegram_user_id),
                "amount_kopeks": amount_kopeks,
                "status": status or "paid",
                "external_id": f"donation_{payment_id}" if payment_id else f"tribute_{telegram_user_id}"
            }
            
        except Exception as e:
            logger.error(f"Ошибка обработки Tribute webhook: {e}")
            logger.error(f"Webhook payload: {json.dumps(payload, ensure_ascii=False)}")
            return None
