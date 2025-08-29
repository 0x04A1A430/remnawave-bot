import logging
import json
import hashlib
import hmac
from typing import Optional, Dict, Any
from aiohttp import web
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.payment_service import PaymentService
from app.database.database import get_db

logger = logging.getLogger(__name__)


class YooKassaWebhookHandler:

    @staticmethod
    def verify_webhook_signature(body: str, signature: str, secret: str) -> bool:
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected_signature)
    
    def __init__(self, payment_service: PaymentService):
        self.payment_service = payment_service
    
    async def handle_webhook(self, request: web.Request) -> web.Response:
        
        try:
            body = await request.text()
            
            if not body:
                logger.warning("Получен пустой webhook от YooKassa")
                return web.Response(status=400, text="Empty body")
            
            if hasattr(settings, 'YOOKASSA_WEBHOOK_SECRET') and settings.YOOKASSA_WEBHOOK_SECRET:
                signature = request.headers.get('X-YooKassa-Signature')
                if not signature:
                    logger.warning("Webhook без подписи")
                    return web.Response(status=400, text="Missing signature")
                
                if not YooKassaWebhookHandler.verify_webhook_signature(body, signature, settings.YOOKASSA_WEBHOOK_SECRET):
                    logger.error("Неверная подпись webhook")
                    return web.Response(status=400, text="Invalid signature")
            
            try:
                webhook_data = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON webhook YooKassa: {e}")
                return web.Response(status=400, text="Invalid JSON")
            
            logger.info(f"Получен webhook YooKassa: {webhook_data.get('event', 'unknown_event')}")
            logger.debug(f"Полные данные webhook: {webhook_data}")
            
            event_type = webhook_data.get("event")
            if not event_type:
                logger.warning("Webhook YooKassa без типа события")
                return web.Response(status=400, text="No event type")
            
            if event_type not in ["payment.succeeded", "payment.waiting_for_capture"]:
                logger.info(f"Игнорируем событие YooKassa: {event_type}")
                return web.Response(status=200, text="OK")
            
            async with get_db() as db:
                success = await self.payment_service.process_yookassa_webhook(db, webhook_data)
                
                if success:
                    logger.info(f"Успешно обработан webhook YooKassa: {event_type}")
                    return web.Response(status=200, text="OK")
                else:
                    logger.error(f"Ошибка обработки webhook YooKassa: {event_type}")
                    return web.Response(status=500, text="Processing error")
        
        except Exception as e:
            logger.error(f"Критическая ошибка обработки webhook YooKassa: {e}", exc_info=True)
            return web.Response(status=500, text="Internal server error")
    
    def setup_routes(self, app: web.Application) -> None:
        
        webhook_path = settings.YOOKASSA_WEBHOOK_PATH
        app.router.add_post(webhook_path, self.handle_webhook)
        
        logger.info(f"Настроен webhook YooKassa на пути: {webhook_path}")


def create_yookassa_webhook_app(payment_service: PaymentService) -> web.Application:
    
    app = web.Application()
    
    # Middleware для логирования
    async def logging_middleware(request, handler):
        start_time = request.loop.time()
        
        try:
            response = await handler(request)
            process_time = request.loop.time() - start_time
            
            logger.info(f"YooKassa webhook {request.method} {request.path_qs} "
                       f"-> {response.status} ({process_time:.3f}s)")
            
            return response
        
        except Exception as e:
            process_time = request.loop.time() - start_time
            logger.error(f"YooKassa webhook {request.method} {request.path_qs} "
                        f"-> ERROR ({process_time:.3f}s): {e}")
            raise
    
    app.middlewares.append(logging_middleware)
    
    webhook_handler = YooKassaWebhookHandler(payment_service)
    webhook_handler.setup_routes(app)
    
    async def health_check(request):
        return web.json_response({"status": "ok", "service": "yookassa_webhook"})
    
    app.router.add_get("/health", health_check)
    
    return app


async def start_yookassa_webhook_server(payment_service: PaymentService) -> None:
    
    if not settings.is_yookassa_enabled():
        logger.info("YooKassa отключен, webhook сервер не запускается")
        return
    
    try:
        from aiohttp import web
        
        app = create_yookassa_webhook_app(payment_service)
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(
            runner, 
            host='0.0.0.0', 
            port=settings.YOOKASSA_WEBHOOK_PORT
        )
        
        await site.start()
        
        logger.info(f"YooKassa webhook сервер запущен на порту {settings.YOOKASSA_WEBHOOK_PORT}")
        logger.info(f"Webhook URL: http://localhost:{settings.YOOKASSA_WEBHOOK_PORT}{settings.YOOKASSA_WEBHOOK_PATH}")
        
    except Exception as e:
        logger.error(f"Ошибка запуска YooKassa webhook сервера: {e}", exc_info=True)
        raise