from fastapi import FastAPI
from fastapi.openapi.docs import get_redoc_html


def add_redoc_endpoint(
    app: FastAPI,
    *,
    redoc_url: str | None,
    openapi_url: str | None,
    title: str | None,
) -> None:
    """Attach a ReDoc endpoint if docs are enabled.

    The default FastAPI ReDoc handler sometimes renders a blank page when the
    CDN bundle fails to load. By explicitly registering the handler and
    pinning the bundle version, we ensure the endpoint always returns a fully
    rendered page.
    """

    if not redoc_url or not openapi_url:
        return

    for route in app.router.routes:
        if getattr(route, "path", None) == redoc_url:
            return

    @app.get(redoc_url, include_in_schema=False)
    async def redoc_html():  # pragma: no cover - template rendering
        return get_redoc_html(
            openapi_url=openapi_url,
            title=f"{title or app.title} - ReDoc",
            redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2.1.5/bundles/redoc.standalone.js",
        )
