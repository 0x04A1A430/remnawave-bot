import logging
from aiogram import Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.states import AdminStates
from app.database.models import User
from app.database.crud.server_squad import (
    get_all_server_squads, get_server_squad_by_id, update_server_squad,
    delete_server_squad, sync_with_remnawave, get_server_statistics,
    create_server_squad, get_available_server_squads
)
from app.services.remnawave_service import RemnaWaveService
from app.utils.decorators import admin_required, error_handler
from app.utils.cache import cache

logger = logging.getLogger(__name__)


@admin_required
@error_handler
async def show_servers_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    stats = await get_server_statistics(db)
    
    text = f"""
🌐 <b>Управление серверами</b>

📊 <b>Статистика:</b>
• Всего серверов: {stats['total_servers']}
• Доступные: {stats['available_servers']}
• Недоступные: {stats['unavailable_servers']}
• С подключениями: {stats['servers_with_connections']}

💰 <b>Выручка от серверов:</b>
• Общая: {stats['total_revenue_rubles']:.2f} ₽

Выберите действие:
"""
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="📋 Список серверов", callback_data="admin_servers_list"),
            types.InlineKeyboardButton(text="🔄 Синхронизация", callback_data="admin_servers_sync")
        ],
        [
            types.InlineKeyboardButton(text="📊 Синхронизировать счетчики", callback_data="admin_servers_sync_counts"),
            types.InlineKeyboardButton(text="📈 Подробная статистика", callback_data="admin_servers_stats")
        ],
        [
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_subscriptions")
        ]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def show_servers_list(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    page: int = 1
):
    
    servers, total_count = await get_all_server_squads(db, page=page, limit=10)
    total_pages = (total_count + 9) // 10
    
    if not servers:
        text = "🌐 <b>Список серверов</b>\n\n❌ Серверы не найдены."
    else:
        text = f"🌐 <b>Список серверов</b>\n\n"
        text += f"📊 Всего: {total_count} | Страница: {page}/{total_pages}\n\n"
        
        for i, server in enumerate(servers, 1 + (page - 1) * 10):
            status_emoji = "✅" if server.is_available else "❌"
            price_text = f"{server.price_rubles:.2f} ₽" if server.price_kopeks > 0 else "Бесплатно"
            
            text += f"{i}. {status_emoji} {server.display_name}\n"
            text += f"   💰 Цена: {price_text}"
            
            if server.max_users:
                text += f" | 👥 {server.current_users}/{server.max_users}"
            
            text += f"\n   UUID: <code>{server.squad_uuid}</code>\n\n"
    
    keyboard = []
    
    for i, server in enumerate(servers):
        row_num = i // 2 
        if len(keyboard) <= row_num:
            keyboard.append([])
        
        status_emoji = "✅" if server.is_available else "❌"
        keyboard[row_num].append(
            types.InlineKeyboardButton(
                text=f"{status_emoji} {server.display_name[:15]}...",
                callback_data=f"admin_server_edit_{server.id}"
            )
        )
    
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(types.InlineKeyboardButton(
                text="⬅️", callback_data=f"admin_servers_list_page_{page-1}"
            ))
        
        nav_row.append(types.InlineKeyboardButton(
            text=f"{page}/{total_pages}", callback_data="current_page"
        ))
        
        if page < total_pages:
            nav_row.append(types.InlineKeyboardButton(
                text="➡️", callback_data=f"admin_servers_list_page_{page+1}"
            ))
        
        keyboard.append(nav_row)
    
    keyboard.extend([
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers")]
    ])
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def sync_servers_with_remnawave(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🔄 Синхронизация с Remnawave...\n\nПодождите, это может занять время.",
        reply_markup=None
    )
    
    try:
        remnawave_service = RemnaWaveService()
        squads = await remnawave_service.get_all_squads()
        
        if not squads:
            await callback.message.edit_text(
                "❌ Не удалось получить данные о сквадах из Remnawave.\n\nПроверьте настройки API.",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers")]
                ])
            )
            return
        
        created, updated, disabled = await sync_with_remnawave(db, squads)
        
        await cache.delete("available_countries")
        
        text = f"""
✅ <b>Синхронизация завершена</b>

📊 <b>Результаты:</b>
• Создано новых серверов: {created}
• Обновлено существующих: {updated}
• Отключено неактивных: {disabled}
• Всего обработано: {len(squads)}

ℹ️ Новые серверы созданы как недоступные.
Настройте их в списке серверов.
"""
        
        keyboard = [
            [
                types.InlineKeyboardButton(text="📋 Список серверов", callback_data="admin_servers_list"),
                types.InlineKeyboardButton(text="🔄 Повторить", callback_data="admin_servers_sync")
            ],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers")]
        ]
        
        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
        
    except Exception as e:
        logger.error(f"Ошибка синхронизации серверов: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка синхронизации: {str(e)}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers")]
            ])
        )
    
    await callback.answer()


@admin_required
@error_handler
async def show_server_edit_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    status_emoji = "✅ Доступен" if server.is_available else "❌ Недоступен"
    price_text = f"{server.price_rubles:.2f} ₽" if server.price_kopeks > 0 else "Бесплатно"
    
    text = f"""
🌐 <b>Редактирование сервера</b>

<b>Информация:</b>
• ID: {server.id}
• UUID: <code>{server.squad_uuid}</code>
• Название: {server.display_name}
• Оригинальное: {server.original_name or 'Не указано'}
• Статус: {status_emoji}

<b>Настройки:</b>
• Цена: {price_text}
• Код страны: {server.country_code or 'Не указан'}
• Лимит пользователей: {server.max_users or 'Без лимита'}
• Текущих пользователей: {server.current_users}

<b>Описание:</b>
{server.description or 'Не указано'}

Выберите что изменить:
"""
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="✏️ Название", callback_data=f"admin_server_edit_name_{server.id}"),
            types.InlineKeyboardButton(text="💰 Цена", callback_data=f"admin_server_edit_price_{server.id}")
        ],
        [
            types.InlineKeyboardButton(text="🌍 Страна", callback_data=f"admin_server_edit_country_{server.id}"),
            types.InlineKeyboardButton(text="👥 Лимит", callback_data=f"admin_server_edit_limit_{server.id}")
        ],
        [
            types.InlineKeyboardButton(text="📝 Описание", callback_data=f"admin_server_edit_desc_{server.id}")
        ],
        [
            types.InlineKeyboardButton(
                text="❌ Отключить" if server.is_available else "✅ Включить",
                callback_data=f"admin_server_toggle_{server.id}"
            )
        ],
        [
            types.InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_server_delete_{server.id}"),
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers_list")
        ]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def toggle_server_availability(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    new_status = not server.is_available
    await update_server_squad(db, server_id, is_available=new_status)
    
    await cache.delete("available_countries")
    
    status_text = "включен" if new_status else "отключен"
    await callback.answer(f"✅ Сервер {status_text}!")
    
    server = await get_server_squad_by_id(db, server_id)
    
    status_emoji = "✅ Доступен" if server.is_available else "❌ Недоступен"
    price_text = f"{server.price_rubles:.2f} ₽" if server.price_kopeks > 0 else "Бесплатно"
    
    text = f"""
🌐 <b>Редактирование сервера</b>

<b>Информация:</b>
• ID: {server.id}
• UUID: <code>{server.squad_uuid}</code>
• Название: {server.display_name}
• Оригинальное: {server.original_name or 'Не указано'}
• Статус: {status_emoji}

<b>Настройки:</b>
• Цена: {price_text}
• Код страны: {server.country_code or 'Не указан'}
• Лимит пользователей: {server.max_users or 'Без лимита'}
• Текущих пользователей: {server.current_users}

<b>Описание:</b>
{server.description or 'Не указано'}

Выберите что изменить:
"""
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="✏️ Название", callback_data=f"admin_server_edit_name_{server.id}"),
            types.InlineKeyboardButton(text="💰 Цена", callback_data=f"admin_server_edit_price_{server.id}")
        ],
        [
            types.InlineKeyboardButton(text="🌍 Страна", callback_data=f"admin_server_edit_country_{server.id}"),
            types.InlineKeyboardButton(text="👥 Лимит", callback_data=f"admin_server_edit_limit_{server.id}")
        ],
        [
            types.InlineKeyboardButton(text="📝 Описание", callback_data=f"admin_server_edit_desc_{server.id}")
        ],
        [
            types.InlineKeyboardButton(
                text="❌ Отключить" if server.is_available else "✅ Включить",
                callback_data=f"admin_server_toggle_{server.id}"
            )
        ],
        [
            types.InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_server_delete_{server.id}"),
            types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers_list")
        ]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )


@admin_required
@error_handler
async def start_server_edit_price(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_price)
    
    current_price = f"{server.price_rubles:.2f} ₽" if server.price_kopeks > 0 else "Бесплатно"
    
    await callback.message.edit_text(
        f"💰 <b>Редактирование цены</b>\n\n"
        f"Текущая цена: <b>{current_price}</b>\n\n"
        f"Отправьте новую цену в рублях (например: 15.50) или 0 для бесплатного доступа:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_edit_{server_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_price_edit(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    try:
        price_rubles = float(message.text.replace(',', '.'))
        
        if price_rubles < 0:
            await message.answer("❌ Цена не может быть отрицательной")
            return
        
        if price_rubles > 10000:
            await message.answer("❌ Слишком высокая цена (максимум 10,000 ₽)")
            return
        
        price_kopeks = int(price_rubles * 100)
        
        server = await update_server_squad(db, server_id, price_kopeks=price_kopeks)
        
        if server:
            await state.clear()
            
            await cache.delete("available_countries")
            
            price_text = f"{price_rubles:.2f} ₽" if price_kopeks > 0 else "Бесплатно"
            await message.answer(
                f"✅ Цена сервера изменена на: <b>{price_text}</b>",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔙 К серверу", callback_data=f"admin_server_edit_{server_id}")]
                ]),
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Ошибка при обновлении сервера")
    
    except ValueError:
        await message.answer("❌ Неверный формат цены. Используйте числа (например: 15.50)")


@admin_required
@error_handler
async def start_server_edit_name(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_name)
    
    await callback.message.edit_text(
        f"✏️ <b>Редактирование названия</b>\n\n"
        f"Текущее название: <b>{server.display_name}</b>\n\n"
        f"Отправьте новое название для сервера:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_edit_{server_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_name_edit(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    new_name = message.text.strip()
    
    if len(new_name) > 255:
        await message.answer("❌ Название слишком длинное (максимум 255 символов)")
        return
    
    if len(new_name) < 3:
        await message.answer("❌ Название слишком короткое (минимум 3 символа)")
        return
    
    server = await update_server_squad(db, server_id, display_name=new_name)
    
    if server:
        await state.clear()
        
        await cache.delete("available_countries")
        
        await message.answer(
            f"✅ Название сервера изменено на: <b>{new_name}</b>",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 К серверу", callback_data=f"admin_server_edit_{server_id}")]
            ]),
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Ошибка при обновлении сервера")


@admin_required
@error_handler
async def delete_server_confirm(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    text = f"""
🗑️ <b>Удаление сервера</b>

Вы действительно хотите удалить сервер:
<b>{server.display_name}</b>

⚠️ <b>Внимание!</b>
Сервер можно удалить только если к нему нет активных подключений.

Это действие нельзя отменить!
"""
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="🗑️ Да, удалить", callback_data=f"admin_server_delete_confirm_{server_id}"),
            types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_edit_{server_id}")
        ]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def delete_server_execute(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    success = await delete_server_squad(db, server_id)
    
    if success:
        await cache.delete("available_countries")
        
        await callback.message.edit_text(
            f"✅ Сервер <b>{server.display_name}</b> успешно удален!",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="📋 К списку серверов", callback_data="admin_servers_list")]
            ]),
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text(
            f"❌ Не удалось удалить сервер <b>{server.display_name}</b>\n\n"
            f"Возможно, к нему есть активные подключения.",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 К серверу", callback_data=f"admin_server_edit_{server_id}")]
            ]),
            parse_mode="HTML"
        )
    
    await callback.answer()


@admin_required
@error_handler
async def show_server_detailed_stats(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    stats = await get_server_statistics(db)
    available_servers = await get_available_server_squads(db)
    
    text = f"""
📊 <b>Подробная статистика серверов</b>

<b>🌐 Общая информация:</b>
• Всего серверов: {stats['total_servers']}
• Доступные: {stats['available_servers']}
• Недоступные: {stats['unavailable_servers']}
• С активными подключениями: {stats['servers_with_connections']}

<b>💰 Финансовая статистика:</b>
• Общая выручка: {stats['total_revenue_rubles']:.2f} ₽
• Средняя цена за сервер: {(stats['total_revenue_rubles'] / max(stats['servers_with_connections'], 1)):.2f} ₽

<b>🔥 Топ серверов по цене:</b>
"""
    
    sorted_servers = sorted(available_servers, key=lambda x: x.price_kopeks, reverse=True)
    
    for i, server in enumerate(sorted_servers[:5], 1):
        price_text = f"{server.price_rubles:.2f} ₽" if server.price_kopeks > 0 else "Бесплатно"
        text += f"{i}. {server.display_name} - {price_text}\n"
    
    if not sorted_servers:
        text += "Нет доступных серверов\n"
    
    keyboard = [
        [
            types.InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_servers_stats"),
            types.InlineKeyboardButton(text="📋 Список", callback_data="admin_servers_list")
        ],
        [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers")]
    ]
    
    await callback.message.edit_text(
        text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
    )
    await callback.answer()


@admin_required
@error_handler
async def start_server_edit_country(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_country)
    
    current_country = server.country_code or "Не указан"
    
    await callback.message.edit_text(
        f"🌍 <b>Редактирование кода страны</b>\n\n"
        f"Текущий код страны: <b>{current_country}</b>\n\n"
        f"Отправьте новый код страны (например: RU, US, DE) или '-' для удаления:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_edit_{server_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_country_edit(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    new_country = message.text.strip().upper()
    
    if new_country == "-":
        new_country = None
    elif len(new_country) > 5:
        await message.answer("❌ Код страны слишком длинный (максимум 5 символов)")
        return
    
    server = await update_server_squad(db, server_id, country_code=new_country)
    
    if server:
        await state.clear()
        
        await cache.delete("available_countries")
        
        country_text = new_country or "Удален"
        await message.answer(
            f"✅ Код страны изменен на: <b>{country_text}</b>",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 К серверу", callback_data=f"admin_server_edit_{server_id}")]
            ]),
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Ошибка при обновлении сервера")


@admin_required
@error_handler
async def start_server_edit_limit(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_limit)
    
    current_limit = server.max_users or "Без лимита"
    
    await callback.message.edit_text(
        f"👥 <b>Редактирование лимита пользователей</b>\n\n"
        f"Текущий лимит: <b>{current_limit}</b>\n\n"
        f"Отправьте новый лимит пользователей (число) или 0 для безлимитного доступа:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_edit_{server_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_limit_edit(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    try:
        limit = int(message.text.strip())
        
        if limit < 0:
            await message.answer("❌ Лимит не может быть отрицательным")
            return
        
        if limit > 10000:
            await message.answer("❌ Слишком большой лимит (максимум 10,000)")
            return
        
        max_users = limit if limit > 0 else None
        
        server = await update_server_squad(db, server_id, max_users=max_users)
        
        if server:
            await state.clear()
            
            limit_text = f"{limit} пользователей" if limit > 0 else "Без лимита"
            await message.answer(
                f"✅ Лимит пользователей изменен на: <b>{limit_text}</b>",
                reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                    [types.InlineKeyboardButton(text="🔙 К серверу", callback_data=f"admin_server_edit_{server_id}")]
                ]),
                parse_mode="HTML"
            )
        else:
            await message.answer("❌ Ошибка при обновлении сервера")
    
    except ValueError:
        await message.answer("❌ Неверный формат числа. Введите целое число.")


@admin_required
@error_handler
async def start_server_edit_description(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    server_id = int(callback.data.split('_')[-1])
    server = await get_server_squad_by_id(db, server_id)
    
    if not server:
        await callback.answer("❌ Сервер не найден!", show_alert=True)
        return
    
    await state.set_data({'server_id': server_id})
    await state.set_state(AdminStates.editing_server_description)
    
    current_desc = server.description or "Не указано"
    
    await callback.message.edit_text(
        f"📝 <b>Редактирование описания</b>\n\n"
        f"Текущее описание:\n<i>{current_desc}</i>\n\n"
        f"Отправьте новое описание сервера или '-' для удаления:",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin_server_edit_{server_id}")]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


@admin_required
@error_handler
async def process_server_description_edit(
    message: types.Message,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    data = await state.get_data()
    server_id = data.get('server_id')
    
    new_description = message.text.strip()
    
    if new_description == "-":
        new_description = None
    elif len(new_description) > 1000:
        await message.answer("❌ Описание слишком длинное (максимум 1000 символов)")
        return
    
    server = await update_server_squad(db, server_id, description=new_description)
    
    if server:
        await state.clear()
        
        desc_text = new_description or "Удалено"
        await message.answer(
            f"✅ Описание сервера изменено:\n\n<i>{desc_text}</i>",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="🔙 К серверу", callback_data=f"admin_server_edit_{server_id}")]
            ]),
            parse_mode="HTML"
        )
    else:
        await message.answer("❌ Ошибка при обновлении сервера")

@admin_required
@error_handler
async def sync_server_user_counts_handler(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    await callback.message.edit_text(
        "🔄 Синхронизация счетчиков пользователей...",
        reply_markup=None
    )
    
    try:
        from app.database.crud.server_squad import sync_server_user_counts
        
        updated_count = await sync_server_user_counts(db)
        
        text = f"""
✅ <b>Синхронизация завершена</b>

📊 <b>Результат:</b>
• Обновлено серверов: {updated_count}

Счетчики пользователей синхронизированы с реальными данными.
"""
        
        keyboard = [
            [
                types.InlineKeyboardButton(text="📋 Список серверов", callback_data="admin_servers_list"),
                types.InlineKeyboardButton(text="🔄 Повторить", callback_data="admin_servers_sync_counts")
            ],
            [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers")]
        ]
        
        await callback.message.edit_text(
            text,
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard)
        )
        
    except Exception as e:
        logger.error(f"Ошибка синхронизации счетчиков: {e}")
        await callback.message.edit_text(
            f"❌ Ошибка синхронизации: {str(e)}",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_servers")]
            ])
        )
    
    await callback.answer()


@admin_required
@error_handler  
async def handle_servers_pagination(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    page = int(callback.data.split('_')[-1])
    await show_servers_list(callback, db_user, db, page)


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(show_servers_menu, F.data == "admin_servers")
    dp.callback_query.register(show_servers_list, F.data == "admin_servers_list")
    dp.callback_query.register(sync_servers_with_remnawave, F.data == "admin_servers_sync")
    dp.callback_query.register(sync_server_user_counts_handler, F.data == "admin_servers_sync_counts")
    dp.callback_query.register(show_server_detailed_stats, F.data == "admin_servers_stats")
    
    dp.callback_query.register(show_server_edit_menu, F.data.startswith("admin_server_edit_") & ~F.data.contains("name") & ~F.data.contains("price") & ~F.data.contains("country") & ~F.data.contains("limit") & ~F.data.contains("desc"))
    dp.callback_query.register(toggle_server_availability, F.data.startswith("admin_server_toggle_"))
    
    dp.callback_query.register(start_server_edit_name, F.data.startswith("admin_server_edit_name_"))
    dp.callback_query.register(start_server_edit_price, F.data.startswith("admin_server_edit_price_"))
    dp.callback_query.register(start_server_edit_country, F.data.startswith("admin_server_edit_country_"))      
    dp.callback_query.register(start_server_edit_limit, F.data.startswith("admin_server_edit_limit_"))         
    dp.callback_query.register(start_server_edit_description, F.data.startswith("admin_server_edit_desc_"))     
    
    dp.message.register(process_server_name_edit, AdminStates.editing_server_name)
    dp.message.register(process_server_price_edit, AdminStates.editing_server_price)
    dp.message.register(process_server_country_edit, AdminStates.editing_server_country)            
    dp.message.register(process_server_limit_edit, AdminStates.editing_server_limit)                
    dp.message.register(process_server_description_edit, AdminStates.editing_server_description)    
    
    dp.callback_query.register(delete_server_confirm, F.data.startswith("admin_server_delete_") & ~F.data.contains("confirm"))
    dp.callback_query.register(delete_server_execute, F.data.startswith("admin_server_delete_confirm_"))
    
    dp.callback_query.register(handle_servers_pagination, F.data.startswith("admin_servers_list_page_"))
