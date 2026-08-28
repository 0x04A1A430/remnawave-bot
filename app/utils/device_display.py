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


def is_junk_device_model(device_model: str) -> bool:
    normalized = (device_model or '').strip().lower()
    if not normalized:
        return True
    return any(junk in normalized for junk in _JUNK_MODEL_SUBSTRINGS)


def format_device_label(platform: str, device_model: str, os_version: str = '', app_version: str = '') -> str:
    """Label строго по данным панели: ОС (+ её версия) и модель устройства (+ версия приложения)."""
    platform = platform or 'Unknown'
    os_version = (os_version or '').strip()
    app_version = (app_version or '').strip()
    if os_version:
        platform = f'{platform} {os_version}'

    device_model = (device_model or '').strip()
    if is_junk_device_model(device_model):
        return f'{platform} ({app_version})' if app_version else platform

    label = f'{platform} - {device_model}'
    if app_version:
        label = f'{label} ({app_version})'
    return label
