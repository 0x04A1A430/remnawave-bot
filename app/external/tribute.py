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
            payment_url = f"{self.donate_link}?user_id={user_id}"
            
            if amount_kopeks > 0:
                amount_rubles = amount_kopeks / 100
                payment_url += f"&amount={amount_rubles:.2f}"
            
            if description:
                payment_url += f"&description={description}"
            
            logger.info(f"Создана ссылка Tribute для пользователя {user_id}: {amount_kopeks} коп.")
            return payment_url
            
        except Exception as e:
            logger.error(f"Ошибка создания Tribute ссылки: {e}")
            return None
    
    def verify_webhook_signature(self, payload: str, signature: str) -> bool:
        """Проверяет подпись webhook"""
        
        if not self.webhook_secret:
            logger.warning("Webhook secret не настроен, пропускаем проверку подписи")
            return True 
        
        try:
            if signature.startswith('sha256='):
                signature = signature[7:]
            
            expected_signature = hmac.new(
                self.webhook_secret.encode('utf-8'),
                payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            result = hmac.compare_digest(signature, expected_signature)
            
            if not result:
                logger.warning(f"Неверная подпись webhook. Получено: {signature[:10]}..., ожидалось: {expected_signature[:10]}...")
            
            return result
            
        except Exception as e:
            logger.error(f"Ошибка проверки подписи webhook: {e}")
            return False
    
    async def process_webhook(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        
        try:
            logger.debug(f"Обработка Tribute webhook: {json.dumps(payload, ensure_ascii=False, indent=2)}")
            
            payment_id = None
            status = None
            amount_kopeks = 0
            telegram_user_id = None
            
            
            payment_id = payload.get("id") or payload.get("payment_id") or payload.get("donation_id")
            status = payload.get("status")
            amount_kopeks = payload.get("amount", 0)
            telegram_user_id = payload.get("telegram_user_id") or payload.get("user_id")
            
            if not payment_id and "payload" in payload:
                data = payload["payload"]
                payment_id = data.get("id") or data.get("payment_id") or data.get("donation_id")
                status = data.get("status")
                amount_kopeks = data.get("amount", 0)
                telegram_user_id = data.get("telegram_user_id") or data.get("user_id")
            
            if not payment_id and "name" in payload:
                event_name = payload.get("name")
                data = payload.get("payload", {})
                
                payment_id = (
                    data.get("donation_request_id") or 
                    data.get("donation_id") or 
                    data.get("id") or
                    data.get("payment_id")
                )
                
                amount_kopeks = data.get("amount", 0)
                telegram_user_id = data.get("telegram_user_id") or data.get("user_id")
                
                if event_name == "new_donation":
                    status = "paid"
                elif event_name == "donation_completed":
                    status = "completed" 
                elif event_name == "cancelled_subscription":
                    status = "cancelled"
                else:
                    status = "unknown"
                    logger.warning(f"Неизвестное событие Tribute: {event_name}")
            
            if not payment_id and "data" in payload:
                data = payload["data"]
                payment_id = data.get("id") or data.get("donation_id")
                status = data.get("status", "paid")
                amount_kopeks = data.get("amount", 0)
                telegram_user_id = data.get("telegram_user_id") or data.get("user_id")
            
            if isinstance(amount_kopeks, (int, float)):
                if amount_kopeks > 1000:
                    amount_kopeks = int(amount_kopeks)
                else:
                    amount_kopeks = int(amount_kopeks * 100)
            else:
                amount_kopeks = 0
            
            logger.info(f"Извлеченные данные Tribute webhook:")
            logger.info(f"  - payment_id: {payment_id}")
            logger.info(f"  - status: {status}")
            logger.info(f"  - amount_kopeks: {amount_kopeks}")
            logger.info(f"  - telegram_user_id: {telegram_user_id}")
            
            if not telegram_user_id:
                logger.error("Не найден telegram_user_id в webhook данных")
                logger.error(f"Полные данные webhook: {json.dumps(payload, ensure_ascii=False)}")
                return None
            
            if amount_kopeks <= 0:
                logger.error(f"Неверная сумма платежа: {amount_kopeks}")
                return None
            
            if not payment_id:
                import time
                payment_id = f"tribute_{telegram_user_id}_{int(time.time())}"
                logger.info(f"Сгенерирован payment_id: {payment_id}")
            else:
                payment_id = str(payment_id)
            
            if not status:
                status = "paid"
            
            result = {
                "event_type": "payment",
                "payment_id": payment_id,
                "user_id": int(telegram_user_id),
                "amount_kopeks": amount_kopeks,
                "status": status,
                "external_id": f"tribute_{payment_id}",
                "provider": "tribute"
            }
            
            logger.info(f"✅ Успешно обработан Tribute webhook: {result}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки Tribute webhook: {e}", exc_info=True)
            logger.error(f"Webhook payload: {json.dumps(payload, ensure_ascii=False)}")
            return None
