import logging
import math
import time
from typing import Iterable, List, Optional, Tuple

from aiogram import Dispatcher, F, types
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveService
from app.services.payment_service import PaymentService
from app.services.tribute_service import TributeService
from app.services.system_settings_service import bot_configuration_service
from app.states import BotConfigStates
from app.utils.decorators import admin_required, error_handler
from app.config import settings
from app.utils.currency_converter import currency_converter


logger = logging.getLogger(__name__)


CATEGORY_PAGE_SIZE = 10
SETTINGS_PAGE_SIZE = 8


CATEGORY_GROUP_DEFINITIONS: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "telegram_bot",
        "🤖 Telegram бот",
        ("SUPPORT", "ADMIN_NOTIFICATIONS", "ADMIN_REPORTS", "CHANNEL"),
    ),
    (
        "database",
        "🗄️ База данных",
        ("DATABASE", "POSTGRES", "SQLITE", "REDIS"),
    ),
    (
        "remnawave",
        "🌊 Remnawave API",
        ("REMNAWAVE",),
    ),
    (
        "subscriptions",
        "🪙 Подписки и тарифы",
        (
            "TRIAL",
            "PAID_SUBSCRIPTION",
            "SUBSCRIPTIONS_GLOBAL",
            "TRAFFIC",
            "PERIODS",
            "SUBSCRIPTION_PRICES",
            "TRAFFIC_PACKAGES",
            "DISCOUNTS",
            "REFERRAL",
            "AUTOPAY",
        ),
    ),
    (
        "payments",
        "💳 Платежные системы",
        ("TELEGRAM", "TRIBUTE", "YOOKASSA", "CRYPTOBOT", "MULENPAY", "PAL24", "PAYMENT"),
    ),
    (
        "interface",
        "🎨 Интерфейс и UX",
        ("INTERFACE_BRANDING", "INTERFACE_SUBSCRIPTION", "CONNECT_BUTTON", "HAPP", "SKIP"),
    ),
    (
        "monitoring",
        "📣 Мониторинг и уведомления",
        ("MONITORING", "NOTIFICATIONS"),
    ),
    (
        "operations",
        "🛠️ Статусы и обслуживание",
        ("SERVER", "MAINTENANCE"),
    ),
    (
        "localization",
        "🈯 Локализация",
        ("LOCALIZATION",),
    ),
    (
        "extras",
        "🧩 Дополнительные настройки",
        ("ADDITIONAL",),
    ),
    (
        "reliability",
        "💾 Бекапы и обновления",
        ("BACKUP", "VERSION"),
    ),
    (
        "technical",
        "🧰 Технические",
        ("LOG", "WEBHOOK", "DEBUG"),
    ),
)

CATEGORY_FALLBACK_KEY = "other"
CATEGORY_FALLBACK_TITLE = "📦 Прочие настройки"


async def _store_setting_context(
    state: FSMContext,
    *,
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
) -> None:
    await state.update_data(
        setting_key=key,
        setting_group_key=group_key,
        setting_category_page=category_page,
        setting_settings_page=settings_page,
        botcfg_origin="bot_config",
        botcfg_timestamp=time.time(),
    )


class BotConfigInputFilter(BaseFilter):
    def __init__(self, timeout: float = 300.0) -> None:
        self.timeout = timeout

    async def __call__(
        self,
        message: types.Message,
        state: FSMContext,
    ) -> bool:
        if not message.text or message.text.startswith("/"):
            return False

        if message.chat.type != "private":
            return False

        data = await state.get_data()

        if data.get("botcfg_origin") != "bot_config":
            return False

        if not data.get("setting_key"):
            return False

        timestamp = data.get("botcfg_timestamp")
        if timestamp is None:
            return True

        try:
            return (time.time() - float(timestamp)) <= self.timeout
        except (TypeError, ValueError):
            return False


def _chunk(buttons: Iterable[types.InlineKeyboardButton], size: int) -> Iterable[List[types.InlineKeyboardButton]]:
    buttons_list = list(buttons)
    for index in range(0, len(buttons_list), size):
        yield buttons_list[index : index + size]


def _parse_category_payload(payload: str) -> Tuple[str, str, int, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    category_key = parts[2] if len(parts) > 2 else ""

    def _safe_int(value: str, default: int = 1) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return default

    category_page = _safe_int(parts[3]) if len(parts) > 3 else 1
    settings_page = _safe_int(parts[4]) if len(parts) > 4 else 1
    return group_key, category_key, category_page, settings_page


def _parse_group_payload(payload: str) -> Tuple[str, int]:
    parts = payload.split(":")
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        page = max(1, int(parts[2]))
    except (IndexError, ValueError):
        page = 1
    return group_key, page


def _get_grouped_categories() -> List[Tuple[str, str, List[Tuple[str, str, int]]]]:
    categories = bot_configuration_service.get_categories()
    categories_map = {key: (label, count) for key, label, count in categories}
    used: set[str] = set()
    grouped: List[Tuple[str, str, List[Tuple[str, str, int]]]] = []

    for group_key, title, category_keys in CATEGORY_GROUP_DEFINITIONS:
        items: List[Tuple[str, str, int]] = []
        for category_key in category_keys:
            if category_key in categories_map:
                label, count = categories_map[category_key]
                items.append((category_key, label, count))
                used.add(category_key)
        if items:
            grouped.append((group_key, title, items))

    remaining = [
        (key, label, count)
        for key, (label, count) in categories_map.items()
        if key not in used
    ]

    if remaining:
        remaining.sort(key=lambda item: item[1])
        grouped.append((CATEGORY_FALLBACK_KEY, CATEGORY_FALLBACK_TITLE, remaining))

    return grouped


def _build_groups_keyboard() -> types.InlineKeyboardMarkup:
    grouped = _get_grouped_categories()
    rows: list[list[types.InlineKeyboardButton]] = []

    for group_key, title, items in grouped:
        total = sum(count for _, _, count in items)
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=f"{title} ({total})",
                    callback_data=f"botcfg_group:{group_key}:1",
                )
            ]
        )

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="admin_submenu_settings",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_categories_keyboard(
    group_key: str,
    group_title: str,
    categories: List[Tuple[str, str, int]],
    page: int = 1,
) -> types.InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(categories) / CATEGORY_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * CATEGORY_PAGE_SIZE
    end = start + CATEGORY_PAGE_SIZE
    sliced = categories[start:end]

    rows: list[list[types.InlineKeyboardButton]] = []
    rows.append(
        [
            types.InlineKeyboardButton(
                text=f"— {group_title} —",
                callback_data="botcfg_group:noop",
            )
        ]
    )

    buttons: List[types.InlineKeyboardButton] = []
    for category_key, label, count in sliced:
        button_text = f"{label} ({count})"
        buttons.append(
            types.InlineKeyboardButton(
                text=button_text,
                callback_data=f"botcfg_cat:{group_key}:{category_key}:{page}:1",
            )
        )

    for chunk in _chunk(buttons, 2):
        rows.append(list(chunk))

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=f"botcfg_group:{group_key}:{page - 1}",
                )
            )
        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data="botcfg_group:noop",
            )
        )
        if page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="➡️",
                    callback_data=f"botcfg_group:{group_key}:{page + 1}",
                )
            )
        rows.append(nav_row)

    rows.append(
        [
            types.InlineKeyboardButton(
                text="⬅️ К разделам",
                callback_data="admin_bot_config",
            )
        ]
    )

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_settings_keyboard(
    category_key: str,
    group_key: str,
    category_page: int,
    language: str,
    page: int = 1,
) -> types.InlineKeyboardMarkup:
    definitions = bot_configuration_service.get_settings_for_category(category_key)
    total_pages = max(1, math.ceil(len(definitions) / SETTINGS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * SETTINGS_PAGE_SIZE
    end = start + SETTINGS_PAGE_SIZE
    sliced = definitions[start:end]

    rows: list[list[types.InlineKeyboardButton]] = []

    if category_key == "REMNAWAVE":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🔌 Проверить подключение",
                    callback_data=(
                        f"botcfg_test_remnawave:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )
    elif category_key == "YOOKASSA":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🧪 Тест YooKassa · 10 ₽",
                    callback_data=(
                        f"botcfg_test_payment:yookassa:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )
    elif category_key == "TRIBUTE":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🧪 Тест Tribute",
                    callback_data=(
                        f"botcfg_test_payment:tribute:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )
    elif category_key == "MULENPAY":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🧪 Тест MulenPay · 1 ₽",
                    callback_data=(
                        f"botcfg_test_payment:mulenpay:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )
    elif category_key == "PAL24":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🧪 Тест Pal24 · 10 ₽",
                    callback_data=(
                        f"botcfg_test_payment:pal24:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )
    elif category_key == "TELEGRAM":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🧪 Тест Stars · 1 ⭐",
                    callback_data=(
                        f"botcfg_test_payment:telegram_stars:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )
    elif category_key == "CRYPTOBOT":
        rows.append(
            [
                types.InlineKeyboardButton(
                    text="🧪 Тест CryptoBot · 100 ₽",
                    callback_data=(
                        f"botcfg_test_payment:cryptobot:{group_key}:{category_key}:{category_page}:{page}"
                    ),
                )
            ]
        )

    for definition in sliced:
        value_preview = bot_configuration_service.format_value_for_list(definition.key)
        button_text = f"{definition.display_name} · {value_preview}"
        if len(button_text) > 64:
            button_text = button_text[:63] + "…"
        callback_token = bot_configuration_service.get_callback_token(definition.key)
        rows.append(
            [
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"botcfg_setting:{group_key}:{category_page}:{page}:{callback_token}"
                    ),
                )
            ]
        )

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="⬅️",
                    callback_data=(
                        f"botcfg_cat:{group_key}:{category_key}:{category_page}:{page - 1}"
                    ),
                )
            )
        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{page}/{total_pages}", callback_data="botcfg_cat_page:noop"
            )
        )
        if page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text="➡️",
                    callback_data=(
                        f"botcfg_cat:{group_key}:{category_key}:{category_page}:{page + 1}"
                    ),
                )
            )
        rows.append(nav_row)

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ К категориям",
            callback_data=f"botcfg_group:{group_key}:{category_page}",
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _build_setting_keyboard(
    key: str,
    group_key: str,
    category_page: int,
    settings_page: int,
) -> types.InlineKeyboardMarkup:
    definition = bot_configuration_service.get_definition(key)
    rows: list[list[types.InlineKeyboardButton]] = []
    callback_token = bot_configuration_service.get_callback_token(key)

    choice_options = bot_configuration_service.get_choice_options(key)
    if choice_options:
        current_value = bot_configuration_service.get_current_value(key)
        choice_buttons: list[types.InlineKeyboardButton] = []
        for option in choice_options:
            choice_token = bot_configuration_service.get_choice_token(key, option.value)
            if choice_token is None:
                continue
            button_text = option.label
            if current_value == option.value and not button_text.startswith("✅"):
                button_text = f"✅ {button_text}"
            choice_buttons.append(
                types.InlineKeyboardButton(
                    text=button_text,
                    callback_data=(
                        f"botcfg_choice:{group_key}:{category_page}:{settings_page}:{callback_token}:{choice_token}"
                    ),
                )
            )

        for chunk in _chunk(choice_buttons, 2):
            rows.append(list(chunk))

    if definition.python_type is bool:
        rows.append([
            types.InlineKeyboardButton(
                text="🔁 Переключить",
                callback_data=(
                    f"botcfg_toggle:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=(
                f"botcfg_edit:{group_key}:{category_page}:{settings_page}:{callback_token}"
            ),
        )
    ])

    if bot_configuration_service.has_override(key):
        rows.append([
            types.InlineKeyboardButton(
                text="♻️ Сбросить",
                callback_data=(
                    f"botcfg_reset:{group_key}:{category_page}:{settings_page}:{callback_token}"
                ),
            )
        ])

    rows.append([
        types.InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=(
                f"botcfg_cat:{group_key}:{definition.category_key}:{category_page}:{settings_page}"
            ),
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _render_setting_text(key: str) -> str:
    summary = bot_configuration_service.get_setting_summary(key)

    lines = [
        "🧩 <b>Настройка</b>",
        f"<b>Название:</b> {summary['name']}",
        f"<b>Ключ:</b> <code>{summary['key']}</code>",
        f"<b>Категория:</b> {summary['category_label']}",
        f"<b>Тип:</b> {summary['type']}",
        f"<b>Текущее значение:</b> {summary['current']}",
        f"<b>Значение по умолчанию:</b> {summary['original']}",
        f"<b>Переопределено в БД:</b> {'✅ Да' if summary['has_override'] else '❌ Нет'}",
    ]

    choices = bot_configuration_service.get_choice_options(key)
    if choices:
        current_raw = bot_configuration_service.get_current_value(key)
        lines.append("")
        lines.append("<b>Доступные значения:</b>")
        for option in choices:
            marker = "✅" if current_raw == option.value else "•"
            value_display = bot_configuration_service.format_value(option.value)
            description = option.description or ""
            if description:
                lines.append(
                    f"{marker} {option.label} — <code>{value_display}</code>\n   {description}"
                )
            else:
                lines.append(f"{marker} {option.label} — <code>{value_display}</code>")

    return "\n".join(lines)


@admin_required
@error_handler
async def show_bot_config_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    keyboard = _build_groups_keyboard()
    await callback.message.edit_text(
        "🧩 <b>Конфигурация бота</b>\n\nВыберите раздел настроек:",
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_group(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    group_key, page = _parse_group_payload(callback.data)
    grouped = _get_grouped_categories()
    group_lookup = {key: (title, items) for key, title, items in grouped}

    if group_key not in group_lookup:
        await callback.answer("Эта группа больше недоступна", show_alert=True)
        return

    group_title, items = group_lookup[group_key]
    keyboard = _build_categories_keyboard(group_key, group_title, items, page)
    await callback.message.edit_text(
        f"🧩 <b>{group_title}</b>\n\nВыберите категорию настроек:",
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def show_bot_config_category(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    group_key, category_key, category_page, settings_page = _parse_category_payload(
        callback.data
    )
    definitions = bot_configuration_service.get_settings_for_category(category_key)

    if not definitions:
        await callback.answer("В этой категории пока нет настроек", show_alert=True)
        return

    category_label = definitions[0].category_label
    keyboard = _build_settings_keyboard(
        category_key,
        group_key,
        category_page,
        db_user.language,
        settings_page,
    )
    await callback.message.edit_text(
        f"🧩 <b>{category_label}</b>\n\nВыберите настройку для просмотра:",
        reply_markup=keyboard,
    )
    await callback.answer()


@admin_required
@error_handler
async def test_remnawave_connection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    category_key = parts[2] if len(parts) > 2 else "REMNAWAVE"

    try:
        category_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        category_page = 1

    try:
        settings_page = max(1, int(parts[4])) if len(parts) > 4 else 1
    except ValueError:
        settings_page = 1

    service = RemnaWaveService()
    result = await service.test_api_connection()

    status = result.get("status")
    message: str

    if status == "connected":
        message = "✅ Подключение успешно"
    elif status == "not_configured":
        message = f"⚠️ {result.get('message', 'RemnaWave API не настроен')}"
    else:
        base_message = result.get("message", "Ошибка подключения")
        status_code = result.get("status_code")
        if status_code:
            message = f"❌ {base_message} (HTTP {status_code})"
        else:
            message = f"❌ {base_message}"

    definitions = bot_configuration_service.get_settings_for_category(category_key)
    if definitions:
        keyboard = _build_settings_keyboard(
            category_key,
            group_key,
            category_page,
            db_user.language,
            settings_page,
        )
        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            # ignore inability to refresh markup, main result shown in alert
            pass

    await callback.answer(message, show_alert=True)


@admin_required
@error_handler
async def test_payment_system(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    parts = callback.data.split(":", 6)
    provider = parts[1] if len(parts) > 1 else ""
    group_key = parts[2] if len(parts) > 2 else CATEGORY_FALLBACK_KEY
    category_key = parts[3] if len(parts) > 3 else ""

    try:
        category_page = max(1, int(parts[4])) if len(parts) > 4 else 1
    except ValueError:
        category_page = 1

    try:
        settings_page = max(1, int(parts[5])) if len(parts) > 5 else 1
    except ValueError:
        settings_page = 1

    payment_service = PaymentService(callback.bot)

    def _refresh_keyboard() -> None:
        definitions = bot_configuration_service.get_settings_for_category(category_key)
        if not definitions:
            return
        keyboard = _build_settings_keyboard(
            category_key,
            group_key,
            category_page,
            db_user.language,
            settings_page,
        )
        try:
            callback.message.edit_reply_markup(reply_markup=keyboard)
        except Exception:
            pass

    def _url_button(text: str, url: str) -> types.InlineKeyboardMarkup:
        return types.InlineKeyboardMarkup(
            inline_keyboard=[[types.InlineKeyboardButton(text=text, url=url)]]
        )

    response_text: Optional[str] = None
    response_markup: Optional[types.InlineKeyboardMarkup] = None
    alert_message: Optional[str] = None

    if provider == "yookassa":
        if not settings.is_yookassa_enabled():
            alert_message = "YooKassa отключена в конфигурации"
        else:
            amount_kopeks = 1_000
            description = settings.get_balance_payment_description(amount_kopeks)
            metadata = {
                "test_payment": True,
                "initiator": "admin_bot_config",
                "provider": "yookassa",
            }
            result = await payment_service.create_yookassa_payment(
                db,
                db_user.id,
                amount_kopeks,
                description,
                metadata=metadata,
            )
            if result:
                confirmation_url = result.get("confirmation_url")
                status = result.get("status", "unknown")
                payment_id = result.get("yookassa_payment_id", "—")
                response_text = (
                    "🧪 <b>Тестовый платеж YooKassa</b>\n\n"
                    f"Сумма: {settings.format_price(amount_kopeks)}\n"
                    f"Статус: {status}\n"
                    f"ID: {payment_id}"
                )
                if confirmation_url:
                    response_markup = _url_button("💳 Открыть платеж", confirmation_url)
                    alert_message = "Ссылка на YooKassa платеж отправлена"
                else:
                    alert_message = "Платеж создан без ссылки подтверждения"
            else:
                alert_message = "Не удалось создать тестовый платеж YooKassa"
    elif provider == "tribute":
        if not settings.TRIBUTE_ENABLED:
            alert_message = "Tribute отключен в конфигурации"
        else:
            tribute_service = TributeService(callback.bot)
            try:
                payment_url = await tribute_service.create_payment_link(
                    user_id=db_user.telegram_id,
                    amount_kopeks=0,
                    description="Тестовое пополнение баланса через Tribute",
                )
            except Exception as error:
                logger.error("Ошибка создания тестового платежа Tribute: %s", error)
                payment_url = None

            if payment_url:
                response_text = (
                    "🧪 <b>Тестовое пополнение через Tribute</b>\n\n"
                    "Кнопка работает так же, как в разделе пополнения.\n"
                    "После оплаты проверьте вебхуки и уведомления."
                )
                response_markup = _url_button("💳 Перейти к оплате", payment_url)
                alert_message = "Ссылка для теста Tribute отправлена"
            else:
                alert_message = "Не удалось создать ссылку Tribute"
    elif provider == "mulenpay":
        if not settings.is_mulenpay_enabled():
            alert_message = "MulenPay отключен в конфигурации"
        else:
            requested_amount = 100
            description = settings.get_balance_payment_description(requested_amount)
            result = await payment_service.create_mulenpay_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=requested_amount,
                description=description,
                language=db_user.language,
            )
            if not result and requested_amount < settings.MULENPAY_MIN_AMOUNT_KOPEKS:
                fallback_amount = settings.MULENPAY_MIN_AMOUNT_KOPEKS
                fallback_description = settings.get_balance_payment_description(
                    fallback_amount
                )
                result = await payment_service.create_mulenpay_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=fallback_amount,
                    description=fallback_description,
                    language=db_user.language,
                )
                if result:
                    response_text = (
                        "⚠️ <b>Минимальная сумма MulenPay</b>\n\n"
                        f"Запрошено: {settings.format_price(requested_amount)}\n"
                        f"Создан платеж на {settings.format_price(fallback_amount)}"
                    )
            if result:
                payment_url = result.get("payment_url")
                actual_amount = result.get("amount_kopeks", requested_amount)
                if not response_text:
                    response_text = (
                        "🧪 <b>Тестовый платеж MulenPay</b>\n\n"
                        f"Сумма: {settings.format_price(actual_amount)}"
                    )
                if payment_url:
                    response_markup = _url_button("💳 Открыть платеж", payment_url)
                alert_message = "Ссылка для теста MulenPay отправлена"
            elif not alert_message:
                alert_message = "Не удалось создать тестовый платеж MulenPay"
    elif provider == "pal24":
        if not settings.is_pal24_enabled():
            alert_message = "Pal24 отключен в конфигурации"
        else:
            requested_amount = 1_000
            description = settings.get_balance_payment_description(requested_amount)
            result = await payment_service.create_pal24_payment(
                db=db,
                user_id=db_user.id,
                amount_kopeks=requested_amount,
                description=description,
                language=db_user.language,
            )
            if not result and requested_amount < settings.PAL24_MIN_AMOUNT_KOPEKS:
                fallback_amount = settings.PAL24_MIN_AMOUNT_KOPEKS
                fallback_description = settings.get_balance_payment_description(
                    fallback_amount
                )
                result = await payment_service.create_pal24_payment(
                    db=db,
                    user_id=db_user.id,
                    amount_kopeks=fallback_amount,
                    description=fallback_description,
                    language=db_user.language,
                )
                if result:
                    response_text = (
                        "⚠️ <b>Минимальная сумма Pal24</b>\n\n"
                        f"Запрошено: {settings.format_price(requested_amount)}\n"
                        f"Создан платеж на {settings.format_price(fallback_amount)}"
                    )
            if result:
                link_url = (
                    result.get("link_url")
                    or result.get("link_page_url")
                )
                actual_amount = result.get("amount_kopeks", requested_amount)
                if not response_text:
                    response_text = (
                        "🧪 <b>Тестовый платеж Pal24</b>\n\n"
                        f"Сумма: {settings.format_price(actual_amount)}"
                    )
                if link_url:
                    response_markup = _url_button("💳 Открыть платеж", link_url)
                alert_message = "Ссылка для теста Pal24 отправлена"
            elif not alert_message:
                alert_message = "Не удалось создать тестовый платеж Pal24"
    elif provider == "telegram_stars":
        if not payment_service.stars_service:
            alert_message = "Сервис Telegram Stars недоступен"
        else:
            rubles_for_star = settings.stars_to_rubles(1)
            amount_kopeks = max(1, int(round(rubles_for_star * 100)))
            payload = f"admin_test_stars_{db_user.id}_{int(time.time())}"
            description = "Тестовое пополнение через Telegram Stars"
            try:
                invoice_link = await payment_service.create_stars_invoice(
                    amount_kopeks=amount_kopeks,
                    description=description,
                    payload=payload,
                )
            except Exception as error:
                logger.error("Ошибка создания Stars invoice: %s", error)
                invoice_link = None

            if invoice_link:
                stars_amount = settings.rubles_to_stars(amount_kopeks / 100)
                response_text = (
                    "🧪 <b>Тестовый платеж Telegram Stars</b>\n\n"
                    f"Сумма: ≈{stars_amount} ⭐ ({settings.format_price(amount_kopeks)})"
                )
                response_markup = _url_button("⭐ Открыть счет", invoice_link)
                alert_message = "Ссылка на Stars invoice отправлена"
            else:
                alert_message = "Не удалось создать Stars invoice"
    elif provider == "cryptobot":
        if not payment_service.cryptobot_service:
            alert_message = "CryptoBot недоступен"
        else:
            rub_amount = 100.0
            try:
                amount_usd = await currency_converter.rub_to_usd(rub_amount)
            except Exception as error:
                logger.error("Ошибка конвертации RUB в USD: %s", error)
                amount_usd = rub_amount / 100  # запасное значение ~1 USD
            amount_usd = round(amount_usd, 2)
            payload = f"admin_test_cryptobot_{db_user.id}_{int(time.time())}"
            description = "Тестовое пополнение через CryptoBot"
            result = await payment_service.create_cryptobot_payment(
                db=db,
                user_id=db_user.id,
                amount_usd=amount_usd,
                description=description,
                payload=payload,
            )
            if result:
                invoice_url = (
                    result.get("web_app_invoice_url")
                    or result.get("bot_invoice_url")
                    or result.get("mini_app_invoice_url")
                )
                response_text = (
                    "🧪 <b>Тестовый платеж CryptoBot</b>\n\n"
                    f"Сумма: 100 ₽ (≈{amount_usd} USD)"
                )
                if invoice_url:
                    response_markup = _url_button("💳 Открыть счет", invoice_url)
                alert_message = "Ссылка для теста CryptoBot отправлена"
            else:
                alert_message = "Не удалось создать счет CryptoBot"
    else:
        await callback.answer("Неизвестный провайдер", show_alert=True)
        return

    if response_text:
        await callback.message.answer(
            response_text,
            reply_markup=response_markup,
            parse_mode="HTML",
        )
    elif response_markup:
        await callback.message.answer(
            "🧪 Тестовый платеж",
            reply_markup=response_markup,
        )

    _refresh_keyboard()

    if alert_message:
        await callback.answer(alert_message, show_alert=True)
    else:
        await callback.answer("Готово")


@admin_required
@error_handler
async def show_bot_config_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer()


@admin_required
@error_handler
async def start_edit_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    definition = bot_configuration_service.get_definition(key)

    summary = bot_configuration_service.get_setting_summary(key)
    texts = get_texts(db_user.language)

    instructions = [
        "✏️ <b>Редактирование настройки</b>",
        f"Название: {summary['name']}",
        f"Ключ: <code>{summary['key']}</code>",
        f"Тип: {summary['type']}",
        f"Текущее значение: {summary['current']}",
        "\nОтправьте новое значение сообщением.",
    ]

    if definition.is_optional:
        instructions.append("Отправьте 'none' или оставьте пустым для сброса на значение по умолчанию.")

    instructions.append("Для отмены отправьте 'cancel'.")

    await callback.message.edit_text(
        "\n".join(instructions),
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text=texts.BACK,
                        callback_data=(
                            f"botcfg_setting:{group_key}:{category_page}:{settings_page}:{token}"
                        ),
                    )
                ]
            ]
        ),
    )

    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await state.set_state(BotConfigStates.waiting_for_value)
    await callback.answer()


@admin_required
@error_handler
async def handle_edit_setting(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()
    key = data.get("setting_key")
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
    category_page = data.get("setting_category_page", 1)
    settings_page = data.get("setting_settings_page", 1)

    if not key:
        await message.answer("Не удалось определить редактируемую настройку. Попробуйте снова.")
        await state.clear()
        return

    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    await bot_configuration_service.set_value(db, key, value)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard)
    await state.clear()
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )


@admin_required
@error_handler
async def handle_direct_setting_input(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    data = await state.get_data()

    key = data.get("setting_key")
    group_key = data.get("setting_group_key", CATEGORY_FALLBACK_KEY)
    category_page = int(data.get("setting_category_page", 1) or 1)
    settings_page = int(data.get("setting_settings_page", 1) or 1)

    if not key:
        return

    try:
        value = bot_configuration_service.parse_user_value(key, message.text or "")
    except ValueError as error:
        await message.answer(f"⚠️ {error}")
        return

    await bot_configuration_service.set_value(db, key, value)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await message.answer("✅ Настройка обновлена")
    await message.answer(text, reply_markup=keyboard)

    await state.clear()
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )


@admin_required
@error_handler
async def reset_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    await bot_configuration_service.reset_value(db, key)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Сброшено к значению по умолчанию")


@admin_required
@error_handler
async def toggle_setting(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 4)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return
    current = bot_configuration_service.get_current_value(key)
    new_value = not bool(current)
    await bot_configuration_service.set_value(db, key, new_value)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Обновлено")


@admin_required
@error_handler
async def apply_setting_choice(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext,
):
    parts = callback.data.split(":", 5)
    group_key = parts[1] if len(parts) > 1 else CATEGORY_FALLBACK_KEY
    try:
        category_page = max(1, int(parts[2])) if len(parts) > 2 else 1
    except ValueError:
        category_page = 1
    try:
        settings_page = max(1, int(parts[3])) if len(parts) > 3 else 1
    except ValueError:
        settings_page = 1
    token = parts[4] if len(parts) > 4 else ""
    choice_token = parts[5] if len(parts) > 5 else ""

    try:
        key = bot_configuration_service.resolve_callback_token(token)
    except KeyError:
        await callback.answer("Эта настройка больше недоступна", show_alert=True)
        return

    try:
        value = bot_configuration_service.resolve_choice_token(key, choice_token)
    except KeyError:
        await callback.answer("Это значение больше недоступно", show_alert=True)
        return

    await bot_configuration_service.set_value(db, key, value)
    await db.commit()

    text = _render_setting_text(key)
    keyboard = _build_setting_keyboard(key, group_key, category_page, settings_page)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _store_setting_context(
        state,
        key=key,
        group_key=group_key,
        category_page=category_page,
        settings_page=settings_page,
    )
    await callback.answer("Значение обновлено")


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(
        show_bot_config_menu,
        F.data == "admin_bot_config",
    )
    dp.callback_query.register(
        show_bot_config_group,
        F.data.startswith("botcfg_group:") & (~F.data.endswith(":noop")),
    )
    dp.callback_query.register(
        show_bot_config_category,
        F.data.startswith("botcfg_cat:"),
    )
    dp.callback_query.register(
        test_remnawave_connection,
        F.data.startswith("botcfg_test_remnawave:"),
    )
    dp.callback_query.register(
        test_payment_system,
        F.data.startswith("botcfg_test_payment:"),
    )
    dp.callback_query.register(
        show_bot_config_setting,
        F.data.startswith("botcfg_setting:"),
    )
    dp.callback_query.register(
        start_edit_setting,
        F.data.startswith("botcfg_edit:"),
    )
    dp.callback_query.register(
        reset_setting,
        F.data.startswith("botcfg_reset:"),
    )
    dp.callback_query.register(
        toggle_setting,
        F.data.startswith("botcfg_toggle:"),
    )
    dp.callback_query.register(
        apply_setting_choice,
        F.data.startswith("botcfg_choice:"),
    )
    dp.message.register(
        handle_direct_setting_input,
        StateFilter(None),
        F.text,
        BotConfigInputFilter(),
    )
    dp.message.register(
        handle_edit_setting,
        BotConfigStates.waiting_for_value,
    )

