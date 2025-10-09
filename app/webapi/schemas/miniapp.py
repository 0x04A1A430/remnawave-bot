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


class MiniAppPromoCode(BaseModel):
    code: str
    type: Optional[str] = None
    balance_bonus_kopeks: int = 0
    subscription_days: int = 0
    max_uses: Optional[int] = None
    current_uses: Optional[int] = None
    valid_until: Optional[datetime] = None


class MiniAppPromoCodeActivationRequest(BaseModel):
    init_data: str = Field(..., alias="initData")
    code: str


class MiniAppPromoCodeActivationResponse(BaseModel):
    success: bool = True
    description: Optional[str] = None
    promocode: Optional[MiniAppPromoCode] = None


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


class MiniAppReferralTerms(BaseModel):
    minimum_topup_kopeks: int = 0
    minimum_topup_label: Optional[str] = None
    first_topup_bonus_kopeks: int = 0
    first_topup_bonus_label: Optional[str] = None
    inviter_bonus_kopeks: int = 0
    inviter_bonus_label: Optional[str] = None
    commission_percent: float = 0.0
    referred_user_reward_kopeks: int = 0
    referred_user_reward_label: Optional[str] = None


class MiniAppReferralStats(BaseModel):
    invited_count: int = 0
    paid_referrals_count: int = 0
    active_referrals_count: int = 0
    total_earned_kopeks: int = 0
    total_earned_label: Optional[str] = None
    month_earned_kopeks: int = 0
    month_earned_label: Optional[str] = None
    conversion_rate: float = 0.0


class MiniAppReferralRecentEarning(BaseModel):
    amount_kopeks: int = 0
    amount_label: Optional[str] = None
    reason: Optional[str] = None
    referral_name: Optional[str] = None
    created_at: Optional[datetime] = None


class MiniAppReferralItem(BaseModel):
    id: int
    telegram_id: Optional[int] = None
    full_name: Optional[str] = None
    username: Optional[str] = None
    created_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    has_made_first_topup: bool = False
    balance_kopeks: int = 0
    balance_label: Optional[str] = None
    total_earned_kopeks: int = 0
    total_earned_label: Optional[str] = None
    topups_count: int = 0
    days_since_registration: Optional[int] = None
    days_since_activity: Optional[int] = None
    status: Optional[str] = None


class MiniAppReferralList(BaseModel):
    total_count: int = 0
    has_next: bool = False
    has_prev: bool = False
    current_page: int = 1
    total_pages: int = 1
    items: List[MiniAppReferralItem] = Field(default_factory=list)


class MiniAppReferralInfo(BaseModel):
    referral_code: Optional[str] = None
    referral_link: Optional[str] = None
    terms: Optional[MiniAppReferralTerms] = None
    stats: Optional[MiniAppReferralStats] = None
    recent_earnings: List[MiniAppReferralRecentEarning] = Field(default_factory=list)
    referrals: Optional[MiniAppReferralList] = None


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
    referral: Optional[MiniAppReferralInfo] = None

