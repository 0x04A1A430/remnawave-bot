import asyncio
import gzip
import json as json_lib
import logging
import os
import shutil
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import pyzipper
from aiogram.types import FSInputFile
from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import (
    User, Subscription, Transaction, PromoCode, PromoCodeUse,
    ReferralEarning, Squad, ServiceRule, SystemSetting, MonitoringLog,
    SubscriptionConversion, SentNotification, BroadcastHistory,
    ServerSquad, SubscriptionServer, UserMessage, YooKassaPayment,
    CryptoBotPayment, WelcomeText, Base, PromoGroup, AdvertisingCampaign,
    AdvertisingCampaignRegistration, SupportAuditLog, Ticket, TicketMessage,
    MulenPayPayment, Pal24Payment, DiscountOffer, WebApiToken, Tariff,
    server_squad_promo_groups
)

logger = logging.getLogger(__name__)


@dataclass
class BackupMetadata:
    timestamp: str
    version: str = "1.2"
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
        self.backup_dir = Path(settings.BACKUP_LOCATION).expanduser().resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = self.backup_dir.parent
        self.archive_format_version = "2.0"
        self._auto_backup_task = None
        self._settings = self._load_settings()

        self._base_backup_models = [
            SystemSetting,
            ServiceRule,
            Squad,
            ServerSquad,
            PromoGroup,
            Tariff,  # Tariff должен быть ДО Subscription из-за FK
            User,
            PromoCode,
            WelcomeText,
            UserMessage,
            Subscription,
            SubscriptionServer,
            SubscriptionConversion,
            Transaction,
            YooKassaPayment,
            CryptoBotPayment,
            MulenPayPayment,
            Pal24Payment,
            PromoCodeUse,
            ReferralEarning,
            SentNotification,
            DiscountOffer,
            BroadcastHistory,
            AdvertisingCampaign,
            AdvertisingCampaignRegistration,
            Ticket,
            TicketMessage,
            SupportAuditLog,
            WebApiToken,
        ]

        self.backup_models_ordered = self._base_backup_models.copy()

        if self._settings.include_logs:
            self.backup_models_ordered.append(MonitoringLog)

        self.association_tables = {
            "server_squad_promo_groups": server_squad_promo_groups,
        }

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

    def _parse_backup_time(self) -> Tuple[int, int]:
        time_str = (self._settings.backup_time or "").strip()

        try:
            parts = time_str.split(":")
            if len(parts) != 2:
                raise ValueError("Invalid time format")

            hours, minutes = map(int, parts)

            if not (0 <= hours < 24 and 0 <= minutes < 60):
                raise ValueError("Hours or minutes out of range")

            return hours, minutes

        except ValueError:
            default_hours, default_minutes = 3, 0
            logger.warning(
                "Некорректное значение BACKUP_TIME='%s'. Используется значение по умолчанию 03:00.",
                self._settings.backup_time
            )
            self._settings.backup_time = "03:00"
            return default_hours, default_minutes

    def _calculate_next_backup_datetime(self, reference: Optional[datetime] = None) -> datetime:
        reference = reference or datetime.now()
        hours, minutes = self._parse_backup_time()

        next_run = reference.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        if next_run <= reference:
            next_run += timedelta(days=1)

        return next_run

    def _get_backup_interval(self) -> timedelta:
        hours = self._settings.backup_interval_hours

        if hours <= 0:
            logger.warning(
                "Некорректное значение BACKUP_INTERVAL_HOURS=%s. Используется значение по умолчанию 24.",
                hours
            )
            hours = 24
            self._settings.backup_interval_hours = hours

        return timedelta(hours=hours)

    def _get_models_for_backup(self, include_logs: bool) -> List[Any]:
        models = self._base_backup_models.copy()

        if include_logs:
            if MonitoringLog not in models:
                models.append(MonitoringLog)
        else:
            models = [model for model in models if model is not MonitoringLog]

        return models

    def _resolve_command_path(self, command: str, env_var: str) -> Optional[str]:
        override = os.getenv(env_var)
        if override:
            override_path = Path(override)
            if override_path.exists() and os.access(override_path, os.X_OK):
                return str(override_path)
            logger.warning(
                "Путь %s из %s недоступен или не является исполняемым",
                override,
                env_var,
            )

        resolved = shutil.which(command)
        if resolved:
            return resolved

        return None

    async def create_backup(
        self,
        created_by: Optional[int] = None,
        compress: bool = True,
        include_logs: bool = None
    ) -> Tuple[bool, str, Optional[str]]:
        try:
            logger.info("📄 Начинаем создание бекапа...")

            if include_logs is None:
                include_logs = self._settings.include_logs

            overview = await self._collect_database_overview()

            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            archive_suffix = ".tar.gz" if compress else ".tar"
            filename = f"backup_{timestamp}{archive_suffix}"
            backup_path = self.backup_dir / filename

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                staging_dir = temp_path / "backup"
                staging_dir.mkdir(parents=True, exist_ok=True)

                database_info = await self._dump_database(
                    staging_dir,
                    include_logs=include_logs
                )
                database_info.setdefault("tables_count", overview.get("tables_count", 0))
                database_info.setdefault("total_records", overview.get("total_records", 0))
                files_info = await self._collect_files(staging_dir, include_logs=include_logs)
                data_snapshot_info = await self._collect_data_snapshot(staging_dir)

                metadata = {
                    "format_version": self.archive_format_version,
                    "timestamp": datetime.utcnow().isoformat(),
                    "database_type": "postgresql" if settings.is_postgresql() else "sqlite",
                    "backup_type": "full",
                    "tables_count": overview.get("tables_count", 0),
                    "total_records": overview.get("total_records", 0),
                    "compressed": True,
                    "created_by": created_by,
                    "database": database_info,
                    "files": files_info,
                    "data_snapshot": data_snapshot_info,
                    "settings": asdict(self._settings),
                }

                metadata_path = staging_dir / "metadata.json"
                async with aiofiles.open(metadata_path, "w", encoding="utf-8") as meta_file:
                    await meta_file.write(json_lib.dumps(metadata, ensure_ascii=False, indent=2))

                mode = "w:gz" if compress else "w"
                with tarfile.open(backup_path, mode) as tar:
                    for item in staging_dir.iterdir():
                        tar.add(item, arcname=item.name)

            file_size = backup_path.stat().st_size

            await self._cleanup_old_backups()

            size_mb = file_size / 1024 / 1024
            message = (f"✅ Бекап успешно создан!\n"
                      f"📁 Файл: {filename}\n"
                      f"📊 Таблиц: {overview.get('tables_count', 0)}\n"
                      f"📈 Записей: {overview.get('total_records', 0):,}\n"
                      f"💾 Размер: {size_mb:.2f} MB")

            logger.info(message)

            if self.bot:
                await self._send_backup_notification(
                    "success", message, str(backup_path)
                )

                await self._send_backup_file_to_chat(str(backup_path))

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
            logger.info(f"📄 Начинаем восстановление из {backup_file_path}")

            backup_path = Path(backup_file_path)
            if not backup_path.exists():
                return False, f"❌ Файл бекапа не найден: {backup_file_path}"

            if self._is_archive_backup(backup_path):
                success, message = await self._restore_from_archive(backup_path, clear_existing)
            else:
                success, message = await self._restore_from_legacy(backup_path, clear_existing)

            if success and self.bot:
                await self._send_backup_notification("restore_success", message)
            elif not success and self.bot:
                await self._send_backup_notification("restore_error", message)

            return success, message

        except Exception as e:
            error_msg = f"❌ Ошибка восстановления: {str(e)}"
            logger.error(error_msg, exc_info=True)

            if self.bot:
                await self._send_backup_notification("restore_error", error_msg)

            return False, error_msg

    async def _collect_database_overview(self) -> Dict[str, Any]:
        overview: Dict[str, Any] = {
            "tables_count": 0,
            "total_records": 0,
            "tables": [],
        }

        try:
            async with engine.begin() as conn:
                table_names = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )

                for table_name in table_names:
                    try:
                        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
                        count = result.scalar_one()
                    except Exception:
                        count = 0

                    overview["tables"].append({"name": table_name, "rows": count})
                    overview["total_records"] += count

                overview["tables_count"] = len(table_names)
        except Exception as exc:
            logger.warning("Не удалось собрать статистику по БД: %s", exc)

        return overview

    async def _dump_database(self, staging_dir: Path, include_logs: bool) -> Dict[str, Any]:
        if settings.is_postgresql():
            pg_dump_path = self._resolve_command_path("pg_dump", "PG_DUMP_PATH")

            if pg_dump_path:
                dump_path = staging_dir / "database.sql"
                await self._dump_postgres(dump_path, pg_dump_path)
                size = dump_path.stat().st_size if dump_path.exists() else 0
                return {
                    "type": "postgresql",
                    "path": dump_path.name,
                    "size_bytes": size,
                    "format": "sql",
                    "tool": pg_dump_path,
                }

            logger.info(
                "pg_dump не найден в PATH. Используется ORM-дамп в формате JSON"
            )
            json_info = await self._dump_postgres_json(staging_dir, include_logs)
            return json_info

        dump_path = staging_dir / "database.sqlite"
        await self._dump_sqlite(dump_path)
        size = dump_path.stat().st_size if dump_path.exists() else 0
        return {
            "type": "sqlite",
            "path": dump_path.name,
            "size_bytes": size,
            "format": "file",
        }

    async def _dump_postgres(self, dump_path: Path, pg_dump_path: str):
        env = os.environ.copy()
        env.update({
            "PGHOST": settings.POSTGRES_HOST,
            "PGPORT": str(settings.POSTGRES_PORT),
            "PGUSER": settings.POSTGRES_USER,
            "PGPASSWORD": settings.POSTGRES_PASSWORD,
        })

        command = [
            pg_dump_path,
            "--format=plain",
            "--no-owner",
            "--no-privileges",
            settings.POSTGRES_DB,
        ]

        logger.info("📦 Экспорт PostgreSQL через pg_dump (%s)...", pg_dump_path)
        dump_path.parent.mkdir(parents=True, exist_ok=True)

        with dump_path.open("wb") as dump_file:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=dump_file,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _, stderr = await process.communicate()

        if process.returncode != 0:
            error_text = stderr.decode() if stderr else "pg_dump error"
            raise RuntimeError(f"pg_dump завершился с ошибкой: {error_text}")

        logger.info("✅ PostgreSQL dump создан (%s)", dump_path)

    async def _dump_postgres_json(self, staging_dir: Path, include_logs: bool) -> Dict[str, Any]:
        models_to_backup = self._get_models_for_backup(include_logs)
        (
            backup_data,
            association_data,
            total_records,
            tables_count,
        ) = await self._export_database_via_orm(models_to_backup)

        dump_path = staging_dir / "database.json"
        dump_structure = {
            "metadata": {
                "timestamp": datetime.utcnow().isoformat(),
                "version": "orm-1.0",
                "database_type": "postgresql",
                "tables_count": tables_count,
                "total_records": total_records,
            },
            "data": backup_data,
            "associations": association_data,
        }

        async with aiofiles.open(dump_path, "w", encoding="utf-8") as dump_file:
            await dump_file.write(
                json_lib.dumps(dump_structure, ensure_ascii=False, indent=2)
            )

        size = dump_path.stat().st_size if dump_path.exists() else 0

        logger.info(
            "✅ PostgreSQL экспортирован через ORM в JSON (%s)",
            dump_path,
        )

        return {
            "type": "postgresql",
            "path": dump_path.name,
            "size_bytes": size,
            "format": "json",
            "tool": "orm",
            "format_version": "orm-1.0",
            "tables_count": tables_count,
            "total_records": total_records,
        }

    async def _dump_sqlite(self, dump_path: Path):
        sqlite_path = Path(settings.SQLITE_PATH)
        if not sqlite_path.exists():
            raise FileNotFoundError(f"SQLite база данных не найдена по пути {sqlite_path}")

        dump_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(shutil.copy2, sqlite_path, dump_path)
        logger.info("✅ SQLite база данных скопирована (%s)", dump_path)

    async def _export_database_via_orm(
        self,
        models_to_backup: List[Any],
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]], int, int]:
        backup_data: Dict[str, List[Dict[str, Any]]] = {}
        total_records = 0

        async with AsyncSessionLocal() as db:
            try:
                for model in models_to_backup:
                    table_name = model.__tablename__
                    logger.info("📊 Экспортируем таблицу: %s", table_name)

                    query = select(model)

                    if model == User:
                        query = query.options(selectinload(User.subscription))
                    elif model == Subscription:
                        query = query.options(selectinload(Subscription.user))
                    elif model == Transaction:
                        query = query.options(selectinload(Transaction.user))

                    result = await db.execute(query)
                    records = result.scalars().all()

                    table_data: List[Dict[str, Any]] = []
                    for record in records:
                        record_dict: Dict[str, Any] = {}
                        for column in model.__table__.columns:
                            value = getattr(record, column.name)

                            if value is None:
                                record_dict[column.name] = None
                            elif isinstance(value, datetime):
                                record_dict[column.name] = value.isoformat()
                            elif isinstance(value, (list, dict)):
                                record_dict[column.name] = (
                                    json_lib.dumps(value) if value else None
                                )
                            elif hasattr(value, "__dict__"):
                                record_dict[column.name] = str(value)
                            else:
                                record_dict[column.name] = value

                        table_data.append(record_dict)

                    backup_data[table_name] = table_data
                    total_records += len(table_data)

                    logger.info(
                        "✅ Экспортировано %s записей из %s",
                        len(table_data),
                        table_name,
                    )

                association_data = await self._export_association_tables(db)
                for records in association_data.values():
                    total_records += len(records)

                tables_count = len(models_to_backup) + len(association_data)
                return backup_data, association_data, total_records, tables_count

            except Exception as exc:
                logger.error("Ошибка при экспорте данных: %s", exc)
                raise exc

    async def _collect_files(self, staging_dir: Path, include_logs: bool) -> List[Dict[str, Any]]:
        files_info: List[Dict[str, Any]] = []
        files_dir = staging_dir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)

        app_config_path = settings.get_app_config_path()
        if app_config_path:
            src = Path(app_config_path)
            if src.exists():
                dest = files_dir / src.name
                await asyncio.to_thread(shutil.copy2, src, dest)
                files_info.append({
                    "path": str(src),
                    "relative_path": f"files/{src.name}",
                })

        if include_logs and settings.LOG_FILE:
            log_path = Path(settings.LOG_FILE)
            if log_path.exists():
                dest = files_dir / log_path.name
                await asyncio.to_thread(shutil.copy2, log_path, dest)
                files_info.append({
                    "path": str(log_path),
                    "relative_path": f"files/{log_path.name}",
                })

        if not files_info and files_dir.exists():
            files_dir.rmdir()

        return files_info

    async def _collect_data_snapshot(self, staging_dir: Path) -> Dict[str, Any]:
        data_dir = staging_dir / "data"
        snapshot_info: Dict[str, Any] = {
            "path": str(self.data_dir),
            "items": 0,
        }

        if not self.data_dir.exists():
            return snapshot_info

        counter = {"items": 0}

        def _copy_data():
            data_dir.mkdir(parents=True, exist_ok=True)
            for item in self.data_dir.iterdir():
                if item.resolve() == self.backup_dir.resolve():
                    continue

                destination = data_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, destination)
                counter["items"] += 1

        await asyncio.to_thread(_copy_data)
        snapshot_info["items"] = counter["items"]
        return snapshot_info

    def _is_archive_backup(self, backup_path: Path) -> bool:
        suffixes = backup_path.suffixes
        if (len(suffixes) >= 2 and suffixes[-2:] == [".tar", ".gz"]) or (suffixes and suffixes[-1] == ".tar"):
            return True
        try:
            return tarfile.is_tarfile(backup_path)
        except Exception:
            return False

    async def _restore_from_archive(
        self,
        backup_path: Path,
        clear_existing: bool,
    ) -> Tuple[bool, str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            mode = "r:gz" if backup_path.suffixes and backup_path.suffixes[-1] == ".gz" else "r"
            with tarfile.open(backup_path, mode) as tar:
                tar.extractall(temp_path)

            metadata_path = temp_path / "metadata.json"
            if not metadata_path.exists():
                return False, "❌ Метаданные бекапа отсутствуют"

            async with aiofiles.open(metadata_path, "r", encoding="utf-8") as meta_file:
                metadata = json_lib.loads(await meta_file.read())

            logger.info("📊 Загружен бекап формата %s", metadata.get("format_version", "unknown"))

            database_info = metadata.get("database", {})
            data_snapshot_info = metadata.get("data_snapshot", {})
            files_info = metadata.get("files", [])

            if database_info.get("type") == "postgresql":
                db_format = database_info.get("format", "sql")
                default_name = "database.json" if db_format == "json" else "database.sql"
                dump_file = temp_path / database_info.get("path", default_name)

                if db_format == "json":
                    await self._restore_postgres_json(dump_file, clear_existing)
                else:
                    await self._restore_postgres(dump_file, clear_existing)
            else:
                dump_file = temp_path / database_info.get("path", "database.sqlite")
                await self._restore_sqlite(dump_file, clear_existing)

            data_dir = temp_path / "data"
            if data_dir.exists():
                await self._restore_data_snapshot(data_dir, clear_existing)

            if files_info:
                await self._restore_files(files_info, temp_path)

            message = (f"✅ Восстановление завершено!\n"
                       f"📊 Таблиц: {metadata.get('tables_count', 0)}\n"
                       f"📈 Записей: {metadata.get('total_records', 0):,}\n"
                       f"📅 Дата бекапа: {metadata.get('timestamp', 'неизвестно')}")

            logger.info(message)
            return True, message

    async def _restore_postgres(self, dump_path: Path, clear_existing: bool):
        if not dump_path.exists():
            raise FileNotFoundError(f"Dump PostgreSQL не найден: {dump_path}")

        psql_path = self._resolve_command_path("psql", "PSQL_PATH")
        if not psql_path:
            raise FileNotFoundError(
                "psql не найден в PATH. Установите клиент PostgreSQL или выполните восстановление из JSON дампа"
            )

        env = os.environ.copy()
        env.update({
            "PGHOST": settings.POSTGRES_HOST,
            "PGPORT": str(settings.POSTGRES_PORT),
            "PGUSER": settings.POSTGRES_USER,
            "PGPASSWORD": settings.POSTGRES_PASSWORD,
        })

        if clear_existing:
            logger.info("🗑️ Полная очистка схемы PostgreSQL перед восстановлением")
            drop_command = [
                psql_path,
                settings.POSTGRES_DB,
                "-c",
                "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public;",
            ]
            proc = await asyncio.create_subprocess_exec(
                *drop_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Не удалось очистить схему: {stderr.decode()}")

        logger.info("📥 Восстановление PostgreSQL через psql (%s)...", psql_path)
        restore_command = [
            psql_path,
            settings.POSTGRES_DB,
            "-f",
            str(dump_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *restore_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Ошибка psql: {stderr.decode()}")

        logger.info("✅ PostgreSQL восстановлен (%s)", dump_path)

    async def _restore_postgres_json(self, dump_path: Path, clear_existing: bool):
        if not dump_path.exists():
            raise FileNotFoundError(f"JSON дамп PostgreSQL не найден: {dump_path}")

        async with aiofiles.open(dump_path, "r", encoding="utf-8") as dump_file:
            dump_data = json_lib.loads(await dump_file.read())

        metadata = dump_data.get("metadata", {})
        backup_data = dump_data.get("data", {})
        association_data = dump_data.get("associations", {})

        await self._restore_database_payload(
            backup_data,
            association_data,
            metadata,
            clear_existing,
        )

        logger.info("✅ PostgreSQL восстановлен из ORM JSON (%s)", dump_path)

    async def _restore_sqlite(self, dump_path: Path, clear_existing: bool):
        if not dump_path.exists():
            raise FileNotFoundError(f"SQLite файл не найден: {dump_path}")

        target_path = Path(settings.SQLITE_PATH)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if clear_existing and target_path.exists():
            target_path.unlink()

        await asyncio.to_thread(shutil.copy2, dump_path, target_path)
        logger.info("✅ SQLite база восстановлена (%s)", target_path)

    async def _restore_data_snapshot(self, source_dir: Path, clear_existing: bool):
        if not source_dir.exists():
            return

        def _restore():
            self.data_dir.mkdir(parents=True, exist_ok=True)
            for item in source_dir.iterdir():
                if item.name == self.backup_dir.name:
                    continue

                destination = self.data_dir / item.name
                if clear_existing and destination.exists():
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    else:
                        destination.unlink()

                if item.is_dir():
                    shutil.copytree(item, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, destination)

        await asyncio.to_thread(_restore)
        logger.info("📁 Снимок директории data восстановлен")

    async def _restore_files(self, files_info: List[Dict[str, Any]], temp_path: Path):
        for file_info in files_info:
            relative_path = file_info.get("relative_path")
            target_path = Path(file_info.get("path", ""))
            if not relative_path or not target_path:
                continue

            source_file = temp_path / relative_path
            if not source_file.exists():
                logger.warning("Файл %s отсутствует в архиве", relative_path)
                continue

            target_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, source_file, target_path)
            logger.info("📁 Файл %s восстановлен", target_path)

    async def _restore_database_payload(
        self,
        backup_data: Dict[str, List[Dict[str, Any]]],
        association_data: Dict[str, List[Dict[str, Any]]],
        metadata: Dict[str, Any],
        clear_existing: bool,
    ) -> Tuple[int, int]:
        if not backup_data:
            raise ValueError("❌ Файл бекапа не содержит данных")

        logger.info(
            "📊 Загружен дамп: %s",
            metadata.get("timestamp", "неизвестная дата"),
        )

        estimated_records = metadata.get("total_records")
        if estimated_records is None:
            estimated_records = sum(len(records) for records in backup_data.values())
            estimated_records += sum(len(records) for records in association_data.values())

        logger.info("📈 Содержит %s записей", estimated_records)

        restored_records = 0
        restored_tables = 0

        async with AsyncSessionLocal() as db:
            try:
                if clear_existing:
                    logger.warning("🗑️ Очищаем существующие данные...")
                    await self._clear_database_tables(db, backup_data)

                models_for_restore = self._get_models_for_backup(True)
                models_by_table = {
                    model.__tablename__: model for model in models_for_restore
                }

                pre_restore_tables = {"promo_groups", "tariffs"}
                for table_name in pre_restore_tables:
                    model = models_by_table.get(table_name)
                    if not model:
                        continue

                    records = backup_data.get(table_name, [])
                    if not records:
                        continue

                    logger.info(
                        "🔥 Восстанавливаем таблицу %s (%s записей)",
                        table_name,
                        len(records),
                    )
                    restored = await self._restore_table_records(
                        db,
                        model,
                        table_name,
                        records,
                        clear_existing,
                    )
                    restored_records += restored

                    if restored:
                        restored_tables += 1
                        logger.info("✅ Таблица %s восстановлена", table_name)

                await self._restore_users_without_referrals(
                    db,
                    backup_data,
                    models_by_table,
                )

                for model in models_for_restore:
                    table_name = model.__tablename__

                    if table_name == "users" or table_name in pre_restore_tables:
                        continue

                    records = backup_data.get(table_name, [])
                    if not records:
                        continue

                    logger.info(
                        "🔥 Восстанавливаем таблицу %s (%s записей)",
                        table_name,
                        len(records),
                    )
                    restored = await self._restore_table_records(
                        db,
                        model,
                        table_name,
                        records,
                        clear_existing,
                    )
                    restored_records += restored

                    if restored:
                        restored_tables += 1
                        logger.info("✅ Таблица %s восстановлена", table_name)

                # Flush все изменения перед обновлением реферальных связей
                await db.flush()

                await self._update_user_referrals(db, backup_data)

                assoc_tables, assoc_records = await self._restore_association_tables(
                    db,
                    association_data,
                    clear_existing,
                )
                restored_tables += assoc_tables
                restored_records += assoc_records

                await db.commit()

            except Exception as exc:
                await db.rollback()
                logger.error("Ошибка при восстановлении: %s", exc)
                raise exc

        return restored_tables, restored_records

    async def _restore_from_legacy(
        self,
        backup_path: Path,
        clear_existing: bool,
    ) -> Tuple[bool, str]:
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
        association_data = backup_structure.get("associations", {})
        file_snapshots = backup_structure.get("files", {})

        try:
            restored_tables, restored_records = await self._restore_database_payload(
                backup_data,
                association_data,
                metadata,
                clear_existing,
            )
        except ValueError as exc:
            return False, str(exc)

        if file_snapshots:
            restored_files = await self._restore_file_snapshots(file_snapshots)
            if restored_files:
                logger.info(f"📁 Восстановлено файлов конфигурации: {restored_files}")

        message = (f"✅ Восстановление завершено!\n"
                   f"📊 Таблиц: {restored_tables}\n"
                   f"📈 Записей: {restored_records:,}\n"
                   f"📅 Дата бекапа: {metadata.get('timestamp', 'неизвестно')}")

        logger.info(message)
        return True, message

    async def _restore_users_without_referrals(self, db: AsyncSession, backup_data: dict, models_by_table: dict):
        users_data = backup_data.get("users", [])
        if not users_data:
            return
        
        logger.info(f"👥 Восстанавливаем {len(users_data)} пользователей без реферальных связей")
        
        User = models_by_table["users"]
        
        for user_data in users_data:
            try:
                processed_data = self._process_record_data(user_data, User, "users")
                processed_data['referred_by_id'] = None 
                
                if 'id' in processed_data:
                    existing_user = await db.execute(
                        select(User).where(User.id == processed_data['id'])
                    )
                    existing = existing_user.scalar_one_or_none()
                    
                    if existing:
                        for key, value in processed_data.items():
                            if key != 'id':
                                setattr(existing, key, value)
                    else:
                        instance = User(**processed_data)
                        db.add(instance)
                else:
                    instance = User(**processed_data)
                    db.add(instance)
                
            except Exception as e:
                logger.error(f"Ошибка при восстановлении пользователя: {e}")
                await db.rollback()
                raise e
        
        await db.commit()
        logger.info("✅ Пользователи без реферальных связей восстановлены")

    async def _update_user_referrals(self, db: AsyncSession, backup_data: dict):
        users_data = backup_data.get("users", [])
        if not users_data:
            return
        
        logger.info("🔗 Обновляем реферальные связи пользователей")
        
        for user_data in users_data:
            try:
                referred_by_id = user_data.get('referred_by_id')
                user_id = user_data.get('id')
                
                if referred_by_id and user_id:
                    referrer_result = await db.execute(
                        select(User).where(User.id == referred_by_id)
                    )
                    referrer = referrer_result.scalar_one_or_none()
                    
                    if referrer:
                        user_result = await db.execute(
                            select(User).where(User.id == user_id)
                        )
                        user = user_result.scalar_one_or_none()
                        
                        if user:
                            user.referred_by_id = referred_by_id
                        else:
                            logger.warning(f"Пользователь {user_id} не найден для обновления реферальной связи")
                    else:
                        logger.warning(f"Реферер {referred_by_id} не найден для пользователя {user_id}")
                        
            except Exception as e:
                logger.error(f"Ошибка при обновлении реферальной связи: {e}")
                continue
        
        await db.commit()
        logger.info("✅ Реферальные связи обновлены")

    def _process_record_data(self, record_data: dict, model, table_name: str) -> dict:
        processed_data = {}
        
        for key, value in record_data.items():
            if value is None:
                processed_data[key] = None
                continue
            
            column = getattr(model.__table__.columns, key, None)
            if column is None:
                logger.warning(f"Колонка {key} не найдена в модели {table_name}")
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
            elif ('INTEGER' in column_type_str or 'INT' in column_type_str or 'BIGINT' in column_type_str) and isinstance(value, str):
                try:
                    processed_data[key] = int(value)
                except ValueError:
                    processed_data[key] = 0
            elif ('FLOAT' in column_type_str or 'REAL' in column_type_str or 'NUMERIC' in column_type_str) and isinstance(value, str):
                try:
                    processed_data[key] = float(value)
                except ValueError:
                    processed_data[key] = 0.0
            elif 'JSON' in column_type_str:
                if isinstance(value, str) and value.strip():
                    try:
                        processed_data[key] = json_lib.loads(value)
                    except (ValueError, TypeError):
                        processed_data[key] = value
                elif isinstance(value, (list, dict)):
                    processed_data[key] = value
                else:
                    processed_data[key] = None
            else:
                processed_data[key] = value
        
        return processed_data

    def _get_primary_key_column(self, model) -> Optional[str]:
        for col in model.__table__.columns:
            if col.primary_key:
                return col.name
        return None

    async def _export_association_tables(self, db: AsyncSession) -> Dict[str, List[Dict[str, Any]]]:
        association_data: Dict[str, List[Dict[str, Any]]] = {}

        for table_name, table_obj in self.association_tables.items():
            try:
                logger.info(f"📊 Экспортируем таблицу связей: {table_name}")
                result = await db.execute(select(table_obj))
                rows = result.mappings().all()
                association_data[table_name] = [dict(row) for row in rows]
                logger.info(
                    f"✅ Экспортировано {len(rows)} связей из {table_name}"
                )
            except Exception as e:
                logger.error(f"Ошибка экспорта таблицы связей {table_name}: {e}")

        return association_data

    async def _restore_association_tables(
        self,
        db: AsyncSession,
        association_data: Dict[str, List[Dict[str, Any]]],
        clear_existing: bool
    ) -> Tuple[int, int]:
        if not association_data:
            return 0, 0

        restored_tables = 0
        restored_records = 0

        if "server_squad_promo_groups" in association_data:
            restored = await self._restore_server_squad_promo_groups(
                db,
                association_data["server_squad_promo_groups"],
                clear_existing
            )
            restored_tables += 1
            restored_records += restored

        return restored_tables, restored_records

    async def _restore_server_squad_promo_groups(
        self,
        db: AsyncSession,
        records: List[Dict[str, Any]],
        clear_existing: bool
    ) -> int:
        if not records:
            return 0

        if clear_existing:
            await db.execute(server_squad_promo_groups.delete())

        restored = 0

        for record in records:
            server_id = record.get("server_squad_id")
            promo_id = record.get("promo_group_id")

            if server_id is None or promo_id is None:
                logger.warning(
                    "Пропущена некорректная запись server_squad_promo_groups: %s",
                    record
                )
                continue

            try:
                exists_stmt = (
                    select(server_squad_promo_groups.c.server_squad_id)
                    .where(
                        server_squad_promo_groups.c.server_squad_id == server_id,
                        server_squad_promo_groups.c.promo_group_id == promo_id,
                    )
                    .limit(1)
                )
                existing = await db.execute(exists_stmt)

                if existing.scalar_one_or_none() is not None:
                    logger.debug(
                        "Запись server_squad_promo_groups (%s, %s) уже существует",
                        server_id,
                        promo_id,
                    )
                    continue

                await db.execute(
                    server_squad_promo_groups.insert().values(
                        server_squad_id=server_id,
                        promo_group_id=promo_id
                    )
                )
                restored += 1
            except Exception as e:
                logger.error(
                    "Ошибка при восстановлении связи server_squad_promo_groups (%s, %s): %s",
                    server_id,
                    promo_id,
                    e
                )
                await db.rollback()
                raise e

        return restored

    async def _restore_table_records(
        self,
        db: AsyncSession,
        model,
        table_name: str,
        records: List[Dict[str, Any]],
        clear_existing: bool
    ) -> int:
        restored_count = 0

        # Кешируем существующие tariff_id для проверки FK
        existing_tariff_ids = set()
        if table_name == "subscriptions":
            try:
                result = await db.execute(select(Tariff.id))
                existing_tariff_ids = {row[0] for row in result.fetchall()}
                logger.info(f"📋 Найдено {len(existing_tariff_ids)} существующих тарифов для валидации FK")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось получить список тарифов: {e}")

        for record_data in records:
            try:
                processed_data = self._process_record_data(record_data, model, table_name)

                # Валидация FK для subscriptions.tariff_id
                if table_name == "subscriptions" and "tariff_id" in processed_data:
                    tariff_id = processed_data.get("tariff_id")
                    if tariff_id is not None and tariff_id not in existing_tariff_ids:
                        logger.warning(
                            f"⚠️ Тариф {tariff_id} не найден, устанавливаем tariff_id=NULL для подписки"
                        )
                        processed_data["tariff_id"] = None

                primary_key_col = self._get_primary_key_column(model)

                if primary_key_col and primary_key_col in processed_data:
                    existing_record = await db.execute(
                        select(model).where(
                            getattr(model, primary_key_col) == processed_data[primary_key_col]
                        )
                    )
                    existing = existing_record.scalar_one_or_none()

                    if existing and not clear_existing:
                        for key, value in processed_data.items():
                            if key != primary_key_col:
                                setattr(existing, key, value)
                    else:
                        instance = model(**processed_data)
                        db.add(instance)
                else:
                    instance = model(**processed_data)
                    db.add(instance)

                restored_count += 1

            except Exception as e:
                logger.error(f"Ошибка восстановления записи в {table_name}: {e}")
                logger.error(f"Проблемные данные: {record_data}")
                await db.rollback()
                raise e

        return restored_count

    async def _clear_database_tables(self, db: AsyncSession, backup_data: Optional[Dict[str, Any]] = None):
        tables_order = [
            "server_squad_promo_groups",
            "ticket_messages", "tickets", "support_audit_logs",
            "advertising_campaign_registrations", "advertising_campaigns",
            "subscription_servers", "sent_notifications",
            "discount_offers", "user_messages", "broadcast_history", "subscription_conversions",
            "referral_earnings", "promocode_uses",
            "yookassa_payments", "cryptobot_payments",
            "mulenpay_payments", "pal24_payments",
            "transactions", "welcome_texts", "subscriptions",
            "promocodes", "users", "promo_groups",
            "tariffs",  # tariffs должен очищаться ПОСЛЕ subscriptions (FK зависимость)
            "server_squads", "squads", "service_rules",
            "system_settings", "web_api_tokens", "monitoring_logs"
        ]

        # Таблицы, которые не нужно очищать если в бекапе нет данных для них
        # (чтобы сохранить существующие настройки)
        preserve_if_no_backup = {"tariffs", "promo_groups", "server_squads", "squads"}

        for table_name in tables_order:
            # Проверяем, нужно ли сохранить таблицу
            if backup_data and table_name in preserve_if_no_backup:
                if not backup_data.get(table_name):
                    logger.info(f"⏭️ Пропускаем очистку {table_name} (нет данных в бекапе)")
                    continue

            try:
                await db.execute(text(f"DELETE FROM {table_name}"))
                logger.info(f"🗑️ Очищена таблица {table_name}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось очистить таблицу {table_name}: {e}")

    async def _collect_file_snapshots(self) -> Dict[str, Dict[str, Any]]:
        snapshots: Dict[str, Dict[str, Any]] = {}

        app_config_path = settings.get_app_config_path()
        if app_config_path:
            path_obj = Path(app_config_path)
            if path_obj.exists() and path_obj.is_file():
                try:
                    async with aiofiles.open(path_obj, 'r', encoding='utf-8') as f:
                        content = await f.read()
                    snapshots["app_config"] = {
                        "path": str(path_obj),
                        "content": content,
                        "modified_at": datetime.fromtimestamp(
                            path_obj.stat().st_mtime
                        ).isoformat()
                    }
                    logger.info(
                        "📁 Добавлен в бекап файл конфигурации: %s",
                        path_obj
                    )
                except Exception as e:
                    logger.error(
                        "Ошибка чтения файла конфигурации %s: %s",
                        path_obj,
                        e
                    )

        return snapshots

    async def _restore_file_snapshots(self, file_snapshots: Dict[str, Dict[str, Any]]) -> int:
        restored_files = 0

        if not file_snapshots:
            return restored_files

        app_config_snapshot = file_snapshots.get("app_config")
        if app_config_snapshot:
            target_path = Path(settings.get_app_config_path())
            target_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                async with aiofiles.open(target_path, 'w', encoding='utf-8') as f:
                    await f.write(app_config_snapshot.get("content", ""))
                restored_files += 1
                logger.info("📁 Файл app-config восстановлен по пути %s", target_path)
            except Exception as e:
                logger.error("Ошибка восстановления файла %s: %s", target_path, e)

        return restored_files

    async def get_backup_list(self) -> List[Dict[str, Any]]:
        backups = []
        
        try:
            for backup_file in sorted(self.backup_dir.glob("backup_*"), reverse=True):
                if not backup_file.is_file():
                    continue

                try:
                    metadata = {}

                    if self._is_archive_backup(backup_file):
                        mode = "r:gz" if backup_file.suffixes and backup_file.suffixes[-1] == ".gz" else "r"
                        with tarfile.open(backup_file, mode) as tar:
                            try:
                                member = tar.getmember("metadata.json")
                                with tar.extractfile(member) as meta_file:
                                    metadata = json_lib.load(meta_file)
                            except KeyError:
                                metadata = {}
                    else:
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
                        "timestamp": metadata.get("timestamp", datetime.fromtimestamp(file_stats.st_mtime).isoformat()),
                        "tables_count": metadata.get("tables_count", metadata.get("database", {}).get("tables_count", 0)),
                        "total_records": metadata.get("total_records", metadata.get("database", {}).get("total_records", 0)),
                        "compressed": self._is_archive_backup(backup_file) or backup_file.suffix == '.gz',
                        "file_size_bytes": file_stats.st_size,
                        "file_size_mb": round(file_stats.st_size / 1024 / 1024, 2),
                        "created_by": metadata.get("created_by"),
                        "database_type": metadata.get("database_type", metadata.get("database", {}).get("type", "unknown")),
                        "version": metadata.get("format_version", metadata.get("version", "1.0")),
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
                        "version": "unknown",
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
            next_run = self._calculate_next_backup_datetime()
            interval = self._get_backup_interval()
            self._auto_backup_task = asyncio.create_task(self._auto_backup_loop(next_run))
            logger.info(
                "📄 Автобекапы включены, интервал: %.2fч, ближайший запуск: %s",
                interval.total_seconds() / 3600,
                next_run.strftime("%d.%m.%Y %H:%M:%S")
            )

    async def stop_auto_backup(self):
        if self._auto_backup_task and not self._auto_backup_task.done():
            self._auto_backup_task.cancel()
            logger.info("ℹ️ Автобекапы остановлены")

    async def _auto_backup_loop(self, next_run: Optional[datetime] = None):
        next_run = next_run or self._calculate_next_backup_datetime()
        interval = self._get_backup_interval()

        while True:
            try:
                now = datetime.now()
                delay = (next_run - now).total_seconds()

                if delay > 0:
                    logger.info(
                        "⏰ Следующий автоматический бекап запланирован на %s (через %.2f ч)",
                        next_run.strftime("%d.%m.%Y %H:%M:%S"),
                        delay / 3600
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.info(
                        "⏰ Время автоматического бекапа %s уже наступило, запускаем немедленно",
                        next_run.strftime("%d.%m.%Y %H:%M:%S")
                    )

                logger.info("📄 Запуск автоматического бекапа...")
                success, message, _ = await self.create_backup()

                if success:
                    logger.info(f"✅ Автобекап завершен: {message}")
                else:
                    logger.error(f"❌ Ошибка автобекапа: {message}")

                next_run = next_run + interval

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле автобекапов: {e}")
                next_run = datetime.now() + interval

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
                "restore_success": "🔥",
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

    async def _send_backup_file_to_chat(self, file_path: str):
        try:
            if not settings.is_backup_send_enabled():
                return

            chat_id = settings.get_backup_send_chat_id()
            if not chat_id:
                return

            password = settings.get_backup_archive_password()
            file_to_send = file_path
            temp_zip_path = None

            if password:
                temp_zip_path = await self._create_password_protected_archive(file_path, password)
                if temp_zip_path:
                    file_to_send = temp_zip_path

            caption = f"📦 <b>Резервная копия</b>\n\n"
            if temp_zip_path:
                caption += f"🔐 <b>Архив защищён паролем</b>\n\n"
            caption += f"⏰ <i>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</i>"

            send_kwargs = {
                'chat_id': chat_id,
                'document': FSInputFile(file_to_send),
                'caption': caption,
                'parse_mode': 'HTML'
            }

            if settings.BACKUP_SEND_TOPIC_ID:
                send_kwargs['message_thread_id'] = settings.BACKUP_SEND_TOPIC_ID

            await self.bot.send_document(**send_kwargs)
            logger.info(f"Бекап отправлен в чат {chat_id}")

            if temp_zip_path and Path(temp_zip_path).exists():
                try:
                    Path(temp_zip_path).unlink()
                except Exception as cleanup_error:
                    logger.warning(f"Не удалось удалить временный архив: {cleanup_error}")

        except Exception as e:
            logger.error(f"Ошибка отправки бекапа в чат: {e}")

    async def _create_password_protected_archive(self, file_path: str, password: str) -> Optional[str]:
        try:
            source_path = Path(file_path)
            if not source_path.exists():
                logger.error(f"Исходный файл бекапа не найден: {file_path}")
                return None

            zip_filename = source_path.stem + ".zip"
            zip_path = source_path.parent / zip_filename

            def create_zip():
                with pyzipper.AESZipFile(
                    zip_path,
                    'w',
                    compression=pyzipper.ZIP_DEFLATED,
                    encryption=pyzipper.WZ_AES
                ) as zf:
                    zf.setpassword(password.encode('utf-8'))
                    zf.write(source_path, arcname=source_path.name)

            await asyncio.to_thread(create_zip)
            logger.info(f"Создан защищённый паролем архив: {zip_path}")
            return str(zip_path)

        except Exception as e:
            logger.error(f"Ошибка создания защищённого архива: {e}")
            return None


backup_service = BackupService()
