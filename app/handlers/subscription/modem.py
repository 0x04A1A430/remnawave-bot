import logging
from datetime import datetime
from aiogram import Dispatcher, types, F
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User, TransactionType
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.keyboards.inline import get_back_keyboard, get_insufficient_balance_keyboard
from app.localization.texts import get_texts
from app.services.subscription_service import SubscriptionService
from app.utils.pricing_utils import get_remaining_months, calculate_prorated_price

logger = logging.getLogger(__name__)


def get_modem_keyboard(language: str, modem_enabled: bool):
    """Клавиатура управления модемом."""
    texts = get_texts(language)
    keyboard = []

    if modem_enabled:
        keyboard.append([
            types.InlineKeyboardButton(
                text=texts.t("MODEM_DISABLE_BUTTON", "❌ Отключить модем"),
                callback_data="modem_disable"
            )
        ])
    else:
        keyboard.append([
            types.InlineKeyboardButton(
                text=texts.t("MODEM_ENABLE_BUTTON", "✅ Подключить модем"),
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


def get_modem_confirm_keyboard(language: str, price: int):
    """Клавиатура подтверждения подключения модема."""
    texts = get_texts(language)
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=texts.t("MODEM_CONFIRM_BUTTON", "✅ Подтвердить подключение"),
                callback_data=f"modem_confirm_{price}"
            )
        ],
        [
            types.InlineKeyboardButton(
                text=texts.CANCEL,
                callback_data="subscription_modem"
            )
        ]
    ])


async def handle_modem_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает меню управления модемом."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t("MODEM_PAID_ONLY", "⚠️ Модем доступен только для платных подписок"),
            show_alert=True,
        )
        return

    if not settings.is_modem_enabled():
        await callback.answer(
            texts.t("MODEM_DISABLED", "⚠️ Функция модема отключена"),
            show_alert=True,
        )
        return

    modem_enabled = getattr(subscription, 'modem_enabled', False) or False
    modem_price = settings.get_modem_price_per_month()

    if modem_enabled:
        status_text = texts.t("MODEM_STATUS_ENABLED", "✅ Подключен")
        info_text = texts.t(
            "MODEM_INFO_ENABLED",
            (
                "📡 <b>Модем</b>\n\n"
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
        status_text = texts.t("MODEM_STATUS_DISABLED", "❌ Не подключен")
        info_text = texts.t(
            "MODEM_INFO_DISABLED",
            (
                "📡 <b>Модем</b>\n\n"
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


async def handle_modem_enable(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Обработчик подключения модема."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t("MODEM_PAID_ONLY", "⚠️ Модем доступен только для платных подписок"),
            show_alert=True,
        )
        return

    if not settings.is_modem_enabled():
        await callback.answer(
            texts.t("MODEM_DISABLED", "⚠️ Функция модема отключена"),
            show_alert=True,
        )
        return

    modem_enabled = getattr(subscription, 'modem_enabled', False) or False
    if modem_enabled:
        await callback.answer(
            texts.t("MODEM_ALREADY_ENABLED", "ℹ️ Модем уже подключен"),
            show_alert=True,
        )
        return

    modem_price_per_month = settings.get_modem_price_per_month()
    base_price, charged_months = calculate_prorated_price(
        modem_price_per_month,
        subscription.end_date,
    )

    # Рассчитываем оставшиеся дни до конца подписки
    now = datetime.utcnow()
    remaining_days = max(0, (subscription.end_date - now).days)
    end_date_str = subscription.end_date.strftime("%d.%m.%Y")

    # Применяем скидку за длительный срок
    discount_percent = settings.get_modem_period_discount(charged_months)
    if discount_percent > 0:
        discount_amount = base_price * discount_percent // 100
        price = base_price - discount_amount
    else:
        discount_amount = 0
        price = base_price

    if price > 0 and db_user.balance_kopeks < price:
        missing_kopeks = price - db_user.balance_kopeks
        if discount_percent > 0:
            required_text = f"{texts.format_price(price)} (за {charged_months} мес, скидка {discount_percent}%)"
        else:
            required_text = f"{texts.format_price(price)} (за {charged_months} мес)"
        message_text = texts.t(
            "MODEM_INSUFFICIENT_FUNDS",
            (
                "⚠️ <b>Недостаточно средств</b>\n\n"
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

    # Формируем предупреждение о сроке действия
    if remaining_days <= 7:
        warning_text = texts.t(
            "MODEM_SHORT_PERIOD_WARNING",
            "\n⚠️ <b>Внимание!</b> До окончания подписки осталось всего <b>{days} дн.</b>\n"
            "После продления подписки модем нужно будет оплатить заново!"
        ).format(days=remaining_days)
    elif remaining_days <= 30:
        warning_text = texts.t(
            "MODEM_PERIOD_NOTE",
            "\nℹ️ До окончания подписки: <b>{days} дн.</b>\n"
            "После продления модем нужно будет оплатить заново."
        ).format(days=remaining_days)
    else:
        warning_text = ""

    # Формируем текст стоимости с учётом скидки
    if discount_percent > 0:
        price_text = texts.t(
            "MODEM_PRICE_WITH_DISCOUNT",
            "Стоимость: <s>{base_price}</s> <b>{final_price}</b> (за {months} мес)\n"
            "🎁 Скидка {discount}%: -{discount_amount}"
        ).format(
            base_price=texts.format_price(base_price),
            final_price=texts.format_price(price),
            months=charged_months,
            discount=discount_percent,
            discount_amount=texts.format_price(discount_amount),
        )
    else:
        price_text = texts.t(
            "MODEM_PRICE_NO_DISCOUNT",
            "Стоимость: {price} (за {months} мес)"
        ).format(
            price=texts.format_price(price),
            months=charged_months,
        )

    confirm_text = texts.t(
        "MODEM_CONFIRM_ENABLE_BASE",
        (
            "📡 <b>Подтверждение подключения модема</b>\n\n"
            "{price_text}\n\n"
            "При подключении модема:\n"
            "• К подписке добавится дополнительное устройство\n"
            "• Ежемесячная плата увеличится на {monthly_price}\n\n"
            "Подтвердить подключение?"
        ),
    ).format(
        price_text=price_text,
        monthly_price=texts.format_price(modem_price_per_month),
    )

    # Добавляем информацию о сроке действия
    period_info = texts.t(
        "MODEM_PERIOD_INFO",
        "\n📅 Модем действует до: <b>{end_date}</b> ({days} дн.)"
    ).format(end_date=end_date_str, days=remaining_days)

    confirm_text += period_info + warning_text

    await callback.message.edit_text(
        confirm_text,
        reply_markup=get_modem_confirm_keyboard(db_user.language, price),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_modem_confirm(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Подтверждение и активация модема."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription or subscription.is_trial:
        await callback.answer(
            texts.t("MODEM_PAID_ONLY", "⚠️ Модем доступен только для платных подписок"),
            show_alert=True,
        )
        return

    if not settings.is_modem_enabled():
        await callback.answer(
            texts.t("MODEM_DISABLED", "⚠️ Функция модема отключена"),
            show_alert=True,
        )
        return

    modem_enabled = getattr(subscription, 'modem_enabled', False) or False
    if modem_enabled:
        await callback.answer(
            texts.t("MODEM_ALREADY_ENABLED", "ℹ️ Модем уже подключен"),
            show_alert=True,
        )
        return

    try:
        price = int(callback.data.split('_')[2])
    except (IndexError, ValueError):
        await callback.answer(texts.ERROR, show_alert=True)
        return

    try:
        if price > 0:
            success = await subtract_user_balance(
                db, db_user, price,
                "Подключение модема"
            )

            if not success:
                await callback.answer(
                    texts.t("PAYMENT_CHARGE_ERROR", "⚠️ Ошибка списания средств"),
                    show_alert=True,
                )
                return

            charged_months = get_remaining_months(subscription.end_date)
            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=price,
                description=f"Подключение модема на {charged_months} мес"
            )

        subscription.modem_enabled = True
        subscription.device_limit = (subscription.device_limit or 1) + 1
        subscription.updated_at = datetime.utcnow()

        await db.commit()

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await db.refresh(db_user)
        await db.refresh(subscription)

        try:
            from app.services.admin_notification_service import AdminNotificationService
            notification_service = AdminNotificationService(callback.bot)
            await notification_service.send_subscription_update_notification(
                db, db_user, subscription, "modem", False, True, price
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления о подключении модема: {e}")

        success_text = texts.t(
            "MODEM_ENABLED_SUCCESS",
            (
                "✅ <b>Модем успешно подключен!</b>\n\n"
                "📡 Модем активирован\n"
                "📱 Добавлено устройство для модема\n"
            ),
        )
        if price > 0:
            success_text += texts.t(
                "MODEM_CHARGED",
                "💰 Списано: {amount}",
            ).format(amount=texts.format_price(price))

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language, "subscription_settings"),
            parse_mode="HTML"
        )

        logger.info(
            f"✅ Пользователь {db_user.telegram_id} подключил модем, списано: {price / 100}₽"
        )

    except Exception as e:
        logger.error(f"Ошибка подключения модема: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language, "subscription_settings")
        )

    await callback.answer()


async def handle_modem_disable(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Отключение модема."""
    texts = get_texts(db_user.language)
    subscription = db_user.subscription

    if not subscription:
        await callback.answer(texts.ERROR, show_alert=True)
        return

    modem_enabled = getattr(subscription, 'modem_enabled', False) or False
    if not modem_enabled:
        await callback.answer(
            texts.t("MODEM_NOT_ENABLED", "ℹ️ Модем не подключен"),
            show_alert=True,
        )
        return

    try:
        subscription.modem_enabled = False
        if subscription.device_limit and subscription.device_limit > 1:
            subscription.device_limit = subscription.device_limit - 1
        subscription.updated_at = datetime.utcnow()

        await db.commit()

        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)

        await db.refresh(db_user)
        await db.refresh(subscription)

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
                "✅ <b>Модем отключен</b>\n\n"
                "📡 Модем деактивирован\n"
                "ℹ️ Возврат средств не производится"
            ),
        )

        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language, "subscription_settings"),
            parse_mode="HTML"
        )

        logger.info(f"✅ Пользователь {db_user.telegram_id} отключил модем")

    except Exception as e:
        logger.error(f"Ошибка отключения модема: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language, "subscription_settings")
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
        F.data.startswith("modem_confirm_")
    )

    dp.callback_query.register(
        handle_modem_disable,
        F.data == "modem_disable"
    )
