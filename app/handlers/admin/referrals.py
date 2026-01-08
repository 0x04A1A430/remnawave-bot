import json
import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import datetime

from app.config import settings
from app.database.models import User, WithdrawalRequest, WithdrawalRequestStatus, ReferralEarning
from app.localization.texts import get_texts
from app.database.crud.referral import (
    get_referral_statistics,
    get_top_referrers_by_period,
    get_user_referral_stats,
)
from app.database.crud.user import get_user_by_id, get_user_by_telegram_id
from app.services.referral_withdrawal_service import referral_withdrawal_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_referral_statistics(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        stats = await get_referral_statistics(db)
        
        avg_per_referrer = 0
        if stats.get('active_referrers', 0) > 0:
            avg_per_referrer = stats.get('total_paid_kopeks', 0) / stats['active_referrers']
        
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        
        text = f"""
🤝 <b>Реферальная статистика</b>

<b>Общие показатели:</b>
- Пользователей с рефералами: {stats.get('users_with_referrals', 0)}
- Активных рефереров: {stats.get('active_referrers', 0)}
- Выплачено всего: {settings.format_price(stats.get('total_paid_kopeks', 0))}

<b>За период:</b>
- Сегодня: {settings.format_price(stats.get('today_earnings_kopeks', 0))}
- За неделю: {settings.format_price(stats.get('week_earnings_kopeks', 0))}
- За месяц: {settings.format_price(stats.get('month_earnings_kopeks', 0))}

<b>Средние показатели:</b>
- На одного реферера: {settings.format_price(int(avg_per_referrer))}

<b>Топ-5 рефереров:</b>
"""
        
        top_referrers = stats.get('top_referrers', [])
        if top_referrers:
            for i, referrer in enumerate(top_referrers[:5], 1):
                earned = referrer.get('total_earned_kopeks', 0)
                count = referrer.get('referrals_count', 0)
                user_id = referrer.get('user_id', 'N/A')
                
                if count > 0:
                    text += f"{i}. ID {user_id}: {settings.format_price(earned)} ({count} реф.)\n"
                else:
                    logger.warning(f"Реферер {user_id} имеет {count} рефералов, но есть в топе")
        else:
            text += "Нет данных\n"
        
        text += f"""

<b>Настройки реферальной системы:</b>
- Минимальное пополнение: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
- Бонус за первое пополнение: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
- Бонус пригласившему: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}
- Комиссия с покупок: {settings.REFERRAL_COMMISSION_PERCENT}%
- Уведомления: {'✅ Включены' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ Отключены'}

<i>🕐 Обновлено: {current_time}</i>
"""
        
        keyboard_rows = [
            [types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_referrals")],
            [types.InlineKeyboardButton(text="👥 Топ рефереров", callback_data="admin_referrals_top")],
        ]

        # Кнопка заявок на вывод (если функция включена)
        if settings.is_referral_withdrawal_enabled():
            keyboard_rows.append([
                types.InlineKeyboardButton(
                    text="💸 Заявки на вывод",
                    callback_data="admin_withdrawal_requests"
                )
            ])

        keyboard_rows.extend([
            [types.InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_referrals_settings")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])

        keyboard = types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            await callback.answer("Обновлено")
        except Exception as edit_error:
            if "message is not modified" in str(edit_error):
                await callback.answer("Данные актуальны")
            else:
                logger.error(f"Ошибка редактирования сообщения: {edit_error}")
                await callback.answer("Ошибка обновления")
        
    except Exception as e:
        logger.error(f"Ошибка в show_referral_statistics: {e}", exc_info=True)
        
        current_time = datetime.datetime.now().strftime("%H:%M:%S")
        text = f"""
🤝 <b>Реферальная статистика</b>

❌ <b>Ошибка загрузки данных</b>

<b>Текущие настройки:</b>
- Минимальное пополнение: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
- Бонус за первое пополнение: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
- Бонус пригласившему: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}
- Комиссия с покупок: {settings.REFERRAL_COMMISSION_PERCENT}%

<i>🕐 Время: {current_time}</i>
"""
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="🔄 Повторить", callback_data="admin_referrals")],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
        
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except:
            pass
        await callback.answer("Произошла ошибка при загрузке статистики")


def _get_top_keyboard(period: str, sort_by: str) -> types.InlineKeyboardMarkup:
    """Создаёт клавиатуру для выбора периода и сортировки."""
    period_week = "✅ Неделя" if period == "week" else "Неделя"
    period_month = "✅ Месяц" if period == "month" else "Месяц"
    sort_earnings = "✅ По заработку" if sort_by == "earnings" else "По заработку"
    sort_invited = "✅ По приглашённым" if sort_by == "invited" else "По приглашённым"

    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(
                text=period_week,
                callback_data=f"admin_top_ref:week:{sort_by}"
            ),
            types.InlineKeyboardButton(
                text=period_month,
                callback_data=f"admin_top_ref:month:{sort_by}"
            ),
        ],
        [
            types.InlineKeyboardButton(
                text=sort_earnings,
                callback_data=f"admin_top_ref:{period}:earnings"
            ),
            types.InlineKeyboardButton(
                text=sort_invited,
                callback_data=f"admin_top_ref:{period}:invited"
            ),
        ],
        [types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin_top_ref:{period}:{sort_by}")],
        [types.InlineKeyboardButton(text="⬅️ К статистике", callback_data="admin_referrals")]
    ])


@admin_required
@error_handler
async def show_top_referrers(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает топ рефереров (по умолчанию: неделя, по заработку)."""
    await _show_top_referrers_filtered(callback, db, period="week", sort_by="earnings")


@admin_required
@error_handler
async def show_top_referrers_filtered(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Обрабатывает выбор периода и сортировки."""
    # Парсим callback_data: admin_top_ref:period:sort_by
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("Ошибка параметров")
        return

    period = parts[1]  # week или month
    sort_by = parts[2]  # earnings или invited

    if period not in ("week", "month"):
        period = "week"
    if sort_by not in ("earnings", "invited"):
        sort_by = "earnings"

    await _show_top_referrers_filtered(callback, db, period, sort_by)


async def _show_top_referrers_filtered(
    callback: types.CallbackQuery,
    db: AsyncSession,
    period: str,
    sort_by: str
):
    """Внутренняя функция отображения топа с фильтрами."""
    try:
        top_referrers = await get_top_referrers_by_period(db, period=period, sort_by=sort_by)

        period_text = "за неделю" if period == "week" else "за месяц"
        sort_text = "по заработку" if sort_by == "earnings" else "по приглашённым"

        text = f"🏆 <b>Топ рефереров {period_text}</b>\n"
        text += f"<i>Сортировка: {sort_text}</i>\n\n"

        if top_referrers:
            for i, referrer in enumerate(top_referrers[:20], 1):
                earned = referrer.get('earnings_kopeks', 0)
                count = referrer.get('invited_count', 0)
                display_name = referrer.get('display_name', 'N/A')
                username = referrer.get('username', '')
                telegram_id = referrer.get('telegram_id', 'N/A')

                if username:
                    display_text = f"@{username} (ID{telegram_id})"
                elif display_name and display_name != f"ID{telegram_id}":
                    display_text = f"{display_name} (ID{telegram_id})"
                else:
                    display_text = f"ID{telegram_id}"

                emoji = ""
                if i == 1:
                    emoji = "🥇 "
                elif i == 2:
                    emoji = "🥈 "
                elif i == 3:
                    emoji = "🥉 "

                # Выделяем основную метрику в зависимости от сортировки
                if sort_by == "invited":
                    text += f"{emoji}{i}. {display_text}\n"
                    text += f"   👥 <b>{count} приглашённых</b> | 💰 {settings.format_price(earned)}\n\n"
                else:
                    text += f"{emoji}{i}. {display_text}\n"
                    text += f"   💰 <b>{settings.format_price(earned)}</b> | 👥 {count} приглашённых\n\n"
        else:
            text += "Нет данных за выбранный период\n"

        keyboard = _get_top_keyboard(period, sort_by)

        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
            await callback.answer()
        except Exception as edit_error:
            if "message is not modified" in str(edit_error):
                await callback.answer("Данные актуальны")
            else:
                raise

    except Exception as e:
        logger.error(f"Ошибка в show_top_referrers_filtered: {e}", exc_info=True)
        await callback.answer("Ошибка загрузки топа рефереров")


@admin_required
@error_handler
async def show_referral_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    text = f"""
⚙️ <b>Настройки реферальной системы</b>

<b>Бонусы и награды:</b>
• Минимальная сумма пополнения для участия: {settings.format_price(settings.REFERRAL_MINIMUM_TOPUP_KOPEKS)}
• Бонус за первое пополнение реферала: {settings.format_price(settings.REFERRAL_FIRST_TOPUP_BONUS_KOPEKS)}
• Бонус пригласившему за первое пополнение: {settings.format_price(settings.REFERRAL_INVITER_BONUS_KOPEKS)}

<b>Комиссионные:</b>
• Процент с каждой покупки реферала: {settings.REFERRAL_COMMISSION_PERCENT}%

<b>Уведомления:</b>
• Статус: {'✅ Включены' if settings.REFERRAL_NOTIFICATIONS_ENABLED else '❌ Отключены'}
• Попытки отправки: {getattr(settings, 'REFERRAL_NOTIFICATION_RETRY_ATTEMPTS', 3)}

<i>💡 Для изменения настроек отредактируйте файл .env и перезапустите бота</i>
"""
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="⬅️ К статистике", callback_data="admin_referrals")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def show_pending_withdrawal_requests(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает список ожидающих заявок на вывод."""
    requests = await referral_withdrawal_service.get_pending_requests(db)

    if not requests:
        text = "📋 <b>Заявки на вывод</b>\n\nНет ожидающих заявок."

        keyboard_rows = []
        # Кнопка тестового начисления (только в тестовом режиме)
        if settings.REFERRAL_WITHDRAWAL_TEST_MODE:
            keyboard_rows.append([
                types.InlineKeyboardButton(
                    text="🧪 Тестовое начисление",
                    callback_data="admin_test_referral_earning"
                )
            ])
        keyboard_rows.append([
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_referrals")
        ])

        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        )
        await callback.answer()
        return

    text = f"📋 <b>Заявки на вывод ({len(requests)})</b>\n\n"

    for req in requests[:10]:
        user = await get_user_by_id(db, req.user_id)
        user_name = user.full_name if user else "Неизвестно"
        user_tg_id = user.telegram_id if user else "N/A"

        risk_emoji = "🟢" if req.risk_score < 30 else "🟡" if req.risk_score < 50 else "🟠" if req.risk_score < 70 else "🔴"

        text += f"<b>#{req.id}</b> — {user_name} (ID{user_tg_id})\n"
        text += f"💰 {req.amount_kopeks / 100:.0f}₽ | {risk_emoji} Риск: {req.risk_score}/100\n"
        text += f"📅 {req.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"

    keyboard_rows = []
    for req in requests[:5]:
        keyboard_rows.append([
            types.InlineKeyboardButton(
                text=f"#{req.id} — {req.amount_kopeks / 100:.0f}₽",
                callback_data=f"admin_withdrawal_view_{req.id}"
            )
        ])

    # Кнопка тестового начисления (только в тестовом режиме)
    if settings.REFERRAL_WITHDRAWAL_TEST_MODE:
        keyboard_rows.append([
            types.InlineKeyboardButton(
                text="🧪 Тестовое начисление",
                callback_data="admin_test_referral_earning"
            )
        ])

    keyboard_rows.append([
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_referrals")
    ])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
    )
    await callback.answer()


@admin_required
@error_handler
async def view_withdrawal_request(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Показывает детали заявки на вывод."""
    request_id = int(callback.data.split("_")[-1])

    result = await db.execute(
        select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
    )
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    user = await get_user_by_id(db, request.user_id)
    user_name = user.full_name if user else "Неизвестно"
    user_tg_id = user.telegram_id if user else "N/A"

    analysis = json.loads(request.risk_analysis) if request.risk_analysis else {}

    status_text = {
        WithdrawalRequestStatus.PENDING.value: "⏳ Ожидает",
        WithdrawalRequestStatus.APPROVED.value: "✅ Одобрена",
        WithdrawalRequestStatus.REJECTED.value: "❌ Отклонена",
        WithdrawalRequestStatus.COMPLETED.value: "✅ Выполнена",
        WithdrawalRequestStatus.CANCELLED.value: "🚫 Отменена",
    }.get(request.status, request.status)

    text = f"""
📋 <b>Заявка #{request.id}</b>

👤 Пользователь: {user_name}
🆔 ID: <code>{user_tg_id}</code>
💰 Сумма: <b>{request.amount_kopeks / 100:.0f}₽</b>
📊 Статус: {status_text}

💳 <b>Реквизиты:</b>
<code>{request.payment_details}</code>

📅 Создана: {request.created_at.strftime('%d.%m.%Y %H:%M')}

{referral_withdrawal_service.format_analysis_for_admin(analysis)}
"""

    keyboard = []

    if request.status == WithdrawalRequestStatus.PENDING.value:
        keyboard.append([
            types.InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=f"admin_withdrawal_approve_{request.id}"
            ),
            types.InlineKeyboardButton(
                text="❌ Отклонить",
                callback_data=f"admin_withdrawal_reject_{request.id}"
            )
        ])

    if request.status == WithdrawalRequestStatus.APPROVED.value:
        keyboard.append([
            types.InlineKeyboardButton(
                text="✅ Деньги переведены",
                callback_data=f"admin_withdrawal_complete_{request.id}"
            )
        ])

    keyboard.append([
        types.InlineKeyboardButton(
            text="👤 Профиль пользователя",
            callback_data=f"admin_user_{user_tg_id}"
        )
    ])
    keyboard.append([
        types.InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_withdrawal_requests")
    ])

    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def approve_withdrawal_request(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Одобряет заявку на вывод."""
    request_id = int(callback.data.split("_")[-1])

    result = await db.execute(
        select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
    )
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    success, error = await referral_withdrawal_service.approve_request(
        db, request_id, db_user.id
    )

    if success:
        # Уведомляем пользователя
        user = await get_user_by_id(db, request.user_id)
        if user:
            try:
                texts = get_texts(user.language)
                await callback.bot.send_message(
                    user.telegram_id,
                    texts.t(
                        "REFERRAL_WITHDRAWAL_APPROVED",
                        "✅ <b>Заявка на вывод #{id} одобрена!</b>\n\n"
                        "Сумма: <b>{amount}</b>\n"
                        "Средства списаны с баланса.\n\n"
                        "Ожидайте перевод на указанные реквизиты."
                    ).format(id=request.id, amount=texts.format_price(request.amount_kopeks))
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю: {e}")

        await callback.answer("✅ Заявка одобрена, средства списаны с баланса")

        # Обновляем отображение
        await view_withdrawal_request(callback, db_user, db)
    else:
        await callback.answer(f"❌ {error}", show_alert=True)


@admin_required
@error_handler
async def reject_withdrawal_request(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Отклоняет заявку на вывод."""
    request_id = int(callback.data.split("_")[-1])

    result = await db.execute(
        select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
    )
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    success = await referral_withdrawal_service.reject_request(
        db, request_id, db_user.id, "Отклонено администратором"
    )

    if success:
        # Уведомляем пользователя
        user = await get_user_by_id(db, request.user_id)
        if user:
            try:
                texts = get_texts(user.language)
                await callback.bot.send_message(
                    user.telegram_id,
                    texts.t(
                        "REFERRAL_WITHDRAWAL_REJECTED",
                        "❌ <b>Заявка на вывод #{id} отклонена</b>\n\n"
                        "Сумма: <b>{amount}</b>\n\n"
                        "Если у вас есть вопросы, обратитесь в поддержку."
                    ).format(id=request.id, amount=texts.format_price(request.amount_kopeks))
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю: {e}")

        await callback.answer("❌ Заявка отклонена")

        # Обновляем отображение
        await view_withdrawal_request(callback, db_user, db)
    else:
        await callback.answer("❌ Ошибка отклонения", show_alert=True)


@admin_required
@error_handler
async def complete_withdrawal_request(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    """Отмечает заявку как выполненную (деньги переведены)."""
    request_id = int(callback.data.split("_")[-1])

    result = await db.execute(
        select(WithdrawalRequest).where(WithdrawalRequest.id == request_id)
    )
    request = result.scalar_one_or_none()

    if not request:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    success = await referral_withdrawal_service.complete_request(
        db, request_id, db_user.id, "Перевод выполнен"
    )

    if success:
        # Уведомляем пользователя
        user = await get_user_by_id(db, request.user_id)
        if user:
            try:
                texts = get_texts(user.language)
                await callback.bot.send_message(
                    user.telegram_id,
                    texts.t(
                        "REFERRAL_WITHDRAWAL_COMPLETED",
                        "💸 <b>Выплата по заявке #{id} выполнена!</b>\n\n"
                        "Сумма: <b>{amount}</b>\n\n"
                        "Деньги отправлены на указанные реквизиты."
                    ).format(id=request.id, amount=texts.format_price(request.amount_kopeks))
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю: {e}")

        await callback.answer("✅ Заявка выполнена")

        # Обновляем отображение
        await view_withdrawal_request(callback, db_user, db)
    else:
        await callback.answer("❌ Ошибка выполнения", show_alert=True)


@admin_required
@error_handler
async def start_test_referral_earning(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Начинает процесс тестового начисления реферального дохода."""
    if not settings.REFERRAL_WITHDRAWAL_TEST_MODE:
        await callback.answer("Тестовый режим отключён", show_alert=True)
        return

    await state.set_state(AdminStates.test_referral_earning_input)

    text = """
🧪 <b>Тестовое начисление реферального дохода</b>

Введите данные в формате:
<code>telegram_id сумма_в_рублях</code>

Примеры:
• <code>123456789 500</code> — начислит 500₽ пользователю 123456789
• <code>987654321 1000</code> — начислит 1000₽ пользователю 987654321

⚠️ Это создаст реальную запись ReferralEarning, как будто пользователь заработал с реферала.
"""

    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="❌ Отмена", callback_data="admin_withdrawal_requests")]
    ])

    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@admin_required
@error_handler
async def process_test_referral_earning(
    message: types.Message,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    """Обрабатывает ввод тестового начисления."""
    if not settings.REFERRAL_WITHDRAWAL_TEST_MODE:
        await message.answer("❌ Тестовый режим отключён")
        await state.clear()
        return

    text_input = message.text.strip()
    parts = text_input.split()

    if len(parts) != 2:
        await message.answer(
            "❌ Неверный формат. Введите: <code>telegram_id сумма</code>\n\n"
            "Например: <code>123456789 500</code>"
        )
        return

    try:
        target_telegram_id = int(parts[0])
        amount_rubles = float(parts[1].replace(",", "."))
        amount_kopeks = int(amount_rubles * 100)

        if amount_kopeks <= 0:
            await message.answer("❌ Сумма должна быть положительной")
            return

        if amount_kopeks > 10000000:  # Лимит 100 000₽
            await message.answer("❌ Максимальная сумма тестового начисления: 100 000₽")
            return

    except ValueError:
        await message.answer(
            "❌ Неверный формат чисел. Введите: <code>telegram_id сумма</code>\n\n"
            "Например: <code>123456789 500</code>"
        )
        return

    # Ищем целевого пользователя
    target_user = await get_user_by_telegram_id(db, target_telegram_id)
    if not target_user:
        await message.answer(f"❌ Пользователь с ID {target_telegram_id} не найден в базе")
        return

    # Создаём тестовое начисление
    earning = ReferralEarning(
        user_id=target_user.id,
        referral_id=target_user.id,  # Сам на себя (тестовое)
        amount_kopeks=amount_kopeks,
        reason="test_earning"
    )
    db.add(earning)

    # Добавляем на баланс пользователя
    target_user.balance_kopeks += amount_kopeks

    await db.commit()
    await state.clear()

    await message.answer(
        f"✅ <b>Тестовое начисление создано!</b>\n\n"
        f"👤 Пользователь: {target_user.full_name or 'Без имени'}\n"
        f"🆔 ID: <code>{target_telegram_id}</code>\n"
        f"💰 Сумма: <b>{amount_rubles:.0f}₽</b>\n"
        f"💳 Новый баланс: <b>{target_user.balance_kopeks / 100:.0f}₽</b>\n\n"
        f"Начисление добавлено как реферальный доход.",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="📋 К заявкам", callback_data="admin_withdrawal_requests")],
            [types.InlineKeyboardButton(text="👤 Профиль", callback_data=f"admin_user_manage_{target_telegram_id}")]
        ])
    )

    logger.info(
        f"Тестовое начисление: админ {db_user.telegram_id} начислил {amount_rubles}₽ "
        f"пользователю {target_telegram_id}"
    )


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_referral_statistics, F.data == "admin_referrals")
    dp.callback_query.register(show_top_referrers, F.data == "admin_referrals_top")
    dp.callback_query.register(show_top_referrers_filtered, F.data.startswith("admin_top_ref:"))
    dp.callback_query.register(show_referral_settings, F.data == "admin_referrals_settings")

    # Хендлеры заявок на вывод
    dp.callback_query.register(show_pending_withdrawal_requests, F.data == "admin_withdrawal_requests")
    dp.callback_query.register(view_withdrawal_request, F.data.startswith("admin_withdrawal_view_"))
    dp.callback_query.register(approve_withdrawal_request, F.data.startswith("admin_withdrawal_approve_"))
    dp.callback_query.register(reject_withdrawal_request, F.data.startswith("admin_withdrawal_reject_"))
    dp.callback_query.register(complete_withdrawal_request, F.data.startswith("admin_withdrawal_complete_"))

    # Тестовое начисление
    dp.callback_query.register(start_test_referral_earning, F.data == "admin_test_referral_earning")
    dp.message.register(process_test_referral_earning, AdminStates.test_referral_earning_input)
