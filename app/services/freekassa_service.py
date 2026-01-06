"""Сервис для работы с API Freekassa."""

import hashlib
import time
import logging
from typing import Optional, Dict, Any, Set

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)

# IP-адреса Freekassa для проверки webhook
FREEKASSA_IPS: Set[str] = {
    "168.119.157.136",
    "168.119.60.227",
    "178.154.197.79",
    "51.250.54.238",
}

API_BASE_URL = "https://api.fk.life/v1"


class FreekassaService:
    """Сервис для работы с API Freekassa."""

    def __init__(self):
        self._shop_id: Optional[int] = None
        self._api_key: Optional[str] = None
        self._secret1: Optional[str] = None
        self._secret2: Optional[str] = None

    @property
    def shop_id(self) -> int:
        if self._shop_id is None:
            self._shop_id = settings.FREEKASSA_SHOP_ID
        return self._shop_id or 0

    @property
    def api_key(self) -> str:
        if self._api_key is None:
            self._api_key = settings.FREEKASSA_API_KEY
        return self._api_key or ""

    @property
    def secret1(self) -> str:
        if self._secret1 is None:
            self._secret1 = settings.FREEKASSA_SECRET_WORD_1
        return self._secret1 or ""

    @property
    def secret2(self) -> str:
        if self._secret2 is None:
            self._secret2 = settings.FREEKASSA_SECRET_WORD_2
        return self._secret2 or ""

    def _generate_api_signature(self, params: Dict[str, Any]) -> str:
        """
        Генерирует подпись для API запроса.
        Сортировка по ключам, конкатенация значений через |
        """
        sorted_keys = sorted(params.keys())
        values = [str(params[k]) for k in sorted_keys if params[k] is not None]
        sign_string = "|".join(values)
        return hashlib.md5(sign_string.encode()).hexdigest()

    def generate_form_signature(
        self, amount: float, currency: str, order_id: str
    ) -> str:
        """
        Генерирует подпись для платежной формы.
        Формат: MD5(shop_id:amount:secret1:currency:order_id)
        """
        sign_string = f"{self.shop_id}:{amount}:{self.secret1}:{currency}:{order_id}"
        return hashlib.md5(sign_string.encode()).hexdigest()

    def verify_webhook_signature(
        self, shop_id: int, amount: float, order_id: str, sign: str
    ) -> bool:
        """
        Проверяет подпись webhook уведомления.
        Формат: MD5(shop_id:amount:secret2:order_id)
        """
        expected_sign = hashlib.md5(
            f"{shop_id}:{amount}:{self.secret2}:{order_id}".encode()
        ).hexdigest()
        return sign.lower() == expected_sign.lower()

    def verify_webhook_ip(self, ip: str) -> bool:
        """Проверяет, что IP входит в разрешенный список Freekassa."""
        return ip in FREEKASSA_IPS

    def build_payment_url(
        self,
        order_id: str,
        amount: float,
        currency: str = "RUB",
        email: Optional[str] = None,
        phone: Optional[str] = None,
        payment_system_id: Optional[int] = None,
        lang: str = "ru",
    ) -> str:
        """
        Формирует URL для перенаправления на оплату.
        """
        signature = self.generate_form_signature(amount, currency, order_id)

        params = {
            "m": self.shop_id,
            "oa": amount,
            "currency": currency,
            "o": order_id,
            "s": signature,
            "lang": lang,
        }

        if email:
            params["em"] = email
        if phone:
            params["phone"] = phone
        if payment_system_id:
            params["i"] = payment_system_id

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"https://pay.freekassa.ru/?{query}"

    async def create_order(
        self,
        order_id: str,
        amount: float,
        currency: str = "RUB",
        email: Optional[str] = None,
        ip: Optional[str] = None,
        payment_system_id: Optional[int] = None,
        success_url: Optional[str] = None,
        failure_url: Optional[str] = None,
        notification_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Создает заказ через API Freekassa.
        POST /orders/create
        """
        params = {
            "shopId": self.shop_id,
            "nonce": int(time.time() * 1000),
            "paymentId": order_id,
            "i": payment_system_id or 1,
            "email": email or "user@example.com",
            "ip": ip or "127.0.0.1",
            "amount": amount,
            "currency": currency,
        }

        if success_url:
            params["success_url"] = success_url
        if failure_url:
            params["failure_url"] = failure_url
        if notification_url:
            params["notification_url"] = notification_url

        params["signature"] = self._generate_api_signature(params)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE_URL}/orders/create",
                    json=params,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    data = await response.json()

                    if response.status != 200 or data.get("type") == "error":
                        logger.error(f"Freekassa create_order error: {data}")
                        raise Exception(
                            f"Freekassa API error: {data.get('message', 'Unknown error')}"
                        )

                    return data
        except aiohttp.ClientError as e:
            logger.exception(f"Freekassa API connection error: {e}")
            raise

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """
        Получает статус заказа.
        POST /orders
        """
        params = {
            "shopId": self.shop_id,
            "nonce": int(time.time() * 1000),
            "paymentId": order_id,
        }
        params["signature"] = self._generate_api_signature(params)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE_URL}/orders",
                    json=params,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    return await response.json()
        except aiohttp.ClientError as e:
            logger.exception(f"Freekassa API connection error: {e}")
            raise

    async def get_balance(self) -> Dict[str, Any]:
        """Получает баланс магазина."""
        params = {
            "shopId": self.shop_id,
            "nonce": int(time.time() * 1000),
        }
        params["signature"] = self._generate_api_signature(params)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE_URL}/balance",
                    json=params,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    return await response.json()
        except aiohttp.ClientError as e:
            logger.exception(f"Freekassa API connection error: {e}")
            raise

    async def get_payment_systems(self) -> Dict[str, Any]:
        """Получает список доступных платежных систем."""
        params = {
            "shopId": self.shop_id,
            "nonce": int(time.time() * 1000),
        }
        params["signature"] = self._generate_api_signature(params)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE_URL}/currencies",
                    json=params,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    return await response.json()
        except aiohttp.ClientError as e:
            logger.exception(f"Freekassa API connection error: {e}")
            raise


# Singleton instance
freekassa_service = FreekassaService()
