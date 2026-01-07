"""Пакет с mixin-классами, делающими платёжный сервис модульным.

Здесь собираем все вспомогательные части, чтобы основной `PaymentService`
оставался компактным и импортировал только нужные компоненты.
"""

from .common import PaymentCommonMixin
from .stars import TelegramStarsMixin
from .yookassa import YooKassaPaymentMixin
from .tribute import TributePaymentMixin
from .cryptobot import CryptoBotPaymentMixin
from .heleket import HeleketPaymentMixin
from .mulenpay import MulenPayPaymentMixin
from .pal24 import Pal24PaymentMixin
from .platega import PlategaPaymentMixin
from .wata import WataPaymentMixin
from .cloudpayments import CloudPaymentsPaymentMixin
from .freekassa import FreekassaPaymentMixin

__all__ = [
    "PaymentCommonMixin",
    "TelegramStarsMixin",
    "YooKassaPaymentMixin",
    "TributePaymentMixin",
    "CryptoBotPaymentMixin",
    "HeleketPaymentMixin",
    "MulenPayPaymentMixin",
    "Pal24PaymentMixin",
    "PlategaPaymentMixin",
    "WataPaymentMixin",
    "CloudPaymentsPaymentMixin",
    "FreekassaPaymentMixin",
]
