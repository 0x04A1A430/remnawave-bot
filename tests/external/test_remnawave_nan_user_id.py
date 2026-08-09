"""Regression tests for NaN/invalid remnawave_id handling.

RemnaWave 3.0.0: panel users are identified by numeric ``id``. A JSON literal
``NaN`` parsed by ``json.loads`` becomes ``float('nan')``; if that leaks into
``remnawave_id`` in the DB, URL paths like ``/api/users/nan`` are built and the
panel rejects them with 400 "Validation failed ... received NaN". These tests
assert the sanitizer rejects such values both when formatting requests and when
parsing panel responses.
"""

from __future__ import annotations

import math

from app.external.remnawave_api import RemnaWaveAPI


def _api() -> RemnaWaveAPI:
    return RemnaWaveAPI('http://panel.local', 'key')


def test_sanitize_user_id_accepts_valid_int():
    api = _api()
    assert api._sanitize_user_id(42) == 42
    assert api._sanitize_user_id(0) == 0
    assert api._sanitize_user_id(42.0) == 42
    assert api._sanitize_user_id('42') == 42


def test_sanitize_user_id_rejects_nan_inf_and_garbage():
    api = _api()
    assert api._sanitize_user_id(float('nan')) is None
    assert api._sanitize_user_id(math.nan) is None
    assert api._sanitize_user_id(float('inf')) is None
    assert api._sanitize_user_id(float('-inf')) is None
    assert api._sanitize_user_id(42.5) is None
    assert api._sanitize_user_id(True) is None
    assert api._sanitize_user_id(False) is None
    assert api._sanitize_user_id(None) is None
    assert api._sanitize_user_id('nan') is None
    assert api._sanitize_user_id('abc') is None
    assert api._sanitize_user_id([]) is None


def test_fmt_user_path_uses_numeric_id_when_valid():
    api = _api()
    api._use_user_id = lambda: True
    assert api._fmt_user_path('uuid-1', 42) == '/api/users/42'


def test_fmt_user_path_rejects_nan_id():
    api = _api()
    api._use_user_id = lambda: True
    assert api._fmt_user_path('uuid-1', float('nan')) == '/api/users/uuid-1'


def test_fmt_user_path_v2_mode_uses_uuid_even_with_id():
    api = _api()
    api._use_user_id = lambda: False
    assert api._fmt_user_path('uuid-1', 42) == '/api/users/uuid-1'


def test_fmt_hwid_path_rejects_nan_id():
    api = _api()
    api._use_user_id = lambda: True
    assert api._fmt_hwid_path('uuid-1', float('nan')) == '/api/hwid/devices/uuid-1'
    assert api._fmt_hwid_path('uuid-1', 42) == '/api/hwid/devices/42'


def test_fmt_hwid_delete_payload_rejects_nan_id():
    api = _api()
    api._use_user_id = lambda: True
    payload = api._fmt_hwid_delete_payload('uuid-1', float('nan'), 'hwid-x')
    assert payload == {'userUuid': 'uuid-1', 'hwid': 'hwid-x'}

    valid_payload = api._fmt_hwid_delete_payload('uuid-1', 42, 'hwid-x')
    assert valid_payload == {'userId': 42, 'hwid': 'hwid-x'}


def test_parse_user_sanitizes_nan_id():
    api = _api()
    raw = {
        'uuid': '',
        'shortUuid': 'short-1',
        'username': 'u1',
        'status': 'ACTIVE',
        'trafficLimitBytes': 0,
        'trafficLimitStrategy': 'NO_RESET',
        'expireAt': '2099-01-01T00:00:00.000Z',
        'createdAt': '2026-01-01T00:00:00.000Z',
        'updatedAt': '2026-01-01T00:00:00.000Z',
        'id': float('nan'),
    }
    user = api._parse_user(raw)
    assert user.id is None


def test_parse_user_keeps_valid_numeric_id():
    api = _api()
    raw = {
        'uuid': '',
        'shortUuid': 'short-1',
        'username': 'u1',
        'status': 'ACTIVE',
        'trafficLimitBytes': 0,
        'trafficLimitStrategy': 'NO_RESET',
        'expireAt': '2099-01-01T00:00:00.000Z',
        'createdAt': '2026-01-01T00:00:00.000Z',
        'updatedAt': '2026-01-01T00:00:00.000Z',
        'id': 42,
    }
    user = api._parse_user(raw)
    assert user.id == 42
