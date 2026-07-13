"""Cabinet Pydantic schemas."""

from .auth import (
    AuthResponse,
    EmailLoginRequest,
    EmailRegisterRequest,
    EmailVerifyRequest,
    PasswordForgotRequest,
    PasswordResetRequest,
    RefreshTokenRequest,
    TelegramAuthRequest,
    TelegramWidgetAuthRequest,
    TokenResponse,
    UserResponse,
)
from .balance import (
    BalanceResponse,
    PaymentMethodResponse,
    TopUpRequest,
    TopUpResponse,
    TransactionListResponse,
    TransactionResponse,
)
from .referral import (
    ReferralEarningResponse,
    ReferralInfoResponse,
    ReferralListResponse,
    ReferralTermsResponse,
)
from .subscription import (
    AutopayUpdateRequest,
    DevicePurchaseRequest,
    RenewalOptionResponse,
    RenewalRequest,
    SubscriptionResponse,
    TrafficPackageResponse,
    TrafficPurchaseInfo,
    TrafficPurchaseRequest,
)
from .tickets import (
    TicketCreateRequest,
    TicketListResponse,
    TicketMessageCreateRequest,
    TicketMessageResponse,
    TicketResponse,
)


__all__ = [
    'AuthResponse',
    'AutopayUpdateRequest',
    # Balance
    'BalanceResponse',
    'DevicePurchaseRequest',
    'EmailLoginRequest',
    'EmailRegisterRequest',
    'EmailVerifyRequest',
    'PasswordForgotRequest',
    'PasswordResetRequest',
    'PaymentMethodResponse',
    'ReferralEarningResponse',
    # Referral
    'ReferralInfoResponse',
    'ReferralListResponse',
    'ReferralTermsResponse',
    'RefreshTokenRequest',
    'RenewalOptionResponse',
    'RenewalRequest',
    # Subscription
    'SubscriptionResponse',
    # Auth
    'TelegramAuthRequest',
    'TelegramWidgetAuthRequest',
    'TicketCreateRequest',
    'TicketListResponse',
    'TicketMessageCreateRequest',
    'TicketMessageResponse',
    # Tickets
    'TicketResponse',
    'TokenResponse',
    'TopUpRequest',
    'TopUpResponse',
    'TrafficPackageResponse',
    'TrafficPurchaseInfo',
    'TrafficPurchaseRequest',
    'TransactionListResponse',
    'TransactionResponse',
    'UserResponse',
]
