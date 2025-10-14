from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import settings  # noqa: E402
from app.services.wata_service import WataService  # noqa: E402


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _enable_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(type(settings), "is_wata_enabled", lambda self: True, raising=False)
    monkeypatch.setattr(settings, "WATA_ACCESS_TOKEN", "token", raising=False)
    monkeypatch.setattr(settings, "WATA_BASE_URL", "https://wata.test", raising=False)


def test_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    service = WataService()
    assert not service.is_configured

    _enable_service(monkeypatch)
    service = WataService()
    assert service.is_configured


@pytest.mark.anyio("asyncio")
async def test_create_payment_link(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_service(monkeypatch)

    captured: Dict[str, Any] = {}

    async def fake_request(method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
        captured.update({"method": method, "endpoint": endpoint, **kwargs})
        return {"id": "link", "url": "https://pay"}

    service = WataService()
    monkeypatch.setattr(service, "_request", fake_request, raising=False)

    response = await service.create_payment_link(amount="100.00", orderId="test")

    assert response == {"id": "link", "url": "https://pay"}
    assert captured["method"] == "POST"
    assert captured["endpoint"] == "/links"
    assert captured["json_data"]["orderId"] == "test"


@pytest.mark.anyio("asyncio")
async def test_get_public_key_caching(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_service(monkeypatch)
    service = WataService()

    calls: list[Dict[str, Any]] = []

    async def fake_request(method: str, endpoint: str, **kwargs: Any) -> Dict[str, Any]:
        calls.append({"method": method, "endpoint": endpoint})
        return {"value": "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----"}

    monkeypatch.setattr(service, "_request", fake_request, raising=False)

    key1 = await service.get_public_key()
    key2 = await service.get_public_key()

    assert key1 == key2
    assert len(calls) == 1


@pytest.mark.anyio("asyncio")
async def test_verify_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_service(monkeypatch)
    service = WataService()

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    async def fake_get_public_key(*args: Any, **kwargs: Any) -> Optional[str]:
        return public_key

    monkeypatch.setattr(service, "get_public_key", fake_get_public_key, raising=False)

    payload = b"{\"event\":\"test\"}"
    signature = private_key.sign(
        payload,
        padding.PKCS1v15(),
        hashes.SHA512(),
    )

    signature_b64 = base64.b64encode(signature).decode()

    assert await service.verify_signature(payload, signature_b64)

