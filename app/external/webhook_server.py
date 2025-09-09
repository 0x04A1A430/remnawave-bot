import logging
import json
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
        
        if settings.is_cryptobot_enabled():
            self.app.router.add_post(settings.CRYPTOBOT_WEBHOOK_PATH, self._cryptobot_webhook_handler)
        
        self.app.router.add_get('/health', self._health_check)
        
        self.app.router.add_options(settings.TRIBUTE_WEBHOOK_PATH, self._options_handler)
        if settings.is_cryptobot_enabled():
            self.app.router.add_options(settings.CRYPTOBOT_WEBHOOK_PATH, self._options_handler)
        
        logger.info(f"Webhook сервер настроен:")
        logger.info(f"  - Tribute webhook: POST {settings.TRIBUTE_WEBHOOK_PATH}")
        if settings.is_cryptobot_enabled():
            logger.info(f"  - CryptoBot webhook: POST {settings.CRYPTOBOT_WEBHOOK_PATH}")
        logger.info(f"  - Health check: GET /health")
        
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
            logger.info(f"🎯 Tribute webhook URL: http://0.0.0.0:{settings.TRIBUTE_WEBHOOK_PORT}{settings.TRIBUTE_WEBHOOK_PATH}")
            if settings.is_cryptobot_enabled():
                logger.info(f"🪙 CryptoBot webhook URL: http://0.0.0.0:{settings.TRIBUTE_WEBHOOK_PORT}{settings.CRYPTOBOT_WEBHOOK_PATH}")
            
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
    
    async def _options_handler(self, request: web.Request) -> web.Response:
        return web.Response(
            status=200,
            headers={
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
                'Access-Control-Allow-Headers': 'Content-Type, trbt-signature, Crypto-Pay-API-Signature',
            }
        )
    
    async def _tribute_webhook_handler(self, request: web.Request) -> web.Response:
        
        try:
            logger.info(f"🔥 Получен Tribute webhook: {request.method} {request.path}")
            logger.info(f"📋 Headers: {dict(request.headers)}")
            
            raw_body = await request.read()
            
            if not raw_body:
                logger.warning("⚠️ Получен пустой webhook от Tribute")
                return web.json_response(
                    {"status": "error", "reason": "empty_body"},
                    status=400
                )
            
            payload = raw_body.decode('utf-8')
            logger.info(f"📄 Payload: {payload}")
            
            try:
                webhook_data = json.loads(payload)
                logger.info(f"📊 Распарсенные данные: {webhook_data}")
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга JSON: {e}")
                return web.json_response(
                    {"status": "error", "reason": "invalid_json"},
                    status=400
                )
            
            signature = request.headers.get('Crypto-Pay-API-Signature')
            logger.info(f"🔐 CryptoBot Signature: {signature}")

            if signature and settings.CRYPTOBOT_WEBHOOK_SECRET:
                from app.external.cryptobot import CryptoBotService
                cryptobot_service = CryptoBotService()
                if not cryptobot_service.verify_webhook_signature(payload, signature):
                    logger.error("❌ Неверная подпись CryptoBot webhook")
                    return web.json_response(
                        {"status": "error", "reason": "invalid_signature"},
                        status=401
                    )

            from app.services.payment_service import PaymentService
            from app.database.database import get_database
            
            payment_service = PaymentService(self.bot)
            
            async with get_database() as db:
                result = await payment_service.process_cryptobot_webhook(db, webhook_data)
            
            if result:
                logger.info(f"✅ CryptoBot webhook обработан успешно")
                return web.json_response({"status": "ok"}, status=200)
            else:
                logger.error("❌ Ошибка обработки CryptoBot webhook")
                return web.json_response(
                    {"status": "error", "reason": "processing_failed"},
                    status=400
                )
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка обработки CryptoBot webhook: {e}", exc_info=True)
            return web.json_response(
                {"status": "error", "reason": "internal_error", "message": str(e)},
                status=500
            )
    
    async def _health_check(self, request: web.Request) -> web.Response:
        
        return web.json_response({
            "status": "ok",
            "service": "payment-webhooks",
            "tribute_enabled": settings.TRIBUTE_ENABLED,
            "cryptobot_enabled": settings.is_cryptobot_enabled(),
            "port": settings.TRIBUTE_WEBHOOK_PORT,
            "tribute_path": settings.TRIBUTE_WEBHOOK_PATH,
            "cryptobot_path": settings.CRYPTOBOT_WEBHOOK_PATH if settings.is_cryptobot_enabled() else None
        })400
                )
            
            signature = request.headers.get('trbt-signature')
            logger.info(f"🔐 Signature: {signature}")

            if not signature:
                logger.error("❌ Отсутствует заголовок подписи Tribute webhook")
                return web.json_response(
                    {"status": "error", "reason": "missing_signature"},
                    status=401
                )

            if settings.TRIBUTE_API_KEY:
                from app.external.tribute import TributeService as TributeAPI
                tribute_api = TributeAPI()
                if not tribute_api.verify_webhook_signature(payload, signature):
                    logger.error("❌ Неверная подпись Tribute webhook")
                    return web.json_response(
                        {"status": "error", "reason": "invalid_signature"},
                        status=401
                    )

            result = await self.tribute_service.process_webhook(payload)
            
            if result:
                logger.info(f"✅ Tribute webhook обработан успешно: {result}")
                return web.json_response({"status": "ok", "result": result}, status=200)
            else:
                logger.error("❌ Ошибка обработки Tribute webhook")
                return web.json_response(
                    {"status": "error", "reason": "processing_failed"},
                    status=400
                )
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка обработки Tribute webhook: {e}", exc_info=True)
            return web.json_response(
                {"status": "error", "reason": "internal_error", "message": str(e)},
                status=500
            )
    
    async def _cryptobot_webhook_handler(self, request: web.Request) -> web.Response:
        
        try:
            logger.info(f"🪙 Получен CryptoBot webhook: {request.method} {request.path}")
            logger.info(f"📋 Headers: {dict(request.headers)}")
            
            raw_body = await request.read()
            
            if not raw_body:
                logger.warning("⚠️ Получен пустой CryptoBot webhook")
                return web.json_response(
                    {"status": "error", "reason": "empty_body"},
                    status=400
                )
            
            payload = raw_body.decode('utf-8')
            logger.info(f"📄 CryptoBot Payload: {payload}")
            
            try:
                webhook_data = json.loads(payload)
                logger.info(f"📊 CryptoBot данные: {webhook_data}")
            except json.JSONDecodeError as e:
                logger.error(f"❌ Ошибка парсинга CryptoBot JSON: {e}")
                return web.json_response(
                    {"status": "error", "reason": "invalid_json"},
                    status=
