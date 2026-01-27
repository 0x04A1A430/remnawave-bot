from __future__ import annotations

import logging
from time import monotonic

from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


logger = logging.getLogger('web_api')


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Логирование входящих запросов в административный API."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = monotonic()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        except (TimeoutError, ConnectionRefusedError, OSError, OperationalError, InterfaceError) as e:
            logger.error(
                'Database connection error on %s %s: %s',
                request.method,
                request.url.path,
                str(e)[:200],
            )
            response = JSONResponse(
                status_code=503,
                content={'detail': 'Service temporarily unavailable. Please try again later.'},
            )
            return response
        finally:
            duration_ms = (monotonic() - start) * 1000
            status = response.status_code if response else 'error'
            logger.debug(
                '%s %s -> %s (%.2f ms)',
                request.method,
                request.url.path,
                status,
                duration_ms,
            )
