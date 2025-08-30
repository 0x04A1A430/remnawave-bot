import logging
import asyncio
from typing import Optional, List
from aiohttp import web
from aiogram import Bot

from app.config import settings
from app.external.tribute import TributeService
from app.services.payment_service import PaymentService
from app.external.yookassa_webhook import YooKassaWebhookHandler

logger = logging.getLogger(__name__)


class WebhookServer:
    
    def __init__(self, bot: Bot, payment_service: PaymentService):
        self.bot = bot
        self.payment_service = payment_service
        self.tribute_service = TributeService()
        self.yookassa_handler = YooKassaWebhookHandler(payment_service)
        
        self.tribute_app = None
        self.tribute_runner = None
        self.tribute_site = None
        
        self.yookassa_app = None
        self.yookassa_runner = None
        self.yookassa_site = None
    
    def _create_logging_middleware(self):
        @web.middleware
        async def logging_middleware(request, handler):
            start_time = request.loop.time()
            
            try:
                response = await handler(request)
                process_time = request.loop.time() - start_time
                
                logger.info(f"{request.method} {request.path_qs} -> {response.status} ({process_time:.3f}s)")
                
                return response
            
            except Exception as e:
                process_time = request.loop.time() - start_time
                logger.error(f"{request.method} {request.path_qs} -> ERROR ({process_time:.3f}s): {e}")
                raise
        
        return logging_middleware
    
    async def create_tribute_app(self) -> web.Application:
        self.tribute_app = web.Application()
        
        self.tribute_app.middlewares.append(self._create_logging_middleware())
        
        self.tribute_app.router.add_post(settings.TRIBUTE_WEBHOOK_PATH, self._tribute_webhook_handler)
        self.tribute_app.router.add_get('/health', self._tribute_health_check)
        
        logger.info(f"Tribute webhook настроен:")
        logger.info(f"  - Webhook path: {settings.TRIBUTE_WEBHOOK_PATH}")
        logger.info(f"  - Port: {settings.TRIBUTE_WEBHOOK_PORT}")
        
        return self.tribute_app
    
    async def create_yookassa_app(self) -> web.Application:
        self.yookassa_app = web.Application()
        
        self.yookassa_app.middlewares.append(self._create_logging_middleware())
        
        self.yookassa_app.router.add_post(settings.YOOKASSA_WEBHOOK_PATH, self.yookassa_handler.handle_webhook)
        self.yookassa_app.router.add_get('/health', self._yookassa_health_check)
        
        logger.info(f"YooKassa webhook настроен:")
        logger.info(f"  - Webhook path: {settings.YOOKASSA_WEBHOOK_PATH}")
        logger.info(f"  - Port: {settings.YOOKASSA_WEBHOOK_PORT}")
        
        return self.yookassa_app
    
    async def start_tribute_server(self):
        if not settings.TRIBUTE_ENABLED:
            logger.info("Tribute отключен, сервер не запускается")
            return
        
        try:
            await self.create_tribute_app()
            
            self.tribute_runner = web.AppRunner(self.tribute_app)
            await self.tribute_runner.setup()
            
            self.tribute_site = web.TCPSite(
                self.tribute_runner,
                host='0.0.0.0', 
                port=settings.TRIBUTE_WEBHOOK_PORT
            )
            
            await self.tribute_site.start()
            
            logger.info(f"✅ Tribute webhook сервер запущен на 0.0.0.0:{settings.TRIBUTE_WEBHOOK_PORT}")
            logger.info(f"🎯 Tribute webhook URL: http://your-server:{settings.TRIBUTE_WEBHOOK_PORT}{settings.TRIBUTE_WEBHOOK_PATH}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска Tribute webhook сервера: {e}")
            raise
    
    async def start_yookassa_server(self):
        if not settings.is_yookassa_enabled():
            logger.info("YooKassa отключен, сервер не запускается")
            return
        
        try:
            await self.create_yookassa_app()
            
            self.yookassa_runner = web.AppRunner(self.yookassa_app)
            await self.yookassa_runner.setup()
            
            self.yookassa_site = web.TCPSite(
                self.yookassa_runner,
                host='0.0.0.0', 
                port=settings.YOOKASSA_WEBHOOK_PORT
            )
            
            await self.yookassa_site.start()
            
            logger.info(f"✅ YooKassa webhook сервер запущен на 0.0.0.0:{settings.YOOKASSA_WEBHOOK_PORT}")
            logger.info(f"🎯 YooKassa webhook URL: http://your-server:{settings.YOOKASSA_WEBHOOK_PORT}{settings.YOOKASSA_WEBHOOK_PATH}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска YooKassa webhook сервера: {e}")
            raise
    
    async def start(self):
        tasks = []
        
        if settings.TRIBUTE_ENABLED:
            tasks.append(self.start_tribute_server())
        
        if settings.is_yookassa_enabled():
            tasks.append(self.start_yookassa_server())
        
        if not tasks:
            logger.warning("Ни один платежный провайдер не включен!")
            return
        
        await asyncio.gather(*tasks)
        
        logger.info("🚀 Все webhook серверы запущены!")
    
    async def stop(self):
        tasks = []
        
        if self.tribute_site:
            tasks.append(self.tribute_site.stop())
        if self.tribute_runner:
            tasks.append(self.tribute_runner.cleanup())
        
        if self.yookassa_site:
            tasks.append(self.yookassa_site.stop())
        if self.yookassa_runner:
            tasks.append(self.yookassa_runner.cleanup())
        
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("🛑 Все webhook серверы остановлены")
            except Exception as e:
                logger.error(f"Ошибка остановки webhook серверов: {e}")
    
    async def _tribute_webhook_handler(self, request: web.Request) -> web.Response:
        try:
            raw_body = await request.read()
            
            if not raw_body:
                logger.warning("Получен пустой Tribute webhook")
                return web.json_response(
                    {"status": "error", "reason": "empty_body"}, 
                    status=400
                )
            
            try:
                payload = raw_body.decode('utf-8')
                import json
                webhook_data = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.error(f"Ошибка декодирования Tribute webhook: {e}")
                return web.json_response(
                    {"status": "error", "reason": "invalid_payload"}, 
                    status=400
                )
            
            signature = request.headers.get('X-Tribute-Signature')
            if signature and not self.tribute_service.verify_webhook_signature(payload, signature):
                logger.error("Неверная подпись Tribute webhook")
                return web.json_response(
                    {"status": "error", "reason": "invalid_signature"}, 
                    status=400
                )
            
            result = await self.tribute_service.process_webhook(webhook_data)
            
            if result:
                from app.database.database import get_db
                async with get_db() as db:
                    success = await self.payment_service.process_tribute_payment(
                        db=db,
                        user_id=result['user_id'],
                        amount_kopeks=result['amount_kopeks'],
                        payment_id=result['payment_id']
                    )
                    
                    if success:
                        logger.info(f"✅ Успешно обработан Tribute платеж: {result['payment_id']}")
                        return web.json_response({"status": "ok"}, status=200)
                    else:
                        logger.error(f"❌ Ошибка обработки Tribute платежа: {result['payment_id']}")
                        return web.json_response(
                            {"status": "error", "reason": "processing_failed"}, 
                            status=500
                        )
            else:
                logger.error("Не удалось обработать Tribute webhook данные")
                return web.json_response(
                    {"status": "error", "reason": "invalid_data"}, 
                    status=400
                )
            
        except Exception as e:
            logger.error(f"Критическая ошибка обработки Tribute webhook: {e}", exc_info=True)
            return web.json_response(
                {"status": "error", "reason": "internal_error"}, 
                status=500
            )
    
    async def _tribute_health_check(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "service": "tribute-webhooks",
            "tribute_enabled": settings.TRIBUTE_ENABLED,
            "port": settings.TRIBUTE_WEBHOOK_PORT
        })
    
    async def _yookassa_health_check(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "service": "yookassa-webhooks", 
            "yookassa_enabled": settings.is_yookassa_enabled(),
            "port": settings.YOOKASSA_WEBHOOK_PORT
        })
