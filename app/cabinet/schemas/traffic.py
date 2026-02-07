"""Schemas for admin traffic usage."""

from pydantic import BaseModel, Field


class TrafficNodeInfo(BaseModel):
    node_uuid: str
    node_name: str
    country_code: str


class UserTrafficItem(BaseModel):
    user_id: int
    telegram_id: int | None
    username: str | None
    full_name: str
    tariff_name: str | None
    subscription_status: str | None
    traffic_limit_gb: float
    device_limit: int
    connected_devices: int = 0
    node_traffic: dict[str, int]  # {node_uuid: total_bytes}
    total_bytes: int


class TrafficUsageResponse(BaseModel):
    items: list[UserTrafficItem]
    nodes: list[TrafficNodeInfo]
    total: int
    offset: int
    limit: int
    period_days: int
    available_tariffs: list[str]
    available_statuses: list[str]


class ExportCsvRequest(BaseModel):
    period: int = Field(30, ge=1, le=30)
    start_date: str = ''
    end_date: str = ''
    tariffs: str = ''
    nodes: str = ''
    statuses: str = ''
    search: str = ''


class ExportCsvResponse(BaseModel):
    success: bool
    message: str
