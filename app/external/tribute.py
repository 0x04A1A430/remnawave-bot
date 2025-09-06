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

        if not self.api_key:
            logger.warning("API key не настроен, пропускаем проверку")
            return True

        try:
            expected_signature = hmac.new(
                self.api_key.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()

            is_valid = hmac.compare_digest(signature, expected_signature)

            if is_valid:
                logger.info("✅ Подпись Tribute webhook проверена успешно")
            else:
                logger.error("❌ Неверная подпись Tribute webhook")

            return is_valid

        except Exception as e:
            logger.error(f"Ошибка проверки подписи webhook: {e}")
            return False
    
    async def process_webhook(self, payload_or_data) -> Optional[Dict[str, Any]]:
        
        try:
            logger.info(f"🔄 Начинаем обработку Tribute webhook")
            
            if isinstance(payload_or_data, str):
                try:
                    webhook_data = json.loads(payload_or_data)
                    logger.info(f"📊 Распарсенные данные: {webhook_data}")
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Ошибка парсинга JSON: {e}")
                    return None
            else:
                webhook_data = payload_or_data
            
            payment_id = None
            status = None
            amount_kopeks = 0
            telegram_user_id = None
            
            payment_id = webhook_data.get("id") or webhook_data.get("payment_id")
            status = webhook_data.get("status")
            amount_kopeks = webhook_data.get("amount", 0) 
            telegram_user_id = webhook_data.get("telegram_user_id") or webhook_data.get("user_id")
            
            if not payment_id and "payload" in webhook_data:
                data = webhook_data["payload"]
                payment_id = data.get("id") or data.get("payment_id")
                status = data.get("status")
                amount_kopeks = data.get("amount", 0) 
                telegram_user_id = data.get("telegram_user_id") or data.get("user_id")
            
            if not payment_id and "name" in webhook_data:
                event_name = webhook_data.get("name")
                data = webhook_data.get("payload", {})
                payment_id = str(data.get("donation_request_id")) 
                amount_kopeks = data.get("amount", 0) 
                telegram_user_id = data.get("telegram_user_id")
                
                if event_name == "new_donation":
                    status = "paid"
                elif event_name == "cancelled_subscription":
                    status = "cancelled"
                else:
                    status = "unknown"
            
            logger.info(f"📝 Извлеченные данные: payment_id={payment_id}, status={status}, amount_kopeks={amount_kopeks}, user_id={telegram_user_id}")
            
            if not telegram_user_id:
                logger.error("❌ Не найден telegram_user_id в webhook данных")
                logger.error(f"🔍 Полные данные для отладки: {json.dumps(webhook_data, ensure_ascii=False, indent=2)}")
                return None
            
            try:
                telegram_user_id = int(telegram_user_id)
            except (ValueError, TypeError):
                logger.error(f"❌ Некорректный telegram_user_id: {telegram_user_id}")
                return None
            
            result = {
                "event_type": "payment",
                "payment_id": payment_id or f"tribute_{telegram_user_id}_{amount_kopeks}",
                "user_id": telegram_user_id,
                "amount_kopeks": int(amount_kopeks) if amount_kopeks else 0,
                "status": status or "paid",
                "external_id": f"donation_{payment_id or 'unknown'}",
                "payment_system": "tribute"
            }
            
            logger.info(f"✅ Tribute webhook обработан успешно: {result}")
            return result
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки Tribute webhook: {e}", exc_info=True)
            logger.error(f"🔍 Webhook data для отладки: {json.dumps(webhook_data, ensure_ascii=False, indent=2)}")
            return None
    
    async def get_payment_status(self, payment_id: str) -> Optional[Dict[str, Any]]:
        try:
            logger.info(f"Запрос статуса платежа {payment_id}")
            return {"status": "unknown", "payment_id": payment_id}
        except Exception as e:
            logger.error(f"Ошибка получения статуса платежа: {e}")
            return None
    
    async def refund_payment(
        self,
        payment_id: str,
        amount_kopeks: Optional[int] = None,
        reason: str = "Возврат по запросу"
    ) -> Optional[Dict[str, Any]]:
        try:
            logger.info(f"Создание возврата для платежа {payment_id}")
            return {"refund_id": f"refund_{payment_id}", "status": "pending"}
        except Exception as e:
            logger.error(f"Ошибка создания возврата: {e}")
            return None
