import logging
import random
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.contest import (
    get_active_rounds,
    get_attempt,
    create_attempt,
    update_attempt,
    increment_winner_count,
)
from app.database.database import AsyncSessionLocal
from app.database.models import ContestRound, ContestTemplate, SubscriptionStatus
from app.localization.texts import get_texts
from app.services.contest_rotation_service import (
    GAME_QUEST,
    GAME_LOCKS,
    GAME_CIPHER,
    GAME_SERVER,
    GAME_BLITZ,
    GAME_EMOJI,
    GAME_ANAGRAM,
)
from app.database.crud.subscription import get_subscription_by_user_id
from app.database.crud.subscription import extend_subscription
from app.utils.decorators import auth_required, error_handler
from app.keyboards.inline import get_back_keyboard
from app.states import ContestStates

logger = logging.getLogger(__name__)

# Rate limiting for contests
_contest_rate_limits = {}


def _check_rate_limit(user_id: int, action: str, limit: int = 1, window_seconds: int = 5) -> bool:
    """Check if user exceeds rate limit for contest actions."""
    key = f"{user_id}_{action}"
    now = datetime.utcnow().timestamp()
    
    if key not in _contest_rate_limits:
        _contest_rate_limits[key] = []
    
    # Clean old entries
    _contest_rate_limits[key] = [t for t in _contest_rate_limits[key] if now - t < window_seconds]
    
    if len(_contest_rate_limits[key]) >= limit:
        return False
    
    _contest_rate_limits[key].append(now)
    return True


def _validate_callback_data(data: str) -> Optional[list]:
    """Validate and parse callback data safely."""
    if not data or not isinstance(data, str):
        return None
    
    parts = data.split("_")
    if len(parts) < 2 or parts[0] != "contest":
        return None
    
    # Basic validation for parts
    for part in parts:
        if not part or len(part) > 50:  # reasonable limit
            return None
    
    return parts


def _user_allowed(subscription) -> bool:
    if not subscription:
        return False
    return subscription.status in {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.TRIAL.value,
    }


async def _award_prize(db: AsyncSession, user_id: int, prize_type: str, prize_value: str, language: str) -> str:
    from app.database.crud.user import get_user_by_id
    user = await get_user_by_id(db, user_id)
    if not user:
        return ""
    
    texts = get_texts(language)
    
    if prize_type == "days":
        subscription = await get_subscription_by_user_id(db, user_id)
        if not subscription:
            return ""
        days = int(prize_value) if prize_value.isdigit() else 1
        await extend_subscription(db, subscription, days)
        return texts.t("CONTEST_PRIZE_GRANTED", "Бонус {days} дней зачислен!").format(days=days)
    
    elif prize_type == "balance":
        kopeks = int(prize_value) if prize_value.isdigit() else 0
        if kopeks > 0:
            user.balance_kopeks += kopeks
            return texts.t("CONTEST_BALANCE_GRANTED", "Бонус {amount} зачислен!").format(amount=settings.format_price(kopeks))
    
    elif prize_type == "custom":
        # For custom prizes, just send a message
        return f"🎁 {prize_value}"
    
    return ""


async def _reply_not_eligible(callback: types.CallbackQuery, language: str):
    texts = get_texts(language)
    await callback.answer(texts.t("CONTEST_NOT_ELIGIBLE", "Игры доступны только с активной или триальной подпиской."), show_alert=True)


# ---------- Handlers ----------


@auth_required
@error_handler
async def show_contests_menu(callback: types.CallbackQuery, db_user, db: AsyncSession):
    texts = get_texts(db_user.language)
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not _user_allowed(subscription):
        await _reply_not_eligible(callback, db_user.language)
        return

    active_rounds = await get_active_rounds(db)
    unique_templates = {}
    for rnd in active_rounds:
        if not rnd.template or not rnd.template.is_enabled:
            continue
        tpl_slug = rnd.template.slug if rnd.template else ""
        if tpl_slug not in unique_templates:
            unique_templates[tpl_slug] = rnd

    buttons = []
    for tpl_slug, rnd in unique_templates.items():
        title = rnd.template.name if rnd.template else tpl_slug
        buttons.append(
            [
                types.InlineKeyboardButton(
                    text=f"▶️ {title}",
                    callback_data=f"contest_play_{tpl_slug}_{rnd.id}",
                )
            ]
        )
    if not buttons:
        buttons.append(
            [types.InlineKeyboardButton(text=texts.t("CONTEST_EMPTY", "Сейчас игр нет"), callback_data="noop")]
        )
    buttons.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")])

    await callback.message.edit_text(
        texts.t("CONTEST_MENU_TITLE", "🎲 <b>Игры/Конкурсы</b>\nВыберите игру:"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


@auth_required
@error_handler
async def play_contest(callback: types.CallbackQuery, state: FSMContext, db_user, db: AsyncSession):
    texts = get_texts(db_user.language)
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not _user_allowed(subscription):
        await _reply_not_eligible(callback, db_user.language)
        return

    # Rate limit check
    if not _check_rate_limit(db_user.id, "contest_play", limit=2, window_seconds=10):
        await callback.answer(texts.t("CONTEST_TOO_FAST", "Слишком быстро! Подождите."), show_alert=True)
        return

    # Validate callback data
    parts = _validate_callback_data(callback.data)
    if not parts or len(parts) < 4 or parts[1] != "play":
        await callback.answer("Некорректные данные", show_alert=True)
        return

    round_id_str = parts[-1]
    try:
        round_id = int(round_id_str)
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return

    slug = "_".join(parts[2:-1])

    # reload round with template
    async with AsyncSessionLocal() as db2:
        active_rounds = await get_active_rounds(db2)
        round_obj = next((r for r in active_rounds if r.id == round_id), None)
        if not round_obj:
            await callback.answer(texts.t("CONTEST_ROUND_FINISHED", "Раунд завершён или недоступен."), show_alert=True)
            return
        if not round_obj.template or not round_obj.template.is_enabled:
            await callback.answer(texts.t("CONTEST_DISABLED", "Игра отключена."), show_alert=True)
            return
        attempt = await get_attempt(db2, round_id, db_user.id)
        if attempt:
            await callback.answer(texts.t("CONTEST_ALREADY_PLAYED", "У вас уже была попытка в этом раунде."), show_alert=True)
            return

        tpl = round_obj.template
        if tpl.slug == GAME_QUEST:
            await _render_quest(callback, db_user, round_obj, tpl)
        elif tpl.slug == GAME_LOCKS:
            await _render_locks(callback, db_user, round_obj, tpl)
        elif tpl.slug == GAME_SERVER:
            await _render_server_lottery(callback, db_user, round_obj, tpl)
        elif tpl.slug == GAME_CIPHER:
            await _render_cipher(callback, db_user, round_obj, tpl, state, db2)
        elif tpl.slug == GAME_EMOJI:
            await _render_emoji(callback, db_user, round_obj, tpl, state, db2)
        elif tpl.slug == GAME_ANAGRAM:
            await _render_anagram(callback, db_user, round_obj, tpl, state, db2)
        elif tpl.slug == GAME_BLITZ:
            await _render_blitz(callback, db_user, round_obj, tpl)
        else:
            await callback.answer(texts.t("CONTEST_UNKNOWN", "Тип конкурса не поддерживается."), show_alert=True)


async def _render_quest(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate):
    texts = get_texts(db_user.language)
    rows = round_obj.payload.get("rows", 3)
    cols = round_obj.payload.get("cols", 3)
    keyboard = []
    for r in range(rows):
        row_buttons = []
        for c in range(cols):
            idx = r * cols + c
            row_buttons.append(
                types.InlineKeyboardButton(
                    text="🎛",
                    callback_data=f"contest_pick_{round_obj.id}_quest_{idx}"
                )
            )
        keyboard.append(row_buttons)
    keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="contests_menu")])
    await callback.message.edit_text(
        texts.t("CONTEST_QUEST_PROMPT", "Выбери один из узлов 3×3:"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


async def _render_locks(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate):
    texts = get_texts(db_user.language)
    total = round_obj.payload.get("total", 20)
    keyboard = []
    row = []
    for i in range(total):
        row.append(types.InlineKeyboardButton(text="🔒", callback_data=f"contest_pick_{round_obj.id}_locks_{i}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="contests_menu")])
    await callback.message.edit_text(
        texts.t("CONTEST_LOCKS_PROMPT", "Найди взломанную кнопку среди замков:"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


async def _render_server_lottery(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate):
    texts = get_texts(db_user.language)
    flags = round_obj.payload.get("flags") or []
    shuffled_flags = flags.copy()
    random.shuffle(shuffled_flags)
    keyboard = []
    row = []
    for flag in shuffled_flags:
        row.append(types.InlineKeyboardButton(text=flag, callback_data=f"contest_pick_{round_obj.id}_{flag}"))
        if len(row) == 5:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([types.InlineKeyboardButton(text=texts.BACK, callback_data="contests_menu")])
    await callback.message.edit_text(
        texts.t("CONTEST_SERVER_PROMPT", "Выбери сервер:"),
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
    await callback.answer()


async def _render_cipher(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate, state: FSMContext, db: AsyncSession):
    texts = get_texts(db_user.language)
    question = round_obj.payload.get("question", "")
    # Create attempt immediately to block re-entry
    await create_attempt(db, round_id=round_obj.id, user_id=db_user.id, answer=None, is_winner=False)
    await state.set_state(ContestStates.waiting_for_answer)
    await state.update_data(contest_round_id=round_obj.id)
    await callback.message.edit_text(
        texts.t("CONTEST_CIPHER_PROMPT", "Расшифруй: {q}").format(q=question),
        reply_markup=get_back_keyboard(db_user.language),
    )
    await callback.answer()


async def _render_emoji(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate, state: FSMContext, db: AsyncSession):
    texts = get_texts(db_user.language)
    question = round_obj.payload.get("question", "🤔")
    emoji_list = question.split()
    random.shuffle(emoji_list)
    shuffled_question = " ".join(emoji_list)
    # Create attempt immediately to block re-entry
    await create_attempt(db, round_id=round_obj.id, user_id=db_user.id, answer=None, is_winner=False)
    await state.set_state(ContestStates.waiting_for_answer)
    await state.update_data(contest_round_id=round_obj.id)
    await callback.message.edit_text(
        texts.t("CONTEST_EMOJI_PROMPT", "Угадай сервис по эмодзи: {q}").format(q=shuffled_question),
        reply_markup=get_back_keyboard(db_user.language),
    )
    await callback.answer()


async def _render_anagram(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate, state: FSMContext, db: AsyncSession):
    texts = get_texts(db_user.language)
    letters = round_obj.payload.get("letters", "")
    # Create attempt immediately to block re-entry
    await create_attempt(db, round_id=round_obj.id, user_id=db_user.id, answer=None, is_winner=False)
    await state.set_state(ContestStates.waiting_for_answer)
    await state.update_data(contest_round_id=round_obj.id)
    await callback.message.edit_text(
        texts.t("CONTEST_ANAGRAM_PROMPT", "Составь слово: {letters}").format(letters=letters),
        reply_markup=get_back_keyboard(db_user.language),
    )
    await callback.answer()


async def _render_blitz(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate):
    texts = get_texts(db_user.language)
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.t("CONTEST_BLITZ_BUTTON", "Я здесь!"), callback_data=f"contest_pick_{round_obj.id}_blitz")],
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="contests_menu")]
        ]
    )
    await callback.message.edit_text(
        texts.t("CONTEST_BLITZ_PROMPT", "⚡️ Блиц! Нажми «Я здесь!»"),
        reply_markup=keyboard,
    )
    await callback.answer()


@auth_required
@error_handler
async def handle_pick(callback: types.CallbackQuery, db_user, db: AsyncSession):
    texts = get_texts(db_user.language)
    
    # Rate limit check
    if not _check_rate_limit(db_user.id, "contest_pick", limit=1, window_seconds=3):
        await callback.answer(texts.t("CONTEST_TOO_FAST", "Слишком быстро! Подождите."), show_alert=True)
        return
    
    # Validate callback data
    parts = _validate_callback_data(callback.data)
    if not parts:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    
    if len(parts) < 4 or parts[1] != "pick":
        await callback.answer("Некорректные данные", show_alert=True)
        return
    
    round_id_str = parts[2]
    pick = "_".join(parts[3:])
    
    try:
        round_id = int(round_id_str)
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    
    # Re-check authorization
    subscription = await get_subscription_by_user_id(db, db_user.id)
    if not _user_allowed(subscription):
        await callback.answer(texts.t("CONTEST_NOT_ELIGIBLE", "Игра недоступна без активной подписки."), show_alert=True)
        return

    async with AsyncSessionLocal() as db2:
        active_rounds = await get_active_rounds(db2)
        round_obj = next((r for r in active_rounds if r.id == round_id), None)
        if not round_obj:
            await callback.answer(texts.t("CONTEST_ROUND_FINISHED", "Раунд завершён."), show_alert=True)
            return

        tpl = round_obj.template
        attempt = await get_attempt(db2, round_id, db_user.id)
        if attempt:
            await callback.answer(texts.t("CONTEST_ALREADY_PLAYED", "У вас уже была попытка."), show_alert=True)
            return

        secret_idx = round_obj.payload.get("secret_idx")
        correct_flag = ""
        if tpl.slug == GAME_SERVER:
            flags = round_obj.payload.get("flags") or []
            correct_flag = flags[secret_idx] if secret_idx is not None and secret_idx < len(flags) else ""

        is_winner = False
        if tpl.slug == GAME_SERVER:
            is_winner = pick == correct_flag
        elif tpl.slug == GAME_QUEST:
            # Format: quest_{idx}
            try:
                if pick.startswith("quest_"):
                    idx = int(pick.split("_")[1])
                    is_winner = secret_idx is not None and idx == secret_idx
            except (ValueError, IndexError):
                is_winner = False
        elif tpl.slug == GAME_LOCKS:
            # Format: locks_{idx}
            try:
                if pick.startswith("locks_"):
                    idx = int(pick.split("_")[1])
                    is_winner = secret_idx is not None and idx == secret_idx
            except (ValueError, IndexError):
                is_winner = False
        elif tpl.slug == GAME_BLITZ:
            is_winner = pick == "blitz"
        else:
            is_winner = False

        # Log attempt
        logger.info(f"Contest attempt: user {db_user.id}, round {round_id}, pick '{pick}', winner {is_winner}")
        
        # Atomic winner check and increment
        from sqlalchemy import select
        stmt = select(ContestRound).where(ContestRound.id == round_id).with_for_update()
        result = await db2.execute(stmt)
        round_obj_locked = result.scalar_one()
        
        if is_winner and round_obj_locked.winners_count >= round_obj_locked.max_winners:
            is_winner = False

        await create_attempt(db2, round_id=round_obj.id, user_id=db_user.id, answer=str(pick), is_winner=is_winner)

        if is_winner:
            round_obj_locked.winners_count += 1
            await db2.commit()
            prize_text = await _award_prize(db2, db_user.id, tpl.prize_type, tpl.prize_value, db_user.language)
            await callback.answer(texts.t("CONTEST_WIN", "🎉 Победа! ") + (prize_text or ""), show_alert=True)
        else:
            responses = {
                GAME_QUEST: ["Пусто", "Ложный сервер", "Найди другой узел"],
                GAME_LOCKS: ["Заблокировано", "Попробуй ещё", "Нет доступа"],
                GAME_SERVER: ["Сервер перегружен", "Нет ответа", "Попробуй завтра"],
            }.get(tpl.slug, ["Неудача"])
            await callback.answer(random.choice(responses), show_alert=True)


@auth_required
@error_handler
async def handle_text_answer(message: types.Message, state: FSMContext, db_user, db: AsyncSession):
    texts = get_texts(db_user.language)
    data = await state.get_data()
    round_id = data.get("contest_round_id")
    if not round_id:
        return

    async with AsyncSessionLocal() as db2:
        active_rounds = await get_active_rounds(db2)
        round_obj = next((r for r in active_rounds if r.id == round_id), None)
        if not round_obj:
            await message.answer(texts.t("CONTEST_ROUND_FINISHED", "Раунд завершён."), reply_markup=get_back_keyboard(db_user.language))
            await state.clear()
            return

        attempt = await get_attempt(db2, round_obj.id, db_user.id)
        if not attempt:
            # No attempt found - user didn't start the game properly
            await message.answer(texts.t("CONTEST_NOT_STARTED", "Сначала начните игру."), reply_markup=get_back_keyboard(db_user.language))
            await state.clear()
            return
        
        if attempt.answer is not None:
            # Already answered - block re-entry
            await message.answer(texts.t("CONTEST_ALREADY_PLAYED", "У вас уже была попытка."), reply_markup=get_back_keyboard(db_user.language))
            await state.clear()
            return

        answer = (message.text or "").strip().upper()
        tpl = round_obj.template
        correct = (round_obj.payload.get("answer") or "").upper()

        is_winner = correct and answer == correct

        # Atomic winner check and increment
        from sqlalchemy import select
        stmt = select(ContestRound).where(ContestRound.id == round_id).with_for_update()
        result = await db2.execute(stmt)
        round_obj_locked = result.scalar_one()

        if is_winner and round_obj_locked.winners_count >= round_obj_locked.max_winners:
            is_winner = False

        await update_attempt(db2, attempt, answer=answer, is_winner=is_winner)

        if is_winner:
            round_obj_locked.winners_count += 1
            await db2.commit()
            prize_text = await _award_prize(db2, db_user.id, tpl.prize_type, tpl.prize_value, db_user.language)
            await message.answer(texts.t("CONTEST_WIN", "🎉 Победа! ") + (prize_text or ""), reply_markup=get_back_keyboard(db_user.language))
        else:
            await message.answer(texts.t("CONTEST_LOSE", "Не верно, попробуй снова в следующем раунде."), reply_markup=get_back_keyboard(db_user.language))
    await state.clear()


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_contests_menu, F.data == "contests_menu")
    dp.callback_query.register(play_contest, F.data.startswith("contest_play_"))
    dp.callback_query.register(handle_pick, F.data.startswith("contest_pick_"))
    dp.message.register(handle_text_answer, ContestStates.waiting_for_answer)
    dp.message.register(lambda message: None, Command("contests"))  # placeholder
