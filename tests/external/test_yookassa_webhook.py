import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.config import settings
from app.external.yookassa_webhook import create_yookassa_webhook_app


class DummyDB:
    async def close(self) -> None:  # pragma: no cover - simple stub
        pass
@pytest.fixture(autouse=True)
def configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "YOOKASSA_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_SHOP_ID", "shop", raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_SECRET_KEY", "key", raising=False)
    monkeypatch.setattr(settings, "YOOKASSA_WEBHOOK_PATH", "/yookassa-webhook", raising=False)


def _build_headers(**overrides: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    headers.update(overrides)
    return headers


async def _post_webhook(client: TestClient, payload: dict, **headers: str) -> web.Response:
    body = json.dumps(payload, ensure_ascii=False)
    return await client.post(
        settings.YOOKASSA_WEBHOOK_PATH,
        data=body.encode("utf-8"),
        headers=_build_headers(**headers),
    )


def _patch_get_db(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get_db():
        yield DummyDB()

    monkeypatch.setattr("app.external.yookassa_webhook.get_db", fake_get_db)


@pytest.mark.asyncio
async def test_handle_webhook_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        payload = {"event": "payment.succeeded"}
        body = json.dumps(payload, ensure_ascii=False)
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=body.encode("utf-8"),
            headers=_build_headers(),
        )
        status = response.status
        text = await response.text()

    assert status == 200
    assert text == "OK"
    process_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_webhook_with_optional_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        payload = {"event": "payment.succeeded"}
        body = json.dumps(payload, ensure_ascii=False)
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=body.encode("utf-8"),
            headers=_build_headers(Signature="test-signature"),
        )
        status = response.status
        text = await response.text()

    assert status == 200
    assert text == "OK"
    process_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_webhook_accepts_canceled_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        payload = {"event": "payment.canceled", "object": {"id": "yk_1"}}
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=json.dumps(payload).encode("utf-8"),
            headers=_build_headers(),
        )

        status = response.status

    assert status == 200
    process_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_webhook_rejects_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=b"",
            headers=_build_headers(),
        )

    assert response.status == 400
    process_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handle_webhook_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=b"{not-json}",
            headers=_build_headers(),
        )

    assert response.status == 400
    process_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handle_webhook_requires_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=json.dumps({}).encode("utf-8"),
            headers=_build_headers(),
        )

    assert response.status == 400
    process_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handle_webhook_ignores_unhandled_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get_db(monkeypatch)

    process_mock = AsyncMock(return_value=True)
    service = SimpleNamespace(process_yookassa_webhook=process_mock)

    app = create_yookassa_webhook_app(service)
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            settings.YOOKASSA_WEBHOOK_PATH,
            data=json.dumps({"event": "unknown.event"}).encode("utf-8"),
            headers=_build_headers(),
        )
        status = response.status
        text = await response.text()

    assert status == 200
    assert text == "OK"
    process_mock.assert_not_called()
