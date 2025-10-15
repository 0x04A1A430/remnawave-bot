from __future__ import annotations

import sys
from types import ModuleType

from fastapi import APIRouter

from . import payments, promo, subscription
from ._state import state

__all__ = [
    "router",
    "PaymentService",
    "Bot",
    "get_wata_payment_by_link_id",
    "create_payment_link",
    "get_payment_methods",
    "get_payment_statuses",
    "_compute_cryptobot_limits",
    "_find_recent_deposit",
    "_resolve_payment_status_entry",
    "_resolve_yookassa_payment_status",
    "_resolve_mulenpay_payment_status",
    "_resolve_wata_payment_status",
    "_resolve_pal24_payment_status",
    "_resolve_cryptobot_payment_status",
    "_resolve_stars_payment_status",
    "_resolve_tribute_payment_status",
    "_resolve_user_from_init_data",
]

router = APIRouter()
router.include_router(payments.router)
router.include_router(promo.router)
router.include_router(subscription.router)

create_payment_link = payments.create_payment_link
get_payment_methods = payments.get_payment_methods
get_payment_statuses = payments.get_payment_statuses

_compute_cryptobot_limits = payments._compute_cryptobot_limits
_find_recent_deposit = payments._find_recent_deposit
_resolve_payment_status_entry = payments._resolve_payment_status_entry
_resolve_yookassa_payment_status = payments._resolve_yookassa_payment_status
_resolve_mulenpay_payment_status = payments._resolve_mulenpay_payment_status
_resolve_wata_payment_status = payments._resolve_wata_payment_status
_resolve_pal24_payment_status = payments._resolve_pal24_payment_status
_resolve_cryptobot_payment_status = payments._resolve_cryptobot_payment_status
_resolve_stars_payment_status = payments._resolve_stars_payment_status
_resolve_tribute_payment_status = payments._resolve_tribute_payment_status
_resolve_user_from_init_data = payments._resolve_user_from_init_data

_STATE_ATTRS = {"PaymentService", "Bot", "get_wata_payment_by_link_id"}
_FORWARDED_ATTRS = {"_resolve_user_from_init_data"}

for attr in _STATE_ATTRS:
    globals()[attr] = getattr(state, attr)


class _MiniappModule(ModuleType):
    def __setattr__(self, name: str, value):  # type: ignore[override]
        if name in _STATE_ATTRS:
            setattr(state, name, value)
        if name in _FORWARDED_ATTRS:
            setattr(payments, name, value)
        super().__setattr__(name, value)


def _patch_module_type() -> None:
    module = sys.modules[__name__]
    if not isinstance(module, _MiniappModule):
        module.__class__ = _MiniappModule  # type: ignore[assignment]


_patch_module_type()
