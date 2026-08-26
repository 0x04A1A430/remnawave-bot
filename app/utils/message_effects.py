"""Эффекты сообщений Telegram (message_effect_id) для ключевых уведомлений.

ID из официального списка core.telegram.org/api/effects. Эффект работает
только в приватных чатах и только при отправке нового сообщения.

Старый self-hosted bot-api может отклонить message_effect_id целиком —
send_message_with_effect() в этом случае один раз повторяет отправку без
эффекта и до рестарта процесса не пытается применить его снова.
"""

import structlog
from aiogram.exceptions import TelegramBadRequest


logger = structlog.get_logger(__name__)

# 🎉 Confetti — успешное пополнение баланса
TOPUP_SUCCESS_EFFECT_ID = '4900136469607067483'

# 🎈 Balloons — подключение нового устройства
DEVICE_ADDED_EFFECT_ID = '5104788382800460697'

_rejected_effects: set[str] = set()


async def send_message_with_effect(bot, chat_id, text, *, effect_id: str, **kwargs):
    """bot.send_message с message_effect_id; при отказе сервера — повтор без эффекта."""
    if effect_id not in _rejected_effects:
        try:
            return await bot.send_message(chat_id=chat_id, text=text, message_effect_id=effect_id, **kwargs)
        except TelegramBadRequest as error:
            if 'effect' not in str(error).lower():
                raise
            _rejected_effects.add(effect_id)
            logger.warning(
                'Сервер отклонил message_effect_id — уведомление отправлено без эффекта',
                effect_id=effect_id,
                error=str(error)[:200],
            )
    return await bot.send_message(chat_id=chat_id, text=text, **kwargs)
