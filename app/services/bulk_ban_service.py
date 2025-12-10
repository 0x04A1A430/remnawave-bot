"""
Модуль для массовой блокировки пользователей по списку Telegram ID
"""

import logging
from typing import List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot

from app.database.crud.user import get_user_by_telegram_id
from app.services.user_service import UserService
from app.services.admin_notification_service import AdminNotificationService
from app.config import settings
from app.database.models import UserStatus


logger = logging.getLogger(__name__)


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
        telegram_ids: List[int],
        reason: str = "Заблокирован администратором по списку",
        bot: Bot = None,
        notify_admin: bool = True,
        admin_name: str = "Администратор"
    ) -> Tuple[int, int, List[int]]:
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
                    logger.warning(f"Пользователь с Telegram ID {telegram_id} не найден")
                    not_found_users.append(telegram_id)
                    continue
                
                # Проверяем, что пользователь не заблокирован уже
                if user.status == UserStatus.BLOCKED.value:
                    logger.info(f"Пользователь {telegram_id} уже заблокирован")
                    continue
                
                # Блокируем пользователя
                ban_success = await self.user_service.block_user(
                    db, user.id, admin_user_id, reason
                )
                
                if ban_success:
                    successfully_banned += 1
                    logger.info(f"Пользователь {telegram_id} успешно заблокирован")
                    
                    # Отправляем уведомление пользователю, если возможно
                    if bot:
                        try:
                            await bot.send_message(
                                chat_id=telegram_id,
                                text=(
                                    f"🚫 <b>Ваш аккаунт заблокирован</b>\n\n"
                                    f"Причина: {reason}\n\n"
                                    f"Если вы считаете, что блокировка произошла ошибочно, "
                                    f"обратитесь в поддержку."
                                ),
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.warning(f"Не удалось отправить уведомление пользователю {telegram_id}: {e}")
                else:
                    logger.error(f"Не удалось заблокировать пользователя {telegram_id}")
                    error_ids.append(telegram_id)
                    
            except Exception as e:
                logger.error(f"Ошибка при блокировке пользователя {telegram_id}: {e}")
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
                    admin_name
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления администратору: {e}")
        
        logger.info(
            f"Массовая блокировка завершена: успешно={successfully_banned}, "
            f"не найдено={len(not_found_users)}, ошибки={len(error_ids)}"
        )
        
        return successfully_banned, len(not_found_users), error_ids

    async def parse_telegram_ids_from_text(self, text: str) -> List[int]:
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
                if token.startswith('@'):
                    token = token[1:]
                
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


# Создаем глобальный экземпляр сервиса
bulk_ban_service = BulkBanService()