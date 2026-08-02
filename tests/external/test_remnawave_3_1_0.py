"""Regression tests for the Remnawave 3.1.0 API changes.

Covers the additive changes that affect this bot:
  * node objects now carry a numeric ``id`` alongside ``uuid``;
  * subscription request history records now include ``srrResponseType``
    and ``srrRuleName`` (records are passed through as raw dicts).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.external.remnawave_api import RemnaWaveAPI, RemnaWaveNode


def _api() -> RemnaWaveAPI:
    return RemnaWaveAPI('http://panel.local', 'key')


def _node_minimal(id_: int | None = 7) -> dict:
    return {
        'uuid': 'node-uuid',
        'id': id_,
        'name': 'DE-Server',
        'address': '10.0.0.1',
        'countryCode': 'DE',
        'isConnected': True,
        'isDisabled': False,
        'usersOnline': 3,
        'activePluginUuid': 'plugin-1',
    }


async def test_parse_node_populates_numeric_id():
    api = _api()
    node = api._parse_node(_node_minimal())

    assert node.id == 7
    assert node.uuid == 'node-uuid'
    assert isinstance(node, RemnaWaveNode)


async def test_parse_node_id_optional_when_absent():
    api = _api()
    node = api._parse_node(_node_minimal(id_=None))

    assert node.id is None


async def test_get_all_nodes_returns_id():
    api = _api()
    api._make_request = AsyncMock(return_value={'response': [_node_minimal()]})

    nodes = await api.get_all_nodes()

    assert len(nodes) == 1
    assert nodes[0].id == 7
    assert nodes[0].uuid == 'node-uuid'


async def test_get_node_by_uuid_returns_id():
    api = _api()
    api._make_request = AsyncMock(return_value={'response': _node_minimal()})

    node = await api.get_node_by_uuid('node-uuid')

    assert node is not None
    assert node.id == 7


async def test_subscription_request_history_passes_srr_fields_through():
    api = _api()
    api._make_request = AsyncMock(
        return_value={
            'response': {
                'total': 1,
                'records': [
                    {
                        'id': 10,
                        'userId': 5,
                        'requestAt': '2026-01-01T00:00:00.000Z',
                        'requestIp': '1.2.3.4',
                        'userAgent': 'clash',
                        'srrResponseType': 'BASE',
                        'srrRuleName': 'my-rule',
                    }
                ],
            }
        }
    )

    result = await api.get_subscription_request_history('panel-uuid')

    assert result['total'] == 1
    record = result['records'][0]
    assert record['srrResponseType'] == 'BASE'
    assert record['srrRuleName'] == 'my-rule'
