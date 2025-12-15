import logging
import random
from datetime import datetime
from typing import Optional

from aiogram import Dispatcher, F, types
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.contest import (
    get_active_rounds,
    get_template_by_slug,
    get_active_round_by_template,
    get_attempt,
    create_attempt,
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


def _user_allowed(subscription) -> bool:
    if not subscription:
        return False
    return subscription.status in {
        SubscriptionStatus.ACTIVE.value,
        SubscriptionStatus.TRIAL.value,
    }


async def _with_session() -> AsyncSession:
    return AsyncSessionLocal()


async def _award_prize(db: AsyncSession, user_id: int, prize_days: int, language: str) -> str:
    from app.database.crud.user import get_user_by_id
    user = await get_user_by_id(db, user_id)
    if not user:
        return ""
    subscription = await get_subscription_by_user_id(db, user_id)
    if not subscription:
        return ""
    await extend_subscription(db, subscription, prize_days)
    texts = get_texts(language)
    return texts.t("CONTEST_PRIZE_GRANTED", "Бонус {days} дней зачислен!").format(days=prize_days)


async def _ensure_round_for_template(template: ContestTemplate) -> Optional[ContestRound]:
    async with AsyncSessionLocal() as db:
        round_obj = await get_active_round_by_template(db, template.id)
        return round_obj


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

    parts = callback.data.split("_")
    if len(parts) < 4 or parts[0] != "contest" or parts[1] != "play":
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
            await _render_cipher(callback, db_user, round_obj, tpl, state)
        elif tpl.slug == GAME_EMOJI:
            await _render_emoji(callback, db_user, round_obj, tpl, state)
        elif tpl.slug == GAME_ANAGRAM:
            await _render_anagram(callback, db_user, round_obj, tpl, state)
        elif tpl.slug == GAME_BLITZ:
            await _render_blitz(callback, db_user, round_obj, tpl)
        else:
            await callback.answer(texts.t("CONTEST_UNKNOWN", "Тип конкурса не поддерживается."), show_alert=True)


async def _render_quest(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate):
    texts = get_texts(db_user.language)
    rows = round_obj.payload.get("rows", 3)
    cols = round_obj.payload.get("cols", 3)
    secret = random.randint(0, rows * cols - 1)
    keyboard = []
    for r in range(rows):
        row_buttons = []
        for c in range(cols):
            idx = r * cols + c
            row_buttons.append(
                types.InlineKeyboardButton(
                    text="🎛",
                    callback_data=f"contest_pick_{round_obj.id}_{idx}_{secret}"
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
    secret = random.randint(0, total - 1)
    keyboard = []
    row = []
    for i in range(total):
        row.append(types.InlineKeyboardButton(text="🔒", callback_data=f"contest_pick_{round_obj.id}_{i}_{secret}"))
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


async def _render_cipher(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate, state: FSMContext):
    texts = get_texts(db_user.language)
    question = round_obj.payload.get("question", "")
    await state.set_state(ContestStates.waiting_for_answer)
    await state.update_data(contest_round_id=round_obj.id)
    await callback.message.edit_text(
        texts.t("CONTEST_CIPHER_PROMPT", "Расшифруй: {q}").format(q=question),
        reply_markup=get_back_keyboard(db_user.language),
    )
    await callback.answer()


async def _render_emoji(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate, state: FSMContext):
    texts = get_texts(db_user.language)
    question = round_obj.payload.get("question", "🤔")
    emoji_list = question.split()
    random.shuffle(emoji_list)
    shuffled_question = " ".join(emoji_list)
    await state.set_state(ContestStates.waiting_for_answer)
    await state.update_data(contest_round_id=round_obj.id)
    await callback.message.edit_text(
        texts.t("CONTEST_EMOJI_PROMPT", "Угадай сервис по эмодзи: {q}").format(q=shuffled_question),
        reply_markup=get_back_keyboard(db_user.language),
    )
    await callback.answer()


async def _render_anagram(callback, db_user, round_obj: ContestRound, tpl: ContestTemplate, state: FSMContext):
    texts = get_texts(db_user.language)
    letters = round_obj.payload.get("letters", "")
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
            [types.InlineKeyboardButton(text=texts.t("CONTEST_BLITZ_BUTTON", "Я здесь!"), callback_data=f"contest_pick_{round_obj.id}_blitz")]
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
    parts = callback.data.split("_")
    if len(parts) < 4 or parts[0] != "contest" or parts[1] != "pick":
        await callback.answer("Некорректные данные", show_alert=True)
        return

    round_id_str = parts[2]
    pick = "_".join(parts[3:])
    try:
        round_id = int(round_id_str)
    except ValueError:
        await callback.answer("Некорректные данные", show_alert=True)
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
        elif tpl.slug in {GAME_QUEST, GAME_LOCKS}:
            try:
                idx_str, secret_str = pick.split("_", 1)
                idx = int(idx_str)
                secret = int(secret_str)
                is_winner = idx == secret
            except ValueError:
                is_winner = False
        elif tpl.slug == GAME_BLITZ:
            is_winner = pick == "blitz"
        else:
            is_winner = False

        await create_attempt(db2, round_id=round_obj.id, user_id=db_user.id, answer=str(pick), is_winner=is_winner)

        if is_winner:
            await increment_winner_count(db2, round_obj)
            prize_text = await _award_prize(db2, db_user.id, tpl.prize_days, db_user.language)
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
        if attempt:
            await message.answer(texts.t("CONTEST_ALREADY_PLAYED", "У вас уже была попытка."), reply_markup=get_back_keyboard(db_user.language))
            await state.clear()
            return

        answer = (message.text or "").strip().upper()
        tpl = round_obj.template
        correct = (round_obj.payload.get("answer") or "").upper()

        is_winner = correct and answer == correct
        await create_attempt(db2, round_id=round_obj.id, user_id=db_user.id, answer=answer, is_winner=is_winner)

        if is_winner:
            await increment_winner_count(db2, round_obj)
            prize_text = await _award_prize(db2, db_user.id, tpl.prize_days, db_user.language)
            await message.answer(texts.t("CONTEST_WIN", "🎉 Победа! ") + (prize_text or ""), reply_markup=get_back_keyboard(db_user.language))
        else:
            await message.answer(texts.t("CONTEST_LOSE", "Не верно, попробуй снова в следующем раунде."), reply_markup=get_back_keyboard(db_user.language))
    await state.clear()


async def _award_prize(db: AsyncSession, user_id: int, prize_days: int, language: str) -> str:
    from app.database.crud.subscription import get_subscription_by_user_id

    logger = logging.getLogger(__name__)

    subscription = await get_subscription_by_user_id(db, user_id)
    if not subscription:
        return "ошибка: подписка не найдена"

    current_time = datetime.utcnow()
    subscription.end_date = subscription.end_date + timedelta(days=prize_days)
    subscription.updated_at = current_time
    await db.commit()
    await db.refresh(subscription)
    logger.info(f"🎁 Продлена подписка пользователя {user_id} на {prize_days} дней за конкурс")
    return f"подписка продлена на {prize_days} дней"


def register_handlers(dp: Dispatcher):
    dp.callback_query.register(show_contests_menu, F.data == "contests_menu")
    dp.callback_query.register(play_contest, F.data.startswith("contest_play_"))
    dp.callback_query.register(handle_pick, F.data.startswith("contest_pick_"))
    dp.message.register(handle_text_answer, ContestStates.waiting_for_answer)
    dp.message.register(lambda message: None, Command("contests"))  # placeholder
