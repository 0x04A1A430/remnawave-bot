"""Unit coverage for webhook device-info extraction.

Covers `_extract_device_info` / `_extract_device_name`: readable device
model (deviceModel/model/name) takes priority over HWID/tag, emoji/HTML
escaping stays intact, and fallbacks behave when fields are missing.
"""

from __future__ import annotations

import pytest

from app.services.remnawave_webhook_service import RemnaWaveWebhookService


@pytest.mark.parametrize(
    ('device_obj', 'expected_platform', 'expected_tag'),
    [
        # deviceModel wins over tag/HWID — the regression case.
        (
            {'platform': 'iOS', 'deviceModel': 'iPhone 14 Pro (27.0)', 'tag': 'efls8jxq', 'hwid': 'ABCDEFGHIJKL'},
            'iOS',
            'iPhone 14 Pro (27.0)',
        ),
        # model alias, then name, then deviceName, then tag, then hwid.
        ({'platform': 'Android', 'model': 'SM-S908U', 'tag': 'android-1', 'hwid': 'X'}, 'Android', 'SM-S908U'),
        ({'platform': 'Windows', 'name': 'Work PC', 'tag': 'win-1'}, 'Windows', 'Work PC'),
        ({'platform': 'macOS', 'deviceName': 'MacBook Air', 'tag': 'mac-1'}, 'macOS', 'MacBook Air'),
        ({'platform': 'Linux', 'tag': 'srv-1'}, 'Linux', 'srv-1'),
        # No readable name and no tag → short hwid suffix.
        ({'platform': 'iOS', 'hwid': 'ABCDEFGHIJKL'}, 'iOS', 'ABCDEFGH'),
    ],
)
def test_extract_device_info_prioritizes_model_over_hwid(
    device_obj: dict, expected_platform: str, expected_tag: str
) -> None:
    platform_display, tag = RemnaWaveWebhookService._extract_device_info({'hwidUserDevice': device_obj})

    assert expected_platform in platform_display
    assert expected_tag in tag


def test_extract_device_info_escapes_html() -> None:
    platform_display, tag = RemnaWaveWebhookService._extract_device_info(
        {'hwidUserDevice': {'platform': 'iOS', 'name': '<b>X</b>'}}
    )

    assert '<b>X</b>' not in tag
    assert '&lt;b&gt;X&lt;/b&gt;' in tag


def test_extract_device_info_no_device_obj_falls_back_to_top_level() -> None:
    platform_display, tag = RemnaWaveWebhookService._extract_device_info({'deviceName': 'Laptop'})

    assert platform_display == ''
    assert 'Laptop' in tag


def test_extract_device_info_empty_returns_empty_strings() -> None:
    platform_display, tag = RemnaWaveWebhookService._extract_device_info({})

    assert platform_display == ''
    assert tag == ''


def test_extract_device_name_combines_platform_and_model() -> None:
    result = RemnaWaveWebhookService._extract_device_name(
        {'hwidUserDevice': {'platform': 'iOS', 'deviceModel': 'iPhone 14 Pro (27.0)'}}
    )

    assert 'iOS' in result
    assert 'iPhone 14 Pro (27.0)' in result
    assert 'efls8jxq' not in result
