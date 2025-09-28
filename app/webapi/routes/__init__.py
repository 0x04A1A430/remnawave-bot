"""Маршруты административного веб-API.

Здесь явно импортируются все подмодули с роутерами, чтобы
``from app.webapi.routes import <router>`` гарантированно подхватывало
нужные атрибуты. Без этого Python может не подгрузить модуль, если он
ещё не импортировался, из-за чего часть эндпоинтов (например, RemnaWave)
оказывалась недоступной в OpenAPI-схеме и, как следствие, в ReDoc.
"""

from . import (
    config,
    health,
    promo_groups,
    remnawave,
    stats,
    subscriptions,
    tickets,
    tokens,
    transactions,
    users,
)

__all__ = [
    "config",
    "health",
    "promo_groups",
    "remnawave",
    "stats",
    "subscriptions",
    "tickets",
    "tokens",
    "transactions",
    "users",
]
