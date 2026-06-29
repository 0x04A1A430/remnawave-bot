"""HWID device-stats aggregation must survive the Remnawave 2.8.0 reshape.

In 2.8.0 the top-level ``byApp`` array was removed; the per-app breakdown now
lives nested under ``byPlatform[].byApp``. ``get_devices_statistics`` must
aggregate the nested form (2.8.0) while still honouring the old top-level form
(2.7.x panels).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.remnawave_service import RemnaWaveService


def _service_with_api(api: MagicMock) -> RemnaWaveService:
    service = RemnaWaveService()
    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=api)
    acm.__aexit__ = AsyncMock(return_value=False)
    service.get_api_client = MagicMock(return_value=acm)
    return service


async def test_devices_statistics_aggregates_nested_byapp_2_8_0():
    api = MagicMock()
    api.get_hwid_devices_stats = AsyncMock(
        return_value={
            'byPlatform': [
                {
                    'platform': 'iOS',
                    'count': 3,
                    'byApp': [{'app': 'Happ', 'count': 2}, {'app': 'Streisand', 'count': 1}],
                },
                {'platform': 'Android', 'count': 2, 'byApp': [{'app': 'Happ', 'count': 2}]},
            ],
            'stats': {'totalUniqueDevices': 5, 'totalHwidDevices': 5, 'averageHwidDevicesPerUser': 1.0},
        }
    )
    api.get_hwid_top_users = AsyncMock(return_value={'users': []})

    result = await _service_with_api(api).get_devices_statistics()

    by_app = {e['app']: e['count'] for e in result['by_app']}
    assert by_app == {'Happ': 4, 'Streisand': 1}  # summed across platforms
    assert {e['platform']: e['count'] for e in result['by_platform']} == {'iOS': 3, 'Android': 2}
    assert result['total_unique_devices'] == 5


async def test_devices_statistics_prefers_top_level_byapp_2_7_x():
    api = MagicMock()
    api.get_hwid_devices_stats = AsyncMock(
        return_value={
            'byPlatform': [{'platform': 'iOS', 'count': 1}],
            'byApp': [{'app': 'Happ', 'count': 7}],
            'stats': {},
        }
    )
    api.get_hwid_top_users = AsyncMock(return_value={'users': []})

    result = await _service_with_api(api).get_devices_statistics()

    assert result['by_app'] == [{'app': 'Happ', 'count': 7}]
