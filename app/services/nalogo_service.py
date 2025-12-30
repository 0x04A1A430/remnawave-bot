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
NALOGO_PENDING_VERIFICATION_KEY = "nalogo:pending_verification"


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
                # Таймаут 30 секунд — nalog.ru иногда отвечает медленно
                timeout = getattr(settings, 'NALOGO_TIMEOUT', 30.0)
                self.client = Client(
                    base_url="https://lknpd.nalog.ru/api",
                    storage_path=storage_path,
                    device_id=device_id or "bot-device-123",
                    timeout=timeout,
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

    async def _save_pending_verification(
        self,
        name: str,
        amount: float,
        quantity: int,
        client_info: Optional[Dict[str, Any]],
        payment_id: Optional[str],
        telegram_user_id: Optional[int],
        amount_kopeks: Optional[int],
        error_message: str,
    ) -> bool:
        """Сохранить чек в очередь ожидающих проверки.

        Используется когда таймаут произошёл ПОСЛЕ успешной аутентификации —
        чек мог быть создан на сервере, но ответ не пришёл.
        """
        receipt_data = {
            "name": name,
            "amount": amount,
            "quantity": quantity,
            "client_info": client_info,
            "payment_id": payment_id,
            "telegram_user_id": telegram_user_id,
            "amount_kopeks": amount_kopeks,
            "created_at": datetime.now().isoformat(),
            "error": error_message,
            "status": "pending_verification",
        }
        success = await cache.lpush(NALOGO_PENDING_VERIFICATION_KEY, receipt_data)
        if success:
            count = await cache.llen(NALOGO_PENDING_VERIFICATION_KEY)
            logger.warning(
                f"Чек сохранён для ручной проверки (payment_id={payment_id}, "
                f"сумма={amount}₽, всего ожидают проверки: {count})"
            )
        return success

    async def get_pending_verification_count(self) -> int:
        """Получить количество чеков ожидающих проверки."""
        return await cache.llen(NALOGO_PENDING_VERIFICATION_KEY)

    async def get_pending_verification_receipts(self) -> list:
        """Получить список чеков ожидающих проверки."""
        return await cache.lrange(NALOGO_PENDING_VERIFICATION_KEY)

    async def mark_pending_as_verified(
        self,
        payment_id: str,
        receipt_uuid: Optional[str] = None,
        was_created: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Пометить чек как проверенный и удалить из очереди.

        Args:
            payment_id: ID платежа
            receipt_uuid: UUID чека если был создан в налоговой
            was_created: True если чек был создан, False если не был

        Returns:
            Данные удалённого чека или None если не найден
        """
        receipts = await self.get_pending_verification_receipts()
        updated_receipts = []
        removed_receipt = None

        for receipt in receipts:
            if receipt.get("payment_id") == payment_id:
                removed_receipt = receipt
                if was_created and receipt_uuid:
                    # Сохраняем что чек создан
                    created_key = f"nalogo:created:{payment_id}"
                    await cache.set(created_key, receipt_uuid, expire=30 * 24 * 3600)
                    logger.info(
                        f"Чек {payment_id} помечен как созданный: {receipt_uuid}"
                    )
            else:
                updated_receipts.append(receipt)

        if removed_receipt:
            # Очищаем и перезаписываем список
            await cache.delete(NALOGO_PENDING_VERIFICATION_KEY)
            for r in reversed(updated_receipts):  # reversed чтобы сохранить порядок
                await cache.lpush(NALOGO_PENDING_VERIFICATION_KEY, r)
            logger.info(f"Чек {payment_id} удалён из очереди проверки")

        return removed_receipt

    async def retry_pending_receipt(self, payment_id: str) -> Optional[str]:
        """Повторно отправить чек из очереди проверки.

        Используется когда проверили что чек НЕ был создан в налоговой.

        Returns:
            UUID созданного чека или None
        """
        receipts = await self.get_pending_verification_receipts()
        target_receipt = None

        for receipt in receipts:
            if receipt.get("payment_id") == payment_id:
                target_receipt = receipt
                break

        if not target_receipt:
            logger.warning(f"Чек {payment_id} не найден в очереди проверки")
            return None

        # Пытаемся создать чек
        receipt_uuid = await self.create_receipt(
            name=target_receipt.get("name", ""),
            amount=target_receipt.get("amount", 0),
            quantity=target_receipt.get("quantity", 1),
            client_info=target_receipt.get("client_info"),
            payment_id=payment_id,
            queue_on_failure=False,  # Не добавлять обратно в очередь
            telegram_user_id=target_receipt.get("telegram_user_id"),
            amount_kopeks=target_receipt.get("amount_kopeks"),
        )

        if receipt_uuid:
            # Удаляем из очереди проверки
            await self.mark_pending_as_verified(payment_id, receipt_uuid, was_created=True)
            logger.info(f"Чек {payment_id} успешно создан после ручной проверки: {receipt_uuid}")

        return receipt_uuid

    async def clear_pending_verification(self) -> int:
        """Очистить всю очередь проверки (после полной ручной сверки)."""
        count = await self.get_pending_verification_count()
        if count > 0:
            await cache.delete(NALOGO_PENDING_VERIFICATION_KEY)
            logger.info(f"Очередь проверки очищена: удалено {count} чеков")
        return count

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

        # ЭТАП 1: Аутентификация
        # Если не прошла — чек точно не создавался, безопасно добавить в очередь
        auth_was_successful = False
        try:
            if not hasattr(self.client, '_access_token') or not self.client._access_token:
                auth_success = await self.authenticate()
                if not auth_success:
                    # Аутентификация не прошла — чек не создавался, безопасно в очередь
                    if queue_on_failure:
                        await self._queue_receipt(
                            name, amount, quantity, client_info, payment_id,
                            telegram_user_id, amount_kopeks
                        )
                    return None
            auth_was_successful = True
        except Exception as auth_error:
            # Ошибка аутентификации — чек не создавался, безопасно в очередь
            if self._is_service_unavailable(auth_error):
                logger.warning(
                    f"NaloGO недоступен при аутентификации, чек в очередь "
                    f"(payment_id={payment_id}, сумма={amount}₽)"
                )
                if queue_on_failure:
                    await self._queue_receipt(
                        name, amount, quantity, client_info, payment_id,
                        telegram_user_id, amount_kopeks
                    )
            else:
                logger.error("Ошибка аутентификации NaloGO: %s", auth_error, exc_info=True)
            return None

        # ЭТАП 2: Создание чека
        # Если аутентификация прошла и получили таймаут — чек МОГ быть создан!
        # НЕ добавляем в очередь, требуется ручная проверка
        try:
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
            # ВАЖНО: Аутентификация была успешной, запрос на создание чека УШЁЛ
            # При таймауте чек МОГ быть создан на сервере — НЕ добавляем в очередь!
            if self._is_service_unavailable(error):
                error_msg = str(error)[:200]
                logger.error(
                    f"⚠️ ТАЙМАУТ после успешной аутентификации! Чек МОГ быть создан! "
                    f"(payment_id={payment_id}, сумма={amount}₽). "
                    f"Сохраняем в очередь проверки. Проверьте lknpd.nalog.ru"
                )
                # Сохраняем в очередь для ручной проверки
                await self._save_pending_verification(
                    name=name,
                    amount=amount,
                    quantity=quantity,
                    client_info=client_info,
                    payment_id=payment_id,
                    telegram_user_id=telegram_user_id,
                    amount_kopeks=amount_kopeks,
                    error_message=error_msg,
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

    async def find_duplicate_receipt(
        self,
        amount: float,
        created_at: datetime,
        time_window_minutes: int = 10,
    ) -> Optional[str]:
        """Проверяет, не был ли уже создан чек с такой суммой в заданном временном окне.

        Используется для защиты от дублей при таймаутах — когда сервер создал чек,
        но ответ не вернулся.

        Args:
            amount: Сумма чека в рублях
            created_at: Время создания записи в очереди
            time_window_minutes: Окно поиска в минутах (±)

        Returns:
            UUID чека если дубликат найден, None если не найден
        """
        if not self.configured:
            return None

        try:
            # Запрашиваем чеки за день когда был создан запрос
            from_date = created_at.date()
            to_date = from_date + timedelta(days=1)

            incomes = await self.get_incomes(
                from_date=from_date,
                to_date=to_date,
                limit=50,
            )

            if not incomes:
                return None

            # Ищем чек с такой же суммой в пределах временного окна
            for income in incomes:
                income_amount = float(income.get("totalAmount", income.get("amount", 0)))

                # Проверяем сумму (с погрешностью 0.01)
                if abs(income_amount - amount) > 0.01:
                    continue

                # Проверяем время
                operation_time_str = income.get("operationTime")
                if operation_time_str:
                    try:
                        from dateutil.parser import isoparse
                        operation_time = isoparse(operation_time_str)

                        # Убираем timezone для сравнения
                        if operation_time.tzinfo:
                            operation_time = operation_time.replace(tzinfo=None)
                        created_at_naive = created_at.replace(tzinfo=None) if created_at.tzinfo else created_at

                        time_diff = abs((operation_time - created_at_naive).total_seconds())
                        if time_diff <= time_window_minutes * 60:
                            receipt_uuid = income.get("approvedReceiptUuid", income.get("receiptUuid"))
                            if receipt_uuid:
                                logger.info(
                                    f"Найден дубликат чека: {receipt_uuid} "
                                    f"(сумма={income_amount}₽, время={operation_time}, "
                                    f"разница={time_diff:.0f}с)"
                                )
                                return receipt_uuid
                    except Exception as parse_error:
                        logger.debug(f"Ошибка парсинга времени чека: {parse_error}")
                        continue

            return None

        except Exception as error:
            logger.warning(f"Ошибка проверки дубликата чека: {error}")
            return None

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
