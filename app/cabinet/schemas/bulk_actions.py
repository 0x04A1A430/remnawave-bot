"""Schemas for admin bulk actions."""

from enum import StrEnum

from pydantic import BaseModel, Field


class BulkActionType(StrEnum):
    EXTEND_SUBSCRIPTION = 'extend_subscription'
    CANCEL_SUBSCRIPTION = 'cancel_subscription'
    ACTIVATE_SUBSCRIPTION = 'activate_subscription'
    CHANGE_TARIFF = 'change_tariff'
    ADD_DAYS = 'add_days'
    ADD_TRAFFIC = 'add_traffic'
    ADD_BALANCE = 'add_balance'
    ASSIGN_PROMO_GROUP = 'assign_promo_group'


class BulkActionParams(BaseModel):
    days: int | None = Field(None, ge=1, le=3650)
    tariff_id: int | None = Field(None, gt=0)
    traffic_gb: int | None = Field(None, ge=1, le=10000)
    amount_kopeks: int | None = Field(None, ge=1, le=2_000_000_000)
    balance_description: str = Field(default='Массовое начисление баланса', max_length=500)
    promo_group_id: int | None = None


class BulkExecuteRequest(BaseModel):
    action: BulkActionType
    user_ids: list[int] = Field(..., min_length=1, max_length=500)
    params: BulkActionParams = Field(default_factory=BulkActionParams)
    dry_run: bool = Field(default=False, description='Preview only, no mutations')


class BulkUserResult(BaseModel):
    user_id: int
    success: bool
    message: str
    username: str | None = None


class BulkExecuteResponse(BaseModel):
    action: str
    total: int
    success_count: int
    error_count: int
    skipped_count: int
    dry_run: bool
    results: list[BulkUserResult]
