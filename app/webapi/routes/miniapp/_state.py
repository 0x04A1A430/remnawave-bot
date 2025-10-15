from __future__ import annotations

from aiogram import Bot

from app.services.payment_service import PaymentService, get_wata_payment_by_link_id


class MiniAppState:
    """Holds mutable dependencies shared across miniapp modules."""

    def __init__(self) -> None:
        self.PaymentService = PaymentService
        self.Bot = Bot
        self.get_wata_payment_by_link_id = get_wata_payment_by_link_id


state = MiniAppState()

__all__ = ["state", "MiniAppState"]
