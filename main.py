import asyncio
import logging
import sys
import os
import signal
from pathlib import Path
from textwrap import wrap

sys.path.append(str(Path(__file__).parent))

from app.bot import setup_bot
from app.config import settings
from app.database.database import init_db
from app.services.monitoring_service import monitoring_service
from app.services.maintenance_service import maintenance_service
from app.services.payment_service import PaymentService
from app.services.version_service import version_service
from app.external.webhook_server import WebhookServer
from app.external.yookassa_webhook import start_yookassa_webhook_server
from app.external.pal24_webhook import start_pal24_webhook_server, Pal24WebhookServer
from app.database.universal_migration import run_universal_migration
from app.services.backup_service import backup_service
from app.services.reporting_service import reporting_service
from app.localization.loader import ensure_locale_templates
from app.services.system_settings_service import bot_configuration_service
from app.services.broadcast_service import broadcast_service


LOG_FRAME_WIDTH = 74


def _wrap_line(text: str, width: int) -> list[str]:
    wrapped = wrap(
        text,
        width=width,
        break_long_words=False,
        replace_whitespace=False,
        drop_whitespace=True,
    )
    return wrapped or [""]


def build_banner(title: str, subtitle: str | None = None) -> str:
    width = LOG_FRAME_WIDTH
    top = "╔" + "═" * (width - 2) + "╗"
    empty = f"║{' ' * (width - 2)}║"
    title_line = f"║ {title.center(width - 4)} ║"
    lines = [top, empty, title_line]

    if subtitle:
        subtitle_lines = _wrap_line(subtitle, width - 4)
        for line in subtitle_lines:
            lines.append(f"║ {line.center(width - 4)} ║")

    lines.extend([empty, "╚" + "═" * (width - 2) + "╝"])
    return "\n".join(lines)


def build_summary_box(title: str, sections: list[tuple[str, list[str]]]) -> str:
    width = LOG_FRAME_WIDTH
    inner_width = width - 4
    bullet_width = width - 6

    lines = ["╔" + "═" * (width - 2) + "╗"]

    for segment in _wrap_line(f"✨ {title}", inner_width):
        lines.append(f"║ {segment.ljust(inner_width)} ║")

    if sections:
        lines.append("╠" + "═" * (width - 2) + "╣")

    for index, (section_title, entries) in enumerate(sections):
        for section_line in _wrap_line(section_title, inner_width):
            lines.append(f"║ {section_line.ljust(inner_width)} ║")

        if entries:
            for entry in entries:
                wrapped_entry = _wrap_line(entry, bullet_width - 2)
                for i, part in enumerate(wrapped_entry):
                    prefix = "• " if i == 0 else "  "
                    available_width = bullet_width - len(prefix)
                    lines.append(
                        f"║   {prefix}{part.ljust(available_width)} ║"
                    )
        else:
            lines.append(f"║   {'— нет данных —'.ljust(bullet_width)} ║")

        if index < len(sections) - 1:
            lines.append("╟" + "─" * (width - 2) + "╢")

    lines.append("╚" + "═" * (width - 2) + "╝")
    return "\n".join(lines)


def format_status_line(name: str, enabled: bool, detail: str | None = None) -> str:
    status_icon = "✅" if enabled else "⛔"
    status_text = "Активен" if enabled else "Отключен"

    if detail:
        status_text = f"{status_text} — {detail}"

    return f"{status_icon} {name}: {status_text}"


class GracefulExit:
    
    def __init__(self):
        self.exit = False
        
    def exit_gracefully(self, signum, frame):
        logging.getLogger(__name__).info(f"Получен сигнал {signum}. Корректное завершение работы...")
        self.exit = True


async def main():
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(settings.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info(
        build_banner(
            "🚀 Bedolaga Remnawave Bot",
            "Старт последовательности запуска",
        )
    )
    
    try:
        ensure_locale_templates()
    except Exception as error:
        logger.warning("Failed to prepare locale templates: %s", error)

    killer = GracefulExit()
    signal.signal(signal.SIGINT, killer.exit_gracefully)
    signal.signal(signal.SIGTERM, killer.exit_gracefully)
    
    webhook_server = None
    yookassa_server_task = None
    pal24_server: Pal24WebhookServer | None = None
    monitoring_task = None
    maintenance_task = None
    version_check_task = None
    polling_task = None
    web_api_server = None

    webhook_endpoints: list[tuple[str, str]] = []
    webhook_services: list[str] = []
    web_api_url: str | None = None
    backup_settings = None
    auto_backup_enabled: bool | None = None
    backup_status_detail: str | None = None
    maintenance_status_detail: str | None = None
    maintenance_running = False
    yookassa_started = False
    pal24_started = False
    
    try:
        logger.info("📊 Инициализация базы данных...")
        await init_db()
        
        skip_migration = os.getenv('SKIP_MIGRATION', 'false').lower() == 'true'
        
        if not skip_migration:
            logger.info("🔧 Выполняем проверку и миграцию базы данных...")
            try:
                migration_success = await run_universal_migration()
                
                if migration_success:
                    logger.info("✅ Миграция базы данных завершена успешно")
                else:
                    logger.warning("⚠️ Миграция завершилась с предупреждениями, но продолжаем запуск")
                    
            except Exception as migration_error:
                logger.error(f"❌ Ошибка выполнения миграции: {migration_error}")
                logger.warning("⚠️ Продолжаем запуск без миграции")
        else:
            logger.info("ℹ️ Миграция пропущена (SKIP_MIGRATION=true)")
        
        logger.info("⚙️ Загрузка конфигурации из БД...")
        try:
            await bot_configuration_service.initialize()
            logger.info("✅ Конфигурация загружена")
        except Exception as error:
            logger.error(f"❌ Не удалось загрузить конфигурацию: {error}")

        logger.info("🤖 Настройка бота...")
        bot, dp = await setup_bot()

        monitoring_service.bot = bot
        maintenance_service.set_bot(bot)
        broadcast_service.set_bot(bot)

        from app.services.admin_notification_service import AdminNotificationService
        admin_notification_service = AdminNotificationService(bot)
        version_service.bot = bot
        version_service.set_notification_service(admin_notification_service)
        logger.info(f"📄 Сервис версий настроен для репозитория: {version_service.repo}")
        logger.info(f"📦 Текущая версия: {version_service.current_version}")
        
        logger.info("🔗 Бот подключен к сервисам мониторинга и техработ")

        logger.info("🗄️ Инициализация сервиса бекапов...")
        try:
            backup_service.bot = bot

            backup_settings = await backup_service.get_backup_settings()
            auto_backup_enabled = backup_settings.auto_backup_enabled

            if auto_backup_enabled:
                await backup_service.start_auto_backup()
                logger.info("✅ Автобекапы запущены")
                backup_status_detail = (
                    f"интервал {backup_settings.backup_interval_hours}ч, время {backup_settings.backup_time}"
                )
            else:
                backup_status_detail = "отключены настройками"

            logger.info("✅ Сервис бекапов инициализирован")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации сервиса бекапов: {e}")

        logger.info("📊 Инициализация сервиса отчетов...")
        try:
            reporting_service.set_bot(bot)
            await reporting_service.start()
        except Exception as e:
            logger.error(f"❌ Ошибка запуска сервиса отчетов: {e}")

        payment_service = PaymentService(bot)
        
        webhook_needed = (
            settings.TRIBUTE_ENABLED
            or settings.is_cryptobot_enabled()
            or settings.is_mulenpay_enabled()
        )
        
        if webhook_needed:
            enabled_services = []
            if settings.TRIBUTE_ENABLED:
                enabled_services.append("Tribute")
                webhook_endpoints.append(
                    (
                        "Tribute",
                        f"{settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.TRIBUTE_WEBHOOK_PATH}",
                    )
                )
            if settings.is_mulenpay_enabled():
                enabled_services.append("Mulen Pay")
                webhook_endpoints.append(
                    (
                        "Mulen Pay",
                        f"{settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.MULENPAY_WEBHOOK_PATH}",
                    )
                )
            if settings.is_cryptobot_enabled():
                enabled_services.append("CryptoBot")
                webhook_endpoints.append(
                    (
                        "CryptoBot",
                        f"{settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.CRYPTOBOT_WEBHOOK_PATH}",
                    )
                )

            webhook_services = enabled_services.copy()
            logger.info(
                f"🌐 Запуск webhook сервера для: {', '.join(enabled_services)}..."
            )
            webhook_server = WebhookServer(bot)
            await webhook_server.start()
        else:
            logger.info("ℹ️ Tribute и CryptoBot отключены, webhook сервер не запускается")
        
        if settings.is_yookassa_enabled():
            logger.info("💳 Запуск YooKassa webhook сервера...")
            yookassa_server_task = asyncio.create_task(
                start_yookassa_webhook_server(payment_service)
            )
            webhook_endpoints.append(
                (
                    "YooKassa",
                    f"{settings.WEBHOOK_URL}:{settings.YOOKASSA_WEBHOOK_PORT}{settings.YOOKASSA_WEBHOOK_PATH}",
                )
            )
            yookassa_started = True
        else:
            logger.info("ℹ️ YooKassa отключена, webhook сервер не запускается")

        if settings.is_pal24_enabled():
            logger.info("💳 Запуск PayPalych webhook сервера...")
            pal24_server = await start_pal24_webhook_server(payment_service)
            webhook_endpoints.append(
                (
                    "PayPalych",
                    f"{settings.WEBHOOK_URL}:{settings.PAL24_WEBHOOK_PORT}{settings.PAL24_WEBHOOK_PATH}",
                )
            )
            pal24_started = True
        else:
            logger.info("ℹ️ PayPalych отключен, webhook сервер не запускается")

        logger.info("📊 Запуск службы мониторинга...")
        monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())
        
        logger.info("🔧 Проверка службы техработ...")
        maintenance_running = False
        if not maintenance_service._check_task or maintenance_service._check_task.done():
            logger.info("🔧 Запуск службы техработ...")
            maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
            maintenance_running = True
            maintenance_status_detail = "запущена при старте"
        else:
            logger.info("🔧 Служба техработ уже запущена")
            maintenance_task = None
            maintenance_running = True
            maintenance_status_detail = "уже активна"
        
        if settings.is_version_check_enabled():
            logger.info("📄 Запуск сервиса проверки версий...")
            version_check_task = asyncio.create_task(version_service.start_periodic_check())
        else:
            logger.info("ℹ️ Проверка версий отключена")
        
        if settings.is_web_api_enabled():
            try:
                from app.webapi import WebAPIServer

                web_api_server = WebAPIServer()
                await web_api_server.start()
                web_api_url = f"http://{settings.WEB_API_HOST}:{settings.WEB_API_PORT}"
                logger.info(
                    "🌐 Административное веб-API запущено: %s",
                    web_api_url,
                )
            except Exception as error:
                logger.error(f"❌ Не удалось запустить веб-API: {error}")
        else:
            logger.info("ℹ️ Веб-API отключено")

        logger.info("📄 Запуск polling...")
        polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
        
        background_services_lines = [
            format_status_line("Мониторинг", monitoring_task is not None),
            format_status_line(
                "Техработы",
                maintenance_running,
                maintenance_status_detail,
            ),
            format_status_line(
                "Проверка версий",
                version_check_task is not None,
            ),
            format_status_line(
                "Отчеты",
                reporting_service.is_running(),
            ),
        ]

        if auto_backup_enabled is not None:
            backup_lines = [
                format_status_line(
                    "Автобекапы",
                    bool(auto_backup_enabled),
                    backup_status_detail,
                )
            ]
        else:
            backup_lines = ["⛔ Автобекапы: не удалось загрузить настройки"]

        admin_services_lines = [
            f"Версия приложения: {version_service.current_version or 'не определена'}",
            format_status_line(
                "Webhook сервер платежей",
                bool(webhook_services),
                ", ".join(webhook_services) if webhook_services else None,
            ),
            format_status_line(
                "YooKassa",
                yookassa_started,
            ),
            format_status_line(
                "PayPalych",
                pal24_started,
            ),
            format_status_line(
                "Административное API",
                web_api_url is not None,
                web_api_url,
            ),
        ]

        summary_sections = [
            ("🌐 Активные webhook endpoints", [f"{name}: {url}" for name, url in webhook_endpoints]),
            ("🧰 Фоновые сервисы", background_services_lines),
            ("🛡️ Административные сервисы", admin_services_lines),
            ("💾 Резервное копирование", backup_lines),
        ]

        summary_message = build_summary_box("Итоги запуска", summary_sections)
        logger.info(summary_message)
        
        try:
            while not killer.exit:
                await asyncio.sleep(1)
                
                if yookassa_server_task and yookassa_server_task.done():
                    exception = yookassa_server_task.exception()
                    if exception:
                        logger.error(f"YooKassa webhook сервер завершился с ошибкой: {exception}")
                        logger.info("🔄 Перезапуск YooKassa webhook сервера...")
                        yookassa_server_task = asyncio.create_task(
                            start_yookassa_webhook_server(payment_service)
                        )
                
                if monitoring_task.done():
                    exception = monitoring_task.exception()
                    if exception:
                        logger.error(f"Служба мониторинга завершилась с ошибкой: {exception}")
                        monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())
                        
                if maintenance_task and maintenance_task.done():
                    exception = maintenance_task.exception()
                    if exception:
                        logger.error(f"Служба техработ завершилась с ошибкой: {exception}")
                        maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
                
                if version_check_task and version_check_task.done():
                    exception = version_check_task.exception()
                    if exception:
                        logger.error(f"Сервис проверки версий завершился с ошибкой: {exception}")
                        if settings.is_version_check_enabled():
                            logger.info("🔄 Перезапуск сервиса проверки версий...")
                            version_check_task = asyncio.create_task(version_service.start_periodic_check())
                        
                if polling_task.done():
                    exception = polling_task.exception()
                    if exception:
                        logger.error(f"Polling завершился с ошибкой: {exception}")
                        break
                        
        except Exception as e:
            logger.error(f"Ошибка в основном цикле: {e}")
            
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске: {e}")
        raise
        
    finally:
        logger.info("🛑 Начинается корректное завершение работы...")
        
        if yookassa_server_task and not yookassa_server_task.done():
            logger.info("ℹ️ Остановка YooKassa webhook сервера...")
            yookassa_server_task.cancel()
            try:
                await yookassa_server_task
            except asyncio.CancelledError:
                pass
        
        if monitoring_task and not monitoring_task.done():
            logger.info("ℹ️ Остановка службы мониторинга...")
            monitoring_service.stop_monitoring()
            monitoring_task.cancel()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass

        if pal24_server:
            logger.info("ℹ️ Остановка PayPalych webhook сервера...")
            await asyncio.get_running_loop().run_in_executor(None, pal24_server.stop)
        
        if maintenance_task and not maintenance_task.done():
            logger.info("ℹ️ Остановка службы техработ...")
            await maintenance_service.stop_monitoring()
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        
        if version_check_task and not version_check_task.done():
            logger.info("ℹ️ Остановка сервиса проверки версий...")
            version_check_task.cancel()
            try:
                await version_check_task
            except asyncio.CancelledError:
                pass

        logger.info("ℹ️ Остановка сервиса отчетов...")
        try:
            await reporting_service.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки сервиса отчетов: {e}")

        logger.info("ℹ️ Остановка сервиса бекапов...")
        try:
            await backup_service.stop_auto_backup()
        except Exception as e:
            logger.error(f"Ошибка остановки сервиса бекапов: {e}")
        
        if polling_task and not polling_task.done():
            logger.info("ℹ️ Остановка polling...")
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        
        if webhook_server:
            logger.info("ℹ️ Остановка webhook сервера...")
            await webhook_server.stop()

        if web_api_server:
            try:
                await web_api_server.stop()
                logger.info("✅ Административное веб-API остановлено")
            except Exception as error:
                logger.error(f"Ошибка остановки веб-API: {error}")
        
        if 'bot' in locals():
            try:
                await bot.session.close()
                logger.info("✅ Сессия бота закрыта")
            except Exception as e:
                logger.error(f"Ошибка закрытия сессии бота: {e}")
        
        logger.info("✅ Завершение работы бота завершено")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)
