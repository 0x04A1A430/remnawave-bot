"""
Модуль для массовой блокировки и разблокировки пользователей
"""

import structlog
from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import get_user_by_telegram_id, update_user
from app.database.models import User, UserStatus
from app.services.admin_notification_service import AdminNotificationService
from app.services.user_revival_service import revive_deleted_user
from app.services.user_service import UserService


logger = structlog.get_logger(__name__)


class BulkBanService:
    """
    Сервис для массовой блокировки пользователей по списку Telegram ID
    """

    def __init__(self):
        self.user_service = UserService()

    async def ban_users_by_telegram_ids(
        self,
        db: AsyncSession,
        admin_user_id: int,
        telegram_ids: list[int],
        reason: str = 'Заблокирован администратором по списку',
        bot: Bot = None,
        notify_admin: bool = True,
        admin_name: str = 'Администратор',
    ) -> tuple[int, int, list[int]]:
        """
        Массовая блокировка пользователей по Telegram ID

        Args:
            db: Асинхронная сессия базы данных
            admin_user_id: ID администратора, который осуществляет блокировку
            telegram_ids: Список Telegram ID для блокировки
            reason: Причина блокировки
            bot: Бот для отправки уведомлений
            notify_admin: Отправлять ли уведомления администратору
            admin_name: Имя администратора для логирования

        Returns:
            Кортеж из (успешно заблокированных, не найденных, список ID с ошибками)
        """
        successfully_banned = 0
        not_found_users = []
        error_ids = []

        for telegram_id in telegram_ids:
            try:
                # Получаем пользователя по Telegram ID
                user = await get_user_by_telegram_id(db, telegram_id)

                if not user:
                    logger.warning('Пользователь с Telegram ID не найден', telegram_id=telegram_id)
                    not_found_users.append(telegram_id)
                    continue

                # Проверяем, что пользователь не заблокирован уже
                if user.status == UserStatus.BLOCKED.value:
                    logger.info('Пользователь уже заблокирован', telegram_id=telegram_id)
                    continue

                # Блокируем пользователя
                ban_success = await self.user_service.block_user(db, user.id, admin_user_id, reason)

                if ban_success:
                    successfully_banned += 1
                    logger.info('Пользователь успешно заблокирован', telegram_id=telegram_id)

                    # Отправляем уведомление пользователю, если возможно
                    if bot:
                        try:
                            await bot.send_message(
                                chat_id=telegram_id,
                                text=(
                                    f'<b>Ваш аккаунт заблокирован</b>\n\n'
                                    f'Причина: {reason}\n\n'
                                    f'Если вы считаете, что блокировка произошла ошибочно, '
                                    f'обратитесь в поддержку.'
                                ),
                                parse_mode='HTML',
                            )
                        except Exception as e:
                            logger.warning(
                                'Не удалось отправить уведомление пользователю',
                                telegram_id=telegram_id,
                                error=e,
                            )
                else:
                    logger.error('Не удалось заблокировать пользователя', telegram_id=telegram_id)
                    error_ids.append(telegram_id)

            except Exception as e:
                logger.error(
                    'Ошибка при блокировке пользователя',
                    telegram_id=telegram_id,
                    error=e,
                )
                error_ids.append(telegram_id)

        # Отправляем уведомление администратору
        if notify_admin and bot:
            try:
                admin_notification_service = AdminNotificationService(bot)
                await admin_notification_service.send_bulk_ban_notification(
                    admin_user_id,
                    successfully_banned,
                    len(not_found_users),
                    len(error_ids),
                    admin_name,
                )
            except Exception as e:
                logger.error('Ошибка при отправке уведомления администратору', error=e)

        logger.info(
            'Массовая блокировка завершена',
            successfully_banned=successfully_banned,
            not_found_users_count=len(not_found_users),
            error_ids_count=len(error_ids),
        )

        return successfully_banned, len(not_found_users), error_ids

    async def parse_telegram_ids_from_text(self, text: str) -> list[int]:
        """
        Парсит Telegram ID из текста. Поддерживает различные форматы:
        - по одному ID на строку
        - через запятую
        - через пробелы
        - с @username (если username соответствует формату ID)
        """
        if not text:
            return []

        # Удаляем лишние пробелы и разбиваем по переносам строк
        lines = text.strip().split('\n')
        ids = []

        for line in lines:
            # Убираем комментарии и лишние пробелы
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            # Разбиваем строку по запятым или пробелам
            tokens = line.replace(',', ' ').split()

            for token in tokens:
                token = token.strip()

                # Убираем символ @ если присутствует
                token = token.removeprefix('@')

                # Проверяем, является ли токен числом (Telegram ID)
                try:
                    telegram_id = int(token)
                    if telegram_id > 0:  # Telegram ID должны быть положительными
                        ids.append(telegram_id)
                except ValueError:
                    # Пропускаем, если не является числом
                    continue

        # Убираем дубликаты, сохранив порядок
        unique_ids = []
        seen = set()
        for tid in ids:
            if tid not in seen:
                unique_ids.append(tid)
                seen.add(tid)

        return unique_ids

    async def unban_users_by_telegram_ids(
        self,
        db: AsyncSession,
        telegram_ids: list[int],
    ) -> tuple[int, int, list[int]]:
        """
        Массовая разблокировка пользователей по Telegram ID (без уведомлений).

        Returns:
            Кортеж из (успешно разблокированных, не найденных, список ID с ошибками)
        """
        successfully_unbanned = 0
        not_found_users = []
        error_ids = []

        for telegram_id in telegram_ids:
            try:
                user = await get_user_by_telegram_id(db, telegram_id)

                if not user:
                    logger.warning('Пользователь с Telegram ID не найден', telegram_id=telegram_id)
                    not_found_users.append(telegram_id)
                    continue

                if user.status == UserStatus.ACTIVE.value:
                    logger.info('Пользователь уже активен', telegram_id=telegram_id)
                    continue

                await update_user(db, user, status=UserStatus.ACTIVE.value)
                successfully_unbanned += 1
                logger.info('Пользователь успешно разблокирован', telegram_id=telegram_id)

            except Exception as e:
                logger.error('Ошибка при разблокировке пользователя', telegram_id=telegram_id, error=e)
                error_ids.append(telegram_id)

        logger.info(
            'Массовая разблокировка завершена',
            successfully_unbanned=successfully_unbanned,
            not_found_users_count=len(not_found_users),
            error_ids_count=len(error_ids),
        )

        return successfully_unbanned, len(not_found_users), error_ids

    async def unban_all_blocked_users(
        self,
        db: AsyncSession,
    ) -> tuple[int, list[int]]:
        """
        Разблокировка всех заблокированных пользователей (без уведомлений).

        Returns:
            Кортеж из (количество разблокированных, список telegram_id с ошибками)
        """
        result = await db.execute(select(User).where(User.status == UserStatus.BLOCKED.value))
        blocked_users = result.scalars().all()

        telegram_ids = [u.telegram_id for u in blocked_users if u.telegram_id]

        if not telegram_ids:
            return 0, []

        successfully, not_found, error_ids = await self.unban_users_by_telegram_ids(
            db=db,
            telegram_ids=telegram_ids,
        )

        return successfully, error_ids

    async def restore_all_deleted_users(
        self,
        db: AsyncSession,
    ) -> tuple[int, list[int]]:
        """
        Восстанавливает всех удалённых пользователей (DELETED → ACTIVE).

        Returns:
            Кортеж из (количество восстановленных, список telegram_id с ошибками)
        """
        result = await db.execute(select(User).where(User.status == UserStatus.DELETED.value))
        deleted_users = result.scalars().all()
        error_ids = []
        restored = 0

        for user in deleted_users:
            try:
                await revive_deleted_user(db, user, source='admin_bulk_restore')
                restored += 1
                logger.info('Пользователь восстановлен из DELETED', telegram_id=user.telegram_id, user_id=user.id)
            except Exception as e:
                logger.error('Ошибка восстановления пользователя', user_id=user.id, error=e)
                if user.telegram_id:
                    error_ids.append(user.telegram_id)

        await db.commit()

        logger.info(
            'Восстановление удалённых пользователей завершено',
            restored=restored,
            errors=len(error_ids),
        )

        return restored, error_ids


# Создаем глобальный экземпляр сервиса
bulk_ban_service = BulkBanService()
