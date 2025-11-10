"""Flask webhook server for PayPalych callbacks."""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FuturesTimeoutError
import json
import logging
import threading
from asyncio import AbstractEventLoop
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request
from werkzeug.serving import make_server

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.services.pal24_service import Pal24Service, Pal24APIError
from app.services.payment_service import PaymentService

logger = logging.getLogger(__name__)


def _normalize_payload() -> Dict[str, str]:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            return {k: str(v) for k, v in payload.items()}
        logger.warning("Pal24 webhook JSON payload не является объектом: %s", payload)
        return {}

    if request.form:
        return {k: v for k, v in request.form.items()}

    try:
        raw_body = request.data.decode("utf-8")
        if raw_body:
            payload = json.loads(raw_body)
            if isinstance(payload, dict):
                return {k: str(v) for k, v in payload.items()}
    except json.JSONDecodeError:
        logger.debug("Pal24 webhook body не удалось распарсить как JSON")

    return {}


def create_pal24_flask_app(
    payment_service: PaymentService,
    loop: AbstractEventLoop,
) -> Flask:
    pal24_service = Pal24Service()
    app = Flask(__name__)

    @app.route(settings.PAL24_WEBHOOK_PATH, methods=["POST"])
    def pal24_webhook() -> tuple:
        if not pal24_service.is_configured:
            logger.error("Pal24 webhook получен, но сервис не настроен")
            return jsonify({"status": "error", "reason": "service_not_configured"}), 503

        logger.debug("Получен Pal24 webhook: headers=%s", dict(request.headers))

        payload = _normalize_payload()
        if not payload:
            logger.warning("Пустой Pal24 webhook")
            return jsonify({"status": "error", "reason": "empty_payload"}), 400

        try:
            parsed_payload = pal24_service.parse_callback(payload)
        except Pal24APIError as error:
            logger.error("Ошибка валидации Pal24 webhook: %s", error)
            return jsonify({"status": "error", "reason": str(error)}), 400

        async def process() -> bool:
            async with AsyncSessionLocal() as db:
                try:
                    return await payment_service.process_pal24_callback(db, parsed_payload)
                except Exception:
                    await db.rollback()
                    raise

        try:
            future = asyncio.run_coroutine_threadsafe(process(), loop)
            processed = future.result(timeout=settings.PAL24_REQUEST_TIMEOUT)
        except FuturesTimeoutError:
            logger.error("Обработка Pal24 webhook превысила таймаут %sс", settings.PAL24_REQUEST_TIMEOUT)
            return jsonify({"status": "error", "reason": "timeout"}), 504
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("Критическая ошибка обработки Pal24 webhook: %s", error)
            return jsonify({"status": "error", "reason": "internal_error"}), 500

        if processed:
            return jsonify({"status": "ok"}), 200
        return jsonify({"status": "error", "reason": "not_processed"}), 400

    @app.route(settings.PAL24_WEBHOOK_PATH, methods=["GET"])
    def pal24_health() -> tuple:
        return jsonify({
            "status": "ok",
            "service": "pal24_webhook",
            "enabled": settings.is_pal24_enabled(),
        }), 200

    @app.route("/pal24/health", methods=["GET"])
    def pal24_additional_health() -> tuple:
        return jsonify({
            "status": "ok",
            "service": "pal24_webhook",
            "path": settings.PAL24_WEBHOOK_PATH,
        }), 200

    return app


class Pal24WebhookServer:
    """Threaded Flask server for Pal24 callbacks."""

    def __init__(self, payment_service: PaymentService, loop: AbstractEventLoop) -> None:
        self.app = create_pal24_flask_app(payment_service, loop)
        self._server: Optional[Any] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._server:
            logger.warning("Pal24 webhook server уже запущен")
            return

        self._server = make_server(
            host="0.0.0.0",
            port=settings.PAL24_WEBHOOK_PORT,
            app=self.app,
            threaded=True,
        )

        def _serve() -> None:
            logger.info(
                "Pal24 webhook сервер запущен на %s:%s%s",
                "0.0.0.0",
                settings.PAL24_WEBHOOK_PORT,
                settings.PAL24_WEBHOOK_PATH,
            )
            self._server.serve_forever()

        self._thread = threading.Thread(target=_serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            logger.info("Останавливаем Pal24 webhook сервер")
            self._server.shutdown()
            self._server = None

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
            self._thread = None


async def start_pal24_webhook_server(payment_service: PaymentService) -> Pal24WebhookServer:
    loop = asyncio.get_running_loop()
    server = Pal24WebhookServer(payment_service, loop)
    await loop.run_in_executor(None, server.start)
    return server

