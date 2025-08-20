import logging
from typing import Optional

from aiohttp import web
from aiogram import Bot

from app.config import settings
from app.services.tribute_service import TributeService

logger = logging.getLogger(__name__)


class WebhookServer:
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.app = None
        self.runner = None
        self.site = None
        self.tribute_service = TributeService(bot)
    
    async def create_app(self) -> web.Application:
        
        self.app = web.Application()
        
        self.app.router.add_post(settings.TRIBUTE_WEBHOOK_PATH, self._tribute_webhook_handler)
        self.app.router.add_get('/health', self._health_check)
        
        logger.info(f"Webhook сервер настроен:")
        logger.info(f"  - Tribute webhook: {settings.TRIBUTE_WEBHOOK_PATH}")
        logger.info(f"  - Health check: /health")
        
        return self.app
    
    async def start(self):
        
        try:
            if not self.app:
                await self.create_app()
            
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            
            self.site = web.TCPSite(
                self.runner,
                host='0.0.0.0',
                port=settings.TRIBUTE_WEBHOOK_PORT
            )
            
            await self.site.start()
            
            logger.info(f"✅ Webhook сервер запущен на порту {settings.TRIBUTE_WEBHOOK_PORT}")
            logger.info(f"🎯 Tribute webhook URL: http://your-server:{settings.TRIBUTE_WEBHOOK_PORT}{settings.TRIBUTE_WEBHOOK_PATH}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска webhook сервера: {e}")
            raise
    
    async def stop(self):
        
        try:
            if self.site:
                await self.site.stop()
                logger.info("Webhook сайт остановлен")
            
            if self.runner:
                await self.runner.cleanup()
                logger.info("Webhook runner очищен")
                
        except Exception as e:
            logger.error(f"Ошибка остановки webhook сервера: {e}")
    
    async def _tribute_webhook_handler(self, request: web.Request) -> web.Response:
        
        try:
            raw_body = await request.read()
            payload = raw_body.decode('utf-8')
            
            signature = request.headers.get('X-Tribute-Signature')
            
            result = await self.tribute_service.process_webhook(payload, signature)
            
            return web.json_response(result, status=200)
            
        except Exception as e:
            logger.error(f"Ошибка обработки Tribute webhook: {e}")
            return web.json_response(
                {"status": "error", "reason": "internal_error"},
                status=500
            )
    
    async def _health_check(self, request: web.Request) -> web.Response:
        
        return web.json_response({
            "status": "ok",
            "service": "tribute-webhooks",
            "tribute_enabled": settings.TRIBUTE_ENABLED
        })