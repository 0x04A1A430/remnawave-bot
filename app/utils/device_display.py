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


def format_device_label(platform: str, device_model: str) -> str:
    platform = platform or 'Unknown'
    device_model = (device_model or '').strip()
    if is_junk_device_model(device_model):
        return platform
    return f'{platform} - {device_model}'
