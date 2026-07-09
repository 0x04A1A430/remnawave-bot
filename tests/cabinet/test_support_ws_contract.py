from __future__ import annotations

import base64
import hashlib
import types
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.cabinet.routes import support_ws


def _context(user_id: int = 10) -> support_ws.WsUserContext:
    user = types.SimpleNamespace(
        id=user_id,
        telegram_id=1000 + user_id,
        email=None,
        email_verified=False,
        username='mobile_user',
        first_name='Mobile',
        last_name='User',
        status='active',
    )
    return support_ws.WsUserContext(user=user, token_payload={'sub': str(user_id)}, role='owner')


def _session() -> support_ws.SupportWsSession:
    websocket = types.SimpleNamespace()
    return support_ws.SupportWsSession(websocket=websocket, context=_context())


def _ws_client() -> TestClient:
    app = FastAPI()
    app.include_router(support_ws.router, prefix='/cabinet')
    return TestClient(app)


def test_shared_error_contract_uses_integer_or_null_retry_after() -> None:
    error = support_ws._shared_error('RATE_LIMITED', 'Try later', retry_after_ms=2500)
    assert error['retryAfterMs'] == 2500
    assert isinstance(error['retryAfterMs'], int)
    assert error['details'] == {}
    assert error['backpressure'] is None

    no_retry_after = support_ws._shared_error('VALIDATION_ERROR', 'Invalid')
    assert no_retry_after['retryAfterMs'] is None


def test_support_ws_rejects_query_token_auth() -> None:
    with _ws_client() as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            '/cabinet/ws/support/v1?token=query-token',
            subprotocols=[support_ws.SUPPORTED_SUBPROTOCOL],
        ):
            pass


def test_support_ws_rejects_missing_subprotocol() -> None:
    with _ws_client() as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/cabinet/ws/support/v1', headers={'authorization': 'Bearer token'}):
            pass


def test_support_ws_accepts_bearer_and_echoes_supported_subprotocol(monkeypatch) -> None:
    async def fake_authenticate(db, websocket, *, access_token=None):
        return _context(), None

    monkeypatch.setattr(support_ws, '_authenticate_ws', fake_authenticate)

    with _ws_client() as client:
        with client.websocket_connect(
            '/cabinet/ws/support/v1',
            headers={'authorization': 'Bearer access-token'},
            subprotocols=['legacy', support_ws.SUPPORTED_SUBPROTOCOL],
        ) as websocket:
            assert websocket.accepted_subprotocol == support_ws.SUPPORTED_SUBPROTOCOL
            ready = websocket.receive_json()
            assert ready['event'] == 'connection.ready'
            assert ready['payload']['mediaDownload']['transport'] == 'websocket'


def test_ticket_snapshot_keeps_assignment_fields_explicitly_nullable() -> None:
    message = types.SimpleNamespace(
        id=7,
        ticket_id=3,
        user_id=10,
        is_from_admin=False,
        message_text='hello',
        media_file_id=None,
        media_items=None,
        created_at=datetime(2026, 7, 9, 0, 0, tzinfo=UTC),
    )
    ticket = types.SimpleNamespace(
        id=3,
        title='Need help',
        status='open',
        priority='normal',
        created_at=datetime(2026, 7, 9, 0, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 9, 0, 1, tzinfo=UTC),
        closed_at=None,
        messages=[message],
        user=None,
    )

    snapshot = support_ws._ticket_snapshot(ticket, include_messages=True)

    assert snapshot['assignedTo'] is None
    assert snapshot['messages'][0]['attachments'] == []


@pytest.mark.asyncio
async def test_upload_lifecycle_makes_media_attachable_only_after_finish(monkeypatch) -> None:
    session = _session()
    data = b'hello websocket media'
    digest = hashlib.sha256(data).hexdigest()

    async def fake_upload_to_telegram(upload: support_ws.UploadTransfer) -> dict:
        assert bytes(upload.chunks) == data
        return {
            'mediaId': 'telegram-file-id',
            'fileUniqueId': 'unique-id',
            'type': upload.media_type,
            'fileName': upload.file_name,
            'contentType': upload.content_type,
            'sizeBytes': len(upload.chunks),
            'caption': None,
        }

    monkeypatch.setattr(support_ws, '_upload_to_telegram', fake_upload_to_telegram)

    begin = await support_ws._handle_upload_begin(
        types.SimpleNamespace(),
        session,
        {
            'fileName': 'proof.png',
            'contentType': 'image/png',
            'mediaType': 'photo',
            'sizeBytes': len(data),
        },
    )
    assert session.completed_media == {}

    chunk = await support_ws._handle_upload_chunk(
        session,
        {
            'uploadId': begin['uploadId'],
            'offset': 0,
            'data': base64.b64encode(data).decode(),
        },
    )
    assert chunk['receivedBytes'] == len(data)

    finish = await support_ws._handle_upload_finish(
        session,
        {'uploadId': begin['uploadId'], 'checksumSha256': digest},
    )
    assert finish['mediaId'] == 'telegram-file-id'
    assert session.completed_media['telegram-file-id']['sha256'] == digest


@pytest.mark.asyncio
async def test_upload_finish_rejects_checksum_mismatch(monkeypatch) -> None:
    session = _session()
    data = b'corrupted'
    begin = await support_ws._handle_upload_begin(
        types.SimpleNamespace(),
        session,
        {
            'fileName': 'safe.txt',
            'contentType': 'text/plain',
            'mediaType': 'document',
            'sizeBytes': len(data),
        },
    )
    await support_ws._handle_upload_chunk(
        session,
        {
            'uploadId': begin['uploadId'],
            'offset': 0,
            'data': base64.b64encode(data).decode(),
        },
    )

    with pytest.raises(RuntimeError, match='UPLOAD_CHECKSUM_MISMATCH'):
        await support_ws._handle_upload_finish(session, {'uploadId': begin['uploadId'], 'checksumSha256': 'bad'})


def test_ticket_create_declared_out_of_scope_in_ready_event() -> None:
    event = support_ws._message_event(
        'connection.ready',
        {
            'ticketCreate': {'supported': False, 'reason': 'mobile_admin_support_scope_excludes_ticket_create'},
            'mediaDownload': {'transport': 'websocket'},
            'assignment': {'assignedTo': None, 'previousAssignedTo': None},
        },
    )

    assert event['payload']['ticketCreate']['supported'] is False
    assert event['payload']['mediaDownload']['transport'] == 'websocket'
    assert event['payload']['assignment']['assignedTo'] is None
    assert event['payload']['assignment']['previousAssignedTo'] is None
