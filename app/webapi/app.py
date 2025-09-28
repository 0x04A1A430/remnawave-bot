from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.config import settings

from .middleware import RequestLoggingMiddleware
from .routes import (
    config,
    health,
    promo_groups,
    stats,
    subscriptions,
    tickets,
    tokens,
    transactions,
    users,
)

from .docs import build_scalar_docs_html


def create_web_api_app() -> FastAPI:
    docs_config = settings.get_web_api_docs_config()

    app = FastAPI(
        title=settings.WEB_API_TITLE,
        version=settings.WEB_API_VERSION,
        docs_url=docs_config.get("docs_url"),
        redoc_url=None,
        openapi_url=docs_config.get("openapi_url"),
        swagger_ui_parameters={"persistAuthorization": True},
    )

    allowed_origins = settings.get_web_api_allowed_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allowed_origins == ["*"] else allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.WEB_API_REQUEST_LOGGING:
        app.add_middleware(RequestLoggingMiddleware)

    app.include_router(health.router)
    app.include_router(stats.router, prefix="/stats", tags=["stats"])
    app.include_router(config.router, prefix="/settings", tags=["settings"])
    app.include_router(users.router, prefix="/users", tags=["users"])
    app.include_router(subscriptions.router, prefix="/subscriptions", tags=["subscriptions"])
    app.include_router(tickets.router, prefix="/tickets", tags=["support"])
    app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
    app.include_router(promo_groups.router, prefix="/promo-groups", tags=["promo-groups"])
    app.include_router(tokens.router, prefix="/tokens", tags=["auth"])

    scalar_url = docs_config.get("scalar_url")
    if scalar_url and app.openapi_url:

        @app.get(scalar_url, include_in_schema=False)
        async def scalar_docs() -> HTMLResponse:
            html_content = build_scalar_docs_html(
                title=settings.WEB_API_TITLE,
                openapi_url=app.openapi_url,
            )
            return HTMLResponse(content=html_content)

    return app
