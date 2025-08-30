import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass

from app.config import settings
from app.external.remnawave_api import RemnaWaveAPI, test_api_connection
from app.utils.cache import cache

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceStatus:
    is_active: bool
    enabled_at: Optional[datetime] = None
    last_check: Optional[datetime] = None
    reason: Optional[str] = None
    auto_enabled: bool = False
    api_status: bool = True
    consecutive_failures: int = 0


class MaintenanceService:
    """
    Сервис для управления режимом технических работ
    """
    
    def __init__(self):
        self._status = MaintenanceStatus(is_active=False)
        self._check_task: Optional[asyncio.Task] = None
        self._is_checking = False
        self._max_consecutive_failures = 3  # После 3 неудачных проверок включаем техработы
        
    @property
    def status(self) -> MaintenanceStatus:
        return self._status
    
    def is_maintenance_active(self) -> bool:
        """Проверяет, активен ли режим техработ"""
        return self._status.is_active
    
    def get_maintenance_message(self) -> str:
        """Получает сообщение о техработах"""
        if self._status.auto_enabled:
            return f"""
🔧 <b>Технические работы</b>

Сервис временно недоступен из-за проблем с подключением к серверам.

⏰ Мы работаем над восстановлением. Попробуйте через несколько минут.

🔄 Последняя проверка: {self._status.last_check.strftime('%H:%M:%S') if self._status.last_check else 'неизвестно'}
"""
        else:
            return settings.get_maintenance_message()
    
    async def enable_maintenance(self, reason: Optional[str] = None, auto: bool = False) -> bool:
        """Включает режим техработ"""
        try:
            if self._status.is_active:
                logger.warning("Режим техработ уже включен")
                return True
            
            self._status.is_active = True
            self._status.enabled_at = datetime.utcnow()
            self._status.reason = reason or ("Автоматическое включение" if auto else "Включено администратором")
            self._status.auto_enabled = auto
            
            # Сохраняем состояние в кеше
            await self._save_status_to_cache()
            
            logger.warning(f"🔧 Режим техработ ВКЛЮЧЕН. Причина: {self._status.reason}")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка включения режима техработ: {e}")
            return False
    
    async def disable_maintenance(self) -> bool:
        """Выключает режим техработ"""
        try:
            if not self._status.is_active:
                logger.info("Режим техработ уже выключен")
                return True
            
            self._status.is_active = False
            self._status.enabled_at = None
            self._status.reason = None
            self._status.auto_enabled = False
            self._status.consecutive_failures = 0
            
            # Сохраняем состояние в кеше
            await self._save_status_to_cache()
            
            logger.info("✅ Режим техработ ВЫКЛЮЧЕН")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка выключения режима техработ: {e}")
            return False
    
    async def start_monitoring(self) -> bool:
        """Запускает мониторинг API RemnaWave"""
        try:
            if self._check_task and not self._check_task.done():
                logger.warning("Мониторинг уже запущен")
                return True
            
            # Загружаем состояние из кеша
            await self._load_status_from_cache()
            
            self._check_task = asyncio.create_task(self._monitoring_loop())
            logger.info(f"🔄 Запущен мониторинг API RemnaWave (интервал: {settings.get_maintenance_check_interval()}с)")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка запуска мониторинга: {e}")
            return False
    
    async def stop_monitoring(self) -> bool:
        """Останавливает мониторинг API"""
        try:
            if self._check_task and not self._check_task.done():
                self._check_task.cancel()
                try:
                    await self._check_task
                except asyncio.CancelledError:
                    pass
            
            logger.info("⏹️ Мониторинг API остановлен")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка остановки мониторинга: {e}")
            return False
    
    async def check_api_status(self) -> bool:
        """Проверяет доступность API RemnaWave"""
        try:
            if self._is_checking:
                return self._status.api_status
            
            self._is_checking = True
            self._status.last_check = datetime.utcnow()
            
            api = RemnaWaveAPI(settings.REMNAWAVE_API_URL, settings.REMNAWAVE_API_KEY)
            
            async with api:
                # Проверяем подключение к API
                is_connected = await test_api_connection(api)
                
                if is_connected:
                    self._status.api_status = True
                    self._status.consecutive_failures = 0
                    
                    # Если техработы были включены автоматически и API восстановился
                    if self._status.is_active and self._status.auto_enabled:
                        await self.disable_maintenance()
                        logger.info("✅ API восстановился, режим техработ автоматически отключен")
                    
                    return True
                else:
                    self._status.api_status = False
                    self._status.consecutive_failures += 1
                    
                    # Включаем техработы автоматически при множественных сбоях
                    if (self._status.consecutive_failures >= self._max_consecutive_failures and
                        not self._status.is_active and
                        settings.is_maintenance_auto_enable()):
                        
                        await self.enable_maintenance(
                            reason=f"Автоматическое включение после {self._status.consecutive_failures} неудачных проверок API",
                            auto=True
                        )
                    
                    return False
                    
        except Exception as e:
            logger.error(f"Ошибка проверки API: {e}")
            self._status.api_status = False
            self._status.consecutive_failures += 1
            return False
        finally:
            self._is_checking = False
            await self._save_status_to_cache()
    
    async def _monitoring_loop(self):
        """Основной цикл мониторинга"""
        while True:
            try:
                await self.check_api_status()
                await asyncio.sleep(settings.get_maintenance_check_interval())
                
            except asyncio.CancelledError:
                logger.info("Мониторинг отменен")
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(30)  # Ждем полминуты при ошибке
    
    async def _save_status_to_cache(self):
        """Сохраняет состояние в кеше"""
        try:
            status_data = {
                "is_active": self._status.is_active,
                "enabled_at": self._status.enabled_at.isoformat() if self._status.enabled_at else None,
                "reason": self._status.reason,
                "auto_enabled": self._status.auto_enabled,
                "consecutive_failures": self._status.consecutive_failures,
                "last_check": self._status.last_check.isoformat() if self._status.last_check else None
            }
            
            await cache.set("maintenance_status", status_data, expire=3600)
            
        except Exception as e:
            logger.error(f"Ошибка сохранения состояния в кеш: {e}")
    
    async def _load_status_from_cache(self):
        """Загружает состояние из кеша"""
        try:
            status_data = await cache.get("maintenance_status")
            if not status_data:
                return
            
            self._status.is_active = status_data.get("is_active", False)
            self._status.reason = status_data.get("reason")
            self._status.auto_enabled = status_data.get("auto_enabled", False)
            self._status.consecutive_failures = status_data.get("consecutive_failures", 0)
            
            if status_data.get("enabled_at"):
                self._status.enabled_at = datetime.fromisoformat(status_data["enabled_at"])
            
            if status_data.get("last_check"):
                self._status.last_check = datetime.fromisoformat(status_data["last_check"])
            
            logger.info(f"📥 Состояние техработ загружено из кеша: активен={self._status.is_active}")
            
        except Exception as e:
            logger.error(f"Ошибка загрузки состояния из кеша: {e}")
    
    def get_status_info(self) -> Dict[str, Any]:
        """Возвращает информацию о статусе техработ для админки"""
        return {
            "is_active": self._status.is_active,
            "enabled_at": self._status.enabled_at,
            "last_check": self._status.last_check,
            "reason": self._status.reason,
            "auto_enabled": self._status.auto_enabled,
            "api_status": self._status.api_status,
            "consecutive_failures": self._status.consecutive_failures,
            "monitoring_active": self._check_task is not None and not self._check_task.done(),
            "auto_enable_configured": settings.is_maintenance_auto_enable(),
            "check_interval": settings.get_maintenance_check_interval()
        }
    
    async def force_api_check(self) -> Dict[str, Any]:
        """Принудительная проверка API для админки"""
        start_time = datetime.utcnow()
        
        try:
            api_status = await self.check_api_status()
            end_time = datetime.utcnow()
            response_time = (end_time - start_time).total_seconds()
            
            return {
                "success": True,
                "api_available": api_status,
                "response_time": round(response_time, 2),
                "checked_at": end_time,
                "consecutive_failures": self._status.consecutive_failures
            }
            
        except Exception as e:
            end_time = datetime.utcnow()
            response_time = (end_time - start_time).total_seconds()
            
            return {
                "success": False,
                "api_available": False,
                "error": str(e),
                "response_time": round(response_time, 2),
                "checked_at": end_time,
                "consecutive_failures": self._status.consecutive_failures
            }


# Глобальный экземпляр сервиса
maintenance_service = MaintenanceService()