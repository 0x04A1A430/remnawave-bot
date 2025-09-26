from __future__ import annotations

import asyncio
import logging
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.webapi.routers import api_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    docs_url = "/docs" if settings.WEBAPI_DOCS_ENABLED else None
    redoc_url = "/redoc" if settings.WEBAPI_DOCS_ENABLED else None

    app = FastAPI(
        title=settings.WEBAPI_TITLE,
        description=settings.WEBAPI_DESCRIPTION,
        version=settings.WEBAPI_VERSION,
        docs_url=docs_url,
        redoc_url=redoc_url,
    )

    origins = settings.get_webapi_allowed_origins()
    allow_all_origins = origins == ["*"]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allow_all_origins else origins,
        allow_credentials=True,
        allow_methods=["*"] if allow_all_origins else ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"] if allow_all_origins else ["*"],
    )

    app.include_router(api_router)

    return app


class WebAPIServer:
    def __init__(self) -> None:
        self.app = create_app()
        self._server: Optional[uvicorn.Server] = None
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return

        config = uvicorn.Config(
            self.app,
            host=settings.WEBAPI_HOST,
            port=settings.WEBAPI_PORT,
            log_level=settings.WEBAPI_LOG_LEVEL.lower(),
            access_log=settings.WEBAPI_ACCESS_LOG,
            loop="asyncio",
            lifespan="on",
            timeout_keep_alive=15,
            root_path=settings.WEBAPI_ROOT_PATH or "",
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = False
        self._server = server

        logger.info(
            "🚀 Запуск Web API на %s:%s",
            settings.WEBAPI_HOST,
            settings.WEBAPI_PORT,
        )

        self._task = asyncio.create_task(server.serve())

    async def stop(self) -> None:
        if not self._server or not self._task:
            return

        logger.info("🛑 Остановка Web API")
        self._server.should_exit = True
        await self._task
        self._task = None
        self._server = None
