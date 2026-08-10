#!/usr/bin/env python
"""One-shot CLI: собрать реальные имена Telegram для пользователей-заглушек.

Пользователи, созданные из панели RemnaWave без распознанного имени, получают
``first_name`` вида "User <telegram_id>" (см. remnawave_service._get_or_create_bot_user_from_panel).
Скрипт проходит по таким записям и через Telegram Bot API (``bot.get_chat``)
заполняет настоящие ``first_name``/``last_name``/``username``.

Usage:
    python -m scripts.backfill_user_names

Never raises — сбои по конкретному пользователю логируются и пропускаются.
"""

import asyncio
import sys

import structlog
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from app.bot_factory import create_bot
from app.database.database import AsyncSessionLocal
from app.database.models import User
from app.utils.validators import sanitize_telegram_name


logger = structlog.get_logger(__name__)


def is_placeholder_user(user: User) -> bool:
    """Заглушка, которую панель-синк пишет в first_name при отсутствии имени."""
    return bool(user.telegram_id and user.first_name == f'User {user.telegram_id}')


async def _run() -> int:
    bot = create_bot()
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.telegram_id.isnot(None)).order_by(User.id))
            candidates = [user for user in result.scalars().all() if is_placeholder_user(user)]

            updated = 0
            skipped = 0
            not_found = 0
            failed = 0

            for user in candidates:
                try:
                    chat = await bot.get_chat(chat_id=user.telegram_id)
                except TelegramBadRequest as error:
                    if 'not found' in str(error).lower():
                        not_found += 1
                    else:
                        logger.warning(
                            'TelegramBadRequest при get_chat',
                            telegram_id=user.telegram_id,
                            error=str(error),
                        )
                        failed += 1
                    continue
                except Exception as error:
                    logger.warning(
                        'Не удалось получить chat пользователя',
                        telegram_id=user.telegram_id,
                        error=str(error),
                    )
                    failed += 1
                    continue

                first_name = getattr(chat, 'first_name', None)
                last_name = getattr(chat, 'last_name', None)
                username = getattr(chat, 'username', None)

                if not first_name and not last_name and not username:
                    skipped += 1
                    continue

                user.first_name = sanitize_telegram_name(first_name) or user.first_name
                user.last_name = sanitize_telegram_name(last_name)
                user.username = username or user.username
                updated += 1

            await db.commit()

        print(f'Пользователей с заглушкой "User <telegram_id>": {len(candidates)}')
        print(f'  обновлено имён: {updated}')
        print(f'  не найдены в Telegram (chat not found): {not_found}')
        print(f'  без имени в Telegram: {skipped}')
        print(f'  ошибки: {failed}')
        return 0
    finally:
        await bot.session.close()


def main() -> int:
    return asyncio.run(_run())


if __name__ == '__main__':
    sys.exit(main())
