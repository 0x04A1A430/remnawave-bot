"""Сервис для работы с API RioPay (api.riopay.online)."""

import asyncio
from typing import Any

import aiohttp
import structlog

from app.config import settings


logger = structlog.get_logger(__name__)

API_BASE_URL = 'https://api.riopay.online'

# Кэш для публичного ключа
_cached_pubkey: str | None = None
_pubkey_lock = asyncio.Lock()


class RioPayService:
    """Сервис для работы с API RioPay."""

    def __init__(self):
        self._api_token: str | None = None
        self._jwt_token: str | None = None
        self._merchant_id: str | None = None

    @property
    def api_token(self) -> str:
        if self._api_token is None:
            self._api_token = settings.RIOPAY_API_TOKEN
        return self._api_token or ''

    @property
    def jwt_token(self) -> str:
        if self._jwt_token is None:
            self._jwt_token = settings.RIOPAY_JWT_TOKEN
        return self._jwt_token or ''

    @property
    def merchant_id(self) -> str | None:
        if self._merchant_id is None:
            self._merchant_id = settings.RIOPAY_MERCHANT_ID
        return self._merchant_id

    def _get_headers(self) -> dict[str, str]:
        """Формирует заголовки для API запросов."""
        headers = {
            'Authorization': f'Bearer {self.jwt_token}',
            'x-api-token': self.api_token,
            'Content-Type': 'application/json',
        }
        if self.merchant_id:
            headers['x-merchant-id'] = self.merchant_id
        return headers

    async def create_order(
        self,
        *,
        amount: float,
        currency: str = 'RUB',
        external_id: str,
        purpose: str = 'Пополнение баланса',
        success_url: str | None = None,
        fail_url: str | None = None,
    ) -> dict[str, Any]:
        """
        Создает заказ через API RioPay.
        POST /v1/orders

        Returns:
            OrderData dict с полями id, status, paymentLink, amount, currency, etc.
        """
        payload: dict[str, Any] = {
            'amount': amount,
            'currency': currency,
            'externalId': external_id,
            'purpose': purpose,
        }

        if success_url:
            payload['successUrl'] = success_url
        if fail_url:
            payload['failUrl'] = fail_url

        logger.info(
            'RioPay API create_order',
            external_id=external_id,
            amount=amount,
            currency=currency,
        )

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    f'{API_BASE_URL}/v1/orders',
                    json=payload,
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response,
            ):
                text = await response.text()
                logger.info('RioPay API response', status_code=response.status, text=text)

                if response.status == 201:
                    data = await response.json(content_type=None)
                    return data

                # Ошибка
                try:
                    error_data = await response.json(content_type=None)
                    error_msg = error_data.get('message') or error_data.get('error') or text
                except Exception:
                    error_msg = text

                logger.error('RioPay create_order error', status_code=response.status, error_msg=error_msg)
                raise Exception(f'RioPay API error ({response.status}): {error_msg}')

        except aiohttp.ClientError as e:
            logger.exception('RioPay API connection error', error=e)
            raise

    async def get_order(self, order_id: str) -> dict[str, Any]:
        """
        Получает заказ по UUID.
        GET /v1/orders/{id}
        """
        logger.info('RioPay get_order', order_id=order_id)

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f'{API_BASE_URL}/v1/orders/{order_id}',
                    headers=self._get_headers(),
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response,
            ):
                text = await response.text()
                logger.info('RioPay get_order response', status_code=response.status, text=text)

                if response.status == 200:
                    return await response.json(content_type=None)

                raise Exception(f'RioPay get_order error ({response.status}): {text}')

        except aiohttp.ClientError as e:
            logger.exception('RioPay API connection error', error=e)
            raise

    async def get_public_key(self) -> str:
        """
        Получает публичный ключ для проверки подписи вебхуков.
        GET /v1/orders/pubkey
        Результат кэшируется.
        """
        global _cached_pubkey

        if _cached_pubkey:
            return _cached_pubkey

        async with _pubkey_lock:
            if _cached_pubkey:
                return _cached_pubkey

            try:
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(
                        f'{API_BASE_URL}/v1/orders/pubkey',
                        headers=self._get_headers(),
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response,
                ):
                    if response.status == 200:
                        pubkey = await response.text()
                        _cached_pubkey = pubkey.strip()
                        logger.info('RioPay: получен публичный ключ для верификации')
                        return _cached_pubkey

                    text = await response.text()
                    logger.error('RioPay pubkey error', status_code=response.status, text=text)
                    raise Exception(f'RioPay pubkey error ({response.status}): {text}')

            except aiohttp.ClientError as e:
                logger.exception('RioPay pubkey connection error', error=e)
                raise

    async def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """
        Проверяет подпись webhook через публичный ключ RSA/ECDSA.

        Args:
            raw_body: Сырое тело запроса (bytes)
            signature: Подпись из заголовка (base64-encoded)

        Returns:
            True если подпись валидна
        """
        try:
            import base64

            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import ec, padding, utils

            pubkey_pem = await self.get_public_key()

            public_key = serialization.load_pem_public_key(pubkey_pem.encode())
            signature_bytes = base64.b64decode(signature)

            # Определяем тип ключа и проверяем подпись
            if hasattr(public_key, 'key_size'):
                # RSA ключ
                public_key.verify(
                    signature_bytes,
                    raw_body,
                    padding.PKCS1v15(),
                    hashes.SHA256(),
                )
            else:
                # ECDSA ключ
                public_key.verify(
                    signature_bytes,
                    raw_body,
                    ec.ECDSA(hashes.SHA256()),
                )

            return True

        except Exception as e:
            logger.error('RioPay webhook verify error', error=e)
            return False


# Singleton instance
riopay_service = RioPayService()
