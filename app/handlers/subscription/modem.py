"""
Хендлеры для управления модемом в подписке.

Модем - это дополнительное устройство, которое можно подключить к подписке
за отдельную плату. При подключении увеличивается лимит устройств.
"""

import logging
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.keyboards.inline import get_back_keyboard, get_insufficient_balance_keyboard
from app.localization.texts import get_texts
from app.services.modem_service import (
    get_modem_service,
    ModemError,
    MODEM_WARNING_DAYS_CRITICAL,
    MODEM_WARNING_DAYS_INFO,
)
from app.utils.decorators import error_handler, modem_available

logger = logging.getLogger(__name__)


def get_modem_keyboard(language: str, modem_enabled: bool):
    """Клавиатура управления модемом."""
    texts = get_texts(language)
    keyboard = []

    if modem_enabled:
        keyboard.append([
            types.InlineKeyboardButton(
                text=texts.t("MODEM_DISABLE_BUTTON", "Отключить модем"),
                callback_data="modem_disable"
            )
        ])
    else:
        keyboard.append([
            types.InlineKeyboardButton(
                text=texts.t("MODEM_ENABLE_BUTTON", "Подключить модем"),
                callback_data="modem_enable"
            )
        ])

    keyboard.append([
        types.InlineKeyboardButton(
            text=texts.BACK,
            callback_data="subscription_settings"
        )
    ])

    return types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_modem_confirm_keyboard(language: str):
    """Клавиатура подтверждения подключения модема."""
    texts = get_texts(language)
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=texts.t("MODEM_CONFIRM_BUTTON", "Подтвердить подключение"),
                callback_data="modem_confirm"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=texts.CANCEL,
                callback_data="subscription_modem"
            )
        ]
    ])


@error_handler
@modem_available()
async def handle_modem_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает меню управления модемом."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    service = get_modem_service()

    modem_enabled = service.get_modem_enabled(subscription)
    modem_price = settings.get_modem_price_per_month()

    if modem_enabled:
        status_text = texts.t("MODEM_STATUS_ENABLED", "Подключен")
        info_text = texts.t(
            "MODEM_INFO_ENABLED",
            (
                "<b>Модем</b>\n\n"
                "Статус: {status}\n\n"
                "Модем подключен к вашей подписке.\n"
                "Ежемесячная плата: {price}\n\n"
                "При отключении модема возврат средств не производится."
            ),
        ).format(
            status=status_text,
            price=texts.format_price(modem_price),
        )
    else:
        status_text = texts.t("MODEM_STATUS_DISABLED", "Не подключен")
        info_text = texts.t(
            "MODEM_INFO_DISABLED",
            (
                "<b>Модем</b>\n\n"
                "Статус: {status}\n\n"
                "Подключите модем к вашей подписке.\n"
                "Ежемесячная плата: {price}\n\n"
                "При подключении модема будет добавлено дополнительное устройство."
            ),
        ).format(
            status=status_text,
            price=texts.format_price(modem_price),
        )

    await callback.message.edit_text(
        info_text,
        reply_markup=get_modem_keyboard(db_user.language, modem_enabled),
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
@modem_available(for_enable=True)
async def handle_modem_enable(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Обработчик подключения модема - показывает информацию о цене."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    service = get_modem_service()

    price_info = service.calculate_price(subscription)
    modem_price_per_month = settings.get_modem_price_per_month()

    has_funds, missing_kopeks = service.check_balance(db_user, price_info.final_price)

    if not has_funds:
        if price_info.has_discount:
            required_text = (
                f"{texts.format_price(price_info.final_price)} "
                f"(за {price_info.charged_months} мес, скидка {price_info.discount_percent}%)"
            )
        else:
            required_text = (
                f"{texts.format_price(price_info.final_price)} "
                f"(за {price_info.charged_months} мес)"
            )

        message_text = texts.t(
            "MODEM_INSUFFICIENT_FUNDS",
            (
                "<b>Недостаточно средств</b>\n\n"
                "Стоимость подключения модема: {required}\n"
                "На балансе: {balance}\n"
                "Не хватает: {missing}\n\n"
                "Выберите способ пополнения."
            ),
        ).format(
            required=required_text,
            balance=texts.format_price(db_user.balance_kopeks),
            missing=texts.format_price(missing_kopeks),
        )

        await callback.message.edit_text(
            message_text,
            reply_markup=get_insufficient_balance_keyboard(
                db_user.language,
                amount_kopeks=missing_kopeks,
            ),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    warning_level = service.get_period_warning_level(price_info.remaining_days)

    if warning_level == "critical":
        warning_text = texts.t(
            "MODEM_SHORT_PERIOD_WARNING",
            "\n<b>Внимание!</b> До окончания подписки осталось всего <b>{days} дн.</b>\n"
            "После продления подписки модем нужно будет оплатить заново!"
        ).format(days=price_info.remaining_days)
    elif warning_level == "info":
        warning_text = texts.t(
            "MODEM_PERIOD_NOTE",
            "\nДо окончания подписки: <b>{days} дн.</b>\n"
            "После продления модем нужно будет оплатить заново."
        ).format(days=price_info.remaining_days)
    else:
        warning_text = ""

    if price_info.has_discount:
        price_text = texts.t(
            "MODEM_PRICE_WITH_DISCOUNT",
            "Стоимость: <s>{base_price}</s> <b>{final_price}</b> (за {months} мес)\n"
            "Скидка {discount}%: -{discount_amount}"
        ).format(
            base_price=texts.format_price(price_info.base_price),
            final_price=texts.format_price(price_info.final_price),
            months=price_info.charged_months,
            discount=price_info.discount_percent,
            discount_amount=texts.format_price(price_info.discount_amount),
        )
    else:
        price_text = texts.t(
            "MODEM_PRICE_NO_DISCOUNT",
            "Стоимость: {price} (за {months} мес)"
        ).format(
            price=texts.format_price(price_info.final_price),
            months=price_info.charged_months,
        )

    confirm_text = texts.t(
        "MODEM_CONFIRM_ENABLE_BASE",
        (
            "<b>Подтверждение подключения модема</b>\n\n"
            "{price_text}\n\n"
            "При подключении модема:\n"
            "К подписке добавится дополнительное устройство\n"
            "Ежемесячная плата увеличится на {monthly_price}\n\n"
            "Подтвердить подключение?"
        ),
    ).format(
        price_text=price_text,
        monthly_price=texts.format_price(modem_price_per_month),
    )

    end_date_str = price_info.end_date.strftime("%d.%m.%Y")
    period_info = texts.t(
        "MODEM_PERIOD_INFO",
        "\nМодем действует до: <b>{end_date}</b> ({days} дн.)"
    ).format(end_date=end_date_str, days=price_info.remaining_days)

    confirm_text += period_info + warning_text

    await callback.message.edit_text(
        confirm_text,
        reply_markup=get_modem_confirm_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
@modem_available(for_enable=True)
async def handle_modem_confirm(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Подтверждение и активация модема."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    service = get_modem_service()

    result = await service.enable_modem(db, db_user, subscription)

    if not result.success:
        error_messages = {
            ModemError.INSUFFICIENT_FUNDS: texts.t(
                "MODEM_INSUFFICIENT_FUNDS_SHORT",
                "Недостаточно средств на балансе"
            ),
            ModemError.CHARGE_ERROR: texts.t(
                "PAYMENT_CHARGE_ERROR",
                "Ошибка списания средств"
            ),
            ModemError.UPDATE_ERROR: texts.ERROR,
        }

        error_text = error_messages.get(result.error, texts.ERROR)

        if result.error == ModemError.INSUFFICIENT_FUNDS:
            await callback.message.edit_text(
                error_text,
                reply_markup=get_back_keyboard(db_user.language, "modem_enable"),
                parse_mode="HTML"
            )
        else:
            await callback.answer(error_text, show_alert=True)
        return

    try:
        from app.services.admin_notification_service import AdminNotificationService
        notification_service = AdminNotificationService(callback.bot)
        await notification_service.send_subscription_update_notification(
            db, db_user, subscription, "modem", False, True, result.charged_amount
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления о подключении модема: {e}")

    success_text = texts.t(
        "MODEM_ENABLED_SUCCESS",
        (
            "<b>Модем успешно подключен!</b>\n\n"
            "Модем активирован\n"
            "Добавлено устройство для модема\n"
        ),
    )
    if result.charged_amount > 0:
        success_text += texts.t(
            "MODEM_CHARGED",
            "Списано: {amount}",
        ).format(amount=texts.format_price(result.charged_amount))

    await callback.message.edit_text(
        success_text,
        reply_markup=get_back_keyboard(db_user.language, "subscription_settings"),
        parse_mode="HTML"
    )
    await callback.answer()


@error_handler
@modem_available(for_disable=True)
async def handle_modem_disable(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Отключение модема."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    service = get_modem_service()

    result = await service.disable_modem(db, db_user, subscription)

    if not result.success:
        await callback.answer(texts.ERROR, show_alert=True)
        return

    try:
        from app.services.admin_notification_service import AdminNotificationService
        notification_service = AdminNotificationService(callback.bot)
        await notification_service.send_subscription_update_notification(
            db, db_user, subscription, "modem", True, False, 0
        )
    except Exception as e:
        logger.error(f"Ошибка отправки уведомления об отключении модема: {e}")

    success_text = texts.t(
        "MODEM_DISABLED_SUCCESS",
        (
            "<b>Модем отключен</b>\n\n"
            "Модем деактивирован\n"
            "Возврат средств не производится"
        ),
    )

    await callback.message.edit_text(
        success_text,
        reply_markup=get_back_keyboard(db_user.language, "subscription_settings"),
        parse_mode="HTML"
    )
    await callback.answer()


def register_modem_handlers(dp: Dispatcher):
    """Регистрация обработчиков модема."""
    dp.callback_query.register(
        handle_modem_menu,
        F.data == "subscription_modem"
    )

    dp.callback_query.register(
        handle_modem_enable,
        F.data == "modem_enable"
    )

    dp.callback_query.register(
        handle_modem_confirm,
        F.data == "modem_confirm"
    )

    dp.callback_query.register(
        handle_modem_disable,
        F.data == "modem_disable"
    )
