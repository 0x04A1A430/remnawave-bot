"""Factory for creating Bot instances with proxy and custom API server support."""

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.config import settings
from app.middlewares.stale_callback_answer import StaleCallbackAnswerMiddleware


def create_bot(token: str | None = None, **kwargs) -> Bot:
    """Create a Bot instance with SOCKS5 proxy and/or custom Telegram API server."""
    proxy_url = settings.get_proxy_url()
    telegram_api_url = settings.get_telegram_api_url()

    from aiogram.client.telegram import TelegramAPIServer

    from app.utils.rich_transport import RichAiohttpSession

    session_kwargs: dict = {}
    if proxy_url:
        session_kwargs['proxy'] = proxy_url
    if telegram_api_url:
        session_kwargs['api'] = TelegramAPIServer.from_base(telegram_api_url)

    # Rich-транспорт: обычные sendMessage/editMessageText/логотип-SendPhoto
    # прозрачно уходят как rich-сообщения с фоллбеком на классику.
    session = RichAiohttpSession(**session_kwargs)

    kwargs.setdefault('default', DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot = Bot(token=token or settings.BOT_TOKEN, session=session, **kwargs)
    # Поздний ответ на нажатие кнопки — предупреждение, а не исключение (см. middleware).
    bot.session.middleware(StaleCallbackAnswerMiddleware())
    return bot
