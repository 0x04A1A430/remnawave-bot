from aiogram import Dispatcher, F, types
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.keyboards.admin import get_admin_tariffs_submenu_keyboard
from app.localization.texts import get_texts
from app.utils.decorators import admin_required, error_handler


async def _show_placeholder(
    callback: types.CallbackQuery,
    db_user: User,
    text_key: str,
    default_text: str,
    *,
    parse_mode: str = "HTML",
) -> None:
    """Render a placeholder message with shared tariffs header."""

    texts = get_texts(db_user.language)
    header = texts.t("ADMIN_TARIFFS_PLACEHOLDER_TITLE", "🧾 <b>Тарифы</b>\n\n")
    body = texts.t(text_key, default_text)

    await callback.message.edit_text(
        header + body,
        reply_markup=get_admin_tariffs_submenu_keyboard(db_user.language),
        parse_mode=parse_mode,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_tariff_mode_activation(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _show_placeholder(
        callback,
        db_user,
        "ADMIN_TARIFFS_ACTIVATE_PLACEHOLDER",
        (
            "⚙️ Режим тарифов пока в разработке.\n\n"
            "После запуска режим заменит слово 'сервера' на 'тарифы' в клиентском интерфейсе"
            " и откроет дополнительные настройки продаж."
        ),
    )


@admin_required
@error_handler
async def show_tariff_creation_placeholder(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _show_placeholder(
        callback,
        db_user,
        "ADMIN_TARIFFS_CREATE_PLACEHOLDER",
        "➕ Создание тарифов появится здесь. Подготовьте список параметров заранее.",
    )


@admin_required
@error_handler
async def show_tariff_list_placeholder(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _show_placeholder(
        callback,
        db_user,
        "ADMIN_TARIFFS_LIST_PLACEHOLDER",
        "📋 Просмотр тарифов скоро будет доступен. Список покажет активные и скрытые тарифы.",
    )


@admin_required
@error_handler
async def show_tariff_stats_placeholder(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
) -> None:
    await _show_placeholder(
        callback,
        db_user,
        "ADMIN_TARIFFS_STATS_PLACEHOLDER",
        "📊 Статистика тарифов появится здесь и поможет отслеживать продажи по тарифам.",
    )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(show_tariff_mode_activation, F.data == "admin_tariffs_activate")
    dp.callback_query.register(show_tariff_creation_placeholder, F.data == "admin_tariffs_create")
    dp.callback_query.register(show_tariff_list_placeholder, F.data == "admin_tariffs_list")
    dp.callback_query.register(show_tariff_stats_placeholder, F.data == "admin_tariffs_stats")

