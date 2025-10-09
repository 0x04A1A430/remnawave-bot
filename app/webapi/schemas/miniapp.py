from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MiniAppBranding(BaseModel):
    service_name: Dict[str, Optional[str]] = Field(default_factory=dict)
    service_description: Dict[str, Optional[str]] = Field(default_factory=dict)


class MiniAppSubscriptionRequest(BaseModel):
    init_data: str = Field(..., alias="initData")


class MiniAppSubscriptionUser(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    display_name: str
    language: Optional[str] = None
    status: str
    subscription_status: str
    subscription_actual_status: str
    status_label: str
    expires_at: Optional[datetime] = None
    device_limit: Optional[int] = None
    traffic_used_gb: float = 0.0
    traffic_used_label: str
    traffic_limit_gb: Optional[int] = None
    traffic_limit_label: str
    lifetime_used_traffic_gb: float = 0.0
    has_active_subscription: bool = False
    promo_offer_discount_percent: int = 0
    promo_offer_discount_expires_at: Optional[datetime] = None
    promo_offer_discount_source: Optional[str] = None


class MiniAppPromoGroup(BaseModel):
    id: int
    name: str
    server_discount_percent: int = 0
    traffic_discount_percent: int = 0
    device_discount_percent: int = 0
    period_discounts: Dict[int, int] = Field(default_factory=dict)
    apply_discounts_to_addons: bool = True


class MiniAppAutoPromoGroupLevel(BaseModel):
    id: int
    name: str
    threshold_kopeks: int
    threshold_rubles: float
    threshold_label: str
    is_reached: bool = False
    is_current: bool = False
    server_discount_percent: int = 0
    traffic_discount_percent: int = 0
    device_discount_percent: int = 0
    period_discounts: Dict[int, int] = Field(default_factory=dict)
    apply_discounts_to_addons: bool = True


class MiniAppConnectedServer(BaseModel):
    uuid: str
    name: str


class MiniAppDevice(BaseModel):
    platform: Optional[str] = None
    device_model: Optional[str] = None
    app_version: Optional[str] = None
    last_seen: Optional[str] = None
    last_ip: Optional[str] = None


class MiniAppTransaction(BaseModel):
    id: int
    type: str
    amount_kopeks: int
    amount_rubles: float
    description: Optional[str] = None
    payment_method: Optional[str] = None
    external_id: Optional[str] = None
    is_completed: bool
    created_at: datetime
    completed_at: Optional[datetime] = None


class MiniAppPromoOffer(BaseModel):
    id: int
    status: str
    notification_type: Optional[str] = None
    offer_type: Optional[str] = None
    effect_type: Optional[str] = None
    discount_percent: int = 0
    bonus_amount_kopeks: int = 0
    bonus_amount_label: Optional[str] = None
    expires_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    is_active: bool = False
    template_id: Optional[int] = None
    template_name: Optional[str] = None
    button_text: Optional[str] = None
    title: Optional[str] = None
    message_text: Optional[str] = None
    icon: Optional[str] = None
    test_squads: List[MiniAppConnectedServer] = Field(default_factory=list)
    active_discount_expires_at: Optional[datetime] = None
    active_discount_started_at: Optional[datetime] = None
    active_discount_duration_seconds: Optional[int] = None


class MiniAppPromoOfferClaimRequest(BaseModel):
    init_data: str = Field(..., alias="initData")


class MiniAppPromoOfferClaimResponse(BaseModel):
    success: bool = True
    code: Optional[str] = None


class MiniAppPromoCodeActivationRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    code: str


class MiniAppPromoCodeActivationResponse(BaseModel):
    success: bool
    description: Optional[str] = None
    code: Optional[str] = None
    error_code: Optional[str] = Field(default=None, alias="errorCode")
    message: Optional[str] = None


class MiniAppFaqItem(BaseModel):
    id: int
    title: Optional[str] = None
    content: Optional[str] = None
    display_order: Optional[int] = None


class MiniAppFaq(BaseModel):
    requested_language: str
    language: str
    is_enabled: bool = True
    total: int = 0
    items: List[MiniAppFaqItem] = Field(default_factory=list)


class MiniAppRichTextDocument(BaseModel):
    requested_language: str
    language: str
    title: Optional[str] = None
    is_enabled: bool = True
    content: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MiniAppLegalDocuments(BaseModel):
    public_offer: Optional[MiniAppRichTextDocument] = None
    service_rules: Optional[MiniAppRichTextDocument] = None
    privacy_policy: Optional[MiniAppRichTextDocument] = None


class MiniAppSubscriptionResponse(BaseModel):
    success: bool = True
    subscription_id: int
    remnawave_short_uuid: Optional[str] = None
    user: MiniAppSubscriptionUser
    subscription_url: Optional[str] = None
    subscription_crypto_link: Optional[str] = None
    subscription_purchase_url: Optional[str] = None
    links: List[str] = Field(default_factory=list)
    ss_conf_links: Dict[str, str] = Field(default_factory=dict)
    connected_squads: List[str] = Field(default_factory=list)
    connected_servers: List[MiniAppConnectedServer] = Field(default_factory=list)
    connected_devices_count: int = 0
    connected_devices: List[MiniAppDevice] = Field(default_factory=list)
    happ: Optional[Dict[str, Any]] = None
    happ_link: Optional[str] = None
    happ_crypto_link: Optional[str] = None
    happ_cryptolink_redirect_link: Optional[str] = None
    balance_kopeks: int = 0
    balance_rubles: float = 0.0
    balance_currency: Optional[str] = None
    transactions: List[MiniAppTransaction] = Field(default_factory=list)
    promo_offers: List[MiniAppPromoOffer] = Field(default_factory=list)
    promo_group: Optional[MiniAppPromoGroup] = None
    auto_assign_promo_groups: List[MiniAppAutoPromoGroupLevel] = Field(default_factory=list)
    total_spent_kopeks: int = 0
    total_spent_rubles: float = 0.0
    total_spent_label: Optional[str] = None
    subscription_type: str
    autopay_enabled: bool = False
    branding: Optional[MiniAppBranding] = None
    faq: Optional[MiniAppFaq] = None
    legal_documents: Optional[MiniAppLegalDocuments] = None

