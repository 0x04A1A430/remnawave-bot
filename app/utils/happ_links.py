import base64
import html
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

HAPP_LINK_QUERY_PARAM = "data"
_HAPP_SCHEME_PREFIX = "happ://"


def _encode_happ_link(link: str) -> str:
    encoded = base64.urlsafe_b64encode(link.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def decode_happ_link(token: str) -> Optional[str]:
    if not token:
        return None

    padding = "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{token}{padding}".encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        logger.warning("Не удалось декодировать cryptoLink из токена")
        return None

    if not decoded.startswith(_HAPP_SCHEME_PREFIX):
        logger.warning("Попытка открыть ссылку с неподдерживаемым протоколом: %s", decoded)
        return None

    return decoded


def build_happ_proxy_link(crypto_link: str) -> Optional[str]:
    base_url = settings.get_happ_cryptolink_proxy_base_url()
    if not base_url:
        logger.error("Не задан базовый URL для прокси Happ cryptoLink")
        return None

    path = settings.get_happ_cryptolink_proxy_path()
    token = _encode_happ_link(crypto_link)
    return f"{base_url}{path}?{HAPP_LINK_QUERY_PARAM}={token}"


def render_happ_redirect_page(happ_link: str) -> str:
    escaped_link = html.escape(happ_link, quote=True)
    return f"""<!DOCTYPE html>
<html lang=\"ru\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Открытие Happ</title>
    <style>
      body {{
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        background-color: #0d1117;
        color: #f0f6fc;
        margin: 0;
        padding: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        min-height: 100vh;
      }}
      .container {{
        max-width: 480px;
        width: 100%;
        background: rgba(13, 17, 23, 0.85);
        border: 1px solid rgba(240, 246, 252, 0.12);
        border-radius: 16px;
        padding: 28px 24px;
        box-shadow: 0 18px 24px rgba(1, 4, 9, 0.45);
        text-align: center;
      }}
      h1 {{
        font-size: 1.4rem;
        margin-bottom: 12px;
        color: #6ca4f7;
      }}
      p {{
        line-height: 1.5;
        margin-bottom: 20px;
        color: rgba(240, 246, 252, 0.82);
      }}
      a.button {{
        display: inline-block;
        padding: 12px 20px;
        border-radius: 10px;
        background: linear-gradient(135deg, #2788f6, #4c9cf6);
        color: #fff;
        text-decoration: none;
        font-weight: 600;
        transition: transform 0.15s ease;
      }}
      a.button:hover {{
        transform: translateY(-2px);
      }}
      .secondary {{
        margin-top: 16px;
        font-size: 0.85rem;
        color: rgba(240, 246, 252, 0.6);
        word-break: break-all;
      }}
      code {{
        display: inline-block;
        background: rgba(240, 246, 252, 0.12);
        padding: 6px 10px;
        border-radius: 6px;
        font-size: 0.85rem;
      }}
    </style>
    <script>
      function openHapp() {{
        window.location.replace('{escaped_link}');
      }}
      window.addEventListener('load', function () {{
        setTimeout(openHapp, 80);
      }});
    </script>
  </head>
  <body>
    <div class=\"container\">
      <h1>Подключение Happ</h1>
      <p>Если приложение Happ не открылось автоматически, нажмите кнопку ниже или скопируйте ссылку вручную.</p>
      <a class=\"button\" href=\"{escaped_link}\">Открыть в Happ</a>
      <p class=\"secondary\">Ссылка для копирования:<br /><code>{escaped_link}</code></p>
    </div>
  </body>
</html>
"""
