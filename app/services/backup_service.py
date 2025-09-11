import asyncio
import json as json_lib
import logging
import gzip
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, asdict
import aiofiles
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, inspect
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import get_db, engine
from app.database.models import (
    User, Subscription, Transaction, PromoCode, PromoCodeUse,
    ReferralEarning, Squad, ServiceRule, SystemSetting, MonitoringLog,
    SubscriptionConversion, SentNotification, BroadcastHistory,
    ServerSquad, SubscriptionServer, UserMessage, YooKassaPayment,
    CryptoBotPayment, Base
)

logger = logging.getLogger(__name__)


@dataclass
class BackupMetadata:
    timestamp: str
    version: str = "1.0"
    database_type: str = "postgresql"
    backup_type: str = "full"
    tables_count: int = 0
    total_records: int = 0
    compressed: bool = True
    file_size_bytes: int = 0
    created_by: Optional[int] = None


@dataclass
class BackupSettings:
    auto_backup_enabled: bool = True
    backup_interval_hours: int = 24
    backup_time: str = "03:00"
    max_backups_keep: int = 7
    compression_enabled: bool = True
    include_logs: bool = False
    backup_location: str = "/app/data/backups"


class BackupService:
    
    def __init__(self, bot=None):
        self.bot = bot
        self.backup_dir = Path(settings.SQLITE_PATH).parent / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        self._auto_backup_task = None
        self._settings = self._load_settings()
        
        self.backup_models = [
            User, Subscription, Transaction, PromoCode, PromoCodeUse,
            ReferralEarning, ServiceRule, SystemSetting,
            SubscriptionConversion, SentNotification, BroadcastHistory,
            ServerSquad, SubscriptionServer, UserMessage,
            YooKassaPayment, CryptoBotPayment
        ]
        
        if self._settings.include_logs:
            self.backup_models.append(MonitoringLog)

    def _load_settings(self) -> BackupSettings:
        return BackupSettings(
            auto_backup_enabled=os.getenv("BACKUP_AUTO_ENABLED", "true").lower() == "true",
            backup_interval_hours=int(os.getenv("BACKUP_INTERVAL_HOURS", "24")),
            backup_time=os.getenv("BACKUP_TIME", "03:00"),
            max_backups_keep=int(os.getenv("BACKUP_MAX_KEEP", "7")),
            compression_enabled=os.getenv("BACKUP_COMPRESSION", "true").lower() == "true",
            include_logs=os.getenv("BACKUP_INCLUDE_LOGS", "false").lower() == "true",
            backup_location=os.getenv("BACKUP_LOCATION", "/app/data/backups")
        )

    async def create_backup(
        self, 
        created_by: Optional[int] = None,
        compress: bool = True,
        include_logs: bool = None
    ) -> Tuple[bool, str, Optional[str]]:
        try:
            logger.info("🔄 Начинаем создание бекапа...")
            
            if include_logs is None:
                include_logs = self._settings.include_logs
            
            models_to_backup = self.backup_models.copy()
            if not include_logs and MonitoringLog in models_to_backup:
                models_to_backup.remove(MonitoringLog)
            elif include_logs and MonitoringLog not in models_to_backup:
                models_to_backup.append(MonitoringLog)
            
            backup_data = {}
            total_records = 0
            
            async for db in get_db():
                try:
                    for model in models_to_backup:
                        table_name = model.__tablename__
                        logger.info(f"📊 Экспортируем таблицу: {table_name}")
                        
                        result = await db.execute(select(model))
                        records = result.scalars().all()
                        
                        table_data = []
                        for record in records:
                            record_dict = {}
                            for column in model.__table__.columns:
                                value = getattr(record, column.name)
                                
                                if isinstance(value, datetime):
                                    record_dict[column.name] = value.isoformat()
                                elif hasattr(value, '__dict__'):
                                    record_dict[column.name] = str(value)
                                else:
                                    record_dict[column.name] = value
                            
                            table_data.append(record_dict)
                        
                        backup_data[table_name] = table_data
                        total_records += len(table_data)
                        
                        logger.info(f"✅ Экспортировано {len(table_data)} записей из {table_name}")
                    
                    break
                except Exception as e:
                    logger.error(f"Ошибка при экспорте данных: {e}")
                    raise e
                finally:
                    await db.close()
            
            metadata = BackupMetadata(
                timestamp=datetime.utcnow().isoformat(),
                database_type="postgresql" if settings.is_postgresql() else "sqlite",
                backup_type="full",
                tables_count=len(models_to_backup),
                total_records=total_records,
                compressed=compress,
                created_by=created_by,
                file_size_bytes=0
            )
            
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"backup_{timestamp}.json"
            if compress:
                filename += ".gz"
            
            backup_path = self.backup_dir / filename
            
            backup_structure = {
                "metadata": asdict(metadata),
                "data": backup_data
            }
            
            if compress:
                backup_json_str = json_lib.dumps(backup_structure, ensure_ascii=False, indent=2)
                async with aiofiles.open(backup_path, 'wb') as f:
                    compressed_data = gzip.compress(backup_json_str.encode('utf-8'))
                    await f.write(compressed_data)
            else:
                async with aiofiles.open(backup_path, 'w', encoding='utf-8') as f:
                    await f.write(json_lib.dumps(backup_structure, ensure_ascii=False, indent=2))
            
            file_size = backup_path.stat().st_size
            backup_structure["metadata"]["file_size_bytes"] = file_size
            
            if compress:
                backup_json_str = json_lib.dumps(backup_structure, ensure_ascii=False, indent=2)
                async with aiofiles.open(backup_path, 'wb') as f:
                    compressed_data = gzip.compress(backup_json_str.encode('utf-8'))
                    await f.write(compressed_data)
            else:
                async with aiofiles.open(backup_path, 'w', encoding='utf-8') as f:
                    await f.write(json_lib.dumps(backup_structure, ensure_ascii=False, indent=2))
            
            await self._cleanup_old_backups()
            
            size_mb = file_size / 1024 / 1024
            message = (f"✅ Бекап успешно создан!\n"
                      f"📁 Файл: {filename}\n"
                      f"📊 Таблиц: {len(models_to_backup)}\n"
                      f"📈 Записей: {total_records:,}\n"
                      f"💾 Размер: {size_mb:.2f} MB")
            
            logger.info(message)
            
            if self.bot:
                await self._send_backup_notification(
                    "success", message, str(backup_path)
                )
            
            return True, message, str(backup_path)
            
        except Exception as e:
            error_msg = f"❌ Ошибка создания бекапа: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            if self.bot:
                await self._send_backup_notification("error", error_msg)
            
            return False, error_msg, None

    async def restore_backup(
        self, 
        backup_file_path: str,
        clear_existing: bool = False
    ) -> Tuple[bool, str]:
        try:
            logger.info(f"🔄 Начинаем восстановление из {backup_file_path}")
            
            backup_path = Path(backup_file_path)
            if not backup_path.exists():
                return False, f"❌ Файл бекапа не найден: {backup_file_path}"
            
            if backup_path.suffix == '.gz':
                async with aiofiles.open(backup_path, 'rb') as f:
                    compressed_data = await f.read()
                    uncompressed_data = gzip.decompress(compressed_data).decode('utf-8')
                    backup_structure = json_lib.loads(uncompressed_data)
            else:
                async with aiofiles.open(backup_path, 'r', encoding='utf-8') as f:
                    file_content = await f.read()
                    backup_structure = json_lib.loads(file_content)
            
            metadata = backup_structure.get("metadata", {})
            backup_data = backup_structure.get("data", {})
            
            if not backup_data:
                return False, "❌ Файл бекапа не содержит данных"
            
            logger.info(f"📊 Загружен бекап от {metadata.get('timestamp')}")
            logger.info(f"📈 Содержит {metadata.get('total_records', 0)} записей")
            
            restored_records = 0
            restored_tables = 0
            
            async for db in get_db():
                try:
                    if clear_existing:
                        logger.warning("🗑️ Очищаем существующие данные...")
                        await self._clear_database_tables(db)
                    
                    for table_name, records in backup_data.items():
                        if not records:
                            continue
                        
                        model = None
                        for m in self.backup_models:
                            if m.__tablename__ == table_name:
                                model = m
                                break
                        
                        if not model:
                            logger.warning(f"⚠️ Модель для таблицы {table_name} не найдена, пропускаем")
                            continue
                        
                        logger.info(f"📥 Восстанавливаем таблицу {table_name} ({len(records)} записей)")
                        
                        for record_data in records:
                            try:
                                processed_data = {}
                                for key, value in record_data.items():
                                    if value is None:
                                        processed_data[key] = None
                                        continue
                                    
                                    column = getattr(model.__table__.columns, key, None)
                                    if column is None:
                                        continue
                                    
                                    column_type_str = str(column.type).upper()
                                    if ('DATETIME' in column_type_str or 'TIMESTAMP' in column_type_str) and isinstance(value, str):
                                        try:
                                            if 'T' in value:
                                                processed_data[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                                            else:
                                                processed_data[key] = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
                                        except (ValueError, TypeError) as e:
                                            logger.warning(f"Не удалось парсить дату {value} для поля {key}: {e}")
                                            processed_data[key] = datetime.utcnow()
                                    elif ('BOOLEAN' in column_type_str or 'BOOL' in column_type_str) and isinstance(value, str):
                                        processed_data[key] = value.lower() in ('true', '1', 'yes', 'on')
                                    elif ('INTEGER' in column_type_str or 'INT' in column_type_str) and isinstance(value, str):
                                        try:
                                            processed_data[key] = int(value)
                                        except ValueError:
                                            processed_data[key] = 0
                                    elif ('FLOAT' in column_type_str or 'REAL' in column_type_str or 'NUMERIC' in column_type_str) and isinstance(value, str):
                                        try:
                                            processed_data[key] = float(value)
                                        except ValueError:
                                            processed_data[key] = 0.0
                                    elif 'JSON' in column_type_str and isinstance(value, str):
                                        try:
                                            processed_data[key] = json_lib.loads(value)
                                        except (ValueError, TypeError):
                                            processed_data[key] = value
                                    else:
                                        processed_data[key] = value
                                
                                # Проверяем существует ли запись с таким ID
                                primary_key_col = None
                                for col in model.__table__.columns:
                                    if col.primary_key:
                                        primary_key_col = col.name
                                        break
                                
                                if primary_key_col and primary_key_col in processed_data:
                                    # Проверяем существование записи
                                    existing_record = await db.execute(
                                        select(model).where(
                                            getattr(model, primary_key_col) == processed_data[primary_key_col]
                                        )
                                    )
                                    existing = existing_record.scalar_one_or_none()
                                    
                                    if existing:
                                        # Обновляем существующую запись
                                        for key, value in processed_data.items():
                                            if key != primary_key_col:  # Не обновляем primary key
                                                setattr(existing, key, value)
                                        logger.debug(f"Обновлена существующая запись {primary_key_col}={processed_data[primary_key_col]} в {table_name}")
                                    else:
                                        # Создаем новую запись
                                        instance = model(**processed_data)
                                        db.add(instance)
                                else:
                                    # Если нет primary key или он не в данных, просто добавляем
                                    instance = model(**processed_data)
                                    db.add(instance)
                                
                                restored_records += 1
                                
                            except Exception as e:
                                logger.error(f"Ошибка восстановления записи в {table_name}: {e}")
                                continue
                        
                        restored_tables += 1
                        logger.info(f"✅ Таблица {table_name} восстановлена")
                    
                    await db.commit()
                    
                    break
                    
                except Exception as e:
                    await db.rollback()
                    logger.error(f"Ошибка при восстановлении: {e}")
                    raise e
                finally:
                    await db.close()
            
            message = (f"✅ Восстановление завершено!\n"
                      f"📊 Таблиц: {restored_tables}\n"
                      f"📈 Записей: {restored_records:,}\n"
                      f"📅 Дата бекапа: {metadata.get('timestamp', 'неизвестно')}")
            
            logger.info(message)
            
            if self.bot:
                await self._send_backup_notification("restore_success", message)
            
            return True, message
            
        except Exception as e:
            error_msg = f"❌ Ошибка восстановления: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            if self.bot:
                await self._send_backup_notification("restore_error", error_msg)
            
            return False, error_msg

    async def _clear_database_tables(self, db: AsyncSession):
        tables_order = [
            "subscription_servers", "sent_notifications", "broadcast_history",
            "subscription_conversions", "referral_earnings", "promocode_uses",
            "transactions", "yookassa_payments", "cryptobot_payments",
            "subscriptions", "users", "promocodes", "server_squads",
            "service_rules", "system_settings", "monitoring_logs", "user_messages"
        ]
        
        for table_name in tables_order:
            try:
                await db.execute(text(f"DELETE FROM {table_name}"))
                logger.info(f"🗑️ Очищена таблица {table_name}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось очистить таблицу {table_name}: {e}")

    async def get_backup_list(self) -> List[Dict[str, Any]]:
        backups = []
        
        try:
            for backup_file in sorted(self.backup_dir.glob("backup_*.json*"), reverse=True):
                try:
                    if backup_file.suffix == '.gz':
                        with gzip.open(backup_file, 'rt', encoding='utf-8') as f:
                            backup_structure = json_lib.load(f)
                    else:
                        with open(backup_file, 'r', encoding='utf-8') as f:
                            backup_structure = json_lib.load(f)
                    
                    metadata = backup_structure.get("metadata", {})
                    file_stats = backup_file.stat()
                    
                    backup_info = {
                        "filename": backup_file.name,
                        "filepath": str(backup_file),
                        "timestamp": metadata.get("timestamp"),
                        "tables_count": metadata.get("tables_count", 0),
                        "total_records": metadata.get("total_records", 0),
                        "compressed": metadata.get("compressed", False),
                        "file_size_bytes": file_stats.st_size,
                        "file_size_mb": round(file_stats.st_size / 1024 / 1024, 2),
                        "created_by": metadata.get("created_by"),
                        "database_type": metadata.get("database_type", "unknown")
                    }
                    
                    backups.append(backup_info)
                    
                except Exception as e:
                    logger.error(f"Ошибка чтения метаданных {backup_file}: {e}")
                    file_stats = backup_file.stat()
                    backups.append({
                        "filename": backup_file.name,
                        "filepath": str(backup_file),
                        "timestamp": datetime.fromtimestamp(file_stats.st_mtime).isoformat(),
                        "tables_count": "?",
                        "total_records": "?",
                        "compressed": backup_file.suffix == '.gz',
                        "file_size_bytes": file_stats.st_size,
                        "file_size_mb": round(file_stats.st_size / 1024 / 1024, 2),
                        "created_by": None,
                        "database_type": "unknown",
                        "error": f"Ошибка чтения: {str(e)}"
                    })
        
        except Exception as e:
            logger.error(f"Ошибка получения списка бекапов: {e}")
        
        return backups

    async def delete_backup(self, backup_filename: str) -> Tuple[bool, str]:
        try:
            backup_path = self.backup_dir / backup_filename
            
            if not backup_path.exists():
                return False, f"❌ Файл бекапа не найден: {backup_filename}"
            
            backup_path.unlink()
            message = f"✅ Бекап {backup_filename} удален"
            logger.info(message)
            
            return True, message
            
        except Exception as e:
            error_msg = f"❌ Ошибка удаления бекапа: {str(e)}"
            logger.error(error_msg)
            return False, error_msg

    async def _cleanup_old_backups(self):
        try:
            backups = await self.get_backup_list()
            
            if len(backups) > self._settings.max_backups_keep:
                backups.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                
                for backup in backups[self._settings.max_backups_keep:]:
                    try:
                        await self.delete_backup(backup["filename"])
                        logger.info(f"🗑️ Удален старый бекап: {backup['filename']}")
                    except Exception as e:
                        logger.error(f"Ошибка удаления старого бекапа {backup['filename']}: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка очистки старых бекапов: {e}")

    async def get_backup_settings(self) -> BackupSettings:
        return self._settings

    async def update_backup_settings(self, **kwargs) -> bool:
        try:
            for key, value in kwargs.items():
                if hasattr(self._settings, key):
                    setattr(self._settings, key, value)
            
            if self._settings.auto_backup_enabled:
                await self.start_auto_backup()
            else:
                await self.stop_auto_backup()
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обновления настроек бекапов: {e}")
            return False

    async def start_auto_backup(self):
        if self._auto_backup_task and not self._auto_backup_task.done():
            self._auto_backup_task.cancel()
        
        if self._settings.auto_backup_enabled:
            self._auto_backup_task = asyncio.create_task(self._auto_backup_loop())
            logger.info(f"🔄 Автобекапы включены, интервал: {self._settings.backup_interval_hours}ч")

    async def stop_auto_backup(self):
        if self._auto_backup_task and not self._auto_backup_task.done():
            self._auto_backup_task.cancel()
            logger.info("⏹️ Автобекапы остановлены")

    async def _auto_backup_loop(self):
        while True:
            try:
                await asyncio.sleep(self._settings.backup_interval_hours * 3600)
                
                logger.info("🔄 Запуск автоматического бекапа...")
                success, message, _ = await self.create_backup()
                
                if success:
                    logger.info(f"✅ Автобекап завершен: {message}")
                else:
                    logger.error(f"❌ Ошибка автобекапа: {message}")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле автобекапов: {e}")
                await asyncio.sleep(3600)

    async def _send_backup_notification(
        self, 
        event_type: str, 
        message: str, 
        file_path: str = None
    ):
        try:
            if not settings.is_admin_notifications_enabled():
                return
            
            icons = {
                "success": "✅",
                "error": "❌", 
                "restore_success": "📥",
                "restore_error": "❌"
            }
            
            icon = icons.get(event_type, "ℹ️")
            notification_text = f"{icon} <b>СИСТЕМА БЕКАПОВ</b>\n\n{message}"
            
            if file_path:
                notification_text += f"\n📁 <code>{Path(file_path).name}</code>"
            
            notification_text += f"\n\n⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"
            
            try:
                from app.services.admin_notification_service import AdminNotificationService
                admin_service = AdminNotificationService(self.bot)
                await admin_service._send_message(notification_text)
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления через AdminNotificationService: {e}")
        
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о бекапе: {e}")


backup_service = BackupService()
