from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp


def _append_query_param_to_fragment(fragment: str, param: str, value: str) -> str:
    base_part, _, query_part = fragment.partition("?")
    query_params = dict(parse_qsl(query_part, keep_blank_values=True))
    query_params[param] = value

    encoded_query = urlencode(query_params)
    return f"{base_part}?{encoded_query}" if base_part else encoded_query


def append_install_code(base_link: str, install_code: str, param_name: str = "installid") -> str:
    """Добавляет параметр installid в ссылку, сохраняя существующие параметры."""

    if not install_code:
        return base_link

    parsed = urlsplit(base_link)

    if parsed.fragment:
        updated_fragment = _append_query_param_to_fragment(parsed.fragment, param_name, install_code)
        return urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.query,
            updated_fragment,
        ))

    query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query_params[param_name] = install_code
    updated_query = urlencode(query_params)

    return urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        updated_query,
        parsed.fragment,
    ))


async def generate_limited_happ_link(
    base_link: str,
    api_url: str,
    provider_code: str,
    auth_key: str,
    install_limit: int,
    *,
    timeout_seconds: int = 15,
) -> Optional[str]:
    if not base_link or install_limit <= 0:
        return None

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    params = {
        "provider_code": provider_code,
        "auth_key": auth_key,
        "install_limit": install_limit,
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(api_url, params=params) as response:
                data = await response.json()
    except Exception:
        return None

    if data.get("rc") != 1:
        return None

    install_code = data.get("install_code") or data.get("installCode")
    if not install_code:
        return None

    return append_install_code(base_link, install_code)
