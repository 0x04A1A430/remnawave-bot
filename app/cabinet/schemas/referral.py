"""Referral program schemas for cabinet."""

from datetime import datetime

from pydantic import BaseModel


class ReferralInfoResponse(BaseModel):
    """Referral program info for current user."""

    referral_code: str
    referral_link: str
    bot_referral_link: str = ''
    total_referrals: int
    active_referrals: int
    total_earnings_kopeks: int
    total_earnings_rubles: float
    # Награда днями имеет amount_kopeks == 0: без своего поля партнёр на
    # «дневной» программе видит нулевой доход при работающих начислениях.
    total_earnings_days: int = 0
    commission_percent: int
    available_balance_kopeks: int = 0
    available_balance_rubles: float = 0
    withdrawn_kopeks: int = 0


class ReferralItemResponse(BaseModel):
    """Single referral info."""

    id: int
    username: str | None = None
    first_name: str | None = None
    created_at: datetime
    has_subscription: bool
    has_paid: bool


class ReferralListResponse(BaseModel):
    """Paginated referral list."""

    items: list[ReferralItemResponse]
    total: int
    page: int
    per_page: int
    pages: int


class ReferralEarningResponse(BaseModel):
    """Referral earning history item.

    ``reward_type`` разделяет деньги и дни: строка с ``amount_kopeks == 0`` и
    ``days_granted > 0`` — это реальная награда, а не пустое начисление, и
    рисовать её как «0 ₽» неверно. ``level`` — единственное, что отличает
    в остальном одинаковые строки от разных звеньев цепочки.
    """

    id: int
    amount_kopeks: int
    amount_rubles: float
    reason: str
    reward_type: str = 'money'
    level: int = 1
    days_granted: int = 0
    tariff_id: int | None = None
    tariff_name: str | None = None
    referral_username: str | None = None
    referral_first_name: str | None = None
    campaign_name: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReferralEarningsListResponse(BaseModel):
    """Paginated referral earnings list."""

    items: list[ReferralEarningResponse]
    total: int
    total_amount_kopeks: int
    total_amount_rubles: float
    total_days_granted: int = 0
    page: int
    per_page: int
    pages: int


class ReferralTermsResponse(BaseModel):
    """Referral program terms."""

    is_enabled: bool
    commission_percent: int
    first_payment_commission_percent: int | None = None
    recurring_commission_tiers: str = ''
    minimum_topup_kopeks: int
    minimum_topup_rubles: float
    first_topup_bonus_kopeks: int
    first_topup_bonus_rubles: float
    inviter_bonus_kopeks: int
    inviter_bonus_rubles: float
    max_commission_payments: int = 0
    partner_section_visible: bool = True
    # Под многоуровневой схемой поля выше ничем не управляют: начисления идут по
    # таблице уровней. Публиковать их как «условия программы» значило бы обещать
    # пользователю то, чего бот не платит.
    scheme: str = 'legacy'
    level_descriptions: list[str] = []
    referee_bonus_description: str | None = None
    max_level_depth: int = 1


class ReferralRewardLevelResponse(BaseModel):
    """Правило награды одного уровня цепочки."""

    level: int
    is_active: bool
    reward_mode: str
    trigger: str
    referrer_percent: int | None = None
    referrer_fixed_kopeks: int | None = None
    referrer_days: int = 0
    referrer_tariff_id: int | None = None
    referrer_tariff_name: str | None = None
    referee_fixed_kopeks: int | None = None
    referee_days: int = 0
    referee_tariff_id: int | None = None
    referee_tariff_name: str | None = None
    max_payments: int = 0

    class Config:
        from_attributes = True


class ReferralRewardTariffOption(BaseModel):
    """Тариф, в который могут лечь дни награды."""

    id: int
    name: str


class ReferralRewardLevelsResponse(BaseModel):
    """Схема наград целиком: флаг режима, правила уровней и выбор тарифов.

    Список тарифов отдаётся здесь, а не берётся с ``/admin/tariffs``: тот
    эндпоинт требует права ``tariffs:read``, и админ с одним лишь
    ``partners:settings`` увидел бы экран без единого тарифа на выбор — то есть
    ровно ту конфигурацию, при которой дни теряются.
    """

    scheme: str
    scheme_locked_by_env: bool = False
    max_level_depth: int
    max_supported_level: int
    levels: list[ReferralRewardLevelResponse]
    available_tariffs: list[ReferralRewardTariffOption] = []
    # Что перенос не смог выразить уровнем. Заполняется только ответом на импорт:
    # молча потерять ступени комиссии хуже, чем не перенести их с предупреждением.
    import_notes: list[str] = []


class ReferralRewardLevelUpdateRequest(BaseModel):
    """Правка уровня.

    Все поля необязательны: экран правит их по одному, и присылать весь объект
    ради одной галочки значило бы затирать чужую правку, сделанную из бота.
    """

    is_active: bool | None = None
    reward_mode: str | None = None
    trigger: str | None = None
    referrer_percent: int | None = None
    referrer_fixed_kopeks: int | None = None
    referrer_days: int | None = None
    referrer_tariff_id: int | None = None
    referee_fixed_kopeks: int | None = None
    referee_days: int | None = None
    referee_tariff_id: int | None = None
    max_payments: int | None = None


class ReferralSchemeUpdateRequest(BaseModel):
    """Переключение схемы наград целиком."""

    scheme: str
