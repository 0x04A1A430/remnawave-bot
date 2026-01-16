import asyncio
import logging
from datetime import datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.services.monitoring_service import monitoring_service
from app.services.nalogo_queue_service import nalogo_queue_service
from app.services.traffic_monitoring_service import (
    traffic_monitoring_service,
    traffic_monitoring_scheduler,
)
from app.utils.decorators import admin_required
from app.utils.pagination import paginate_list
from app.keyboards.admin import get_monitoring_keyboard, get_admin_main_keyboard
from app.localization.texts import get_texts
from app.services.notification_settings_service import NotificationSettingsService
from app.states import AdminStates

logger = logging.getLogger(__name__)
router = Router()


def _format_toggle(enabled: bool) -> str:
    return "🟢 Вкл" if enabled else "🔴 Выкл"


def _build_notification_settings_view(language: str):
    texts = get_texts(language)
    config = NotificationSettingsService.get_config()

    second_percent = NotificationSettingsService.get_second_wave_discount_percent()
    second_hours = NotificationSettingsService.get_second_wave_valid_hours()
    third_percent = NotificationSettingsService.get_third_wave_discount_percent()
    third_hours = NotificationSettingsService.get_third_wave_valid_hours()
    third_days = NotificationSettingsService.get_third_wave_trigger_days()

    trial_1h_status = _format_toggle(config["trial_inactive_1h"].get("enabled", True))
    trial_24h_status = _format_toggle(config["trial_inactive_24h"].get("enabled", True))
    trial_channel_status = _format_toggle(
        config["trial_channel_unsubscribed"].get("enabled", True)
    )
    expired_1d_status = _format_toggle(config["expired_1d"].get("enabled", True))
    second_wave_status = _format_toggle(config["expired_second_wave"].get("enabled", True))
    third_wave_status = _format_toggle(config["expired_third_wave"].get("enabled", True))

    summary_text = (
        "🔔 <b>Уведомления пользователям</b>\n\n"
        f"• 1 час после триала: {trial_1h_status}\n"
        f"• 24 часа после триала: {trial_24h_status}\n"
        f"• Отписка от канала: {trial_channel_status}\n"
        f"• 1 день после истечения: {expired_1d_status}\n"
        f"• 2-3 дня (скидка {second_percent}% / {second_hours} ч): {second_wave_status}\n"
        f"• {third_days} дней (скидка {third_percent}% / {third_hours} ч): {third_wave_status}"
    )

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{trial_1h_status} • 1 час после триала", callback_data="admin_mon_notify_toggle_trial_1h")],
        [InlineKeyboardButton(text="🧪 Тест: 1 час после триала", callback_data="admin_mon_notify_preview_trial_1h")],
        [InlineKeyboardButton(text=f"{trial_24h_status} • 24 часа после триала", callback_data="admin_mon_notify_toggle_trial_24h")],
        [InlineKeyboardButton(text="🧪 Тест: 24 часа после триала", callback_data="admin_mon_notify_preview_trial_24h")],
        [InlineKeyboardButton(text=f"{trial_channel_status} • Отписка от канала", callback_data="admin_mon_notify_toggle_trial_channel")],
        [InlineKeyboardButton(text="🧪 Тест: отписка от канала", callback_data="admin_mon_notify_preview_trial_channel")],
        [InlineKeyboardButton(text=f"{expired_1d_status} • 1 день после истечения", callback_data="admin_mon_notify_toggle_expired_1d")],
        [InlineKeyboardButton(text="🧪 Тест: 1 день после истечения", callback_data="admin_mon_notify_preview_expired_1d")],
        [InlineKeyboardButton(text=f"{second_wave_status} • 2-3 дня со скидкой", callback_data="admin_mon_notify_toggle_expired_2d")],
        [InlineKeyboardButton(text="🧪 Тест: скидка 2-3 день", callback_data="admin_mon_notify_preview_expired_2d")],
        [InlineKeyboardButton(text=f"✏️ Скидка 2-3 дня: {second_percent}%", callback_data="admin_mon_notify_edit_2d_percent")],
        [InlineKeyboardButton(text=f"⏱️ Срок скидки 2-3 дня: {second_hours} ч", callback_data="admin_mon_notify_edit_2d_hours")],
        [InlineKeyboardButton(text=f"{third_wave_status} • {third_days} дней со скидкой", callback_data="admin_mon_notify_toggle_expired_nd")],
        [InlineKeyboardButton(text="🧪 Тест: скидка спустя дни", callback_data="admin_mon_notify_preview_expired_nd")],
        [InlineKeyboardButton(text=f"✏️ Скидка {third_days} дней: {third_percent}%", callback_data="admin_mon_notify_edit_nd_percent")],
        [InlineKeyboardButton(text=f"⏱️ Срок скидки {third_days} дней: {third_hours} ч", callback_data="admin_mon_notify_edit_nd_hours")],
        [InlineKeyboardButton(text=f"📆 Порог уведомления: {third_days} дн.", callback_data="admin_mon_notify_edit_nd_threshold")],
        [InlineKeyboardButton(text="🧪 Отправить все тесты", callback_data="admin_mon_notify_preview_all")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_settings")],
    ])

    return summary_text, keyboard


def _build_notification_preview_message(language: str, notification_type: str):
    texts = get_texts(language)
    now = datetime.now()
    price_30_days = settings.format_price(settings.PRICE_30_DAYS)

    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    header = "🧪 <b>Тестовое уведомление мониторинга</b>\n\n"

    if notification_type == "trial_inactive_1h":
        template = texts.get(
            "TRIAL_INACTIVE_1H",
            (
                "⏳ <b>Прошёл час, а подключения нет</b>\n\n"
                "Если возникли сложности с запуском — воспользуйтесь инструкциями."
            ),
        )
        message = template.format(
            price=price_30_days,
            end_date=(now + timedelta(days=settings.TRIAL_DURATION_DAYS)).strftime("%d.%m.%Y %H:%M"),
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                        callback_data="subscription_connect",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("MY_SUBSCRIPTION_BUTTON", "📱 Моя подписка"),
                        callback_data="menu_subscription",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"),
                        callback_data="menu_support",
                    )
                ],
            ]
        )
    elif notification_type == "trial_inactive_24h":
        template = texts.get(
            "TRIAL_INACTIVE_24H",
            (
                "⏳ <b>Вы ещё не подключились к VPN</b>\n\n"
                "Прошли сутки с активации тестового периода, но трафик не зафиксирован."
                "\n\nНажмите кнопку ниже, чтобы подключиться."
            ),
        )
        message = template.format(
            price=price_30_days,
            end_date=(now + timedelta(days=1)).strftime("%d.%m.%Y %H:%M"),
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t("CONNECT_BUTTON", "🔗 Подключиться"),
                        callback_data="subscription_connect",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("MY_SUBSCRIPTION_BUTTON", "📱 Моя подписка"),
                        callback_data="menu_subscription",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"),
                        callback_data="menu_support",
                    )
                ],
            ]
        )
    elif notification_type == "trial_channel_unsubscribed":
        template = texts.get(
            "TRIAL_CHANNEL_UNSUBSCRIBED",
            (
                "🚫 <b>Доступ приостановлен</b>\n\n"
                "Мы не нашли вашу подписку на наш канал, поэтому тестовая подписка отключена.\n\n"
                "Подпишитесь на канал и нажмите «{check_button}», чтобы вернуть доступ."
            ),
        )
        check_button = texts.t("CHANNEL_CHECK_BUTTON", "✅ Я подписался")
        message = template.format(check_button=check_button)
        buttons: list[list[InlineKeyboardButton]] = []
        if settings.CHANNEL_LINK:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=texts.t("CHANNEL_SUBSCRIBE_BUTTON", "🔗 Подписаться"),
                        url=settings.CHANNEL_LINK,
                    )
                ]
            )
        buttons.append(
            [
                InlineKeyboardButton(
                    text=check_button,
                    callback_data="sub_channel_check",
                )
            ]
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    elif notification_type == "expired_1d":
        template = texts.get(
            "SUBSCRIPTION_EXPIRED_1D",
            (
                "⛔ <b>Подписка закончилась</b>\n\n"
                "Доступ был отключён {end_date}. Продлите подписку, чтобы вернуться в сервис."
            ),
        )
        message = template.format(
            end_date=(now - timedelta(days=1)).strftime("%d.%m.%Y %H:%M"),
            price=price_30_days,
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t("SUBSCRIPTION_EXTEND", "💎 Продлить подписку"),
                        callback_data="subscription_extend",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("BALANCE_TOPUP", "💳 Пополнить баланс"),
                        callback_data="balance_topup",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"),
                        callback_data="menu_support",
                    )
                ],
            ]
        )
    elif notification_type == "expired_2d":
        percent = NotificationSettingsService.get_second_wave_discount_percent()
        valid_hours = NotificationSettingsService.get_second_wave_valid_hours()
        template = texts.get(
            "SUBSCRIPTION_EXPIRED_SECOND_WAVE",
            (
                "🔥 <b>Скидка {percent}% на продление</b>\n\n"
                "Активируйте предложение, чтобы получить дополнительную скидку. "
                "Она суммируется с вашей промогруппой и действует до {expires_at}."
            ),
        )
        message = template.format(
            percent=percent,
            expires_at=(now + timedelta(hours=valid_hours)).strftime("%d.%m.%Y %H:%M"),
            trigger_days=3,
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎁 Получить скидку",
                        callback_data="claim_discount_preview",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUBSCRIPTION_EXTEND", "💎 Продлить подписку"),
                        callback_data="subscription_extend",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("BALANCE_TOPUP", "💳 Пополнить баланс"),
                        callback_data="balance_topup",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"),
                        callback_data="menu_support",
                    )
                ],
            ]
        )
    elif notification_type == "expired_nd":
        percent = NotificationSettingsService.get_third_wave_discount_percent()
        valid_hours = NotificationSettingsService.get_third_wave_valid_hours()
        trigger_days = NotificationSettingsService.get_third_wave_trigger_days()
        template = texts.get(
            "SUBSCRIPTION_EXPIRED_THIRD_WAVE",
            (
                "🎁 <b>Индивидуальная скидка {percent}%</b>\n\n"
                "Прошло {trigger_days} дней без подписки — возвращайтесь и активируйте дополнительную скидку. "
                "Она суммируется с промогруппой и действует до {expires_at}."
            ),
        )
        message = template.format(
            percent=percent,
            trigger_days=trigger_days,
            expires_at=(now + timedelta(hours=valid_hours)).strftime("%d.%m.%Y %H:%M"),
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎁 Получить скидку",
                        callback_data="claim_discount_preview",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUBSCRIPTION_EXTEND", "💎 Продлить подписку"),
                        callback_data="subscription_extend",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("BALANCE_TOPUP", "💳 Пополнить баланс"),
                        callback_data="balance_topup",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=texts.t("SUPPORT_BUTTON", "🆘 Поддержка"),
                        callback_data="menu_support",
                    )
                ],
            ]
        )
    else:
        raise ValueError(f"Unsupported notification type: {notification_type}")

    footer = "\n\n<i>Сообщение отправлено только вам для проверки оформления.</i>"
    return header + message + footer, keyboard


async def _send_notification_preview(bot, chat_id: int, language: str, notification_type: str) -> None:
    message, keyboard = _build_notification_preview_message(language, notification_type)
    await bot.send_message(
        chat_id,
        message,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def _render_notification_settings(callback: CallbackQuery) -> None:
    language = (callback.from_user.language_code or settings.DEFAULT_LANGUAGE)
    text, keyboard = _build_notification_settings_view(language)
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _render_notification_settings_for_state(
    bot,
    chat_id: int,
    message_id: int,
    language: str,
    business_connection_id: str | None = None,
) -> None:
    text, keyboard = _build_notification_settings_view(language)

    edit_kwargs = {
        "text": text,
        "chat_id": chat_id,
        "message_id": message_id,
        "parse_mode": "HTML",
        "reply_markup": keyboard,
    }

    if business_connection_id:
        edit_kwargs["business_connection_id"] = business_connection_id

    try:
        await bot.edit_message_text(**edit_kwargs)
    except TelegramBadRequest as exc:
        if "no text in the message to edit" in (exc.message or "").lower():
            caption_kwargs = {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": text,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
            }

            if business_connection_id:
                caption_kwargs["business_connection_id"] = business_connection_id

            await bot.edit_message_caption(**caption_kwargs)
        else:
            raise

@router.callback_query(F.data == "admin_monitoring")
@admin_required
async def admin_monitoring_menu(callback: CallbackQuery):
    try:
        async with AsyncSessionLocal() as db:
            status = await monitoring_service.get_monitoring_status(db)

            running_status = "🟢 Работает" if status['is_running'] else "🔴 Остановлен"
            last_update = status['last_update'].strftime('%H:%M:%S') if status['last_update'] else "Никогда"

            text = f"""
🔍 <b>Система мониторинга</b>

📊 <b>Статус:</b> {running_status}
🕐 <b>Последнее обновление:</b> {last_update}
⚙️ <b>Интервал проверки:</b> {settings.MONITORING_INTERVAL} мин

📈 <b>Статистика за 24 часа:</b>
• Всего событий: {status['stats_24h']['total_events']}
• Успешных: {status['stats_24h']['successful']}
• Ошибок: {status['stats_24h']['failed']}
• Успешность: {status['stats_24h']['success_rate']}%

🔧 Выберите действие:
"""

            language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
            keyboard = get_monitoring_keyboard(language)
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка в админ меню мониторинга: {e}")
        await callback.answer("❌ Ошибка получения данных", show_alert=True)


@router.callback_query(F.data == "admin_mon_settings")
@admin_required
async def admin_monitoring_settings(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        global_status = "🟢 Включены" if NotificationSettingsService.are_notifications_globally_enabled() else "🔴 Отключены"
        second_percent = NotificationSettingsService.get_second_wave_discount_percent()
        third_percent = NotificationSettingsService.get_third_wave_discount_percent()
        third_days = NotificationSettingsService.get_third_wave_trigger_days()

        text = (
            "⚙️ <b>Настройки мониторинга</b>\n\n"
            f"🔔 <b>Уведомления пользователям:</b> {global_status}\n"
            f"• Скидка 2-3 дня: {second_percent}%\n"
            f"• Скидка после {third_days} дней: {third_percent}%\n\n"
            "Выберите раздел для настройки."
        )

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔔 Уведомления пользователям", callback_data="admin_mon_notify_settings")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_submenu_settings")],
        ])

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка отображения настроек мониторинга: {e}")
        await callback.answer("❌ Не удалось открыть настройки", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_settings")
@admin_required
async def admin_notify_settings(callback: CallbackQuery):
    try:
        await _render_notification_settings(callback)
    except Exception as e:
        logger.error(f"Ошибка отображения настроек уведомлений: {e}")
        await callback.answer("❌ Не удалось загрузить настройки", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_toggle_trial_1h")
@admin_required
async def toggle_trial_1h_notification(callback: CallbackQuery):
    enabled = NotificationSettingsService.is_trial_inactive_1h_enabled()
    NotificationSettingsService.set_trial_inactive_1h_enabled(not enabled)
    await callback.answer("✅ Включено" if not enabled else "⏸️ Отключено")
    await _render_notification_settings(callback)


@router.callback_query(F.data == "admin_mon_notify_preview_trial_1h")
@admin_required
async def preview_trial_1h_notification(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        await _send_notification_preview(callback.bot, callback.from_user.id, language, "trial_inactive_1h")
        await callback.answer("✅ Пример отправлен")
    except Exception as exc:
        logger.error("Failed to send trial 1h preview: %s", exc)
        await callback.answer("❌ Не удалось отправить тест", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_toggle_trial_24h")
@admin_required
async def toggle_trial_24h_notification(callback: CallbackQuery):
    enabled = NotificationSettingsService.is_trial_inactive_24h_enabled()
    NotificationSettingsService.set_trial_inactive_24h_enabled(not enabled)
    await callback.answer("✅ Включено" if not enabled else "⏸️ Отключено")
    await _render_notification_settings(callback)


@router.callback_query(F.data == "admin_mon_notify_preview_trial_24h")
@admin_required
async def preview_trial_24h_notification(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        await _send_notification_preview(callback.bot, callback.from_user.id, language, "trial_inactive_24h")
        await callback.answer("✅ Пример отправлен")
    except Exception as exc:
        logger.error("Failed to send trial 24h preview: %s", exc)
        await callback.answer("❌ Не удалось отправить тест", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_toggle_trial_channel")
@admin_required
async def toggle_trial_channel_notification(callback: CallbackQuery):
    enabled = NotificationSettingsService.is_trial_channel_unsubscribed_enabled()
    NotificationSettingsService.set_trial_channel_unsubscribed_enabled(not enabled)
    await callback.answer("✅ Включено" if not enabled else "⏸️ Отключено")
    await _render_notification_settings(callback)


@router.callback_query(F.data == "admin_mon_notify_preview_trial_channel")
@admin_required
async def preview_trial_channel_notification(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        await _send_notification_preview(callback.bot, callback.from_user.id, language, "trial_channel_unsubscribed")
        await callback.answer("✅ Пример отправлен")
    except Exception as exc:
        logger.error("Failed to send trial channel preview: %s", exc)
        await callback.answer("❌ Не удалось отправить тест", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_toggle_expired_1d")
@admin_required
async def toggle_expired_1d_notification(callback: CallbackQuery):
    enabled = NotificationSettingsService.is_expired_1d_enabled()
    NotificationSettingsService.set_expired_1d_enabled(not enabled)
    await callback.answer("✅ Включено" if not enabled else "⏸️ Отключено")
    await _render_notification_settings(callback)


@router.callback_query(F.data == "admin_mon_notify_preview_expired_1d")
@admin_required
async def preview_expired_1d_notification(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        await _send_notification_preview(callback.bot, callback.from_user.id, language, "expired_1d")
        await callback.answer("✅ Пример отправлен")
    except Exception as exc:
        logger.error("Failed to send expired 1d preview: %s", exc)
        await callback.answer("❌ Не удалось отправить тест", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_toggle_expired_2d")
@admin_required
async def toggle_second_wave_notification(callback: CallbackQuery):
    enabled = NotificationSettingsService.is_second_wave_enabled()
    NotificationSettingsService.set_second_wave_enabled(not enabled)
    await callback.answer("✅ Включено" if not enabled else "⏸️ Отключено")
    await _render_notification_settings(callback)


@router.callback_query(F.data == "admin_mon_notify_preview_expired_2d")
@admin_required
async def preview_second_wave_notification(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        await _send_notification_preview(callback.bot, callback.from_user.id, language, "expired_2d")
        await callback.answer("✅ Пример отправлен")
    except Exception as exc:
        logger.error("Failed to send second wave preview: %s", exc)
        await callback.answer("❌ Не удалось отправить тест", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_toggle_expired_nd")
@admin_required
async def toggle_third_wave_notification(callback: CallbackQuery):
    enabled = NotificationSettingsService.is_third_wave_enabled()
    NotificationSettingsService.set_third_wave_enabled(not enabled)
    await callback.answer("✅ Включено" if not enabled else "⏸️ Отключено")
    await _render_notification_settings(callback)


@router.callback_query(F.data == "admin_mon_notify_preview_expired_nd")
@admin_required
async def preview_third_wave_notification(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        await _send_notification_preview(callback.bot, callback.from_user.id, language, "expired_nd")
        await callback.answer("✅ Пример отправлен")
    except Exception as exc:
        logger.error("Failed to send third wave preview: %s", exc)
        await callback.answer("❌ Не удалось отправить тест", show_alert=True)


@router.callback_query(F.data == "admin_mon_notify_preview_all")
@admin_required
async def preview_all_notifications(callback: CallbackQuery):
    try:
        language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
        chat_id = callback.from_user.id
        for notification_type in [
            "trial_inactive_1h",
            "trial_inactive_24h",
            "trial_channel_unsubscribed",
            "expired_1d",
            "expired_2d",
            "expired_nd",
        ]:
            await _send_notification_preview(callback.bot, chat_id, language, notification_type)
        await callback.answer("✅ Все тестовые уведомления отправлены")
    except Exception as exc:
        logger.error("Failed to send all notification previews: %s", exc)
        await callback.answer("❌ Не удалось отправить тесты", show_alert=True)


async def _start_notification_value_edit(
    callback: CallbackQuery,
    state: FSMContext,
    setting_key: str,
    field: str,
    prompt_key: str,
    default_prompt: str,
):
    language = callback.from_user.language_code or settings.DEFAULT_LANGUAGE
    await state.set_state(AdminStates.editing_notification_value)
    await state.update_data(
        notification_setting_key=setting_key,
        notification_setting_field=field,
        settings_message_chat=callback.message.chat.id,
        settings_message_id=callback.message.message_id,
        settings_business_connection_id=(
            str(getattr(callback.message, "business_connection_id", None))
            if getattr(callback.message, "business_connection_id", None) is not None
            else None
        ),
        settings_language=language,
    )
    texts = get_texts(language)
    await callback.answer()
    await callback.message.answer(texts.get(prompt_key, default_prompt))


@router.callback_query(F.data == "admin_mon_notify_edit_2d_percent")
@admin_required
async def edit_second_wave_percent(callback: CallbackQuery, state: FSMContext):
    await _start_notification_value_edit(
        callback,
        state,
        "expired_second_wave",
        "percent",
        "NOTIFY_PROMPT_SECOND_PERCENT",
        "Введите новый процент скидки для уведомления через 2-3 дня (0-100):",
    )


@router.callback_query(F.data == "admin_mon_notify_edit_2d_hours")
@admin_required
async def edit_second_wave_hours(callback: CallbackQuery, state: FSMContext):
    await _start_notification_value_edit(
        callback,
        state,
        "expired_second_wave",
        "hours",
        "NOTIFY_PROMPT_SECOND_HOURS",
        "Введите количество часов действия скидки (1-168):",
    )


@router.callback_query(F.data == "admin_mon_notify_edit_nd_percent")
@admin_required
async def edit_third_wave_percent(callback: CallbackQuery, state: FSMContext):
    await _start_notification_value_edit(
        callback,
        state,
        "expired_third_wave",
        "percent",
        "NOTIFY_PROMPT_THIRD_PERCENT",
        "Введите новый процент скидки для позднего предложения (0-100):",
    )


@router.callback_query(F.data == "admin_mon_notify_edit_nd_hours")
@admin_required
async def edit_third_wave_hours(callback: CallbackQuery, state: FSMContext):
    await _start_notification_value_edit(
        callback,
        state,
        "expired_third_wave",
        "hours",
        "NOTIFY_PROMPT_THIRD_HOURS",
        "Введите количество часов действия скидки (1-168):",
    )


@router.callback_query(F.data == "admin_mon_notify_edit_nd_threshold")
@admin_required
async def edit_third_wave_threshold(callback: CallbackQuery, state: FSMContext):
    await _start_notification_value_edit(
        callback,
        state,
        "expired_third_wave",
        "trigger",
        "NOTIFY_PROMPT_THIRD_DAYS",
        "Через сколько дней после истечения отправлять предложение? (минимум 2):",
    )


@router.callback_query(F.data == "admin_mon_start")
@admin_required
async def start_monitoring_callback(callback: CallbackQuery):
    try:
        if monitoring_service.is_running:
            await callback.answer("ℹ️ Мониторинг уже запущен")
            return
        
        if not monitoring_service.bot:
            monitoring_service.bot = callback.bot
        
        asyncio.create_task(monitoring_service.start_monitoring())
        
        await callback.answer("✅ Мониторинг запущен!")
        
        await admin_monitoring_menu(callback)
        
    except Exception as e:
        logger.error(f"Ошибка запуска мониторинга: {e}")
        await callback.answer(f"❌ Ошибка запуска: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_stop")
@admin_required
async def stop_monitoring_callback(callback: CallbackQuery):
    try:
        if not monitoring_service.is_running:
            await callback.answer("ℹ️ Мониторинг уже остановлен")
            return
        
        monitoring_service.stop_monitoring()
        await callback.answer("⏹️ Мониторинг остановлен!")
        
        await admin_monitoring_menu(callback)
        
    except Exception as e:
        logger.error(f"Ошибка остановки мониторинга: {e}")
        await callback.answer(f"❌ Ошибка остановки: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_force_check")
@admin_required
async def force_check_callback(callback: CallbackQuery):
    try:
        await callback.answer("⏳ Выполняем проверку подписок...")

        async with AsyncSessionLocal() as db:
            results = await monitoring_service.force_check_subscriptions(db)

            text = f"""
✅ <b>Принудительная проверка завершена</b>

📊 <b>Результаты проверки:</b>
• Истекших подписок: {results['expired']}
• Истекающих подписок: {results['expiring']}
• Готовых к автооплате: {results['autopay_ready']}

🕐 <b>Время проверки:</b> {datetime.now().strftime('%H:%M:%S')}

Нажмите "Назад" для возврата в меню мониторинга.
"""

            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")]
            ])

            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка принудительной проверки: {e}")
        await callback.answer(f"❌ Ошибка проверки: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_traffic_check")
@admin_required
async def traffic_check_callback(callback: CallbackQuery):
    """Ручная проверка трафика — использует snapshot и дельту."""
    try:
        # Проверяем, включен ли мониторинг трафика
        if not traffic_monitoring_scheduler.is_enabled():
            await callback.answer(
                "⚠️ Мониторинг трафика отключен в настройках\n"
                "Включите TRAFFIC_FAST_CHECK_ENABLED=true в .env",
                show_alert=True
            )
            return

        await callback.answer("⏳ Запускаем проверку трафика (дельта)...")

        # Используем run_fast_check — он сравнивает с snapshot и отправляет уведомления
        from app.services.traffic_monitoring_service import traffic_monitoring_scheduler_v2

        # Устанавливаем бота, если не установлен
        if not traffic_monitoring_scheduler_v2.bot:
            traffic_monitoring_scheduler_v2.set_bot(callback.bot)

        violations = await traffic_monitoring_scheduler_v2.run_fast_check_now()

        # Получаем информацию о snapshot
        snapshot_age = await traffic_monitoring_scheduler_v2.service.get_snapshot_age_minutes()
        threshold_gb = traffic_monitoring_scheduler_v2.service.get_fast_check_threshold_gb()

        text = f"""
📊 <b>Проверка трафика завершена</b>

🔍 <b>Результаты (дельта):</b>
• Превышений за интервал: {len(violations)}
• Порог дельты: {threshold_gb} ГБ
• Возраст snapshot: {snapshot_age:.1f} мин

🕐 <b>Время проверки:</b> {datetime.now().strftime('%H:%M:%S')}
"""

        if violations:
            text += "\n⚠️ <b>Превышения дельты:</b>\n"
            for v in violations[:10]:
                name = v.full_name or v.user_uuid[:8]
                text += f"• {name}: +{v.used_traffic_gb:.1f} ГБ\n"
            if len(violations) > 10:
                text += f"... и ещё {len(violations) - 10}\n"
            text += "\n📨 Уведомления отправлены (с учётом кулдауна)"
        else:
            text += "\n✅ Превышений не обнаружено"

        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Повторить", callback_data="admin_mon_traffic_check")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")]
        ])

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка проверки трафика: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("admin_mon_logs"))
@admin_required
async def monitoring_logs_callback(callback: CallbackQuery):
    try:
        page = 1
        if "_page_" in callback.data:
            page = int(callback.data.split("_page_")[1])
        
        async with AsyncSessionLocal() as db:
            all_logs = await monitoring_service.get_monitoring_logs(db, limit=1000)

            if not all_logs:
                text = "📋 <b>Логи мониторинга пусты</b>\n\nСистема еще не выполнила проверки."
                keyboard = get_monitoring_logs_back_keyboard()
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
                return

            per_page = 8
            paginated_logs = paginate_list(all_logs, page=page, per_page=per_page)

            text = f"📋 <b>Логи мониторинга</b> (стр. {page}/{paginated_logs.total_pages})\n\n"

            for log in paginated_logs.items:
                icon = "✅" if log['is_success'] else "❌"
                time_str = log['created_at'].strftime('%m-%d %H:%M')
                event_type = log['event_type'].replace('_', ' ').title()

                message = log['message']
                if len(message) > 45:
                    message = message[:45] + "..."

                text += f"{icon} <code>{time_str}</code> {event_type}\n"
                text += f"   📄 {message}\n\n"

            total_success = sum(1 for log in all_logs if log['is_success'])
            total_failed = len(all_logs) - total_success
            success_rate = round(total_success / len(all_logs) * 100, 1) if all_logs else 0

            text += f"📊 <b>Общая статистика:</b>\n"
            text += f"• Всего событий: {len(all_logs)}\n"
            text += f"• Успешных: {total_success}\n"
            text += f"• Ошибок: {total_failed}\n"
            text += f"• Успешность: {success_rate}%"

            keyboard = get_monitoring_logs_keyboard(page, paginated_logs.total_pages)
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка получения логов: {e}")
        await callback.answer("❌ Ошибка получения логов", show_alert=True)


@router.callback_query(F.data == "admin_mon_clear_logs")
@admin_required
async def clear_logs_callback(callback: CallbackQuery):
    try:
        async with AsyncSessionLocal() as db:
            deleted_count = await monitoring_service.cleanup_old_logs(db, days=0)
            await db.commit()

            if deleted_count > 0:
                await callback.answer(f"🗑️ Удалено {deleted_count} записей логов")
            else:
                await callback.answer("ℹ️ Логи уже пусты")

            await monitoring_logs_callback(callback)

    except Exception as e:
        logger.error(f"Ошибка очистки логов: {e}")
        await callback.answer(f"❌ Ошибка очистки: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_test_notifications")
@admin_required
async def test_notifications_callback(callback: CallbackQuery):
    try:
        test_message = f"""
🧪 <b>Тестовое уведомление системы мониторинга</b>

Это тестовое сообщение для проверки работы системы уведомлений.

📊 <b>Статус системы:</b>
• Мониторинг: {'🟢 Работает' if monitoring_service.is_running else '🔴 Остановлен'}
• Уведомления: {'🟢 Включены' if settings.ENABLE_NOTIFICATIONS else '🔴 Отключены'}
• Время теста: {datetime.now().strftime('%H:%M:%S %d.%m.%Y')}

✅ Если вы получили это сообщение, система уведомлений работает корректно!
"""
        
        await callback.bot.send_message(
            callback.from_user.id,
            test_message,
            parse_mode="HTML"
        )
        
        await callback.answer("✅ Тестовое уведомление отправлено!")
        
    except Exception as e:
        logger.error(f"Ошибка отправки тестового уведомления: {e}")
        await callback.answer(f"❌ Ошибка отправки: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_statistics")
@admin_required
async def monitoring_statistics_callback(callback: CallbackQuery):
    try:
        async with AsyncSessionLocal() as db:
            from app.database.crud.subscription import get_subscriptions_statistics
            sub_stats = await get_subscriptions_statistics(db)

            mon_status = await monitoring_service.get_monitoring_status(db)

            week_ago = datetime.now() - timedelta(days=7)
            week_logs = await monitoring_service.get_monitoring_logs(db, limit=1000)
            week_logs = [log for log in week_logs if log['created_at'] >= week_ago]

            week_success = sum(1 for log in week_logs if log['is_success'])
            week_errors = len(week_logs) - week_success

            text = f"""
📊 <b>Статистика мониторинга</b>

📱 <b>Подписки:</b>
• Всего: {sub_stats['total_subscriptions']}
• Активных: {sub_stats['active_subscriptions']}
• Тестовых: {sub_stats['trial_subscriptions']}
• Платных: {sub_stats['paid_subscriptions']}

📈 <b>За сегодня:</b>
• Успешных операций: {mon_status['stats_24h']['successful']}
• Ошибок: {mon_status['stats_24h']['failed']}
• Успешность: {mon_status['stats_24h']['success_rate']}%

📊 <b>За неделю:</b>
• Всего событий: {len(week_logs)}
• Успешных: {week_success}
• Ошибок: {week_errors}
• Успешность: {round(week_success/len(week_logs)*100, 1) if week_logs else 0}%

🔧 <b>Система:</b>
• Интервал: {settings.MONITORING_INTERVAL} мин
• Уведомления: {'🟢 Вкл' if getattr(settings, 'ENABLE_NOTIFICATIONS', True) else '🔴 Выкл'}
• Автооплата: {', '.join(map(str, settings.get_autopay_warning_days()))} дней
"""

            # Добавляем информацию о чеках NaloGO
            if settings.is_nalogo_enabled():
                nalogo_status = await nalogo_queue_service.get_status()
                queue_len = nalogo_status.get("queue_length", 0)
                total_amount = nalogo_status.get("total_amount", 0)
                running = nalogo_status.get("running", False)
                pending_count = nalogo_status.get("pending_verification_count", 0)
                pending_amount = nalogo_status.get("pending_verification_amount", 0)

                nalogo_section = f"""
🧾 <b>Чеки NaloGO:</b>
• Сервис: {'🟢 Работает' if running else '🔴 Остановлен'}
• В очереди: {queue_len} чек(ов)"""
                if queue_len > 0:
                    nalogo_section += f"\n• На сумму: {total_amount:,.2f} ₽"
                if pending_count > 0:
                    nalogo_section += f"\n⚠️ <b>Требуют проверки: {pending_count} ({pending_amount:,.2f} ₽)</b>"
                text += nalogo_section

            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            buttons = []
            # Кнопки для работы с чеками NaloGO
            if settings.is_nalogo_enabled():
                nalogo_status = await nalogo_queue_service.get_status()
                nalogo_buttons = []
                if nalogo_status.get("queue_length", 0) > 0:
                    nalogo_buttons.append(InlineKeyboardButton(
                        text=f"🧾 Отправить ({nalogo_status['queue_length']})",
                        callback_data="admin_mon_nalogo_force_process"
                    ))
                pending_count = nalogo_status.get("pending_verification_count", 0)
                if pending_count > 0:
                    nalogo_buttons.append(InlineKeyboardButton(
                        text=f"⚠️ Проверить ({pending_count})",
                        callback_data="admin_mon_nalogo_pending"
                    ))
                nalogo_buttons.append(InlineKeyboardButton(
                    text="📊 Сверка чеков",
                    callback_data="admin_mon_receipts_missing"
                ))
                buttons.append(nalogo_buttons)

            buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await callback.answer(f"❌ Ошибка получения статистики: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_nalogo_force_process")
@admin_required
async def nalogo_force_process_callback(callback: CallbackQuery):
    """Принудительная отправка чеков из очереди."""
    try:
        await callback.answer("🔄 Запускаю обработку очереди чеков...", show_alert=False)

        result = await nalogo_queue_service.force_process()

        if "error" in result:
            await callback.answer(f"❌ {result['error']}", show_alert=True)
            return

        message = result.get("message", "Готово")
        processed = result.get("processed", 0)
        remaining = result.get("remaining", 0)

        if processed > 0:
            text = f"✅ Обработано: {processed} чек(ов)"
            if remaining > 0:
                text += f"\n⏳ Осталось в очереди: {remaining}"
        else:
            if remaining > 0:
                text = f"⚠️ Сервис nalog.ru недоступен\n⏳ В очереди: {remaining} чек(ов)"
            else:
                text = "📭 Очередь пуста"

        await callback.answer(text, show_alert=True)

        # Обновляем страницу статистики
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        # Перезагружаем статистику
        async with AsyncSessionLocal() as db:
            from app.database.crud.subscription import get_subscriptions_statistics
            sub_stats = await get_subscriptions_statistics(db)
            mon_status = await monitoring_service.get_monitoring_status(db)

            week_ago = datetime.now() - timedelta(days=7)
            week_logs = await monitoring_service.get_monitoring_logs(db, limit=1000)
            week_logs = [log for log in week_logs if log['created_at'] >= week_ago]
            week_success = sum(1 for log in week_logs if log['is_success'])
            week_errors = len(week_logs) - week_success

            stats_text = f"""
📊 <b>Статистика мониторинга</b>

📱 <b>Подписки:</b>
• Всего: {sub_stats['total_subscriptions']}
• Активных: {sub_stats['active_subscriptions']}
• Тестовых: {sub_stats['trial_subscriptions']}
• Платных: {sub_stats['paid_subscriptions']}

📈 <b>За сегодня:</b>
• Успешных операций: {mon_status['stats_24h']['successful']}
• Ошибок: {mon_status['stats_24h']['failed']}
• Успешность: {mon_status['stats_24h']['success_rate']}%

📊 <b>За неделю:</b>
• Всего событий: {len(week_logs)}
• Успешных: {week_success}
• Ошибок: {week_errors}
• Успешность: {round(week_success/len(week_logs)*100, 1) if week_logs else 0}%

🔧 <b>Система:</b>
• Интервал: {settings.MONITORING_INTERVAL} мин
• Уведомления: {'🟢 Вкл' if getattr(settings, 'ENABLE_NOTIFICATIONS', True) else '🔴 Выкл'}
• Автооплата: {', '.join(map(str, settings.get_autopay_warning_days()))} дней
"""

            if settings.is_nalogo_enabled():
                nalogo_status = await nalogo_queue_service.get_status()
                queue_len = nalogo_status.get("queue_length", 0)
                total_amount = nalogo_status.get("total_amount", 0)
                running = nalogo_status.get("running", False)

                nalogo_section = f"""
🧾 <b>Чеки NaloGO:</b>
• Сервис: {'🟢 Работает' if running else '🔴 Остановлен'}
• В очереди: {queue_len} чек(ов)"""
                if queue_len > 0:
                    nalogo_section += f"\n• На сумму: {total_amount:,.2f} ₽"
                stats_text += nalogo_section

            buttons = []
            # Кнопки для работы с чеками NaloGO
            if settings.is_nalogo_enabled():
                nalogo_status = await nalogo_queue_service.get_status()
                nalogo_buttons = []
                if nalogo_status.get("queue_length", 0) > 0:
                    nalogo_buttons.append(InlineKeyboardButton(
                        text=f"🧾 Отправить ({nalogo_status['queue_length']})",
                        callback_data="admin_mon_nalogo_force_process"
                    ))
                nalogo_buttons.append(InlineKeyboardButton(
                    text="📊 Сверка чеков",
                    callback_data="admin_mon_receipts_missing"
                ))
                buttons.append(nalogo_buttons)

            buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            await callback.message.edit_text(stats_text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка принудительной обработки чеков: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_nalogo_pending")
@admin_required
async def nalogo_pending_callback(callback: CallbackQuery):
    """Просмотр чеков ожидающих ручной проверки."""
    try:
        from app.services.nalogo_service import NaloGoService
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

        nalogo_service = NaloGoService()
        receipts = await nalogo_service.get_pending_verification_receipts()

        if not receipts:
            await callback.answer("✅ Нет чеков на проверку", show_alert=True)
            return

        text = f"⚠️ <b>Чеки требующие проверки: {len(receipts)}</b>\n\n"
        text += "Проверьте в lknpd.nalog.ru созданы ли эти чеки.\n\n"

        buttons = []
        for i, receipt in enumerate(receipts[:10], 1):
            payment_id = receipt.get("payment_id", "unknown")
            amount = receipt.get("amount", 0)
            created_at = receipt.get("created_at", "")[:16].replace("T", " ")
            error = receipt.get("error", "")[:50]

            text += f"<b>{i}. {amount:,.2f} ₽</b>\n"
            text += f"   📅 {created_at}\n"
            text += f"   🆔 <code>{payment_id[:20]}...</code>\n"
            if error:
                text += f"   ❌ {error}\n"
            text += "\n"

            # Кнопки для каждого чека
            buttons.append([
                InlineKeyboardButton(
                    text=f"✅ Создан ({i})",
                    callback_data=f"admin_nalogo_verified:{payment_id[:30]}"
                ),
                InlineKeyboardButton(
                    text=f"🔄 Отправить ({i})",
                    callback_data=f"admin_nalogo_retry:{payment_id[:30]}"
                ),
            ])

        if len(receipts) > 10:
            text += f"\n... и ещё {len(receipts) - 10} чек(ов)"

        buttons.append([
            InlineKeyboardButton(
                text="🗑 Очистить всё (проверено)",
                callback_data="admin_nalogo_clear_pending"
            )
        ])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_statistics")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка просмотра очереди проверки: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("admin_nalogo_verified:"))
@admin_required
async def nalogo_mark_verified_callback(callback: CallbackQuery):
    """Пометить чек как созданный в налоговой."""
    try:
        from app.services.nalogo_service import NaloGoService

        payment_id = callback.data.split(":", 1)[1]
        nalogo_service = NaloGoService()

        # Помечаем как проверенный (чек был создан)
        removed = await nalogo_service.mark_pending_as_verified(
            payment_id, receipt_uuid=None, was_created=True
        )

        if removed:
            await callback.answer(f"✅ Чек помечен как созданный", show_alert=True)
            # Обновляем список
            await nalogo_pending_callback(callback)
        else:
            await callback.answer("❌ Чек не найден", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка пометки чека: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.startswith("admin_nalogo_retry:"))
@admin_required
async def nalogo_retry_callback(callback: CallbackQuery):
    """Повторно отправить чек в налоговую."""
    try:
        from app.services.nalogo_service import NaloGoService

        payment_id = callback.data.split(":", 1)[1]
        nalogo_service = NaloGoService()

        await callback.answer("🔄 Отправляю чек...", show_alert=False)

        receipt_uuid = await nalogo_service.retry_pending_receipt(payment_id)

        if receipt_uuid:
            await callback.answer(f"✅ Чек создан: {receipt_uuid}", show_alert=True)
            # Обновляем список
            await nalogo_pending_callback(callback)
        else:
            await callback.answer("❌ Не удалось создать чек", show_alert=True)

    except Exception as e:
        logger.error(f"Ошибка повторной отправки чека: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_nalogo_clear_pending")
@admin_required
async def nalogo_clear_pending_callback(callback: CallbackQuery):
    """Очистить всю очередь проверки."""
    try:
        from app.services.nalogo_service import NaloGoService

        nalogo_service = NaloGoService()
        count = await nalogo_service.clear_pending_verification()

        await callback.answer(f"✅ Очищено: {count} чек(ов)", show_alert=True)
        # Возвращаемся на статистику
        await callback.message.edit_text(
            "✅ Очередь проверки очищена",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_statistics")]
            ])
        )

    except Exception as e:
        logger.error(f"Ошибка очистки очереди: {e}")
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_receipts_missing")
@admin_required
async def receipts_missing_callback(callback: CallbackQuery):
    """Сверка чеков по логам."""
    # Напрямую вызываем сверку по логам
    await _do_reconcile_logs(callback)


@router.callback_query(F.data == "admin_mon_receipts_link_old")
@admin_required
async def receipts_link_old_callback(callback: CallbackQuery):
    """Привязать старые чеки из NaloGO к транзакциям по сумме и дате."""
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from sqlalchemy import select, and_
        from datetime import date, timedelta
        from app.database.models import Transaction, PaymentMethod, TransactionType
        from app.services.nalogo_service import NaloGoService

        await callback.answer("🔄 Загружаю чеки из NaloGO...", show_alert=False)

        TRACKING_START_DATE = datetime(2024, 12, 29, 0, 0, 0)

        async with AsyncSessionLocal() as db:
            # Получаем старые транзакции без чеков
            query = select(Transaction).where(
                and_(
                    Transaction.type == TransactionType.DEPOSIT.value,
                    Transaction.payment_method == PaymentMethod.YOOKASSA.value,
                    Transaction.receipt_uuid.is_(None),
                    Transaction.is_completed == True,
                    Transaction.created_at < TRACKING_START_DATE,
                )
            ).order_by(Transaction.created_at.desc())

            result = await db.execute(query)
            transactions = result.scalars().all()

            if not transactions:
                await callback.answer("✅ Нет старых транзакций для привязки", show_alert=True)
                return

            # Получаем чеки из NaloGO за последние 60 дней
            nalogo_service = NaloGoService()
            to_date = date.today()
            from_date = to_date - timedelta(days=60)

            incomes = await nalogo_service.get_incomes(
                from_date=from_date,
                to_date=to_date,
                limit=500,
            )

            if not incomes:
                await callback.answer("❌ Не удалось получить чеки из NaloGO", show_alert=True)
                return

            # Создаём словарь чеков по сумме для быстрого поиска
            # Ключ: сумма в копейках, значение: список чеков
            incomes_by_amount = {}
            for income in incomes:
                amount = float(income.get("totalAmount", income.get("amount", 0)))
                amount_kopeks = int(amount * 100)
                if amount_kopeks not in incomes_by_amount:
                    incomes_by_amount[amount_kopeks] = []
                incomes_by_amount[amount_kopeks].append(income)

            linked = 0
            for t in transactions:
                if t.amount_kopeks in incomes_by_amount:
                    matching_incomes = incomes_by_amount[t.amount_kopeks]
                    if matching_incomes:
                        # Берём первый подходящий чек
                        income = matching_incomes.pop(0)
                        receipt_uuid = income.get("approvedReceiptUuid", income.get("receiptUuid"))
                        if receipt_uuid:
                            t.receipt_uuid = receipt_uuid
                            # Парсим дату чека
                            operation_time = income.get("operationTime")
                            if operation_time:
                                try:
                                    from dateutil.parser import isoparse
                                    t.receipt_created_at = isoparse(operation_time)
                                except Exception:
                                    t.receipt_created_at = datetime.utcnow()
                            linked += 1

            if linked > 0:
                await db.commit()

            text = f"🔗 <b>Привязка завершена</b>\n\n"
            text += f"Всего транзакций: {len(transactions)}\n"
            text += f"Чеков в NaloGO: {len(incomes)}\n"
            text += f"Привязано: <b>{linked}</b>\n"
            text += f"Не удалось привязать: {len(transactions) - linked}"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_statistics")],
            ])

            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка привязки старых чеков: {e}", exc_info=True)
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_receipts_reconcile")
@admin_required
async def receipts_reconcile_menu_callback(callback: CallbackQuery, state: FSMContext):
    """Меню выбора периода сверки."""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    # Очищаем состояние на случай если остался ввод даты
    await state.clear()

    # Сразу показываем сверку по логам
    await _do_reconcile_logs(callback)


async def _do_reconcile_logs(callback: CallbackQuery):
    """Внутренняя функция сверки по логам."""
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from pathlib import Path
        import re
        from collections import defaultdict

        await callback.answer("🔄 Анализирую логи платежей...", show_alert=False)

        # Путь к файлу логов платежей (logs/current/)
        log_file_path = Path(settings.LOG_FILE).resolve()
        log_dir = log_file_path.parent
        current_dir = log_dir / "current"
        payments_log = current_dir / settings.LOG_PAYMENTS_FILE

        if not payments_log.exists():
            try:
                await callback.message.edit_text(
                    "❌ <b>Файл логов не найден</b>\n\n"
                    f"Путь: <code>{payments_log}</code>\n\n"
                    "<i>Логи появятся после первого успешного платежа.</i>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_mon_reconcile_logs")],
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_statistics")]
                    ])
                )
            except TelegramBadRequest:
                pass  # Сообщение не изменилось
            return

        # Паттерны для парсинга логов
        # Успешный платёж: "Успешно обработан платеж YooKassa 30e3c6fc-000f-5001-9000-1a9c8b242396: пользователь 1046 пополнил баланс на 200.0₽"
        payment_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}.*Успешно обработан платеж YooKassa ([a-f0-9-]+).*на ([\d.]+)₽"
        )
        # Чек создан: "Чек NaloGO создан для платежа 30e3c6fc-000f-5001-9000-1a9c8b242396: 243udsqtik"
        receipt_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2}.*Чек NaloGO создан для платежа ([a-f0-9-]+): (\w+)"
        )

        # Читаем и парсим логи
        payments = {}  # payment_id -> {date, amount}
        receipts = {}  # payment_id -> {date, receipt_uuid}

        try:
            with open(payments_log, "r", encoding="utf-8") as f:
                for line in f:
                    # Проверяем платежи
                    match = payment_pattern.search(line)
                    if match:
                        date_str, payment_id, amount = match.groups()
                        payments[payment_id] = {
                            "date": date_str,
                            "amount": float(amount)
                        }
                        continue

                    # Проверяем чеки
                    match = receipt_pattern.search(line)
                    if match:
                        date_str, payment_id, receipt_uuid = match.groups()
                        receipts[payment_id] = {
                            "date": date_str,
                            "receipt_uuid": receipt_uuid
                        }
        except Exception as e:
            logger.error(f"Ошибка чтения логов: {e}")
            await callback.message.edit_text(
                f"❌ <b>Ошибка чтения логов</b>\n\n{str(e)}",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_statistics")]
                ])
            )
            return

        # Находим платежи без чеков
        payments_without_receipts = []
        for payment_id, payment_data in payments.items():
            if payment_id not in receipts:
                payments_without_receipts.append({
                    "payment_id": payment_id,
                    "date": payment_data["date"],
                    "amount": payment_data["amount"]
                })

        # Группируем по датам
        by_date = defaultdict(list)
        for p in payments_without_receipts:
            by_date[p["date"]].append(p)

        # Формируем отчёт
        total_payments = len(payments)
        total_receipts = len(receipts)
        missing_count = len(payments_without_receipts)
        missing_amount = sum(p["amount"] for p in payments_without_receipts)

        text = "📋 <b>Сверка по логам</b>\n\n"
        text += f"📦 <b>Всего платежей:</b> {total_payments}\n"
        text += f"🧾 <b>Чеков создано:</b> {total_receipts}\n\n"

        if missing_count == 0:
            text += "✅ <b>Все платежи имеют чеки!</b>"
        else:
            text += f"⚠️ <b>Без чеков:</b> {missing_count} платежей на {missing_amount:,.2f} ₽\n\n"

            # Показываем по датам (последние)
            sorted_dates = sorted(by_date.keys(), reverse=True)
            for date_str in sorted_dates[:7]:
                date_payments = by_date[date_str]
                date_amount = sum(p["amount"] for p in date_payments)
                text += f"• <b>{date_str}:</b> {len(date_payments)} шт. на {date_amount:,.2f} ₽\n"

            if len(sorted_dates) > 7:
                text += f"\n<i>...и ещё {len(sorted_dates) - 7} дней</i>"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_mon_reconcile_logs")],
            [InlineKeyboardButton(text="📄 Детали", callback_data="admin_mon_reconcile_logs_details")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_statistics")],
        ])

        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramBadRequest:
            pass  # Сообщение не изменилось

    except TelegramBadRequest:
        pass  # Игнорируем если сообщение не изменилось
    except Exception as e:
        logger.error(f"Ошибка сверки по логам: {e}", exc_info=True)
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_mon_reconcile_logs")
@admin_required
async def receipts_reconcile_logs_refresh_callback(callback: CallbackQuery):
    """Обновить сверку по логам."""
    await _do_reconcile_logs(callback)


@router.callback_query(F.data == "admin_mon_reconcile_logs_details")
@admin_required
async def receipts_reconcile_logs_details_callback(callback: CallbackQuery):
    """Детальный список платежей без чеков."""
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from pathlib import Path
        import re

        await callback.answer("🔄 Загружаю детали...", show_alert=False)

        # Путь к логам (logs/current/)
        log_file_path = Path(settings.LOG_FILE).resolve()
        log_dir = log_file_path.parent
        current_dir = log_dir / "current"
        payments_log = current_dir / settings.LOG_PAYMENTS_FILE

        if not payments_log.exists():
            await callback.answer("❌ Файл логов не найден", show_alert=True)
            return

        payment_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*Успешно обработан платеж YooKassa ([a-f0-9-]+).*пользователь (\d+).*на ([\d.]+)₽"
        )
        receipt_pattern = re.compile(
            r"Чек NaloGO создан для платежа ([a-f0-9-]+)"
        )

        payments = {}
        receipts = set()

        with open(payments_log, "r", encoding="utf-8") as f:
            for line in f:
                match = payment_pattern.search(line)
                if match:
                    date_str, time_str, payment_id, user_id, amount = match.groups()
                    payments[payment_id] = {
                        "date": date_str,
                        "time": time_str,
                        "user_id": user_id,
                        "amount": float(amount)
                    }
                    continue

                match = receipt_pattern.search(line)
                if match:
                    receipts.add(match.group(1))

        # Платежи без чеков
        missing = []
        for payment_id, data in payments.items():
            if payment_id not in receipts:
                missing.append({"payment_id": payment_id, **data})

        # Сортируем по дате (новые сверху)
        missing.sort(key=lambda x: (x["date"], x["time"]), reverse=True)

        if not missing:
            text = "✅ <b>Все платежи имеют чеки!</b>"
        else:
            text = f"📄 <b>Платежи без чеков ({len(missing)} шт.)</b>\n\n"

            for p in missing[:20]:
                text += (
                    f"• <b>{p['date']} {p['time']}</b>\n"
                    f"  User: {p['user_id']} | {p['amount']:.0f}₽\n"
                    f"  <code>{p['payment_id'][:18]}...</code>\n\n"
                )

            if len(missing) > 20:
                text += f"<i>...и ещё {len(missing) - 20} платежей</i>"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_mon_reconcile_logs")],
        ])

        try:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
        except TelegramBadRequest:
            pass

    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.error(f"Ошибка детализации: {e}", exc_info=True)
        await callback.answer(f"❌ Ошибка: {str(e)}", show_alert=True)


def get_monitoring_logs_keyboard(current_page: int, total_pages: int):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    keyboard = []
    
    if total_pages > 1:
        nav_row = []
        
        if current_page > 1:
            nav_row.append(InlineKeyboardButton(
                text="⬅️", 
                callback_data=f"admin_mon_logs_page_{current_page - 1}"
            ))
        
        nav_row.append(InlineKeyboardButton(
            text=f"{current_page}/{total_pages}", 
            callback_data="current_page"
        ))
        
        if current_page < total_pages:
            nav_row.append(InlineKeyboardButton(
                text="➡️", 
                callback_data=f"admin_mon_logs_page_{current_page + 1}"
            ))
        
        keyboard.append(nav_row)
    
    keyboard.extend([
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_mon_logs"),
            InlineKeyboardButton(text="🗑️ Очистить", callback_data="admin_mon_clear_logs")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")]
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_monitoring_logs_back_keyboard():
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_mon_logs"),
            InlineKeyboardButton(text="🔍 Фильтры", callback_data="admin_mon_logs_filters")
        ],
        [
            InlineKeyboardButton(text="🗑️ Очистить логи", callback_data="admin_mon_clear_logs")
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_monitoring")]
    ])


@router.message(Command("monitoring"))
@admin_required
async def monitoring_command(message: Message):
    try:
        async with AsyncSessionLocal() as db:
            status = await monitoring_service.get_monitoring_status(db)

            running_status = "🟢 Работает" if status['is_running'] else "🔴 Остановлен"

            text = f"""
🔍 <b>Быстрый статус мониторинга</b>

📊 <b>Статус:</b> {running_status}
📈 <b>События за 24ч:</b> {status['stats_24h']['total_events']}
✅ <b>Успешность:</b> {status['stats_24h']['success_rate']}%

Для подробного управления используйте админ-панель.
"""

            await message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка команды /monitoring: {e}")
        await message.answer(f"❌ Ошибка: {str(e)}")


@router.message(AdminStates.editing_notification_value)
async def process_notification_value_input(message: Message, state: FSMContext):
    data = await state.get_data()
    if not data:
        await state.clear()
        await message.answer("ℹ️ Контекст утерян, попробуйте снова из меню настроек.")
        return

    raw_value = (message.text or "").strip()
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        language = data.get("settings_language") or message.from_user.language_code or settings.DEFAULT_LANGUAGE
        texts = get_texts(language)
        await message.answer(texts.get("NOTIFICATION_VALUE_INVALID", "❌ Введите целое число."))
        return

    key = data.get("notification_setting_key")
    field = data.get("notification_setting_field")
    language = data.get("settings_language") or message.from_user.language_code or settings.DEFAULT_LANGUAGE
    texts = get_texts(language)

    # Добавляем дополнительные проверки диапазона значений
    if (key == "expired_second_wave" and field == "percent") or (key == "expired_third_wave" and field == "percent"):
        if value < 0 or value > 100:
            await message.answer("❌ Процент скидки должен быть от 0 до 100.")
            return
    elif (key == "expired_second_wave" and field == "hours") or (key == "expired_third_wave" and field == "hours"):
        if value < 1 or value > 168:  # Максимум 168 часов (7 дней)
            await message.answer("❌ Количество часов должно быть от 1 до 168.")
            return
    elif key == "expired_third_wave" and field == "trigger":
        if value < 2:  # Минимум 2 дня
            await message.answer("❌ Количество дней должно быть не менее 2.")
            return

    success = False
    if key == "expired_second_wave" and field == "percent":
        success = NotificationSettingsService.set_second_wave_discount_percent(value)
    elif key == "expired_second_wave" and field == "hours":
        success = NotificationSettingsService.set_second_wave_valid_hours(value)
    elif key == "expired_third_wave" and field == "percent":
        success = NotificationSettingsService.set_third_wave_discount_percent(value)
    elif key == "expired_third_wave" and field == "hours":
        success = NotificationSettingsService.set_third_wave_valid_hours(value)
    elif key == "expired_third_wave" and field == "trigger":
        success = NotificationSettingsService.set_third_wave_trigger_days(value)

    if not success:
        await message.answer(texts.get("NOTIFICATION_VALUE_INVALID", "❌ Некорректное значение, попробуйте снова."))
        return

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.get("BACK", "⬅️ Назад"),
                    callback_data="admin_mon_notify_settings",
                )
            ]
        ]
    )

    await message.answer(
        texts.get("NOTIFICATION_VALUE_UPDATED", "✅ Настройки обновлены."),
        reply_markup=back_keyboard,
    )

    chat_id = data.get("settings_message_chat")
    message_id = data.get("settings_message_id")
    business_connection_id = data.get("settings_business_connection_id")
    if chat_id and message_id:
        await _render_notification_settings_for_state(
            message.bot,
            chat_id,
            message_id,
            language,
            business_connection_id=business_connection_id,
        )

    await state.clear()


def register_handlers(dp):
    dp.include_router(router)
