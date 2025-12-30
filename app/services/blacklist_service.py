"""
Сервис для работы с черным списком пользователей
Проверяет пользователей по списку из GitHub репозитория
"""
import asyncio
import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import aiohttp
from app.config import settings


logger = logging.getLogger(__name__)


class BlacklistService:
    """
    Сервис для проверки пользователей по черному списку
    """
    
    def __init__(self):
        self.blacklist_data = []  # Список в формате [(telegram_id, username, reason), ...]
        self.last_update = None
        # Используем интервал из настроек, по умолчанию 24 часа
        interval_hours = self.get_blacklist_update_interval_hours()
        self.update_interval = timedelta(hours=interval_hours)
        self.lock = asyncio.Lock()  # Блокировка для предотвращения одновременных обновлений

    def is_blacklist_check_enabled(self) -> bool:
        """Проверяет, включена ли проверка черного списка"""
        return getattr(settings, 'BLACKLIST_CHECK_ENABLED', False)

    def get_blacklist_github_url(self) -> Optional[str]:
        """Получает URL к файлу черного списка на GitHub"""
        return getattr(settings, 'BLACKLIST_GITHUB_URL', None)

    def get_blacklist_update_interval_hours(self) -> int:
        """Получает интервал обновления черного списка в часах"""
        return getattr(settings, 'BLACKLIST_UPDATE_INTERVAL_HOURS', 24)

    def should_ignore_admins(self) -> bool:
        """Проверяет, нужно ли игнорировать администраторов при проверке черного списка"""
        return getattr(settings, 'BLACKLIST_IGNORE_ADMINS', True)

    def is_admin(self, telegram_id: int) -> bool:
        """Проверяет, является ли пользователь администратором"""
        return settings.is_admin(telegram_id)

    async def update_blacklist(self) -> bool:
        """
        Обновляет черный список из GitHub репозитория
        """
        async with self.lock:
            github_url = self.get_blacklist_github_url()
            if not github_url:
                logger.warning("URL к черному списку не задан в настройках")
                return False

            try:
                # Заменяем github.com на raw.githubusercontent.com для получения raw содержимого
                if "github.com" in github_url:
                    raw_url = github_url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                else:
                    raw_url = github_url

                # Получаем содержимое файла
                async with aiohttp.ClientSession() as session:
                    async with session.get(raw_url) as response:
                        if response.status != 200:
                            logger.error(f"Ошибка при получении черного списка: статус {response.status}")
                            return False

                        content = await response.text()

                # Разбираем содержимое файла
                blacklist_data = []
                lines = content.splitlines()

                for line_num, line in enumerate(lines, 1):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue  # Пропускаем пустые строки и комментарии

                    # В формате '7021477105 #@MAMYT_PAXAL2016, перепродажа подписок'
                    # только первая часть до пробела - это Telegram ID, всё остальное комментарий
                    parts = line.split()
                    if not parts:
                        continue

                    try:
                        telegram_id = int(parts[0])  # Первое число - это Telegram ID
                        # Всё остальное - просто комментарий, не используем его для логики
                        # Но можем использовать первую часть после ID как username для отображения
                        username = ""
                        if len(parts) > 1:
                            # Берем вторую часть как username (если начинается с @)
                            if parts[1].startswith('@'):
                                username = parts[1]

                        # По умолчанию используем "Занесен в черный список", если нет другой информации
                        reason = "Занесен в черный список"

                        # Если есть запятая в строке, можем использовать часть после нее как причину
                        full_line_after_id = line[len(str(telegram_id)):].strip()
                        if ',' in full_line_after_id:
                            # Извлекаем причину после запятой
                            after_comma = full_line_after_id.split(',', 1)[1].strip()
                            reason = after_comma

                        blacklist_data.append((telegram_id, username, reason))
                    except ValueError:
                        # Если не удается преобразовать в число, это не ID
                        logger.warning(f"Неверный формат строки {line_num} в черном списке - первое значение не является числом: {line}")

                self.blacklist_data = blacklist_data
                self.last_update = datetime.utcnow()
                logger.info(f"Черный список успешно обновлен. Найдено {len(blacklist_data)} записей")
                return True

            except ValueError as e:
                logger.error(f"Ошибка при парсинге ID из черного списка: {e}")
                return False
            except Exception as e:
                logger.error(f"Ошибка при обновлении черного списка: {e}")
                return False

    async def is_user_blacklisted(self, telegram_id: int, username: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        """
        Проверяет, находится ли пользователь в черном списке

        Args:
            telegram_id: Telegram ID пользователя
            username: Username пользователя (опционально)

        Returns:
            Кортеж (в черном списке, причина)
        """
        if not self.is_blacklist_check_enabled():
            return False, None

        # Проверяем, является ли пользователь администратором и нужно ли его игнорировать
        if self.should_ignore_admins() and self.is_admin(telegram_id):
            logger.info(f"Пользователь {telegram_id} является администратором, игнорируем проверку черного списка")
            return False, None

        # Если черный список пуст или устарел, обновляем его
        interval_hours = self.get_blacklist_update_interval_hours()
        required_interval = timedelta(hours=interval_hours)
        if not self.blacklist_data or (self.last_update and
                                     datetime.utcnow() - self.last_update > required_interval):
            await self.update_blacklist()

        # Проверяем по Telegram ID
        for bl_id, bl_username, bl_reason in self.blacklist_data:
            if bl_id == telegram_id:
                logger.info(f"Пользователь {telegram_id} найден в черном списке по ID: {bl_reason}")
                return True, bl_reason

        # Проверяем по username, если он передан
        if username:
            for bl_id, bl_username, bl_reason in self.blacklist_data:
                if bl_username and (bl_username == username or bl_username == f"@{username}"):
                    logger.info(f"Пользователь {username} ({telegram_id}) найден в черном списке по username: {bl_reason}")
                    return True, bl_reason

        return False, None

    async def get_all_blacklisted_users(self) -> List[Tuple[int, str, str]]:
        """
        Возвращает весь черный список
        """
        interval_hours = self.get_blacklist_update_interval_hours()
        required_interval = timedelta(hours=interval_hours)
        if not self.blacklist_data or (self.last_update and
                                     datetime.utcnow() - self.last_update > required_interval):
            await self.update_blacklist()

        return self.blacklist_data.copy()

    async def get_user_by_telegram_id(self, telegram_id: int) -> Optional[Tuple[int, str, str]]:
        """
        Возвращает информацию о пользователе из черного списка по Telegram ID

        Args:
            telegram_id: Telegram ID пользователя

        Returns:
            Кортеж (telegram_id, username, reason) или None, если не найден
        """
        for bl_id, bl_username, bl_reason in self.blacklist_data:
            if bl_id == telegram_id:
                return (bl_id, bl_username, bl_reason)
        return None

    async def get_user_by_username(self, username: str) -> Optional[Tuple[int, str, str]]:
        """
        Возвращает информацию о пользователе из черного списка по username

        Args:
            username: Username пользователя

        Returns:
            Кортеж (telegram_id, username, reason) или None, если не найден
        """
        # Проверяем как с @, так и без
        username_with_at = f"@{username}" if not username.startswith('@') else username
        username_without_at = username.lstrip('@')

        for bl_id, bl_username, bl_reason in self.blacklist_data:
            if bl_username == username_with_at or bl_username.lstrip('@') == username_without_at:
                return (bl_id, bl_username, bl_reason)
        return None

    async def force_update_blacklist(self) -> Tuple[bool, str]:
        """
        Принудительно обновляет черный список
        
        Returns:
            Кортеж (успешно, сообщение)
        """
        success = await self.update_blacklist()
        if success:
            return True, f"Черный список обновлен успешно. Записей: {len(self.blacklist_data)}"
        else:
            return False, "Ошибка обновления черного списка"


# Глобальный экземпляр сервиса
blacklist_service = BlacklistService()