"""Человекочитаемая подпись устройства для списков и кнопок."""

import re


_JUNK_MODEL_SUBSTRINGS = (
    'to be filled by o.e.m',
    'system product name',
    'system manufacturer',
    'system version',
    'default string',
    'not applicable',
    'not specified',
    'unknown',
    'none',
    'n/a',
)

# U+FFFC и невидимые маркеры приходят из user-agent/панели и выглядят мусором.
_INVISIBLE = dict.fromkeys(map(ord, '\ufffc\u200b\u200c\u200d\ufeff'), None)
_SPACES = re.compile(r'\s+')


def _clean(value: str) -> str:
    return _SPACES.sub(' ', (value or '').translate(_INVISIBLE)).strip()


def is_junk_device_model(device_model: str) -> bool:
    normalized = _clean(device_model).lower()
    if not normalized:
        return True
    return any(junk in normalized for junk in _JUNK_MODEL_SUBSTRINGS)


def format_device_label(
    platform: str,
    device_model: str,
    os_version: str = '',
    app_version: str = '',
    max_length: int = 30,
) -> str:
    """Build a short device label from the panel's platform, model, and version data.

    Модель в панели часто уже называет ОС («Windows 11 Pro», «iPhone 14 Pro»):
    дублировать её префиксом «Windows 25H2 -» незачем. Мусорные модели
    («System Product Name») не показываем вовсе, а длинные версии обрезаем,
    чтобы список не превращался в простыню.
    """
    platform = _clean(platform) or 'Unknown'
    os_version = _clean(os_version)
    app_version = _clean(app_version)

    platform_with_os = platform
    if os_version and os_version.lower() not in platform.lower():
        platform_with_os = f'{platform} {os_version}'

    device_model = _clean(device_model)
    if is_junk_device_model(device_model):
        label = platform_with_os
    else:
        lowered_model = device_model.lower()
        # Модель сама содержит ОС (Windows/iPhone/iPad/macOS) — префикс лишний.
        if platform.lower() in lowered_model or (os_version and os_version.lower() in lowered_model):
            label = device_model
        else:
            label = f'{platform_with_os} · {device_model}'

    if app_version:
        label = f'{label} ({app_version})'

    if len(label) > max_length:
        label = label[: max_length - 1].rstrip(' -·') + '…'
    return label
