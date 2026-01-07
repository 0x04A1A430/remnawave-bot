"""Управление тарифами в админ-панели."""
import logging
from typing import Dict, List, Optional, Tuple

from aiogram import Dispatcher, types, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.tariff import (
    get_all_tariffs,
    get_tariff_by_id,
    create_tariff,
    update_tariff,
    delete_tariff,
    get_tariff_subscriptions_count,
    get_tariffs_with_subscriptions_count,
)
from app.database.crud.promo_group import get_promo_groups_with_counts
from app.database.crud.server_squad import get_all_server_squads
from app.database.models import Tariff, User
from app.localization.texts import get_texts
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 10


def _format_traffic(gb: int) -> str:
    """Форматирует трафик."""
    if gb == 0:
        return "Безлимит"
    return f"{gb} ГБ"


def _format_price_kopeks(kopeks: int) -> str:
    """Форматирует цену из копеек в рубли."""
    rubles = kopeks / 100
    if rubles == int(rubles):
        return f"{int(rubles)} ₽"
    return f"{rubles:.2f} ₽"


def _format_period(days: int) -> str:
    """Форматирует период."""
    if days == 1:
        return "1 день"
    elif days < 5:
        return f"{days} дня"
    elif days < 21 or days % 10 >= 5 or days % 10 == 0:
        return f"{days} дней"
    elif days % 10 == 1:
        return f"{days} день"
    else:
        return f"{days} дня"


def _parse_period_prices(text: str) -> Dict[str, int]:
    """
    Парсит строку с ценами периодов.
    Формат: "30:9900, 90:24900, 180:44900" или "30=9900; 90=24900"
    """
    prices = {}
    text = text.replace(";", ",").replace("=", ":")

    for part in text.split(","):
        part = part.strip()
        if not part:
            continue

        if ":" not in part:
            continue

        period_str, price_str = part.split(":", 1)
        try:
            period = int(period_str.strip())
            price = int(price_str.strip())
            if period > 0 and price >= 0:
                prices[str(period)] = price
        except ValueError:
            continue

    return prices


def _format_period_prices_display(prices: Dict[str, int]) -> str:
    """Форматирует цены периодов для отображения."""
    if not prices:
        return "Не заданы"

    lines = []
    for period_str in sorted(prices.keys(), key=int):
        period = int(period_str)
        price = prices[period_str]
        lines.append(f"  • {_format_period(period)}: {_format_price_kopeks(price)}")

    return "\n".join(lines)


def _format_period_prices_for_edit(prices: Dict[str, int]) -> str:
    """Форматирует цены периодов для редактирования."""
    if not prices:
        return "30:9900, 90:24900, 180:44900"

    parts = []
    for period_str in sorted(prices.keys(), key=int):
        parts.append(f"{period_str}:{prices[period_str]}")

    return ", ".join(parts)


def get_tariffs_list_keyboard(
    tariffs: List[Tuple[Tariff, int]],
    language: str,
    page: int = 0,
    total_pages: int = 1,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру списка тарифов."""
    texts = get_texts(language)
    buttons = []

    for tariff, subs_count in tariffs:
        status = "✅" if tariff.is_active else "❌"
        button_text = f"{status} {tariff.name} ({subs_count})"
        buttons.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"admin_tariff_view:{tariff.id}"
            )
        ])

    # Пагинация
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton(text="◀️", callback_data=f"admin_tariffs_page:{page-1}")
        )
    if page < total_pages - 1:
        nav_buttons.append(
            InlineKeyboardButton(text="▶️", callback_data=f"admin_tariffs_page:{page+1}")
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    # Кнопка создания
    buttons.append([
        InlineKeyboardButton(
            text="➕ Создать тариф",
            callback_data="admin_tariff_create"
        )
    ])

    # Кнопка назад
    buttons.append([
        InlineKeyboardButton(
            text=texts.BACK,
            callback_data="admin_submenu_settings"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_tariff_view_keyboard(
    tariff: Tariff,
    language: str,
) -> InlineKeyboardMarkup:
    """Создает клавиатуру просмотра тарифа."""
    texts = get_texts(language)
    buttons = []

    # Редактирование полей
    buttons.append([
        InlineKeyboardButton(text="✏️ Название", callback_data=f"admin_tariff_edit_name:{tariff.id}"),
        InlineKeyboardButton(text="📝 Описание", callback_data=f"admin_tariff_edit_desc:{tariff.id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📊 Трафик", callback_data=f"admin_tariff_edit_traffic:{tariff.id}"),
        InlineKeyboardButton(text="📱 Устройства", callback_data=f"admin_tariff_edit_devices:{tariff.id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="💰 Цены", callback_data=f"admin_tariff_edit_prices:{tariff.id}"),
        InlineKeyboardButton(text="🎚️ Уровень", callback_data=f"admin_tariff_edit_tier:{tariff.id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="📱💰 Цена за устройство", callback_data=f"admin_tariff_edit_device_price:{tariff.id}"),
        InlineKeyboardButton(text="⏰ Дни триала", callback_data=f"admin_tariff_edit_trial_days:{tariff.id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text="🌐 Серверы", callback_data=f"admin_tariff_edit_squads:{tariff.id}"),
        InlineKeyboardButton(text="👥 Промогруппы", callback_data=f"admin_tariff_edit_promo:{tariff.id}"),
    ])

    # Переключение триала
    if tariff.is_trial_available:
        buttons.append([
            InlineKeyboardButton(text="🎁 ❌ Убрать триал", callback_data=f"admin_tariff_toggle_trial:{tariff.id}")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🎁 Сделать триальным", callback_data=f"admin_tariff_toggle_trial:{tariff.id}")
        ])

    # Переключение активности
    if tariff.is_active:
        buttons.append([
            InlineKeyboardButton(text="❌ Деактивировать", callback_data=f"admin_tariff_toggle:{tariff.id}")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="✅ Активировать", callback_data=f"admin_tariff_toggle:{tariff.id}")
        ])

    # Удаление
    buttons.append([
        InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_tariff_delete:{tariff.id}")
    ])

    # Назад к списку
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data="admin_tariffs")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_tariff_info(tariff: Tariff, language: str, subs_count: int = 0) -> str:
    """Форматирует информацию о тарифе."""
    texts = get_texts(language)

    status = "✅ Активен" if tariff.is_active else "❌ Неактивен"
    traffic = _format_traffic(tariff.traffic_limit_gb)
    prices_display = _format_period_prices_display(tariff.period_prices or {})

    # Форматируем список серверов
    squads_list = tariff.allowed_squads or []
    squads_display = f"{len(squads_list)} серверов" if squads_list else "Все серверы"

    # Форматируем промогруппы
    promo_groups = tariff.allowed_promo_groups or []
    if promo_groups:
        promo_display = ", ".join(pg.name for pg in promo_groups)
    else:
        promo_display = "Доступен всем"

    trial_status = "✅ Да" if tariff.is_trial_available else "❌ Нет"

    # Форматируем дни триала
    trial_days = getattr(tariff, 'trial_duration_days', None)
    if trial_days:
        trial_days_display = f"{trial_days} дней"
    else:
        trial_days_display = f"По умолчанию ({settings.TRIAL_DURATION_DAYS} дней)"

    # Форматируем цену за устройство
    device_price = getattr(tariff, 'device_price_kopeks', None)
    if device_price is not None and device_price > 0:
        device_price_display = _format_price_kopeks(device_price) + "/мес"
    else:
        device_price_display = "Недоступно"

    return f"""📦 <b>Тариф: {tariff.name}</b>

{status}
🎚️ Уровень: {tariff.tier_level}
📊 Порядок: {tariff.display_order}

<b>Параметры:</b>
• Трафик: {traffic}
• Устройств: {tariff.device_limit}
• Цена за доп. устройство: {device_price_display}
• Триал: {trial_status}
• Дней триала: {trial_days_display}

<b>Цены:</b>
{prices_display}

<b>Серверы:</b> {squads_display}
<b>Промогруппы:</b> {promo_display}

📊 Подписок на тарифе: {subs_count}

{f"📝 {tariff.description}" if tariff.description else ""}"""


@admin_required
@error_handler
async def show_tariffs_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает список тарифов."""
    await state.clear()
    texts = get_texts(db_user.language)

    # Проверяем режим продаж
    if not settings.is_tariffs_mode():
        await callback.message.edit_text(
            "⚠️ <b>Режим тарифов отключен</b>\n\n"
            "Для использования тарифов установите:\n"
            "<code>SALES_MODE=tariffs</code>\n\n"
            "Текущий режим: <code>classic</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_settings")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)

    if not tariffs_data:
        await callback.message.edit_text(
            "📦 <b>Тарифы</b>\n\n"
            "Тарифы ещё не созданы.\n"
            "Создайте первый тариф для начала работы.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать тариф", callback_data="admin_tariff_create")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_settings")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page_data = tariffs_data[:ITEMS_PER_PAGE]

    total_subs = sum(count for _, count in tariffs_data)
    active_count = sum(1 for t, _ in tariffs_data if t.is_active)

    await callback.message.edit_text(
        f"📦 <b>Тарифы</b>\n\n"
        f"Всего: {len(tariffs_data)} (активных: {active_count})\n"
        f"Подписок на тарифах: {total_subs}\n\n"
        "Выберите тариф для просмотра и редактирования:",
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, 0, total_pages),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def show_tariffs_page(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает страницу списка тарифов."""
    texts = get_texts(db_user.language)
    page = int(callback.data.split(":")[1])

    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)
    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_data = tariffs_data[start_idx:end_idx]

    total_subs = sum(count for _, count in tariffs_data)
    active_count = sum(1 for t, _ in tariffs_data if t.is_active)

    await callback.message.edit_text(
        f"📦 <b>Тарифы</b> (стр. {page + 1}/{total_pages})\n\n"
        f"Всего: {len(tariffs_data)} (активных: {active_count})\n"
        f"Подписок на тарифах: {total_subs}\n\n"
        "Выберите тариф для просмотра и редактирования:",
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, page, total_pages),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def view_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Просмотр тарифа."""
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает активность тарифа."""
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    tariff = await update_tariff(db, tariff, is_active=not tariff.is_active)
    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    status = "активирован" if tariff.is_active else "деактивирован"
    await callback.answer(f"Тариф {status}", show_alert=True)

    await callback.message.edit_text(
        format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def toggle_trial_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает тариф как триальный."""
    from app.database.crud.tariff import set_trial_tariff, clear_trial_tariff

    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    if tariff.is_trial_available:
        # Снимаем флаг триала
        await clear_trial_tariff(db)
        await callback.answer("Триал снят с тарифа", show_alert=True)
    else:
        # Устанавливаем этот тариф как триальный (снимает флаг с других)
        await set_trial_tariff(db, tariff_id)
        await callback.answer(f"Тариф «{tariff.name}» установлен как триальный", show_alert=True)

    # Перезагружаем тариф
    tariff = await get_tariff_by_id(db, tariff_id)
    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await callback.message.edit_text(
        format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


# ============ СОЗДАНИЕ ТАРИФА ============

@admin_required
@error_handler
async def start_create_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает создание тарифа."""
    texts = get_texts(db_user.language)

    await state.set_state(AdminStates.creating_tariff_name)
    await state.update_data(language=db_user.language)

    await callback.message.edit_text(
        "📦 <b>Создание тарифа</b>\n\n"
        "Шаг 1/6: Введите название тарифа\n\n"
        "Пример: <i>Базовый</i>, <i>Премиум</i>, <i>Бизнес</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data="admin_tariffs")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_tariff_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает название тарифа."""
    texts = get_texts(db_user.language)
    name = message.text.strip()

    if len(name) < 2:
        await message.answer("Название должно быть не короче 2 символов")
        return

    if len(name) > 50:
        await message.answer("Название должно быть не длиннее 50 символов")
        return

    await state.update_data(tariff_name=name)
    await state.set_state(AdminStates.creating_tariff_traffic)

    await message.answer(
        "📦 <b>Создание тарифа</b>\n\n"
        f"Название: <b>{name}</b>\n\n"
        "Шаг 2/6: Введите лимит трафика в ГБ\n\n"
        "Введите <code>0</code> для безлимитного трафика\n"
        "Пример: <i>100</i>, <i>500</i>, <i>0</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data="admin_tariffs")]
        ]),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def process_tariff_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает лимит трафика."""
    texts = get_texts(db_user.language)

    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное число (0 или больше)")
        return

    data = await state.get_data()
    await state.update_data(tariff_traffic=traffic)
    await state.set_state(AdminStates.creating_tariff_devices)

    traffic_display = _format_traffic(traffic)

    await message.answer(
        "📦 <b>Создание тарифа</b>\n\n"
        f"Название: <b>{data['tariff_name']}</b>\n"
        f"Трафик: <b>{traffic_display}</b>\n\n"
        "Шаг 3/6: Введите лимит устройств\n\n"
        "Пример: <i>1</i>, <i>3</i>, <i>5</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data="admin_tariffs")]
        ]),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def process_tariff_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает лимит устройств."""
    texts = get_texts(db_user.language)

    try:
        devices = int(message.text.strip())
        if devices < 1:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное число (1 или больше)")
        return

    data = await state.get_data()
    await state.update_data(tariff_devices=devices)
    await state.set_state(AdminStates.creating_tariff_tier)

    traffic_display = _format_traffic(data['tariff_traffic'])

    await message.answer(
        "📦 <b>Создание тарифа</b>\n\n"
        f"Название: <b>{data['tariff_name']}</b>\n"
        f"Трафик: <b>{traffic_display}</b>\n"
        f"Устройств: <b>{devices}</b>\n\n"
        "Шаг 4/6: Введите уровень тарифа (1-10)\n\n"
        "Уровень используется для визуального отображения\n"
        "1 - базовый, 10 - максимальный\n"
        "Пример: <i>1</i>, <i>2</i>, <i>3</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data="admin_tariffs")]
        ]),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def process_tariff_tier(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает уровень тарифа."""
    texts = get_texts(db_user.language)

    try:
        tier = int(message.text.strip())
        if tier < 1 or tier > 10:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 1 до 10")
        return

    data = await state.get_data()
    await state.update_data(tariff_tier=tier)
    await state.set_state(AdminStates.creating_tariff_prices)

    traffic_display = _format_traffic(data['tariff_traffic'])

    await message.answer(
        "📦 <b>Создание тарифа</b>\n\n"
        f"Название: <b>{data['tariff_name']}</b>\n"
        f"Трафик: <b>{traffic_display}</b>\n"
        f"Устройств: <b>{data['tariff_devices']}</b>\n"
        f"Уровень: <b>{tier}</b>\n\n"
        "Шаг 5/6: Введите цены на периоды\n\n"
        "Формат: <code>дней:цена_в_копейках</code>\n"
        "Несколько периодов через запятую\n\n"
        "Пример:\n<code>30:9900, 90:24900, 180:44900, 360:79900</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data="admin_tariffs")]
        ]),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def process_tariff_prices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает цены тарифа."""
    texts = get_texts(db_user.language)

    prices = _parse_period_prices(message.text.strip())

    if not prices:
        await message.answer(
            "Не удалось распознать цены.\n\n"
            "Формат: <code>дней:цена_в_копейках</code>\n"
            "Пример: <code>30:9900, 90:24900</code>",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    await state.update_data(tariff_prices=prices)

    traffic_display = _format_traffic(data['tariff_traffic'])
    prices_display = _format_period_prices_display(prices)

    # Создаем тариф
    tariff = await create_tariff(
        db,
        name=data['tariff_name'],
        traffic_limit_gb=data['tariff_traffic'],
        device_limit=data['tariff_devices'],
        tier_level=data['tariff_tier'],
        period_prices=prices,
        is_active=True,
    )

    await state.clear()

    subs_count = 0

    await message.answer(
        f"✅ <b>Тариф создан!</b>\n\n"
        + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


# ============ РЕДАКТИРОВАНИЕ ТАРИФА ============

@admin_required
@error_handler
async def start_edit_tariff_name(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование названия тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_name)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        f"✏️ <b>Редактирование названия</b>\n\n"
        f"Текущее название: <b>{tariff.name}</b>\n\n"
        "Введите новое название:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_name(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое название тарифа."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    name = message.text.strip()
    if len(name) < 2 or len(name) > 50:
        await message.answer("Название должно быть от 2 до 50 символов")
        return

    tariff = await update_tariff(db, tariff, name=name)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Название изменено!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def start_edit_tariff_description(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование описания тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_description)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_desc = tariff.description or "Не задано"

    await callback.message.edit_text(
        f"📝 <b>Редактирование описания</b>\n\n"
        f"Текущее описание:\n{current_desc}\n\n"
        "Введите новое описание (или <code>-</code> для удаления):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_description(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое описание тарифа."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    description = message.text.strip()
    if description == "-":
        description = None

    tariff = await update_tariff(db, tariff, description=description)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Описание изменено!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def start_edit_tariff_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование трафика тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_traffic)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_traffic = _format_traffic(tariff.traffic_limit_gb)

    await callback.message.edit_text(
        f"📊 <b>Редактирование трафика</b>\n\n"
        f"Текущий лимит: <b>{current_traffic}</b>\n\n"
        "Введите новый лимит в ГБ (0 = безлимит):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_traffic(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый лимит трафика."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    try:
        traffic = int(message.text.strip())
        if traffic < 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное число (0 или больше)")
        return

    tariff = await update_tariff(db, tariff, traffic_limit_gb=traffic)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Трафик изменен!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def start_edit_tariff_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование лимита устройств."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_devices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        f"📱 <b>Редактирование устройств</b>\n\n"
        f"Текущий лимит: <b>{tariff.device_limit}</b>\n\n"
        "Введите новый лимит устройств:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_devices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый лимит устройств."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    try:
        devices = int(message.text.strip())
        if devices < 1:
            raise ValueError
    except ValueError:
        await message.answer("Введите корректное число (1 или больше)")
        return

    tariff = await update_tariff(db, tariff, device_limit=devices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Лимит устройств изменен!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def start_edit_tariff_tier(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование уровня тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_tier)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    await callback.message.edit_text(
        f"🎚️ <b>Редактирование уровня</b>\n\n"
        f"Текущий уровень: <b>{tariff.tier_level}</b>\n\n"
        "Введите новый уровень (1-10):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_tier(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новый уровень тарифа."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    try:
        tier = int(message.text.strip())
        if tier < 1 or tier > 10:
            raise ValueError
    except ValueError:
        await message.answer("Введите число от 1 до 10")
        return

    tariff = await update_tariff(db, tariff, tier_level=tier)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Уровень изменен!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def start_edit_tariff_prices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование цен тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_prices)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    current_prices = _format_period_prices_for_edit(tariff.period_prices or {})
    prices_display = _format_period_prices_display(tariff.period_prices or {})

    await callback.message.edit_text(
        f"💰 <b>Редактирование цен</b>\n\n"
        f"Текущие цены:\n{prices_display}\n\n"
        "Введите новые цены в формате:\n"
        f"<code>{current_prices}</code>\n\n"
        "(дней:цена_в_копейках, через запятую)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_prices(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новые цены тарифа."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    prices = _parse_period_prices(message.text.strip())
    if not prices:
        await message.answer(
            "Не удалось распознать цены.\n"
            "Формат: <code>дней:цена</code>\n"
            "Пример: <code>30:9900, 90:24900</code>",
            parse_mode="HTML"
        )
        return

    tariff = await update_tariff(db, tariff, period_prices=prices)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Цены изменены!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


# ============ РЕДАКТИРОВАНИЕ ЦЕНЫ ЗА УСТРОЙСТВО ============

@admin_required
@error_handler
async def start_edit_tariff_device_price(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование цены за устройство."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_device_price)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    device_price = getattr(tariff, 'device_price_kopeks', None)
    if device_price is not None and device_price > 0:
        current_price = _format_price_kopeks(device_price) + "/мес"
    else:
        current_price = "Недоступно (докупка устройств запрещена)"

    await callback.message.edit_text(
        f"📱💰 <b>Редактирование цены за устройство</b>\n\n"
        f"Текущая цена: <b>{current_price}</b>\n\n"
        "Введите цену в копейках за одно устройство в месяц.\n\n"
        "• <code>0</code> или <code>-</code> — докупка устройств недоступна\n"
        "• Например: <code>5000</code> = 50₽/мес за устройство",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_device_price(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новую цену за устройство."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    text = message.text.strip()

    if text == "-" or text == "0":
        device_price = None
    else:
        try:
            device_price = int(text)
            if device_price < 0:
                raise ValueError
        except ValueError:
            await message.answer(
                "Введите корректное число (0 или больше).\n"
                "Для отключения докупки введите <code>0</code> или <code>-</code>",
                parse_mode="HTML"
            )
            return

    tariff = await update_tariff(db, tariff, device_price_kopeks=device_price)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Цена за устройство изменена!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


# ============ РЕДАКТИРОВАНИЕ ДНЕЙ ТРИАЛА ============

@admin_required
@error_handler
async def start_edit_tariff_trial_days(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Начинает редактирование дней триала."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(AdminStates.editing_tariff_trial_days)
    await state.update_data(tariff_id=tariff_id, language=db_user.language)

    trial_days = getattr(tariff, 'trial_duration_days', None)
    if trial_days:
        current_days = f"{trial_days} дней"
    else:
        current_days = f"По умолчанию ({settings.TRIAL_DURATION_DAYS} дней)"

    await callback.message.edit_text(
        f"⏰ <b>Редактирование дней триала</b>\n\n"
        f"Текущее значение: <b>{current_days}</b>\n\n"
        "Введите количество дней триала.\n\n"
        f"• <code>0</code> или <code>-</code> — использовать настройку по умолчанию ({settings.TRIAL_DURATION_DAYS} дней)\n"
        "• Например: <code>7</code> = 7 дней триала",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=texts.CANCEL, callback_data=f"admin_tariff_view:{tariff_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_edit_tariff_trial_days(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Обрабатывает новое количество дней триала."""
    data = await state.get_data()
    tariff_id = data.get("tariff_id")

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await message.answer("Тариф не найден")
        await state.clear()
        return

    text = message.text.strip()

    if text == "-" or text == "0":
        trial_days = None
    else:
        try:
            trial_days = int(text)
            if trial_days < 1:
                raise ValueError
        except ValueError:
            await message.answer(
                "Введите корректное число дней (1 или больше).\n"
                "Для использования настройки по умолчанию введите <code>0</code> или <code>-</code>",
                parse_mode="HTML"
            )
            return

    tariff = await update_tariff(db, tariff, trial_duration_days=trial_days)
    await state.clear()

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    await message.answer(
        f"✅ Дни триала изменены!\n\n" + format_tariff_info(tariff, db_user.language, subs_count),
        reply_markup=get_tariff_view_keyboard(tariff, db_user.language),
        parse_mode="HTML"
    )


# ============ УДАЛЕНИЕ ТАРИФА ============

@admin_required
@error_handler
async def confirm_delete_tariff(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Запрашивает подтверждение удаления тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    subs_count = await get_tariff_subscriptions_count(db, tariff_id)

    warning = ""
    if subs_count > 0:
        warning = f"\n\n⚠️ <b>Внимание!</b> На этом тарифе {subs_count} подписок.\nОни будут отвязаны от тарифа."

    await callback.message.edit_text(
        f"🗑️ <b>Удаление тарифа</b>\n\n"
        f"Вы действительно хотите удалить тариф <b>{tariff.name}</b>?"
        f"{warning}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"admin_tariff_delete_confirm:{tariff_id}"),
                InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_tariff_view:{tariff_id}"),
            ]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_tariff_confirmed(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Удаляет тариф после подтверждения."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    tariff_name = tariff.name
    await delete_tariff(db, tariff)

    await callback.answer(f"Тариф «{tariff_name}» удален", show_alert=True)

    # Возвращаемся к списку
    tariffs_data = await get_tariffs_with_subscriptions_count(db, include_inactive=True)

    if not tariffs_data:
        await callback.message.edit_text(
            "📦 <b>Тарифы</b>\n\n"
            "Тарифы ещё не созданы.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать тариф", callback_data="admin_tariff_create")],
                [InlineKeyboardButton(text=texts.BACK, callback_data="admin_submenu_settings")]
            ]),
            parse_mode="HTML"
        )
        return

    total_pages = (len(tariffs_data) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
    page_data = tariffs_data[:ITEMS_PER_PAGE]

    await callback.message.edit_text(
        f"📦 <b>Тарифы</b>\n\n"
        f"✅ Тариф «{tariff_name}» удален\n\n"
        f"Всего: {len(tariffs_data)}",
        reply_markup=get_tariffs_list_keyboard(page_data, db_user.language, 0, total_pages),
        parse_mode="HTML"
    )


# ============ РЕДАКТИРОВАНИЕ СЕРВЕРОВ ============

@admin_required
@error_handler
async def start_edit_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    """Показывает меню выбора серверов для тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    squads, _ = await get_all_server_squads(db)

    if not squads:
        await callback.answer("Нет доступных серверов", show_alert=True)
        return

    current_squads = set(tariff.allowed_squads or [])

    buttons = []
    for squad in squads:
        is_selected = squad.squad_uuid in current_squads
        prefix = "✅" if is_selected else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix} {squad.display_name}",
                callback_data=f"admin_tariff_toggle_squad:{tariff_id}:{squad.squad_uuid}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Очистить все", callback_data=f"admin_tariff_clear_squads:{tariff_id}"),
        InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"admin_tariff_select_all_squads:{tariff_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=f"admin_tariff_view:{tariff_id}")
    ])

    selected_count = len(current_squads)

    await callback.message.edit_text(
        f"🌐 <b>Серверы для тарифа «{tariff.name}»</b>\n\n"
        f"Выбрано: {selected_count} из {len(squads)}\n\n"
        "Если не выбран ни один сервер - доступны все.\n"
        "Нажмите на сервер для выбора/отмены:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_squad(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает выбор сервера для тарифа."""
    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    squad_uuid = parts[2]

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    current_squads = set(tariff.allowed_squads or [])

    if squad_uuid in current_squads:
        current_squads.remove(squad_uuid)
    else:
        current_squads.add(squad_uuid)

    tariff = await update_tariff(db, tariff, allowed_squads=list(current_squads))

    # Перерисовываем меню
    squads, _ = await get_all_server_squads(db)
    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        is_selected = squad.squad_uuid in current_squads
        prefix = "✅" if is_selected else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix} {squad.display_name}",
                callback_data=f"admin_tariff_toggle_squad:{tariff_id}:{squad.squad_uuid}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Очистить все", callback_data=f"admin_tariff_clear_squads:{tariff_id}"),
        InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"admin_tariff_select_all_squads:{tariff_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=f"admin_tariff_view:{tariff_id}")
    ])

    try:
        await callback.message.edit_text(
            f"🌐 <b>Серверы для тарифа «{tariff.name}»</b>\n\n"
            f"Выбрано: {len(current_squads)} из {len(squads)}\n\n"
            "Если не выбран ни один сервер - доступны все.\n"
            "Нажмите на сервер для выбора/отмены:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


@admin_required
@error_handler
async def clear_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Очищает список серверов тарифа."""
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    tariff = await update_tariff(db, tariff, allowed_squads=[])
    await callback.answer("Все серверы очищены")

    # Перерисовываем меню
    squads, _ = await get_all_server_squads(db)
    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        buttons.append([
            InlineKeyboardButton(
                text=f"⬜ {squad.display_name}",
                callback_data=f"admin_tariff_toggle_squad:{tariff_id}:{squad.squad_uuid}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Очистить все", callback_data=f"admin_tariff_clear_squads:{tariff_id}"),
        InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"admin_tariff_select_all_squads:{tariff_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=f"admin_tariff_view:{tariff_id}")
    ])

    try:
        await callback.message.edit_text(
            f"🌐 <b>Серверы для тарифа «{tariff.name}»</b>\n\n"
            f"Выбрано: 0 из {len(squads)}\n\n"
            "Если не выбран ни один сервер - доступны все.\n"
            "Нажмите на сервер для выбора/отмены:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass


@admin_required
@error_handler
async def select_all_tariff_squads(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Выбирает все серверы для тарифа."""
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    squads, _ = await get_all_server_squads(db)
    all_uuids = [s.squad_uuid for s in squads]

    tariff = await update_tariff(db, tariff, allowed_squads=all_uuids)
    await callback.answer("Все серверы выбраны")

    texts = get_texts(db_user.language)

    buttons = []
    for squad in squads:
        buttons.append([
            InlineKeyboardButton(
                text=f"✅ {squad.display_name}",
                callback_data=f"admin_tariff_toggle_squad:{tariff_id}:{squad.squad_uuid}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Очистить все", callback_data=f"admin_tariff_clear_squads:{tariff_id}"),
        InlineKeyboardButton(text="✅ Выбрать все", callback_data=f"admin_tariff_select_all_squads:{tariff_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=f"admin_tariff_view:{tariff_id}")
    ])

    try:
        await callback.message.edit_text(
            f"🌐 <b>Серверы для тарифа «{tariff.name}»</b>\n\n"
            f"Выбрано: {len(squads)} из {len(squads)}\n\n"
            "Если не выбран ни один сервер - доступны все.\n"
            "Нажмите на сервер для выбора/отмены:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass


# ============ РЕДАКТИРОВАНИЕ ПРОМОГРУПП ============

@admin_required
@error_handler
async def start_edit_tariff_promo_groups(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Показывает меню выбора промогрупп для тарифа."""
    texts = get_texts(db_user.language)
    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    promo_groups_data = await get_promo_groups_with_counts(db)

    if not promo_groups_data:
        await callback.answer("Нет промогрупп", show_alert=True)
        return

    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    buttons = []
    for promo_group, _ in promo_groups_data:
        is_selected = promo_group.id in current_groups
        prefix = "✅" if is_selected else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix} {promo_group.name}",
                callback_data=f"admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Очистить все", callback_data=f"admin_tariff_clear_promo:{tariff_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=f"admin_tariff_view:{tariff_id}")
    ])

    selected_count = len(current_groups)

    await callback.message.edit_text(
        f"👥 <b>Промогруппы для тарифа «{tariff.name}»</b>\n\n"
        f"Выбрано: {selected_count}\n\n"
        "Если не выбрана ни одна группа - тариф доступен всем.\n"
        "Выберите группы, которым доступен этот тариф:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_tariff_promo_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Переключает выбор промогруппы для тарифа."""
    from app.database.crud.tariff import add_promo_group_to_tariff, remove_promo_group_from_tariff

    parts = callback.data.split(":")
    tariff_id = int(parts[1])
    promo_group_id = int(parts[2])

    tariff = await get_tariff_by_id(db, tariff_id)
    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    if promo_group_id in current_groups:
        await remove_promo_group_from_tariff(db, tariff, promo_group_id)
        current_groups.remove(promo_group_id)
    else:
        await add_promo_group_to_tariff(db, tariff, promo_group_id)
        current_groups.add(promo_group_id)

    # Обновляем тариф из БД
    tariff = await get_tariff_by_id(db, tariff_id)
    current_groups = {pg.id for pg in (tariff.allowed_promo_groups or [])}

    # Перерисовываем меню
    promo_groups_data = await get_promo_groups_with_counts(db)
    texts = get_texts(db_user.language)

    buttons = []
    for promo_group, _ in promo_groups_data:
        is_selected = promo_group.id in current_groups
        prefix = "✅" if is_selected else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{prefix} {promo_group.name}",
                callback_data=f"admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Очистить все", callback_data=f"admin_tariff_clear_promo:{tariff_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=f"admin_tariff_view:{tariff_id}")
    ])

    try:
        await callback.message.edit_text(
            f"👥 <b>Промогруппы для тарифа «{tariff.name}»</b>\n\n"
            f"Выбрано: {len(current_groups)}\n\n"
            "Если не выбрана ни одна группа - тариф доступен всем.\n"
            "Выберите группы, которым доступен этот тариф:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass

    await callback.answer()


@admin_required
@error_handler
async def clear_tariff_promo_groups(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    """Очищает список промогрупп тарифа."""
    from app.database.crud.tariff import set_tariff_promo_groups

    tariff_id = int(callback.data.split(":")[1])
    tariff = await get_tariff_by_id(db, tariff_id)

    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await set_tariff_promo_groups(db, tariff, [])
    await callback.answer("Все промогруппы очищены")

    # Перерисовываем меню
    promo_groups_data = await get_promo_groups_with_counts(db)
    texts = get_texts(db_user.language)

    buttons = []
    for promo_group, _ in promo_groups_data:
        buttons.append([
            InlineKeyboardButton(
                text=f"⬜ {promo_group.name}",
                callback_data=f"admin_tariff_toggle_promo:{tariff_id}:{promo_group.id}"
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Очистить все", callback_data=f"admin_tariff_clear_promo:{tariff_id}"),
    ])
    buttons.append([
        InlineKeyboardButton(text=texts.BACK, callback_data=f"admin_tariff_view:{tariff_id}")
    ])

    try:
        await callback.message.edit_text(
            f"👥 <b>Промогруппы для тарифа «{tariff.name}»</b>\n\n"
            f"Выбрано: 0\n\n"
            "Если не выбрана ни одна группа - тариф доступен всем.\n"
            "Выберите группы, которым доступен этот тариф:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass


def register_handlers(dp: Dispatcher):
    """Регистрирует обработчики для управления тарифами."""
    # Список тарифов
    dp.callback_query.register(show_tariffs_list, F.data == "admin_tariffs")
    dp.callback_query.register(show_tariffs_page, F.data.startswith("admin_tariffs_page:"))

    # Просмотр и переключение
    dp.callback_query.register(view_tariff, F.data.startswith("admin_tariff_view:"))
    dp.callback_query.register(toggle_tariff, F.data.startswith("admin_tariff_toggle:") & ~F.data.startswith("admin_tariff_toggle_trial:"))
    dp.callback_query.register(toggle_trial_tariff, F.data.startswith("admin_tariff_toggle_trial:"))

    # Создание тарифа
    dp.callback_query.register(start_create_tariff, F.data == "admin_tariff_create")
    dp.message.register(process_tariff_name, AdminStates.creating_tariff_name)
    dp.message.register(process_tariff_traffic, AdminStates.creating_tariff_traffic)
    dp.message.register(process_tariff_devices, AdminStates.creating_tariff_devices)
    dp.message.register(process_tariff_tier, AdminStates.creating_tariff_tier)
    dp.message.register(process_tariff_prices, AdminStates.creating_tariff_prices)

    # Редактирование названия
    dp.callback_query.register(start_edit_tariff_name, F.data.startswith("admin_tariff_edit_name:"))
    dp.message.register(process_edit_tariff_name, AdminStates.editing_tariff_name)

    # Редактирование описания
    dp.callback_query.register(start_edit_tariff_description, F.data.startswith("admin_tariff_edit_desc:"))
    dp.message.register(process_edit_tariff_description, AdminStates.editing_tariff_description)

    # Редактирование трафика
    dp.callback_query.register(start_edit_tariff_traffic, F.data.startswith("admin_tariff_edit_traffic:"))
    dp.message.register(process_edit_tariff_traffic, AdminStates.editing_tariff_traffic)

    # Редактирование устройств
    dp.callback_query.register(start_edit_tariff_devices, F.data.startswith("admin_tariff_edit_devices:"))
    dp.message.register(process_edit_tariff_devices, AdminStates.editing_tariff_devices)

    # Редактирование уровня
    dp.callback_query.register(start_edit_tariff_tier, F.data.startswith("admin_tariff_edit_tier:"))
    dp.message.register(process_edit_tariff_tier, AdminStates.editing_tariff_tier)

    # Редактирование цен
    dp.callback_query.register(start_edit_tariff_prices, F.data.startswith("admin_tariff_edit_prices:"))
    dp.message.register(process_edit_tariff_prices, AdminStates.editing_tariff_prices)

    # Редактирование цены за устройство
    dp.callback_query.register(start_edit_tariff_device_price, F.data.startswith("admin_tariff_edit_device_price:"))
    dp.message.register(process_edit_tariff_device_price, AdminStates.editing_tariff_device_price)

    # Редактирование дней триала
    dp.callback_query.register(start_edit_tariff_trial_days, F.data.startswith("admin_tariff_edit_trial_days:"))
    dp.message.register(process_edit_tariff_trial_days, AdminStates.editing_tariff_trial_days)

    # Удаление
    dp.callback_query.register(confirm_delete_tariff, F.data.startswith("admin_tariff_delete:"))
    dp.callback_query.register(delete_tariff_confirmed, F.data.startswith("admin_tariff_delete_confirm:"))

    # Редактирование серверов
    dp.callback_query.register(start_edit_tariff_squads, F.data.startswith("admin_tariff_edit_squads:"))
    dp.callback_query.register(toggle_tariff_squad, F.data.startswith("admin_tariff_toggle_squad:"))
    dp.callback_query.register(clear_tariff_squads, F.data.startswith("admin_tariff_clear_squads:"))
    dp.callback_query.register(select_all_tariff_squads, F.data.startswith("admin_tariff_select_all_squads:"))

    # Редактирование промогрупп
    dp.callback_query.register(start_edit_tariff_promo_groups, F.data.startswith("admin_tariff_edit_promo:"))
    dp.callback_query.register(toggle_tariff_promo_group, F.data.startswith("admin_tariff_toggle_promo:"))
    dp.callback_query.register(clear_tariff_promo_groups, F.data.startswith("admin_tariff_clear_promo:"))
