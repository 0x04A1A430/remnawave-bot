"""Кто сейчас подключён к VPN — по панели, а не по кнопкам в боте.

«Онлайн» в админке кабинета раньше значил «нажимал что-то в боте за 5 минут»
(``User.last_activity``), а карточка пользователя — отметку панели
``userTraffic.onlineAt``. Список и карточка спорили: человек, сидящий в VPN, в
списке был «2 дня назад», а открывший бота без подключения — «онлайн».

Владельцу VPN «онлайн» — это подключение. Панель ставит ``onlineAt`` по отчётам нод
и сама красит пользователя зелёным, пока с отметки прошло не больше минуты; здесь
то же окно. Фильтр по ``onlineAt`` у ``GET /api/users`` — только точное равенство,
выбрать им «свежее минуты» нельзя. Зато по нему сортирует таблица самой панели:
берём список по убыванию отметки и листаем, пока она свежее окна.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

import structlog

from app.services.connected_accounts import ConnectedAccounts


logger = structlog.get_logger(__name__)

#: Панель красит пользователя зелёным, пока с ``onlineAt`` прошло не больше минуты.
ONLINE_WINDOW = timedelta(seconds=60)
#: Максимум контракта ``GET /api/users``.
PAGE_SIZE = 1000
#: Потолок обхода: 10 000 подключённых одновременно — больше, чем держит любая панель бота.
MAX_PAGES = 10
#: Список и сегмент «Онлайн» открывают подряд — одного запроса к панели на 20 секунд хватает.
CACHE_TTL_SECONDS = 20.0
#: Список пользователей не должен ждать упавшую панель дольше этого.
PANEL_TIMEOUT_SECONDS = 8.0


class _PanelUser(Protocol):
    id: int
    telegram_id: int | None

    @property
    def online_at(self) -> datetime | None:
        """Когда аккаунт последний раз был подключён к VPN (по панели)."""


class _PanelUsersSource(Protocol):
    async def get_users_by_last_online(self, start: int, size: int) -> list[_PanelUser]:
        """Аккаунты панели по убыванию времени последнего подключения."""


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _connected(users: Iterable[_PanelUser], since: datetime) -> tuple[list[_PanelUser], bool]:
    """Подключённые с начала страницы и признак, что дальше по списку только отключённые."""
    connected: list[_PanelUser] = []
    for user in users:
        online_at = user.online_at
        if online_at is None or _aware(online_at) < since:
            return connected, True
        connected.append(user)
    return connected, False


async def fetch_connected_accounts(
    source: _PanelUsersSource,
    *,
    now: datetime | None = None,
    window: timedelta = ONLINE_WINDOW,
) -> ConnectedAccounts:
    """Обойти список панели по убыванию ``onlineAt``, пока отметка свежее окна."""
    since = (now or datetime.now(UTC)) - window
    connected: list[_PanelUser] = []
    for page in range(MAX_PAGES):
        users = await source.get_users_by_last_online(start=page * PAGE_SIZE, size=PAGE_SIZE)
        fresh, reached_offline = _connected(users, since)
        connected.extend(fresh)
        if reached_offline or len(users) < PAGE_SIZE:
            break
    else:
        logger.warning('Список подключённых обрезан потолком обхода', max_accounts=MAX_PAGES * PAGE_SIZE)

    return ConnectedAccounts(
        panel_ids=frozenset(user.id for user in connected),
        telegram_ids=frozenset(user.telegram_id for user in connected if user.telegram_id),
    )


async def _fetch_from_panel() -> ConnectedAccounts | None:
    from app.services.remnawave_service import RemnaWaveService

    service = RemnaWaveService()
    if not service.is_configured:
        return None
    async with service.get_api_client() as api:
        return await fetch_connected_accounts(api)


@dataclass
class _Cache:
    """Последний ответ панели — удачный или «не знаем» — на ``CACHE_TTL_SECONDS``.

    Отказ тоже запоминается: иначе при лежащей панели каждое открытие списка ждало бы
    таймаут и повторы клиента заново.
    """

    stored_at: float = float('-inf')
    accounts: ConnectedAccounts | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def lookup(self) -> tuple[bool, ConnectedAccounts | None]:
        return time.monotonic() - self.stored_at < CACHE_TTL_SECONDS, self.accounts

    def store(self, accounts: ConnectedAccounts | None) -> ConnectedAccounts | None:
        self.stored_at = time.monotonic()
        self.accounts = accounts
        return accounts


_cache = _Cache()


async def get_connected_accounts() -> ConnectedAccounts | None:
    """Подключённые сейчас; ``None`` — панель не настроена или не ответила (это «не знаем», не «никого»)."""
    cache = _cache
    hit, accounts = cache.lookup()
    if hit:
        return accounts
    async with cache.lock:
        hit, accounts = cache.lookup()
        if hit:
            return accounts
        try:
            fetched = await asyncio.wait_for(_fetch_from_panel(), timeout=PANEL_TIMEOUT_SECONDS)
        except Exception as error:
            logger.warning('Не удалось узнать у панели, кто подключён', error=str(error))
            fetched = None
        return cache.store(fetched)
