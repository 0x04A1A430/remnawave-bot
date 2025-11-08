import asyncio
import json
import logging
from typing import Any, Dict

from aiohttp import web

from app.config import settings
from app.database.database import get_db
from app.services.payment_service import PaymentService

logger = logging.getLogger(__name__)


YOOKASSA_ALLOWED_EVENTS: tuple[str, ...] = (
    "payment.succeeded",
    "payment.waiting_for_capture",
    "payment.canceled",
)


class YooKassaWebhookHandler:
    
    def __init__(self, payment_service: PaymentService):
        self.payment_service = payment_service
    
    async def handle_webhook(self, request: web.Request) -> web.Response:

        try:
            logger.info(f"📥 Получен YooKassa webhook: {request.method} {request.path}")
            logger.info(f"📋 Headers: {dict(request.headers)}")

            body = await request.text()

            if not body:
                logger.warning("⚠️ Получен пустой webhook от YooKassa")
                return web.Response(status=400, text="Empty body")

            logger.info(f"📄 Body: {body}")

            signature = request.headers.get('Signature') or request.headers.get('X-YooKassa-Signature')
            if signature:
                logger.info("ℹ️ Получена подпись YooKassa: %s", signature)

            try:
                webhook_data = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON webhook YooKassa: {e}")
                return web.Response(status=400, text="Invalid JSON")
            
            logger.info(f"📊 Обработка webhook YooKassa: {webhook_data.get('event', 'unknown_event')}")
            logger.debug(f"🔍 Полные данные webhook: {webhook_data}")
            
            event_type = webhook_data.get("event")
            if not event_type:
                logger.warning("⚠️ Webhook YooKassa без типа события")
                return web.Response(status=400, text="No event type")
            
            if event_type not in YOOKASSA_ALLOWED_EVENTS:
                logger.info(f"ℹ️ Игнорируем событие YooKassa: {event_type}")
                return web.Response(status=200, text="OK")
            
            async for db in get_db():
                try:
                    success = await self.payment_service.process_yookassa_webhook(db, webhook_data)
                    
                    if success:
                        logger.info(f"✅ Успешно обработан webhook YooKassa: {event_type}")
                        return web.Response(status=200, text="OK")
                    else:
                        logger.error(f"❌ Ошибка обработки webhook YooKassa: {event_type}")
                        return web.Response(status=500, text="Processing error")
                        
                finally:
                    await db.close()
        
        except Exception as e:
            logger.error(f"❌ Критическая ошибка обработки webhook YooKassa: {e}", exc_info=True)
            return web.Response(status=500, text="Internal server error")
    
    def setup_routes(self, app: web.Application) -> None:
        
        webhook_path = settings.YOOKASSA_WEBHOOK_PATH
        app.router.add_post(webhook_path, self.handle_webhook)
        app.router.add_get(webhook_path, self._get_handler) 
        app.router.add_options(webhook_path, self._options_handler) 
        
        logger.info(f"✅ Настроен YooKassa webhook на пути: POST {webhook_path}")
    
    async def _get_handler(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "message": "YooKassa webhook endpoint is working",
            "method": "GET",
            "path": request.path,
            "note": "Use POST method for actual webhooks"
        })
    
    async def _options_handler(self, request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, X-YooKassa-Signature',
            }
        )


def create_yookassa_webhook_app(payment_service: PaymentService) -> web.Application:
    
    app = web.Application()
    
    webhook_handler = YooKassaWebhookHandler(payment_service)
    webhook_handler.setup_routes(app)
    
    async def health_check(request):
        return web.json_response({
            "status": "ok", 
            "service": "yookassa_webhook",
            "port": settings.YOOKASSA_WEBHOOK_PORT,
            "path": settings.YOOKASSA_WEBHOOK_PATH,
            "enabled": settings.is_yookassa_enabled()
        })
    
    app.router.add_get("/health", health_check)
    
    return app


async def start_yookassa_webhook_server(payment_service: PaymentService) -> None:
    
    if not settings.is_yookassa_enabled():
        logger.info("ℹ️ YooKassa отключена, webhook сервер не запускается")
        return
    
    try:
        app = create_yookassa_webhook_app(payment_service)
        
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(
            runner,
            host=settings.YOOKASSA_WEBHOOK_HOST,
            port=settings.YOOKASSA_WEBHOOK_PORT
        )
        
        await site.start()
        
        logger.info(
            "✅ YooKassa webhook сервер запущен на %s:%s",
            settings.YOOKASSA_WEBHOOK_HOST,
            settings.YOOKASSA_WEBHOOK_PORT,
        )
        logger.info(
            "🎯 YooKassa webhook URL: http://%s:%s%s",
            settings.YOOKASSA_WEBHOOK_HOST,
            settings.YOOKASSA_WEBHOOK_PORT,
            settings.YOOKASSA_WEBHOOK_PATH,
        )
        
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("🛑 YooKassa webhook сервер получил сигнал остановки")
        finally:
            await site.stop()
            await runner.cleanup()
            logger.info("✅ YooKassa webhook сервер остановлен")
        
    except Exception as e:
        logger.error(f"❌ Ошибка запуска YooKassa webhook сервера: {e}", exc_info=True)
        raise
