import asyncio
from datetime import datetime, timedelta, timezone
import logging
import random
from typing import Optional, List

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database import Database, User
from keyboards import back_keyboard
from translations import t
from utils import log_user_action, format_datetime

logger = logging.getLogger(__name__)

class LuckyGameStates(StatesGroup):
    waiting_number_choice = State()

lucky_game_router = Router()


@lucky_game_router.callback_query(F.data == "lucky_game")
async def lucky_game_menu_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Главное меню игры удачи"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        # Получаем настройки игры из конфига
        reward_amount = getattr(config, 'LUCKY_GAME_REWARD', 50.0)
        numbers_count = getattr(config, 'LUCKY_GAME_NUMBERS', 30)
        winning_numbers = getattr(config, 'LUCKY_GAME_WINNING_COUNT', 3)
        
        # Проверяем, можно ли играть сегодня
        can_play, next_game_time = await check_can_play_today(db, user.telegram_id)
        
        # Получаем статистику игр пользователя
        games_played, total_won, win_count = await get_user_game_stats(db, user.telegram_id)
        
        text = "🎰 **Проверь свою удачу!**\n\n"
        text += "🎯 **Как играть:**\n"
        text += f"• Выберите число от 1 до {numbers_count}\n"
        text += f"• Из {numbers_count} чисел {winning_numbers} - выигрышные\n"
        text += f"• Угадали - получаете {reward_amount:.0f}₽ на баланс!\n"
        text += "• Играть можно 1 раз в сутки\n\n"
        
        text += "📊 **Ваша статистика:**\n"
        text += f"• Игр сыграно: {games_played}\n"
        text += f"• Выигрышей: {win_count}\n"
        text += f"• Всего выиграно: {total_won:.0f}₽\n"
        
        if games_played > 0:
            win_rate = (win_count / games_played) * 100
            text += f"• Процент побед: {win_rate:.1f}%\n"
        
        # Создаем клавиатуру
        buttons = []
        
        if can_play:
            buttons.append([InlineKeyboardButton(text="🎲 Играть!", callback_data="start_lucky_game")])
        else:
            # Вычисляем оставшееся время
            now = datetime.utcnow()
            time_left = next_game_time - now
            hours_left = int(time_left.total_seconds() // 3600)
            minutes_left = int((time_left.total_seconds() % 3600) // 60)
            
            if hours_left > 0:
                time_text = f"{hours_left}ч {minutes_left}м"
            else:
                time_text = f"{minutes_left}м"
                
            buttons.append([InlineKeyboardButton(text=f"⏰ Приходи через {time_text}", callback_data="noop")])
        
        buttons.extend([
            [InlineKeyboardButton(text="📈 История игр", callback_data="lucky_game_history")],
            [InlineKeyboardButton(text="🔙 " + t('back', user.language), callback_data="main_menu")]
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error in lucky game menu: {e}")
        await callback.answer("❌ Ошибка загрузки игры")

@lucky_game_router.callback_query(F.data == "start_lucky_game")
async def start_lucky_game_callback(callback: CallbackQuery, db: Database, state: FSMContext, **kwargs):
    """Начать игру - показать числа для выбора"""
    user = kwargs.get('user')
    config = kwargs.get('config')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        # Проверяем, можно ли играть
        can_play, next_game_time = await check_can_play_today(db, user.telegram_id)
        
        if not can_play:
            await callback.answer("⏰ Вы уже играли сегодня! Приходите завтра.", show_alert=True)
            return
        
        # Получаем настройки
        numbers_count = getattr(config, 'LUCKY_GAME_NUMBERS', 30)
        winning_numbers = getattr(config, 'LUCKY_GAME_WINNING_COUNT', 3)
        reward_amount = getattr(config, 'LUCKY_GAME_REWARD', 50.0)
        
        # Генерируем выигрышные числа заранее и сохраняем в состоянии
        winning_nums = random.sample(range(1, numbers_count + 1), winning_numbers)
        
        await state.update_data(
            winning_numbers=winning_nums,
            reward_amount=reward_amount,
            numbers_count=numbers_count
        )
        await state.set_state(LuckyGameStates.waiting_number_choice)
        
        text = f"🎯 **Выберите число от 1 до {numbers_count}**\n\n"
        text += f"🎁 Награда: {reward_amount:.0f}₽\n"
        text += f"🍀 Удачных чисел: {winning_numbers} из {numbers_count}\n\n"
        text += "Нажмите на число, чтобы испытать удачу!"
        
        # Создаем кнопки с числами (5 в ряд)
        buttons = []
        for i in range(0, numbers_count, 5):
            row = []
            for j in range(5):
                if i + j + 1 <= numbers_count:
                    number = i + j + 1
                    row.append(InlineKeyboardButton(
                        text=str(number),
                        callback_data=f"choose_number_{number}"
                    ))
            buttons.append(row)
        
        buttons.append([InlineKeyboardButton(text="❌ Отмена", callback_data="lucky_game")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error starting lucky game: {e}")
        await callback.answer("❌ Ошибка запуска игры")

@lucky_game_router.callback_query(F.data.startswith("choose_number_"), StateFilter(LuckyGameStates.waiting_number_choice))
async def choose_number_callback(callback: CallbackQuery, db: Database, state: FSMContext, **kwargs):
    """Обработка выбора числа"""
    user = kwargs.get('user')
    bot = kwargs.get('bot')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        chosen_number = int(callback.data.split("_")[2])
        
        # Получаем данные из состояния
        state_data = await state.get_data()
        winning_numbers = state_data['winning_numbers']
        reward_amount = state_data['reward_amount']
        numbers_count = state_data['numbers_count']
        
        # Проверяем, выиграл ли пользователь
        is_winner = chosen_number in winning_numbers
        
        # Сохраняем результат игры в БД
        await save_game_result(db, user.telegram_id, chosen_number, winning_numbers, is_winner, reward_amount if is_winner else 0.0)
        
        # Формируем сообщение с результатом
        if is_winner:
            # Начисляем награду
            await db.add_balance(user.telegram_id, reward_amount)
            
            # Создаем платеж
            await db.create_payment(
                user_id=user.telegram_id,
                amount=reward_amount,
                payment_type='lucky_game',
                description=f'Выигрыш в игре удачи (число {chosen_number})',
                status='completed'
            )
            
            text = "🎉 **ПОЗДРАВЛЯЕМ! ВЫ ВЫИГРАЛИ!** 🎉\n\n"
            text += f"🎯 Ваше число: **{chosen_number}**\n"
            text += f"💰 Награда: **{reward_amount:.0f}₽** зачислена на баланс!\n"
            text += f"🆕 Новый баланс: **{user.balance + reward_amount:.0f}₽**\n\n"
        else:
            text = "😔 **Не повезло в этот раз...**\n\n"
            text += f"🎯 Ваше число: **{chosen_number}**\n"
            text += f"🍀 Выигрышные числа: **{', '.join(map(str, sorted(winning_numbers)))}**\n\n"
            text += "🔄 Попробуйте завтра!"
        
        # Показываем все выигрышные числа
        winning_nums_str = ', '.join(map(str, sorted(winning_numbers)))
        text += f"\n🎲 Сегодняшние удачные числа: {winning_nums_str}"
        text += f"\n\n⏰ Следующая игра будет доступна завтра!"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📈 История игр", callback_data="lucky_game_history")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
        # Логируем действие
        log_user_action(user.telegram_id, "lucky_game_played", 
                       f"Number: {chosen_number}, Winner: {is_winner}, Reward: {reward_amount if is_winner else 0}")
        
    except Exception as e:
        logger.error(f"Error in choose number: {e}")
        await callback.answer("❌ Ошибка обработки выбора")
    
    await state.clear()

@lucky_game_router.callback_query(F.data == "lucky_game_history")
async def lucky_game_history_callback(callback: CallbackQuery, db: Database, **kwargs):
    """Показать историю игр пользователя"""
    user = kwargs.get('user')
    
    if not user:
        await callback.answer("❌ Ошибка пользователя")
        return
    
    try:
        # Получаем последние 10 игр
        games = await get_user_game_history(db, user.telegram_id, limit=10)
        
        if not games:
            text = "📈 **История ваших игр**\n\n"
            text += "🎯 Вы еще не играли в игру удачи.\n\n"
            text += "Начните играть, чтобы увидеть историю!"
        else:
            text = "📈 **История ваших игр** (последние 10)\n\n"
            
            for i, game in enumerate(games, 1):
                date_str = format_datetime(game['played_at'], user.language)
                
                if game['is_winner']:
                    emoji = "🎉"
                    result = f"Выиграли {game['reward_amount']:.0f}₽"
                else:
                    emoji = "😔"
                    result = "Не повезло"
                
                text += f"{i}. {emoji} Число: **{game['chosen_number']}** - {result}\n"
                text += f"   📅 {date_str}\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎰 К игре", callback_data="lucky_game")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
        ])
        
        await callback.message.edit_text(
            text,
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error showing game history: {e}")
        await callback.answer("❌ Ошибка загрузки истории")

@lucky_game_router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery, **kwargs):
    """Пустая операция для неактивных кнопок"""
    await callback.answer()

# Вспомогательные функции

async def check_can_play_today(db: Database, user_id: int) -> tuple[bool, Optional[datetime]]:
    """Проверяет, может ли пользователь играть сегодня"""
    try:
        can_play = await db.can_play_lucky_game_today(user_id)
        
        if not can_play:
            # Вычисляем время следующей игры (завтра в 00:00)
            now = datetime.utcnow()
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            return False, tomorrow
        
        return True, None
        
    except Exception as e:
        logger.error(f"Error checking can play today: {e}")
        return True, None

async def get_user_game_stats(db: Database, user_id: int) -> tuple[int, float, int]:
    """Получает статистику игр пользователя"""
    try:
        stats = await db.get_user_game_stats(user_id)
        return stats['total_games'], stats['total_won'], stats['total_wins']
        
    except Exception as e:
        logger.error(f"Error getting user game stats: {e}")
        return 0, 0.0, 0

async def save_game_result(db: Database, user_id: int, chosen_number: int, 
                          winning_numbers: List[int], is_winner: bool, reward_amount: float):
    """Сохраняет результат игры в базу данных"""
    try:
        game = await db.create_lucky_game(
            user_id=user_id,
            chosen_number=chosen_number,
            winning_numbers=winning_numbers,
            is_winner=is_winner,
            reward_amount=reward_amount
        )
        
        if game:
            logger.info(f"Game result saved for user {user_id}: number={chosen_number}, winner={is_winner}, reward={reward_amount}")
        else:
            logger.error(f"Failed to save game result for user {user_id}")
        
    except Exception as e:
        logger.error(f"Error saving game result: {e}")

async def get_user_game_history(db: Database, user_id: int, limit: int = 10) -> List[dict]:
    """Получает историю игр пользователя"""
    try:
        return await db.get_user_game_history(user_id, limit)
        
    except Exception as e:
        logger.error(f"Error getting user game history: {e}")
        return []
