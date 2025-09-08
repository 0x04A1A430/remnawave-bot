import logging
from aiogram import Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.services.version_service import version_service
from app.utils.decorators import admin_required, error_handler

logger = logging.getLogger(__name__)


def get_updates_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text="🔄 Проверить обновления",
                callback_data="admin_updates_check"
            )
        ],
        [
            InlineKeyboardButton(
                text="📋 Информация о версии",
                callback_data="admin_updates_info"
            )
        ],
        [
            InlineKeyboardButton(
                text="🔗 Открыть репозиторий",
                url=f"https://github.com/{version_service.repo}/releases"
            )
        ],
        [
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="admin_panel"
            )
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_version_info_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text="🔄 Обновить",
                callback_data="admin_updates_info"
            )
        ],
        [
            InlineKeyboardButton(
                text="◀️ К обновлениям",
                callback_data="admin_updates"
            )
        ]
    ]
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@admin_required
@error_handler
async def show_updates_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    try:
        version_info = await version_service.get_version_info()
        
        current_version = version_info['current_version']
        has_updates = version_info['has_updates']
        total_newer = version_info['total_newer']
        last_check = version_info['last_check']
        
        status_icon = "🆕" if has_updates else "✅"
        status_text = f"Доступно {total_newer} обновлений" if has_updates else "Актуальная версия"
        
        last_check_text = ""
        if last_check:
            last_check_text = f"\n🕐 Последняя проверка: {last_check.strftime('%d.%m.%Y %H:%M')}"
        
        message = f"""🔄 <b>СИСТЕМА ОБНОВЛЕНИЙ</b>

📦 <b>Текущая версия:</b> <code>{current_version}</code>
{status_icon} <b>Статус:</b> {status_text}

🔗 <b>Репозиторий:</b> {version_service.repo}{last_check_text}

ℹ️ Система автоматически проверяет обновления каждый час и отправляет уведомления о новых версиях."""
        
        await callback.message.edit_text(
            message,
            reply_markup=get_updates_keyboard(db_user.language),
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка показа меню обновлений: {e}")
        await callback.answer("❌ Ошибка загрузки меню обновлений", show_alert=True)


@admin_required
@error_handler
async def check_updates(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    await callback.answer("🔄 Проверяю обновления...")
    
    try:
        has_updates, newer_releases = await version_service.check_for_updates(force=True)
        
        if not has_updates:
            message = f"""✅ <b>ОБНОВЛЕНИЯ НЕ НАЙДЕНЫ</b>

📦 <b>Текущая версия:</b> <code>{version_service.current_version}</code>
🎯 <b>Статус:</b> У вас установлена последняя версия

🔗 <b>Репозиторий:</b> {version_service.repo}"""
            
        else:
            updates_list = []
            for i, release in enumerate(newer_releases[:5]): 
                icon = version_service.format_version_display(release).split()[0]
                updates_list.append(
                    f"{i+1}. {icon} <code>{release.tag_name}</code> • {release.formatted_date}"
                )
            
            updates_text = "\n".join(updates_list)
            more_text = f"\n\n📋 И еще {len(newer_releases) - 5} обновлений..." if len(newer_releases) > 5 else ""
            
            message = f"""🆕 <b>НАЙДЕНЫ ОБНОВЛЕНИЯ</b>

📦 <b>Текущая версия:</b> <code>{version_service.current_version}</code>
🎯 <b>Доступно обновлений:</b> {len(newer_releases)}

📋 <b>Последние версии:</b>
{updates_text}{more_text}

🔗 <b>Репозиторий:</b> {version_service.repo}"""
        
        keyboard = get_updates_keyboard(db_user.language)
        
        if has_updates:
            keyboard.inline_keyboard.insert(-2, [
                InlineKeyboardButton(
                    text="📋 Подробнее о версиях",
                    callback_data="admin_updates_info"
                )
            ])
        
        await callback.message.edit_text(
            message,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка проверки обновлений: {e}")
        await callback.message.edit_text(
            f"❌ <b>ОШИБКА ПРОВЕРКИ ОБНОВЛЕНИЙ</b>\n\n"
            f"Не удалось связаться с сервером GitHub.\n"
            f"Попробуйте позже.\n\n"
            f"📦 <b>Текущая версия:</b> <code>{version_service.current_version}</code>",
            reply_markup=get_updates_keyboard(db_user.language),
            parse_mode="HTML"
        )


@admin_required
@error_handler
async def show_version_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    await callback.answer("📋 Загружаю информацию о версиях...")
    
    try:
        version_info = await version_service.get_version_info()
        
        current_version = version_info['current_version']
        current_release = version_info['current_release']
        newer_releases = version_info['newer_releases']
        has_updates = version_info['has_updates']
        last_check = version_info['last_check']
        repo_url = version_info['repo_url']
        
        current_info = f"📦 <b>ТЕКУЩАЯ ВЕРСИЯ</b>\n\n"
        
        if current_release:
            current_info += f"🏷️ <b>Версия:</b> <code>{current_release.tag_name}</code>\n"
            current_info += f"📅 <b>Дата релиза:</b> {current_release.formatted_date}\n"
            if current_release.short_description:
                current_info += f"📝 <b>Описание:</b>\n{current_release.short_description}\n"
        else:
            current_info += f"🏷️ <b>Версия:</b> <code>{current_version}</code>\n"
            current_info += f"ℹ️ <b>Статус:</b> Информация о релизе недоступна\n"
        
        message_parts = [current_info]
        
        if has_updates and newer_releases:
            updates_info = f"\n🆕 <b>ДОСТУПНЫЕ ОБНОВЛЕНИЯ</b>\n\n"
            
            for i, release in enumerate(newer_releases):
                icon = "🔥" if i == 0 else "📦"
                if release.prerelease:
                    icon = "🧪"
                elif release.is_dev:
                    icon = "🔧"
                
                updates_info += f"{icon} <b>{release.tag_name}</b>\n"
                updates_info += f"   📅 {release.formatted_date}\n"
                if release.short_description:
                    updates_info += f"   📝 {release.short_description}\n"
                updates_info += "\n"
            
            message_parts.append(updates_info.rstrip())
        
        system_info = f"\n🔧 <b>СИСТЕМА ОБНОВЛЕНИЙ</b>\n\n"
        system_info += f"🔗 <b>Репозиторий:</b> {version_service.repo}\n"
        system_info += f"⚡ <b>Автопроверка:</b> {'Включена' if version_service.enabled else 'Отключена'}\n"
        system_info += f"🕐 <b>Интервал:</b> Каждый час\n"
        
        if last_check:
            system_info += f"🕐 <b>Последняя проверка:</b> {last_check.strftime('%d.%m.%Y %H:%M')}\n"
        
        message_parts.append(system_info.rstrip())
        
        final_message = "\n".join(message_parts)
        
        if len(final_message) > 4000:
            final_message = final_message[:3900] + "\n\n... (информация обрезана)"
        
        await callback.message.edit_text(
            final_message,
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Ошибка получения информации о версиях: {e}")
        await callback.message.edit_text(
            f"❌ <b>ОШИБКА ЗАГРУЗКИ</b>\n\n"
            f"Не удалось получить информацию о версиях.\n\n"
            f"📦 <b>Текущая версия:</b> <code>{version_service.current_version}</code>",
            reply_markup=get_version_info_keyboard(db_user.language),
            parse_mode="HTML"
        )


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_updates_menu,
        F.data == "admin_updates"
    )
    
    dp.callback_query.register(
        check_updates,
        F.data == "admin_updates_check"
    )
    
    dp.callback_query.register(
        show_version_info,
        F.data == "admin_updates_info"
    )
