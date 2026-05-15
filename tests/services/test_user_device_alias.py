"""Unit coverage for `user_device_aliases` CRUD helpers.

Covers the normalization rules, alias merge into RemnaWave device dicts,
and length-cap behaviour expected by both the bot UI and the cabinet API.
The DB-touching upsert/get/delete functions are exercised in integration
tests; this file pins the pure helpers so regressions surface fast.
"""

from __future__ import annotations

import pytest

from app.database.crud.user_device_alias import (
    ALIAS_MAX_LENGTH,
    attach_aliases_to_devices,
    normalize_alias,
)


# ---------------------------------------------------------------------------
# normalize_alias
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [
        (None, ''),
        ('', ''),
        ('   ', ''),
        ('iPhone Жены', 'iPhone Жены'),
        # Inner-whitespace runs collapse to a single space — pasted line breaks etc.
        ('iPhone  \n\t  Жены', 'iPhone Жены'),
        ('  trim me  ', 'trim me'),
    ],
)
def test_normalize_alias_basic(raw: str | None, expected: str) -> None:
    assert normalize_alias(raw) == expected


def test_normalize_alias_caps_at_max_length() -> None:
    huge = 'A' * (ALIAS_MAX_LENGTH + 100)

    result = normalize_alias(huge)

    assert len(result) == ALIAS_MAX_LENGTH
    assert result == 'A' * ALIAS_MAX_LENGTH


def test_normalize_alias_preserves_unicode() -> None:
    # Cyrillic + emoji + dash — common real-world aliases.
    raw = '🏠 Домашний MacBook —    Pro'

    result = normalize_alias(raw)

    assert result == '🏠 Домашний MacBook — Pro'


# ---------------------------------------------------------------------------
# attach_aliases_to_devices
# ---------------------------------------------------------------------------


def test_attach_aliases_to_devices_sets_local_name_when_match() -> None:
    devices = [
        {'hwid': 'ABC123', 'platform': 'iOS', 'deviceModel': 'iPhone15,2'},
        {'hwid': 'DEF456', 'platform': 'Android', 'deviceModel': 'SM-S908U'},
    ]
    aliases = {'ABC123': 'Жены iPhone'}

    result = attach_aliases_to_devices(devices, aliases)

    assert result[0]['local_name'] == 'Жены iPhone'
    # No alias for DEF456 → explicit None so callers can fall back uniformly.
    assert result[1]['local_name'] is None


def test_attach_aliases_to_devices_handles_empty_aliases() -> None:
    devices = [{'hwid': 'X', 'platform': 'Win'}]

    result = attach_aliases_to_devices(devices, {})

    assert result[0]['local_name'] is None


def test_attach_aliases_to_devices_handles_missing_hwid() -> None:
    """Device without hwid key — alias merge must not crash, just yield None."""
    devices = [{'platform': 'Linux'}]

    result = attach_aliases_to_devices(devices, {'whatever': 'X'})

    assert result[0]['local_name'] is None


def test_attach_aliases_to_devices_is_in_place_mutation() -> None:
    """The helper mutates each dict for cheap downstream rendering."""
    devices = [{'hwid': 'A', 'platform': 'iOS'}]

    result = attach_aliases_to_devices(devices, {'A': 'Mine'})

    assert result is devices  # same list
    assert devices[0]['local_name'] == 'Mine'


def test_attach_aliases_empty_alias_string_falls_back_to_none() -> None:
    """`''` in the alias dict is treated as 'not set' so renderers fall back."""
    devices = [{'hwid': 'A', 'platform': 'iOS'}]

    result = attach_aliases_to_devices(devices, {'A': ''})

    # Empty alias → None (caller can do `device.local_name or device.device_model`).
    assert result[0]['local_name'] is None
