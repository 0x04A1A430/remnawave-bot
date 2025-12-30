import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional, Dict, Any, List
from decimal import Decimal

# Используем локальную исправленную версию библиотеки
from app.lib.nalogo import Client
from app.lib.nalogo.dto.income import IncomeClient, IncomeType, MOSCOW_TZ

from app.config import settings
from app.utils.cache import cache

logger = logging.getLogger(__name__)

NALOGO_QUEUE_KEY = "nalogo:receipt_queue"


class NaloGoService:
    """Сервис для работы с API NaloGO (налоговая служба самозанятых)."""

    def __init__(self,
                 inn: Optional[str] = None,
                 password: Optional[str] = None,
                 device_id: Optional[str] = None,
                 storage_path: Optional[str] = None):

        inn = inn or getattr(settings, 'NALOGO_INN', None)
        password = password or getattr(settings, 'NALOGO_PASSWORD', None)
        device_id = device_id or getattr(settings, 'NALOGO_DEVICE_ID', None)
        storage_path = storage_path or getattr(settings, 'NALOGO_STORAGE_PATH', './nalogo_tokens.json')

        self.configured = False

        if not inn or not password:
            logger.warning(
                "NaloGO INN или PASSWORD не настроены в settings. "
                "Функционал чеков будет ОТКЛЮЧЕН.")
        else:
            try:
                self.client = Client(
                    base_url="https://lknpd.nalog.ru/api",
                    storage_path=storage_path,
                    device_id=device_id or "bot-device-123"
                )
                self.inn = inn
                self.password = password
                self.configured = True
                logger.info(f"NaloGO клиент инициализирован для ИНН: {inn[:5]}...")
            except Exception as error:
                logger.error(
                    "Ошибка инициализации NaloGO клиента: %s",
                    error,
                    exc_info=True,
                )
                self.configured = False

    @staticmethod
    def _is_service_unavailable(error: Exception) -> bool:
        """Проверяет, является ли ошибка временной недоступностью сервиса."""
        error_str = str(error).lower()
        error_type = type(error).__name__.lower()
        return (
            "503" in error_str
            or "500" in error_str
            or "internal server error" in error_str
            or "внутренняя ошибка" in error_str
            or "service temporarily unavailable" in error_str
            or "service unavailable" in error_str
            or "ведутся работы" in error_str
            or ("health" in error_str and "false" in error_str)
            # Таймауты и сетевые ошибки — временные проблемы
            or "timeout" in error_type
            or "timeout" in error_str
            or "readtimeout" in error_type
            or "connecttimeout" in error_type
            or "connectionerror" in error_type
            or "connecterror" in error_type
        )

    async def _queue_receipt(
        self,
        name: str,
        amount: float,
        quantity: int,
        client_info: Optional[Dict[str, Any]],
        payment_id: Optional[str] = None,
        telegram_user_id: Optional[int] = None,
        amount_kopeks: Optional[int] = None,
    ) -> bool:
        """Добавить чек в очередь для отложенной отправки."""
        if payment_id:
            # Защита от дубликатов: проверяем не был ли чек уже создан
            created_key = f"nalogo:created:{payment_id}"
            already_created = await cache.get(created_key)
            if already_created:
                logger.info(
                    f"Чек для payment_id={payment_id} уже создан ({already_created}), "
                    "не добавляем в очередь"
                )
                return False

            # Атомарная проверка и установка флага "в очереди" (защита от race condition)
            queued_key = f"nalogo:queued:{payment_id}"
            lock_acquired = await cache.setnx(queued_key, "queued", expire=7 * 24 * 3600)
            if not lock_acquired:
                # Ключ уже существует — чек уже в очереди
                logger.info(
                    f"Чек для payment_id={payment_id} уже в очереди, пропускаем дубликат"
                )
                return False

        receipt_data = {
            "name": name,
            "amount": amount,
            "quantity": quantity,
            "client_info": client_info,
            "payment_id": payment_id,
            "telegram_user_id": telegram_user_id,
            "amount_kopeks": amount_kopeks,
            "created_at": datetime.now().isoformat(),
            "attempts": 0,
        }
        success = await cache.lpush(NALOGO_QUEUE_KEY, receipt_data)
        if success:
            queue_len = await cache.llen(NALOGO_QUEUE_KEY)
            logger.info(
                f"Чек добавлен в очередь (payment_id={payment_id}, "
                f"сумма={amount}₽, в очереди: {queue_len})"
            )
        else:
            # Если не удалось добавить в очередь — удаляем флаг
            if payment_id:
                queued_key = f"nalogo:queued:{payment_id}"
                await cache.delete(queued_key)
        return success

    async def authenticate(self) -> bool:
        """Аутентификация в сервисе NaloGO."""
        if not self.configured:
            return False

        try:
            token = await self.client.create_new_access_token(self.inn, self.password)
            await self.client.authenticate(token)
            logger.info("Успешная аутентификация в NaloGO")
            return True
        except Exception as error:
            if self._is_service_unavailable(error):
                logger.warning(
                    "NaloGO временно недоступен (техработы): %s",
                    str(error)[:200]
                )
            else:
                logger.error("Ошибка аутентификации в NaloGO: %s", error, exc_info=True)
            return False

    async def create_receipt(
        self,
        name: str,
        amount: float,
        quantity: int = 1,
        client_info: Optional[Dict[str, Any]] = None,
        payment_id: Optional[str] = None,
        queue_on_failure: bool = True,
        telegram_user_id: Optional[int] = None,
        amount_kopeks: Optional[int] = None,
        operation_time: Optional[datetime] = None,
    ) -> Optional[str]:
        """Создание чека о доходе.

        Args:
            name: Название услуги
            amount: Сумма в рублях
            quantity: Количество
            client_info: Информация о клиенте (опционально)
            payment_id: ID платежа для логирования
            queue_on_failure: Добавить в очередь при временной недоступности
            telegram_user_id: Telegram ID пользователя для формирования описания
            amount_kopeks: Сумма в копейках для формирования описания
            operation_time: Время операции (по умолчанию текущее)

        Returns:
            UUID чека или None при ошибке
        """
        if not self.configured:
            logger.warning("NaloGO не настроен, чек не создан")
            return None

        # Защита от дублей: проверяем не был ли уже создан чек для этого payment_id
        if payment_id:
            created_key = f"nalogo:created:{payment_id}"
            already_created = await cache.get(created_key)
            if already_created:
                logger.info(
                    f"Чек для payment_id={payment_id} уже был создан ({already_created}), "
                    "пропускаем повторное создание"
                )
                return already_created  # Возвращаем ранее созданный uuid

        try:
            # Аутентифицируемся, если нужно
            if not hasattr(self.client, '_access_token') or not self.client._access_token:
                auth_success = await self.authenticate()
                if not auth_success:
                    # Если сервис недоступен — добавляем в очередь
                    if queue_on_failure:
                        await self._queue_receipt(
                            name, amount, quantity, client_info, payment_id,
                            telegram_user_id, amount_kopeks
                        )
                    return None

            income_api = self.client.income()

            # Создаем клиента, если передана информация
            income_client = None
            if client_info:
                income_client = IncomeClient(
                    contact_phone=client_info.get("phone"),
                    display_name=client_info.get("name"),
                    income_type=client_info.get("income_type", IncomeType.FROM_INDIVIDUAL),
                    inn=client_info.get("inn")
                )

            # Используем переданное время операции или текущее
            result = await income_api.create(
                name=name,
                amount=Decimal(str(amount)),
                quantity=quantity,
                operation_time=operation_time,
                client=income_client,
            )

            receipt_uuid = result.get("approvedReceiptUuid")
            if receipt_uuid:
                logger.info(f"Чек создан успешно: {receipt_uuid} на сумму {amount}₽")

                # Сохраняем в Redis чтобы предотвратить дубли (TTL 30 дней)
                if payment_id:
                    created_key = f"nalogo:created:{payment_id}"
                    await cache.set(created_key, receipt_uuid, expire=30 * 24 * 3600)

                return receipt_uuid
            else:
                logger.error(f"Ошибка создания чека: {result}")
                return None

        except Exception as error:
            if self._is_service_unavailable(error):
                logger.warning(
                    "NaloGO временно недоступен, чек будет отправлен позже "
                    f"(payment_id={payment_id}, сумма={amount}₽)"
                )
                if queue_on_failure:
                    await self._queue_receipt(
                        name, amount, quantity, client_info, payment_id,
                        telegram_user_id, amount_kopeks
                    )
            else:
                logger.error("Ошибка создания чека в NaloGO: %s", error, exc_info=True)
            return None

    async def get_queue_length(self) -> int:
        """Получить количество чеков в очереди."""
        return await cache.llen(NALOGO_QUEUE_KEY)

    async def get_queued_receipts(self) -> list:
        """Получить список чеков в очереди (без удаления)."""
        return await cache.lrange(NALOGO_QUEUE_KEY)

    async def pop_receipt_from_queue(self) -> Optional[Dict[str, Any]]:
        """Извлечь следующий чек из очереди."""
        return await cache.rpop(NALOGO_QUEUE_KEY)

    async def requeue_receipt(self, receipt_data: Dict[str, Any]) -> bool:
        """Вернуть чек обратно в очередь (при неудачной отправке)."""
        receipt_data["attempts"] = receipt_data.get("attempts", 0) + 1
        return await cache.lpush(NALOGO_QUEUE_KEY, receipt_data)

    async def get_incomes(
        self,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 100,
    ) -> Optional[List[Dict[str, Any]]]:
        """Получить список доходов (чеков) за период.

        Args:
            from_date: Начало периода (по умолчанию 30 дней назад)
            to_date: Конец периода (по умолчанию сегодня)
            limit: Максимальное количество записей

        Returns:
            Список чеков с информацией, или None при ошибке
        """
        if not self.configured:
            logger.warning("NaloGO не настроен, невозможно получить список доходов")
            return None

        try:
            # Аутентифицируемся если нужно
            if not hasattr(self.client, '_access_token') or not self.client._access_token:
                auth_success = await self.authenticate()
                if not auth_success:
                    return []

            income_api = self.client.income()
            result = await income_api.get_list(
                from_date=from_date,
                to_date=to_date,
                limit=limit,
            )

            # API возвращает структуру с полем content или items
            incomes = result.get("content", result.get("items", []))
            logger.info(f"Получено {len(incomes)} доходов из NaloGO")
            return incomes

        except Exception as error:
            if self._is_service_unavailable(error):
                logger.warning(f"NaloGO временно недоступен: {error}")
            else:
                logger.error(f"Ошибка получения списка доходов: {error}", exc_info=True)
            return None  # None = ошибка, [] = нет чеков
