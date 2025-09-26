from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.database.models import TicketStatus


class APIMessage(BaseModel):
    message: str


class TokenCreateRequest(BaseModel):
    name: str
    secret: str = Field(..., description="Секрет из конфигурации WEBAPI_MASTER_KEY")
    description: Optional[str] = None
    permissions: Optional[List[str]] = None
    allowed_ips: Optional[List[str]] = None
    expires_in_hours: Optional[int] = Field(
        default=None,
        ge=1,
        description="Время жизни токена в часах. Если не указано, используется глобальное значение",
    )


class TokenUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[List[str]] = None
    allowed_ips: Optional[List[str]] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None


class TokenResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    permissions: List[str]
    allowed_ips: List[str]
    is_active: bool
    token_prefix: str
    created_at: datetime
    updated_at: datetime
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class TokenWithSecretResponse(TokenResponse):
    token: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    timestamp: datetime


class StatsResponse(BaseModel):
    total_users: int
    active_users: int
    blocked_users: int
    total_balance_kopeks: int
    active_subscriptions: int
    expired_subscriptions: int
    open_tickets: int
    pending_payments: int


class Pagination(BaseModel):
    total: int
    limit: int
    offset: int


class SubscriptionSchema(BaseModel):
    id: int
    status: str
    is_trial: bool
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    traffic_limit_gb: Optional[int]
    traffic_used_gb: Optional[int]
    device_limit: Optional[int]
    connected_squads: List[str]

    model_config = ConfigDict(from_attributes=True)


class TransactionSchema(BaseModel):
    id: int
    type: str
    amount_kopeks: int
    description: Optional[str]
    payment_method: Optional[str]
    external_id: Optional[str]
    is_completed: bool
    created_at: datetime
    completed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class UserListItem(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    language: Optional[str]
    status: str
    balance_kopeks: int
    referral_code: Optional[str]
    created_at: datetime
    updated_at: datetime
    subscription_status: Optional[str] = None
    subscription_end_date: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    pagination: Pagination
    items: List[UserListItem]


class UserCreateRequest(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language: Optional[str] = Field(default="ru", max_length=5)
    referred_by_id: Optional[int] = None
    referral_code: Optional[str] = None


class UserUpdateRequest(BaseModel):
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    language: Optional[str] = Field(default=None, max_length=5)
    referral_code: Optional[str] = None


class UserBalanceUpdateRequest(BaseModel):
    amount_kopeks: int
    description: Optional[str] = Field(default="Пополнение/списание через API")


class UserStatusUpdateRequest(BaseModel):
    status: str = Field(..., description="Новый статус пользователя")


class SubscriptionExtendRequest(BaseModel):
    days: int = Field(..., gt=0)


class SubscriptionTrafficRequest(BaseModel):
    gb: int = Field(..., gt=0)


class SubscriptionDevicesRequest(BaseModel):
    devices: int = Field(..., gt=0)


class SubscriptionSquadRequest(BaseModel):
    squad_uuid: str = Field(..., min_length=3)


class UserDetailResponse(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    language: Optional[str]
    status: str
    balance_kopeks: int
    referred_by_id: Optional[int]
    referral_code: Optional[str]
    promo_group_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    subscription: Optional[SubscriptionSchema]
    transactions: List[TransactionSchema]


class TransactionListResponse(BaseModel):
    pagination: Pagination
    items: List[TransactionSchema]


class TicketMessageSchema(BaseModel):
    id: int
    author_type: str
    content: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketSchema(BaseModel):
    id: int
    user_id: int
    title: str
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
    user_reply_block_permanent: bool
    user_reply_block_until: Optional[datetime]
    messages: List[TicketMessageSchema]

    model_config = ConfigDict(from_attributes=True)


class TicketListResponse(BaseModel):
    pagination: Pagination
    items: List[TicketSchema]


class TicketUpdateRequest(BaseModel):
    status: Optional[TicketStatus] = None
    priority: Optional[str] = Field(default=None, regex=r"^(low|normal|high|urgent)$")
    user_reply_block_permanent: Optional[bool] = None
    user_reply_block_until: Optional[datetime] = None


class SettingUpdateRequest(BaseModel):
    value: Optional[Any]


class SettingResponse(BaseModel):
    key: str
    name: str
    value: Optional[str]
    original: Optional[str]
    has_override: bool
    category_key: str
    category_label: str
    type: str


class SettingsListResponse(BaseModel):
    items: List[SettingResponse]


class PromoGroupSchema(BaseModel):
    id: int
    name: str
    server_discount_percent: int
    traffic_discount_percent: int
    device_discount_percent: int
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PromoGroupListResponse(BaseModel):
    items: List[PromoGroupSchema]


class PromoGroupUpdateRequest(BaseModel):
    name: Optional[str] = None
    server_discount_percent: Optional[int] = Field(default=None, ge=0, le=100)
    traffic_discount_percent: Optional[int] = Field(default=None, ge=0, le=100)
    device_discount_percent: Optional[int] = Field(default=None, ge=0, le=100)
    is_default: Optional[bool] = None


class YooKassaPaymentSchema(BaseModel):
    id: int
    user_id: int
    yookassa_payment_id: str
    amount_kopeks: int
    currency: str
    description: Optional[str]
    status: str
    is_paid: bool
    is_captured: bool
    confirmation_url: Optional[str]
    payment_method_type: Optional[str]
    refundable: bool
    created_at: datetime
    updated_at: datetime
    yookassa_created_at: Optional[datetime]
    captured_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class CryptoBotPaymentSchema(BaseModel):
    id: int
    user_id: int
    invoice_id: str
    amount: str
    asset: str
    status: str
    description: Optional[str]
    paid_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MulenPayPaymentSchema(BaseModel):
    id: int
    user_id: int
    mulen_payment_id: Optional[int]
    uuid: str
    amount_kopeks: int
    currency: str
    status: str
    is_paid: bool
    paid_at: Optional[datetime]
    payment_url: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Pal24PaymentSchema(BaseModel):
    id: int
    user_id: int
    pal24_payment_id: Optional[str]
    amount_kopeks: int
    currency: str
    status: str
    is_paid: bool
    paid_at: Optional[datetime]
    payment_url: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class YooKassaPaymentListResponse(BaseModel):
    pagination: Pagination
    items: List[YooKassaPaymentSchema]


class CryptoBotPaymentListResponse(BaseModel):
    pagination: Pagination
    items: List[CryptoBotPaymentSchema]


class MulenPayPaymentListResponse(BaseModel):
    pagination: Pagination
    items: List[MulenPayPaymentSchema]


class Pal24PaymentListResponse(BaseModel):
    pagination: Pagination
    items: List[Pal24PaymentSchema]
