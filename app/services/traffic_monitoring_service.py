"""
Сервис для мониторинга трафика пользователей
Проверяет, не превышает ли пользователь заданный порог трафика за сутки
"""
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.services.admin_notification_service import AdminNotificationService
from app.services.remnawave_service import RemnaWaveService
from app.database.crud.user import get_user_by_remnawave_uuid
from app.database.models import User
from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


class TrafficMonitoringService:
    """
    Сервис для мониторинга трафика пользователей
    """
    
    def __init__(self):
        self.remnawave_service = RemnaWaveService()
        self.lock = asyncio.Lock()  # Блокировка для предотвращения одновременных проверок

    def is_traffic_monitoring_enabled(self) -> bool:
        """Проверяет, включен ли мониторинг трафика"""
        return getattr(settings, 'TRAFFIC_MONITORING_ENABLED', False)

    def get_traffic_threshold_gb(self) -> float:
        """Получает порог трафика в ГБ за сутки"""
        return getattr(settings, 'TRAFFIC_THRESHOLD_GB_PER_DAY', 10.0)

    def get_monitoring_interval_hours(self) -> int:
        """Получает интервал мониторинга в часах"""
        return getattr(settings, 'TRAFFIC_MONITORING_INTERVAL_HOURS', 24)

    def get_suspicious_notifications_topic_id(self) -> Optional[int]:
        """Получает ID топика для уведомлений о подозрительной активности"""
        return getattr(settings, 'SUSPICIOUS_NOTIFICATIONS_TOPIC_ID', None)

    async def get_user_daily_traffic(self, user_uuid: str) -> Dict:
        """
        Получает статистику трафика пользователя за последние 24 часа

        Args:
            user_uuid: UUID пользователя в Remnawave

        Returns:
            Словарь с информацией о трафике
        """
        try:
            # Получаем время начала и конца суток (сегодня)
            now = datetime.utcnow()
            start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)

            # Форматируем даты в ISO формат
            start_date = start_of_day.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            end_date = end_of_day.strftime("%Y-%m-%dT%H:%M:%S.999Z")

            # Получаем API клиент и вызываем метод получения статистики
            async with self.remnawave_service.get_api_client() as api:
                traffic_data = await api.get_user_stats_usage(user_uuid, start_date, end_date)

            # Обрабатываем ответ API
            if traffic_data and 'response' in traffic_data:
                response = traffic_data['response']

                # Вычисляем общий трафик
                total_gb = 0
                nodes_info = []

                if isinstance(response, list):
                    for item in response:
                        node_name = item.get('nodeName', 'Unknown')
                        total_bytes = item.get('total', 0)
                        total_gb_item = round(total_bytes / (1024**3), 2)  # Конвертируем в ГБ
                        total_gb += total_gb_item

                        nodes_info.append({
                            'node': node_name,
                            'gb': total_gb_item
                        })
                else:
                    # Если response - это уже результат обработки (как в примере)
                    total_gb = response.get('total_gb', 0)
                    nodes_info = response.get('nodes', [])

                return {
                    'total_gb': total_gb,
                    'nodes': nodes_info,
                    'date_range': {
                        'start': start_date,
                        'end': end_date
                    }
                }
            else:
                logger.warning(f"Нет данных о трафике для пользователя {user_uuid}")
                return {
                    'total_gb': 0,
                    'nodes': [],
                    'date_range': {
                        'start': start_date,
                        'end': end_date
                    }
                }

        except Exception as e:
            logger.error(f"Ошибка при получении статистики трафика для {user_uuid}: {e}")
            return {
                'total_gb': 0,
                'nodes': [],
                'date_range': {
                    'start': None,
                    'end': None
                }
            }

    async def check_user_traffic_threshold(
        self, 
        db: AsyncSession, 
        user_uuid: str, 
        user_telegram_id: int = None
    ) -> Tuple[bool, Dict]:
        """
        Проверяет, превышает ли трафик пользователя заданный порог
        
        Args:
            db: Сессия базы данных
            user_uuid: UUID пользователя в Remnawave
            user_telegram_id: Telegram ID пользователя (для логирования)
            
        Returns:
            Кортеж (превышен ли порог, информация о трафике)
        """
        if not self.is_traffic_monitoring_enabled():
            return False, {}

        # Получаем статистику трафика
        traffic_info = await self.get_user_daily_traffic(user_uuid)
        total_gb = traffic_info.get('total_gb', 0)

        # Получаем порог для сравнения
        threshold_gb = self.get_traffic_threshold_gb()

        # Проверяем, превышает ли трафик порог
        is_exceeded = total_gb > threshold_gb

        # Логируем проверку
        user_id_info = f"telegram_id={user_telegram_id}" if user_telegram_id else f"uuid={user_uuid}"
        status = "ПРЕВЫШЕНИЕ" if is_exceeded else "норма"
        logger.info(
            f"📊 Проверка трафика для {user_id_info}: {total_gb} ГБ, "
            f"порог: {threshold_gb} ГБ, статус: {status}"
        )

        return is_exceeded, traffic_info

    async def process_suspicious_traffic(
        self,
        db: AsyncSession,
        user_uuid: str,
        traffic_info: Dict,
        bot
    ):
        """
        Обрабатывает подозрительный трафик - отправляет уведомление администраторам
        """
        try:
            # Получаем информацию о пользователе из базы данных
            user = await get_user_by_remnawave_uuid(db, user_uuid)
            if not user:
                logger.warning(f"Пользователь с UUID {user_uuid} не найден в базе данных")
                return

            # Формируем сообщение для администраторов
            total_gb = traffic_info.get('total_gb', 0)
            threshold_gb = self.get_traffic_threshold_gb()

            message = (
                f"⚠️ <b>Подозрительная активность трафика</b>\n\n"
                f"👤 Пользователь: {user.full_name} (ID: {user.telegram_id})\n"
                f"🔑 UUID: {user_uuid}\n"
                f"📊 Трафик за сутки: <b>{total_gb} ГБ</b>\n"
                f"📈 Порог: <b>{threshold_gb} ГБ</b>\n"
                f"🚨 Превышение: <b>{total_gb - threshold_gb:.2f} ГБ</b>\n\n"
            )

            # Добавляем информацию по нодам, если есть
            nodes = traffic_info.get('nodes', [])
            if nodes:
                message += "<b>Разбивка по нодам:</b>\n"
                for node_info in nodes[:5]:  # Показываем первые 5 нод
                    message += f"  • {node_info.get('node', 'Unknown')}: {node_info.get('gb', 0)} ГБ\n"
                if len(nodes) > 5:
                    message += f"  • и ещё {len(nodes) - 5} нод(ы)\n"

            message += f"\n⏰ Время проверки: {datetime.utcnow().strftime('%d.%m.%Y %H:%M:%S UTC')}"

            # Создаем AdminNotificationService с ботом
            admin_notification_service = AdminNotificationService(bot)

            # Отправляем уведомление администраторам
            topic_id = self.get_suspicious_notifications_topic_id()

            await admin_notification_service.send_suspicious_traffic_notification(
                message,
                bot,
                topic_id
            )

            logger.info(
                f"✅ Уведомление о подозрительном трафике отправлено для пользователя {user.telegram_id}"
            )

        except Exception as e:
            logger.error(f"❌ Ошибка при обработке подозрительного трафика для {user_uuid}: {e}")

    async def check_all_users_traffic(self, db: AsyncSession, bot):
        """
        Проверяет трафик всех пользователей с активной подпиской
        """
        if not self.is_traffic_monitoring_enabled():
            logger.info("Мониторинг трафика отключен, пропускаем проверку всех пользователей")
            return

        try:
            from app.database.crud.user import get_users_with_active_subscriptions

            # Получаем всех пользователей с активной подпиской
            users = await get_users_with_active_subscriptions(db)

            logger.info(f"Начинаем проверку трафика для {len(users)} пользователей")

            # Проверяем трафик для каждого пользователя
            for user in users:
                if user.remnawave_uuid:  # Проверяем только пользователей с UUID
                    is_exceeded, traffic_info = await self.check_user_traffic_threshold(
                        db, 
                        user.remnawave_uuid, 
                        user.telegram_id
                    )

                    if is_exceeded:
                        await self.process_suspicious_traffic(
                            db, 
                            user.remnawave_uuid, 
                            traffic_info,
                            bot
                        )

            logger.info("Завершена проверка трафика всех пользователей")

        except Exception as e:
            logger.error(f"❌ Ошибка при проверке трафика всех пользователей: {e}")


class TrafficMonitoringScheduler:
    """
    Класс для планирования периодических проверок трафика
    """
    def __init__(self, traffic_service: TrafficMonitoringService):
        self.traffic_service = traffic_service
        self.check_task = None
        self.is_running = False

    async def start_monitoring(self, db: AsyncSession, bot):
        """
        Запускает периодическую проверку трафика
        """
        if self.is_running:
            logger.warning("Мониторинг трафика уже запущен")
            return

        if not self.traffic_service.is_traffic_monitoring_enabled():
            logger.info("Мониторинг трафика отключен в настройках")
            return

        self.is_running = True
        interval_hours = self.traffic_service.get_monitoring_interval_hours()
        interval_seconds = interval_hours * 3600

        logger.info(f"Запуск мониторинга трафика с интервалом {interval_hours} часов")

        # Запускаем задачу с интервалом
        self.check_task = asyncio.create_task(self._periodic_check(db, bot, interval_seconds))

    async def stop_monitoring(self):
        """
        Останавливает периодическую проверку трафика
        """
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        self.is_running = False
        logger.info("Мониторинг трафика остановлен")

    async def _periodic_check(self, db: AsyncSession, bot, interval_seconds: int):
        """
        Выполняет периодическую проверку трафика
        """
        while self.is_running:
            try:
                logger.info("Запуск периодической проверки трафика")
                await self.traffic_service.check_all_users_traffic(db, bot)

                # Ждем указанный интервал перед следующей проверкой
                await asyncio.sleep(interval_seconds)

            except asyncio.CancelledError:
                logger.info("Задача периодической проверки трафика отменена")
                break
            except Exception as e:
                logger.error(f"Ошибка в периодической проверке трафика: {e}")
                # Даже при ошибке продолжаем цикл, ждем интервал и пробуем снова
                await asyncio.sleep(interval_seconds)


# Глобальные экземпляры сервисов
traffic_monitoring_service = TrafficMonitoringService()
traffic_monitoring_scheduler = TrafficMonitoringScheduler(traffic_monitoring_service)