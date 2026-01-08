"""Сервис для работы с API Freekassa."""

import hashlib
import hmac
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

    def _generate_api_signature_hmac(self, params: Dict[str, Any]) -> str:
        """
        Генерирует подпись для API запроса (HMAC-SHA256).
        Используется для API методов (создание заказа и т.д.)
        """
        # Исключаем signature из параметров и сортируем по ключу
        sign_data = {k: v for k, v in params.items() if k != "signature"}
        sorted_items = sorted(sign_data.items())

        # Формируем строку: значения через |
        msg = "|".join(str(v) for _, v in sorted_items)

        # HMAC-SHA256
        return hmac.new(
            self.api_key.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _generate_api_signature(self, params: Dict[str, Any]) -> str:
        """
        Генерирует подпись для API запроса.
        Для новых API методов используется HMAC-SHA256.
        """
        return self._generate_api_signature_hmac(params)

    def generate_form_signature(
        self, amount: float, currency: str, order_id: str
    ) -> str:
        """
        Генерирует подпись для платежной формы.
        Формат: MD5(shop_id:amount:secret1:currency:order_id)
        """
        # Приводим amount к int, если это целое число
        final_amount = int(amount) if float(amount).is_integer() else amount
        sign_string = f"{self.shop_id}:{final_amount}:{self.secret1}:{currency}:{order_id}"
        return hashlib.md5(sign_string.encode()).hexdigest()

    def verify_webhook_signature(
        self, shop_id: int, amount: float, order_id: str, sign: str
    ) -> bool:
        """
        Проверяет подпись webhook уведомления.
        Формат: MD5(shop_id:amount:secret2:order_id)
        """
        # Приводим amount к int, если это целое число
        final_amount = int(amount) if float(amount).is_integer() else amount
        expected_sign = hashlib.md5(
            f"{shop_id}:{final_amount}:{self.secret2}:{order_id}".encode()
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
        Формирует URL для перенаправления на оплату (форма выбора).
        Используется когда FREEKASSA_USE_API = False.
        """
        # Приводим amount к int, если это целое число
        final_amount = int(amount) if float(amount).is_integer() else amount
        signature = self.generate_form_signature(final_amount, currency, order_id)

        params = {
            "m": self.shop_id,
            "oa": final_amount,
            "currency": currency,
            "o": order_id,
            "s": signature,
            "lang": lang,
        }

        if email:
            params["em"] = email
        if phone:
            params["phone"] = phone

        # Используем payment_system_id из настроек, если не передан явно
        ps_id = payment_system_id or settings.FREEKASSA_PAYMENT_SYSTEM_ID
        if ps_id:
            params["i"] = ps_id

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

        Используется для NSPK СБП (payment_system_id=44) и других методов.
        Возвращает словарь с 'location' (ссылка на оплату).
        """
        # Приводим amount к int, если это целое число
        final_amount = int(amount) if float(amount).is_integer() else amount

        # Используем payment_system_id из настроек, если не передан явно
        ps_id = payment_system_id or settings.FREEKASSA_PAYMENT_SYSTEM_ID or 1

        params = {
            "shopId": self.shop_id,
            "nonce": int(time.time_ns()),  # Наносекунды для уникальности
            "paymentId": str(order_id),
            "i": ps_id,
            "email": email or "user@example.com",
            "ip": ip or "127.0.0.1",
            "amount": final_amount,
            "currency": currency,
        }

        # Генерируем подпись HMAC-SHA256
        params["signature"] = self._generate_api_signature(params)

        logger.info(f"Freekassa API create_order params: {params}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE_URL}/orders/create",
                    json=params,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    text = await response.text()
                    logger.info(f"Freekassa API response: {text}")

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

    async def create_order_and_get_url(
        self,
        order_id: str,
        amount: float,
        currency: str = "RUB",
        email: Optional[str] = None,
        ip: Optional[str] = None,
        payment_system_id: Optional[int] = None,
    ) -> str:
        """
        Создает заказ через API и возвращает URL для оплаты.
        Удобный метод для получения только ссылки.
        """
        result = await self.create_order(
            order_id=order_id,
            amount=amount,
            currency=currency,
            email=email,
            ip=ip,
            payment_system_id=payment_system_id,
        )
        location = result.get("location")
        if not location:
            raise Exception("Freekassa API did not return payment URL (location)")
        return location

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """
        Получает статус заказа.
        POST /orders
        """
        params = {
            "shopId": self.shop_id,
            "nonce": int(time.time_ns()),
            "paymentId": str(order_id),
        }
        params["signature"] = self._generate_api_signature(params)

        logger.info(f"Freekassa get_order_status params: {params}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{API_BASE_URL}/orders",
                    json=params,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    text = await response.text()
                    logger.info(f"Freekassa get_order_status response: {text}")
                    return await response.json()
        except aiohttp.ClientError as e:
            logger.exception(f"Freekassa API connection error: {e}")
            raise

    async def get_balance(self) -> Dict[str, Any]:
        """Получает баланс магазина."""
        params = {
            "shopId": self.shop_id,
            "nonce": int(time.time_ns()),
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
            "nonce": int(time.time_ns()),
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
