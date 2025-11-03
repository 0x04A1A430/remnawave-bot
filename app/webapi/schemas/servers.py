"""Pydantic-схемы для управления серверами через Web API."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .users import PromoGroupSummary


class ServerResponse(BaseModel):
    """Полная информация о сервере."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    squad_uuid: str = Field(alias="squadUuid")
    display_name: str = Field(alias="displayName")
    original_name: Optional[str] = Field(default=None, alias="originalName")
    country_code: Optional[str] = Field(default=None, alias="countryCode")
    is_available: bool = Field(alias="isAvailable")
    is_trial_eligible: bool = Field(default=False, alias="isTrialEligible")
    price_kopeks: int = Field(alias="priceKopeks")
    price_rubles: float = Field(alias="priceRubles")
    description: Optional[str] = None
    sort_order: int = Field(default=0, alias="sortOrder")
    max_users: Optional[int] = Field(default=None, alias="maxUsers")
    current_users: int = Field(default=0, alias="currentUsers")
    created_at: Optional[datetime] = Field(default=None, alias="createdAt")
    updated_at: Optional[datetime] = Field(default=None, alias="updatedAt")
    promo_groups: List[PromoGroupSummary] = Field(
        default_factory=list, alias="promoGroups"
    )


class ServerListResponse(BaseModel):
    """Список серверов с пагинацией."""

    items: List[ServerResponse]
    total: int
    page: int
    limit: int


class ServerCreateRequest(BaseModel):
    """Запрос на создание сервера."""

    squad_uuid: str = Field(alias="squadUuid")
    display_name: str = Field(alias="displayName")
    original_name: Optional[str] = Field(default=None, alias="originalName")
    country_code: Optional[str] = Field(default=None, alias="countryCode")
    price_kopeks: int = Field(default=0, alias="priceKopeks")
    description: Optional[str] = None
    max_users: Optional[int] = Field(default=None, alias="maxUsers")
    is_available: bool = Field(default=True, alias="isAvailable")
    is_trial_eligible: bool = Field(default=False, alias="isTrialEligible")
    sort_order: int = Field(default=0, alias="sortOrder")
    promo_group_ids: Optional[List[int]] = Field(
        default=None,
        alias="promoGroupIds",
        description="Список идентификаторов промогрупп, доступных на сервере.",
    )


class ServerUpdateRequest(BaseModel):
    """Запрос на обновление свойств сервера."""

    display_name: Optional[str] = Field(default=None, alias="displayName")
    original_name: Optional[str] = Field(default=None, alias="originalName")
    country_code: Optional[str] = Field(default=None, alias="countryCode")
    price_kopeks: Optional[int] = Field(default=None, alias="priceKopeks")
    description: Optional[str] = None
    max_users: Optional[int] = Field(default=None, alias="maxUsers")
    is_available: Optional[bool] = Field(default=None, alias="isAvailable")
    is_trial_eligible: Optional[bool] = Field(
        default=None, alias="isTrialEligible"
    )
    sort_order: Optional[int] = Field(default=None, alias="sortOrder")
    promo_group_ids: Optional[List[int]] = Field(
        default=None,
        alias="promoGroupIds",
        description="Если передан список, он заменит текущие промогруппы сервера.",
    )


class ServerSyncResponse(BaseModel):
    """Результат синхронизации серверов с RemnaWave."""

    model_config = ConfigDict(populate_by_name=True)

    created: int
    updated: int
    removed: int
    total: int


class ServerStatisticsResponse(BaseModel):
    """Агрегированная статистика по серверам."""

    model_config = ConfigDict(populate_by_name=True)

    total_servers: int = Field(alias="totalServers")
    available_servers: int = Field(alias="availableServers")
    unavailable_servers: int = Field(alias="unavailableServers")
    servers_with_connections: int = Field(alias="serversWithConnections")
    total_revenue_kopeks: int = Field(alias="totalRevenueKopeks")
    total_revenue_rubles: float = Field(alias="totalRevenueRubles")


class ServerCountsSyncResponse(BaseModel):
    """Результат обновления счетчиков пользователей серверов."""

    model_config = ConfigDict(populate_by_name=True)

    updated: int


class ServerConnectedUser(BaseModel):
    """Краткая информация о пользователе, подключенном к серверу."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    telegram_id: int = Field(alias="telegramId")
    username: Optional[str] = None
    first_name: Optional[str] = Field(default=None, alias="firstName")
    last_name: Optional[str] = Field(default=None, alias="lastName")
    status: str
    balance_kopeks: int = Field(alias="balanceKopeks")
    balance_rubles: float = Field(alias="balanceRubles")
    subscription_id: Optional[int] = Field(default=None, alias="subscriptionId")
    subscription_status: Optional[str] = Field(
        default=None, alias="subscriptionStatus"
    )
    subscription_end_date: Optional[datetime] = Field(
        default=None, alias="subscriptionEndDate"
    )


class ServerConnectedUsersResponse(BaseModel):
    """Список пользователей, подключенных к серверу."""

    model_config = ConfigDict(populate_by_name=True)

    items: List[ServerConnectedUser]
    total: int
    limit: int
    offset: int


class ServerDeleteResponse(BaseModel):
    """Ответ при удалении сервера."""

    model_config = ConfigDict(populate_by_name=True)

    success: bool
    message: str

