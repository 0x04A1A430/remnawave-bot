from typing import Optional

from aiogram.fsm.context import FSMContext
from aiogram.types import TelegramObject, Message, CallbackQuery

from app.states import RegistrationStates


def is_registration_process(event: TelegramObject, current_state: Optional[str]) -> bool:
    registration_states = [
        RegistrationStates.waiting_for_rules_accept.state,
        RegistrationStates.waiting_for_referral_code.state
    ]

    is_registration_process = (
            (isinstance(event, Message) and event.text and event.text.startswith("/start"))
            or (current_state in registration_states)
            or (
                    isinstance(event, CallbackQuery)
                    and event.data
                    and (
                            event.data in ["rules_accept", "rules_decline", "referral_skip"]
                            or event.data in ["sub_channel_check"]
                    )
            )
    )
    return is_registration_process