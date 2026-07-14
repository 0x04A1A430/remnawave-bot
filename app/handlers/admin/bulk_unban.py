"""
Обработчики команд для массовой разблокировки пользователей
"""

import structlog
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services.bulk_ban_service import bulk_ban_service
from app.states import AdminStates
from app.utils.decorators import admin_required, error_handler


logger = structlog.get_logger(__name__)


@admin_required
@error_handler
async def start_bulk_unban_process(callback: types.CallbackQuery, db_user: User, state: FSMContext):
    await callback.message.edit_text(
        '<b>Массовая разблокировка пользователей</b>\n\n'
        'Введите список Telegram ID для разблокировки.\n\n'
        '<b>Форматы ввода:</b>\n'
        '• По одному ID на строку\n'
        '• Через запятую\n'
        '• Через пробел\n\n'
        'Пример:\n'
        '<code>123456789\n'
        '987654321\n'
        '111222333</code>\n\n'
        'Или нажмите кнопку "Разблокировать всех", чтобы разблокировать всех заблокированных пользователей.\n\n'
        'Для отмены используйте команду /cancel',
        parse_mode='HTML',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text='Разблокировать всех', callback_data='admin_bulk_unban_all')],
                [types.InlineKeyboardButton(text='Отмена', callback_data='admin_users')],
            ]
        ),
    )

    await state.set_state(AdminStates.waiting_for_bulk_unban_list)
    await callback.answer()


@admin_required
@error_handler
async def process_bulk_unban_list(message: types.Message, db_user: User, state: FSMContext, db: AsyncSession):
    if not message.text:
        await message.answer(
            'Отправьте текстовое сообщение со списком Telegram ID',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )
        return

    input_text = message.text.strip()

    if not input_text:
        await message.answer(
            'Введите корректный список Telegram ID',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )
        return

    try:
        telegram_ids = await bulk_ban_service.parse_telegram_ids_from_text(input_text)
    except Exception as e:
        logger.error('Ошибка парсинга Telegram ID', error=e)
        await message.answer(
            'Ошибка при обработке списка ID. Проверьте формат ввода.',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )
        return

    if not telegram_ids:
        await message.answer(
            'Не найдено корректных Telegram ID в списке',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )
        return

    if len(telegram_ids) > 1000:
        await message.answer(
            f'Слишком много ID в списке ({len(telegram_ids)}). Максимум: 1000',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )
        return

    try:
        successfully_unbanned, not_found, error_ids = await bulk_ban_service.unban_users_by_telegram_ids(
            db=db,
            telegram_ids=telegram_ids,
        )

        result_text = '<b>Массовая разблокировка завершена</b>\n\n'
        result_text += '<b>Результаты:</b>\n'
        result_text += f'Успешно разблокировано: {successfully_unbanned}\n'
        result_text += f'Не найдено: {not_found}\n'
        result_text += f'Ошибок: {len(error_ids)}\n\n'
        result_text += f'Всего обработано: {len(telegram_ids)}'

        if successfully_unbanned > 0:
            result_text += f'\nПроцент успеха: {round((successfully_unbanned / len(telegram_ids)) * 100, 1)}%'

        if error_ids:
            result_text += '\n\n<b>Telegram ID с ошибками:</b>\n'
            result_text += f'<code>{", ".join(map(str, error_ids[:10]))}</code>'
            if len(error_ids) > 10:
                result_text += f' и еще {len(error_ids) - 10}...'

        await message.answer(
            result_text,
            parse_mode='HTML',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='К пользователям', callback_data='admin_users')]]
            ),
        )

    except Exception as e:
        logger.error('Ошибка при выполнении массовой разблокировки', error=e)
        await message.answer(
            'Произошла ошибка при выполнении массовой разблокировки',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )

    await state.clear()


@admin_required
@error_handler
async def confirm_unban_all(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    from sqlalchemy import func, select

    from app.database.models import User, UserStatus

    result = await db.execute(select(func.count(User.id)).where(User.status == UserStatus.BLOCKED.value))
    blocked_count = result.scalar()

    if blocked_count == 0:
        await callback.message.edit_text(
            'Нет заблокированных пользователей для разблокировки.',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f'<b>Разблокировка всех пользователей</b>\n\n'
        f'Вы уверены, что хотите разблокировать <b>всех</b> заблокированных пользователей?\n'
        f'Всего заблокировано: <b>{blocked_count}</b>\n\n'
        f'Это действие нельзя отменить.',
        parse_mode='HTML',
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text='Да, разблокировать всех', callback_data='admin_bulk_unban_all_confirm'
                    )
                ],
                [types.InlineKeyboardButton(text='Отмена', callback_data='admin_users')],
            ]
        ),
    )
    await callback.answer()


@admin_required
@error_handler
async def execute_unban_all(callback: types.CallbackQuery, db_user: User, state: FSMContext, db: AsyncSession):
    await callback.message.edit_text('Разблокировка всех пользователей... Пожалуйста, подождите.')

    try:
        successfully, error_ids = await bulk_ban_service.unban_all_blocked_users(
            db=db,
        )

        result_text = '<b>Разблокировка всех пользователей завершена</b>\n\n'
        result_text += f'Успешно разблокировано: {successfully}\n'
        result_text += f'Ошибок: {len(error_ids)}'

        if error_ids:
            result_text += '\n\n<b>Telegram ID с ошибками:</b>\n'
            result_text += f'<code>{", ".join(map(str, error_ids[:10]))}</code>'
            if len(error_ids) > 10:
                result_text += f' и еще {len(error_ids) - 10}...'

        await callback.message.edit_text(
            result_text,
            parse_mode='HTML',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='К пользователям', callback_data='admin_users')]]
            ),
        )

    except Exception as e:
        logger.error('Ошибка при массовой разблокировке всех пользователей', error=e)
        await callback.message.edit_text(
            'Произошла ошибка при разблокировке всех пользователей.',
            reply_markup=types.InlineKeyboardMarkup(
                inline_keyboard=[[types.InlineKeyboardButton(text='Назад', callback_data='admin_users')]]
            ),
        )

    await callback.answer()
    await state.clear()


def register_bulk_unban_handlers(dp: Dispatcher):
    dp.callback_query.register(start_bulk_unban_process, F.data == 'admin_bulk_unban_start')
    dp.callback_query.register(confirm_unban_all, F.data == 'admin_bulk_unban_all')
    dp.callback_query.register(execute_unban_all, F.data == 'admin_bulk_unban_all_confirm')
    dp.message.register(process_bulk_unban_list, AdminStates.waiting_for_bulk_unban_list)
