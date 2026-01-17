from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field, validator

CampaignBonusType = Annotated[
    Literal["balance", "subscription", "none", "tariff"],
    Field(description="Тип бонуса кампании: balance (баланс), subscription (пробная подписка), none (без награды), tariff (тариф)")
]


class CampaignBase(BaseModel):
    name: str = Field(..., max_length=255)
    start_parameter: str = Field(..., max_length=64, description="Start parameter для deep-link (уникальный)")
    bonus_type: CampaignBonusType
    balance_bonus_kopeks: int = Field(0, ge=0)
    subscription_duration_days: Optional[int] = Field(None, ge=0)
    subscription_traffic_gb: Optional[int] = Field(None, ge=0)
    subscription_device_limit: Optional[int] = Field(None, ge=0)
    subscription_squads: list[str] = Field(default_factory=list)
    # Поля для типа "tariff"
    tariff_id: Optional[int] = Field(None, ge=1, description="ID тарифа для выдачи")
    tariff_duration_days: Optional[int] = Field(None, ge=1, description="Длительность тарифа в днях")

    @validator("name", "start_parameter")
    def strip_strings(cls, value: str) -> str:  # noqa: D401,B902
        return value.strip()


class CampaignCreateRequest(CampaignBase):
    is_active: bool = True

    @validator("balance_bonus_kopeks")
    def validate_balance_bonus(cls, value: int, values: dict) -> int:  # noqa: D401,B902
        if values.get("bonus_type") == "balance" and value <= 0:
            raise ValueError("balance_bonus_kopeks must be positive for balance bonus")
        return value

    @validator("subscription_duration_days")
    def validate_subscription_bonus(cls, value: Optional[int], values: dict):  # noqa: D401,B902
        if values.get("bonus_type") == "subscription":
            if value is None or value <= 0:
                raise ValueError("subscription_duration_days must be positive for subscription bonus")
        return value

    @validator("tariff_id")
    def validate_tariff_id(cls, value: Optional[int], values: dict):  # noqa: D401,B902
        if values.get("bonus_type") == "tariff":
            if value is None or value <= 0:
                raise ValueError("tariff_id must be specified for tariff bonus")
        return value

    @validator("tariff_duration_days")
    def validate_tariff_duration(cls, value: Optional[int], values: dict):  # noqa: D401,B902
        if values.get("bonus_type") == "tariff":
            if value is None or value <= 0:
                raise ValueError("tariff_duration_days must be positive for tariff bonus")
        return value


class CampaignResponse(BaseModel):
    id: int
    name: str
    start_parameter: str
    bonus_type: CampaignBonusType
    balance_bonus_kopeks: int
    balance_bonus_rubles: float
    subscription_duration_days: Optional[int] = None
    subscription_traffic_gb: Optional[int] = None
    subscription_device_limit: Optional[int] = None
    subscription_squads: list[str] = Field(default_factory=list)
    # Поля для типа "tariff"
    tariff_id: Optional[int] = None
    tariff_duration_days: Optional[int] = None
    tariff_name: Optional[str] = None  # Для отображения названия тарифа
    is_active: bool
    created_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    registrations_count: int = 0


class CampaignListResponse(BaseModel):
    items: list[CampaignResponse]
    total: int
    limit: int
    offset: int


class CampaignUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    start_parameter: Optional[str] = Field(None, max_length=64)
    bonus_type: Optional[CampaignBonusType] = None
    balance_bonus_kopeks: Optional[int] = Field(None, ge=0)
    subscription_duration_days: Optional[int] = Field(None, ge=0)
    subscription_traffic_gb: Optional[int] = Field(None, ge=0)
    subscription_device_limit: Optional[int] = Field(None, ge=0)
    subscription_squads: Optional[list[str]] = None
    # Поля для типа "tariff"
    tariff_id: Optional[int] = Field(None, ge=1)
    tariff_duration_days: Optional[int] = Field(None, ge=1)
    is_active: Optional[bool] = None

    @validator("name", "start_parameter", pre=True)
    def strip_optional_strings(cls, value: Optional[str]):  # noqa: D401,B902
        if isinstance(value, str):
            return value.strip()
        return value

    @validator("balance_bonus_kopeks")
    def validate_balance_bonus(cls, value: Optional[int], values: dict):  # noqa: D401,B902
        bonus_type = values.get("bonus_type")
        if bonus_type == "balance" and value is not None and value <= 0:
            raise ValueError("balance_bonus_kopeks must be positive for balance bonus")
        return value

    @validator("subscription_duration_days")
    def validate_subscription_bonus(cls, value: Optional[int], values: dict):  # noqa: D401,B902
        bonus_type = values.get("bonus_type")
        if bonus_type == "subscription" and value is not None and value <= 0:
            raise ValueError("subscription_duration_days must be positive for subscription bonus")
        return value

    @validator("tariff_id")
    def validate_tariff_id(cls, value: Optional[int], values: dict):  # noqa: D401,B902
        bonus_type = values.get("bonus_type")
        if bonus_type == "tariff" and value is not None and value <= 0:
            raise ValueError("tariff_id must be positive for tariff bonus")
        return value

    @validator("tariff_duration_days")
    def validate_tariff_duration(cls, value: Optional[int], values: dict):  # noqa: D401,B902
        bonus_type = values.get("bonus_type")
        if bonus_type == "tariff" and value is not None and value <= 0:
            raise ValueError("tariff_duration_days must be positive for tariff bonus")
        return value
