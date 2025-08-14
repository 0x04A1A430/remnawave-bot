import logging
from aiohttp import web, ClientSession
from aiogram import Bot
from database import Database
from config import Config
from tribute_service import TributeService, tribute_webhook_route

logger = logging.getLogger(__name__)

class WebhookServer:
    def __init__(self, bot: Bot, db: Database, config: Config):
        self.bot = bot
        self.db = db
        self.config = config
        self.app = None
        self.runner = None
        self.site = None
        
    async def create_app(self):
        self.app = web.Application()
        
        tribute_service = TributeService(self.bot, self.config, self.db)
        
        self.app['tribute_service'] = tribute_service
        
        self.app.router.add_post(self.config.TRIBUTE_WEBHOOK_PATH, tribute_webhook_route)
        
        async def health_check(request):
            return web.json_response({"status": "ok", "service": "tribute-webhooks"})
        
        self.app.router.add_get('/health', health_check)
        
        logger.info(f"Webhook server configured with route: {self.config.TRIBUTE_WEBHOOK_PATH}")
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
                port=self.config.TRIBUTE_WEBHOOK_PORT
            )
            
            await self.site.start()
            
            logger.info(f"✅ Webhook server started on port {self.config.TRIBUTE_WEBHOOK_PORT}")
            logger.info(f"🎯 Tribute webhook URL: http://your-server:{self.config.TRIBUTE_WEBHOOK_PORT}{self.config.TRIBUTE_WEBHOOK_PATH}")
            
        except Exception as e:
            logger.error(f"❌ Failed to start webhook server: {e}")
            raise
    
    async def stop(self):
        try:
            if self.site:
                await self.site.stop()
                logger.info("Webhook site stopped")
            
            if self.runner:
                await self.runner.cleanup()
                logger.info("Webhook runner cleaned up")
                
        except Exception as e:
            logger.error(f"Error stopping webhook server: {e}")
