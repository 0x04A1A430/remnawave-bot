import html
import logging
from aiogram import Dispatcher, types, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.config import settings
from app.database.crud.user import get_user_by_telegram_id, update_user
from app.keyboards.inline import (
    get_main_menu_keyboard,
    get_language_selection_keyboard,
    get_info_menu_keyboard,
)
from app.localization.texts import get_texts, get_rules
from app.database.models import User
from app.database.crud.user_message import get_random_active_message
from app.services.subscription_checkout_service import (
    has_subscription_checkout_draft,
    should_offer_checkout_resume,
)
from app.utils.photo_message import edit_or_answer_photo
from app.services.support_settings_service import SupportSettingsService
from app.utils.promo_offer import (
    build_promo_offer_hint,
    build_test_access_hint,
)
from app.services.privacy_policy_service import PrivacyPolicyService
from app.services.public_offer_service import PublicOfferService
from app.services.faq_service import FaqService

logger = logging.getLogger(__name__)


async def show_main_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    *,
    skip_callback_answer: bool = False,
):
    texts = get_texts(db_user.language)

    db_user.last_activity = datetime.utcnow()
    await db.commit()

    has_active_subscription = bool(db_user.subscription)
    subscription_is_active = False

    if db_user.subscription:
        subscription_is_active = db_user.subscription.is_active

    menu_text = await get_main_menu_text(db_user, texts, db)

    draft_exists = await has_subscription_checkout_draft(db_user.id)
    show_resume_checkout = should_offer_checkout_resume(db_user, draft_exists)

    is_admin = settings.is_admin(db_user.telegram_id)
    is_moderator = (not is_admin) and SupportSettingsService.is_moderator(
        db_user.telegram_id
    )

    await edit_or_answer_photo(
        callback=callback,
        caption=menu_text,
        keyboard=get_main_menu_keyboard(
            language=db_user.language,
            is_admin=is_admin,
            is_moderator=is_moderator,
            has_had_paid_subscription=db_user.has_had_paid_subscription,
            has_active_subscription=has_active_subscription,
            subscription_is_active=subscription_is_active,
            balance_kopeks=db_user.balance_kopeks,
            subscription=db_user.subscription,
            show_resume_checkout=show_resume_checkout,
        ),
        parse_mode="HTML",
    )
    if not skip_callback_answer:
        await callback.answer()


async def show_service_rules(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    from app.database.crud.rules import get_current_rules_content

    texts = get_texts(db_user.language)
    rules_text = await get_current_rules_content(db, db_user.language)

    if not rules_text:
        rules_text = await get_rules(db_user.language)

    await callback.message.edit_text(
        f"{texts.t('RULES_HEADER', '📋 <b>Правила сервиса</b>')}\n\n{rules_text}",
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=texts.BACK, callback_data="back_to_menu")]
        ])
    )
    await callback.answer()


async def show_info_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    header = texts.t("MENU_INFO_HEADER", "ℹ️ <b>Инфо</b>")
    prompt = texts.t("MENU_INFO_PROMPT", "Выберите раздел:")
    caption = f"{header}\n\n{prompt}" if prompt else header

    privacy_enabled = await PrivacyPolicyService.is_policy_enabled(db, db_user.language)
    public_offer_enabled = await PublicOfferService.is_offer_enabled(db, db_user.language)
    faq_enabled = await FaqService.is_enabled(db, db_user.language)

    await edit_or_answer_photo(
        callback=callback,
        caption=caption,
        keyboard=get_info_menu_keyboard(
            language=db_user.language,
            show_privacy_policy=privacy_enabled,
            show_public_offer=public_offer_enabled,
            show_faq=faq_enabled,
        ),
        parse_mode="HTML",
    )
    await callback.answer()


async def show_faq_pages(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    pages = await FaqService.get_pages(db, db_user.language)
    if not pages:
        await callback.answer(
            texts.t("FAQ_NOT_AVAILABLE", "FAQ временно недоступен."),
            show_alert=True,
        )
        return

    header = texts.t("FAQ_HEADER", "❓ <b>FAQ</b>")
    prompt = texts.t("FAQ_PAGES_PROMPT", "Выберите вопрос:" )
    caption = f"{header}\n\n{prompt}" if prompt else header

    buttons: list[list[types.InlineKeyboardButton]] = []
    for index, page in enumerate(pages, start=1):
        raw_title = (page.title or "").strip()
        if not raw_title:
            raw_title = texts.t("FAQ_PAGE_UNTITLED", "Без названия")
        if len(raw_title) > 60:
            raw_title = f"{raw_title[:57]}..."
        buttons.append([
            types.InlineKeyboardButton(
                text=f"{index}. {raw_title}",
                callback_data=f"menu_faq_page:{page.id}:1",
            )
        ])

    buttons.append([
        types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_info")
    ])

    await callback.message.edit_text(
        caption,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await callback.answer()


async def show_faq_page(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    raw_data = callback.data or ""
    parts = raw_data.split(":")

    page_id = None
    requested_page = 1

    if len(parts) >= 2:
        try:
            page_id = int(parts[1])
        except ValueError:
            page_id = None

    if len(parts) >= 3:
        try:
            requested_page = int(parts[2])
        except ValueError:
            requested_page = 1

    if not page_id:
        await callback.answer()
        return

    page = await FaqService.get_page(db, page_id, db_user.language)

    if not page or not page.is_active:
        await callback.answer(
            texts.t("FAQ_PAGE_NOT_AVAILABLE", "Эта страница FAQ недоступна."),
            show_alert=True,
        )
        return

    content_pages = FaqService.split_content_into_pages(page.content)

    if not content_pages:
        await callback.answer(
            texts.t("FAQ_PAGE_EMPTY", "Текст для этой страницы ещё не добавлен."),
            show_alert=True,
        )
        return

    total_pages = len(content_pages)
    current_page = max(1, min(requested_page, total_pages))

    header = texts.t("FAQ_HEADER", "❓ <b>FAQ</b>")
    title_template = texts.t("FAQ_PAGE_TITLE", "<b>{title}</b>")
    page_title = (page.title or "").strip()
    if not page_title:
        page_title = texts.t("FAQ_PAGE_UNTITLED", "Без названия")
    title_block = title_template.format(title=html.escape(page_title))

    body = content_pages[current_page - 1]

    footer_template = texts.t(
        "FAQ_PAGE_FOOTER",
        "Страница {current} из {total}",
    )
    footer = ""
    if total_pages > 1 and footer_template:
        try:
            footer = footer_template.format(current=current_page, total=total_pages)
        except Exception:
            footer = f"{current_page}/{total_pages}"

    parts_to_join = [header, title_block]
    if body:
        parts_to_join.append(body)
    if footer:
        parts_to_join.append(f"<code>{footer}</code>")

    message_text = "\n\n".join(segment for segment in parts_to_join if segment)

    keyboard_rows: list[list[types.InlineKeyboardButton]] = []

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if current_page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text=texts.t("PAGINATION_PREV", "⬅️"),
                    callback_data=f"menu_faq_page:{page.id}:{current_page - 1}",
                )
            )

        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{current_page}/{total_pages}",
                callback_data="noop",
            )
        )

        if current_page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text=texts.t("PAGINATION_NEXT", "➡️"),
                    callback_data=f"menu_faq_page:{page.id}:{current_page + 1}",
                )
            )

        keyboard_rows.append(nav_row)

    keyboard_rows.append([
        types.InlineKeyboardButton(
            text=texts.t("FAQ_BACK_TO_LIST", "⬅️ К списку FAQ"),
            callback_data="menu_faq",
        )
    ])
    keyboard_rows.append([
        types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_info")
    ])

    await callback.message.edit_text(
        message_text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()

async def show_privacy_policy(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    raw_page = 1
    if callback.data and ":" in callback.data:
        try:
            raw_page = int(callback.data.split(":", 1)[1])
        except ValueError:
            raw_page = 1

    if raw_page < 1:
        raw_page = 1

    policy = await PrivacyPolicyService.get_active_policy(db, db_user.language)

    if not policy:
        await callback.answer(
            texts.t(
                "PRIVACY_POLICY_NOT_AVAILABLE",
                "Политика конфиденциальности временно недоступна.",
            ),
            show_alert=True,
        )
        return

    pages = PrivacyPolicyService.split_content_into_pages(policy.content)

    if not pages:
        await callback.answer(
            texts.t(
                "PRIVACY_POLICY_EMPTY_ALERT",
                "Политика конфиденциальности ещё не заполнена.",
            ),
            show_alert=True,
        )
        return

    total_pages = len(pages)
    current_page = raw_page if raw_page <= total_pages else total_pages

    header = texts.t(
        "PRIVACY_POLICY_HEADER",
        "🛡️ <b>Политика конфиденциальности</b>",
    )
    body = pages[current_page - 1]

    footer_template = texts.t(
        "PRIVACY_POLICY_PAGE_INFO",
        "Страница {current} из {total}",
    )
    footer = ""
    if total_pages > 1 and footer_template:
        try:
            footer = footer_template.format(current=current_page, total=total_pages)
        except Exception:
            footer = f"{current_page}/{total_pages}"

    message_text = header
    if body:
        message_text += f"\n\n{body}"
    if footer:
        message_text += f"\n\n<code>{footer}</code>"

    keyboard_rows: list[list[types.InlineKeyboardButton]] = []

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if current_page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text=texts.t("PAGINATION_PREV", "⬅️"),
                    callback_data=f"menu_privacy_policy:{current_page - 1}",
                )
            )

        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{current_page}/{total_pages}",
                callback_data="noop",
            )
        )

        if current_page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text=texts.t("PAGINATION_NEXT", "➡️"),
                    callback_data=f"menu_privacy_policy:{current_page + 1}",
                )
            )

        keyboard_rows.append(nav_row)

    keyboard_rows.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_info")]
    )

    await callback.message.edit_text(
        message_text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


async def show_public_offer(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    raw_page = 1
    if callback.data and ":" in callback.data:
        try:
            raw_page = int(callback.data.split(":", 1)[1])
        except ValueError:
            raw_page = 1

    if raw_page < 1:
        raw_page = 1

    offer = await PublicOfferService.get_active_offer(db, db_user.language)

    if not offer:
        await callback.answer(
            texts.t(
                "PUBLIC_OFFER_NOT_AVAILABLE",
                "Публичная оферта временно недоступна.",
            ),
            show_alert=True,
        )
        return

    pages = PublicOfferService.split_content_into_pages(offer.content)

    if not pages:
        await callback.answer(
            texts.t(
                "PUBLIC_OFFER_EMPTY_ALERT",
                "Публичная оферта ещё не заполнена.",
            ),
            show_alert=True,
        )
        return

    total_pages = len(pages)
    current_page = raw_page if raw_page <= total_pages else total_pages

    header = texts.t(
        "PUBLIC_OFFER_HEADER",
        "📄 <b>Публичная оферта</b>",
    )
    body = pages[current_page - 1]

    footer_template = texts.t(
        "PUBLIC_OFFER_PAGE_INFO",
        "Страница {current} из {total}",
    )
    footer = ""
    if total_pages > 1 and footer_template:
        try:
            footer = footer_template.format(current=current_page, total=total_pages)
        except Exception:
            footer = f"{current_page}/{total_pages}"

    message_text = header
    if body:
        message_text += f"\n\n{body}"
    if footer:
        message_text += f"\n\n<code>{footer}</code>"

    keyboard_rows: list[list[types.InlineKeyboardButton]] = []

    if total_pages > 1:
        nav_row: list[types.InlineKeyboardButton] = []
        if current_page > 1:
            nav_row.append(
                types.InlineKeyboardButton(
                    text=texts.t("PAGINATION_PREV", "⬅️"),
                    callback_data=f"menu_public_offer:{current_page - 1}",
                )
            )

        nav_row.append(
            types.InlineKeyboardButton(
                text=f"{current_page}/{total_pages}",
                callback_data="noop",
            )
        )

        if current_page < total_pages:
            nav_row.append(
                types.InlineKeyboardButton(
                    text=texts.t("PAGINATION_NEXT", "➡️"),
                    callback_data=f"menu_public_offer:{current_page + 1}",
                )
            )

        keyboard_rows.append(nav_row)

    keyboard_rows.append(
        [types.InlineKeyboardButton(text=texts.BACK, callback_data="menu_info")]
    )

    await callback.message.edit_text(
        message_text,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await callback.answer()


async def show_language_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    if not settings.is_language_selection_enabled():
        await callback.answer(
            texts.t(
                "LANGUAGE_SELECTION_DISABLED",
                "⚙️ Выбор языка временно недоступен.",
            ),
            show_alert=True,
        )
        return

    await edit_or_answer_photo(
        callback=callback,
        caption=texts.t("LANGUAGE_PROMPT", "🌐 Выберите язык интерфейса:"),
        keyboard=get_language_selection_keyboard(
            current_language=db_user.language,
            include_back=True,
            language=db_user.language,
        ),
        parse_mode="HTML",
    )
    await callback.answer()


async def process_language_change(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
):
    texts = get_texts(db_user.language)

    if not settings.is_language_selection_enabled():
        await callback.answer(
            texts.t(
                "LANGUAGE_SELECTION_DISABLED",
                "⚙️ Выбор языка временно недоступен.",
            ),
            show_alert=True,
        )
        return

    selected_raw = (callback.data or "").split(":", 1)[-1]
    normalized_selected = selected_raw.strip().lower()

    available_map = {
        lang.strip().lower(): lang.strip()
        for lang in settings.get_available_languages()
        if isinstance(lang, str) and lang.strip()
    }

    if normalized_selected not in available_map:
        await callback.answer("❌ Unsupported language", show_alert=True)
        return

    resolved_language = available_map[normalized_selected].lower()

    if db_user.language.lower() == normalized_selected:
        await show_main_menu(
            callback,
            db_user,
            db,
            skip_callback_answer=True,
        )
        await callback.answer(texts.t("LANGUAGE_SELECTED", "🌐 Язык интерфейса обновлен."))
        return

    updated_user = await update_user(db, db_user, language=resolved_language)
    texts = get_texts(updated_user.language)

    await show_main_menu(
        callback,
        updated_user,
        db,
        skip_callback_answer=True,
    )
    await callback.answer(texts.t("LANGUAGE_SELECTED", "🌐 Язык интерфейса обновлен."))


async def handle_back_to_menu(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    await state.clear()

    texts = get_texts(db_user.language)

    has_active_subscription = db_user.subscription is not None
    subscription_is_active = False

    if db_user.subscription:
        subscription_is_active = db_user.subscription.is_active

    menu_text = await get_main_menu_text(db_user, texts, db)

    draft_exists = await has_subscription_checkout_draft(db_user.id)
    show_resume_checkout = should_offer_checkout_resume(db_user, draft_exists)

    is_admin = settings.is_admin(db_user.telegram_id)
    is_moderator = (not is_admin) and SupportSettingsService.is_moderator(
        db_user.telegram_id
    )

    await edit_or_answer_photo(
        callback=callback,
        caption=menu_text,
        keyboard=get_main_menu_keyboard(
            language=db_user.language,
            is_admin=is_admin,
            is_moderator=is_moderator,
            has_had_paid_subscription=db_user.has_had_paid_subscription,
            has_active_subscription=has_active_subscription,
            subscription_is_active=subscription_is_active,
            balance_kopeks=db_user.balance_kopeks,
            subscription=db_user.subscription,
            show_resume_checkout=show_resume_checkout,
        ),
        parse_mode="HTML",
    )
    await callback.answer()

def _get_subscription_status(user: User, texts) -> str:
    if not user.subscription:
        return texts.t("SUB_STATUS_NONE", "❌ Отсутствует")
    
    subscription = user.subscription
    current_time = datetime.utcnow()
    
    if subscription.end_date <= current_time:
        return texts.t(
            "SUB_STATUS_EXPIRED",
            "🔴 Истекла\n📅 {end_date}",
        ).format(end_date=subscription.end_date.strftime('%d.%m.%Y'))
    
    days_left = (subscription.end_date - current_time).days
    
    if subscription.is_trial:
        if days_left > 1:
            return texts.t(
                "SUB_STATUS_TRIAL_ACTIVE",
                "🎁 Тестовая подписка\n📅 до {end_date} ({days} дн.)",
            ).format(
                end_date=subscription.end_date.strftime('%d.%m.%Y'),
                days=days_left,
            )
        elif days_left == 1:
            return texts.t(
                "SUB_STATUS_TRIAL_TOMORROW",
                "🎁 Тестовая подписка\n⚠️ истекает завтра!",
            )
        else:
            return texts.t(
                "SUB_STATUS_TRIAL_TODAY",
                "🎁 Тестовая подписка\n⚠️ истекает сегодня!",
            )

    else: 
        if days_left > 7:
            return texts.t(
                "SUB_STATUS_ACTIVE_LONG",
                "💎 Активна\n📅 до {end_date} ({days} дн.)",
            ).format(
                end_date=subscription.end_date.strftime('%d.%m.%Y'),
                days=days_left,
            )
        elif days_left > 1:
            return texts.t(
                "SUB_STATUS_ACTIVE_FEW_DAYS",
                "💎 Активна\n⚠️ истекает через {days} дн.",
            ).format(days=days_left)
        elif days_left == 1:
            return texts.t(
                "SUB_STATUS_ACTIVE_TOMORROW",
                "💎 Активна\n⚠️ истекает завтра!",
            )
        else:
            return texts.t(
                "SUB_STATUS_ACTIVE_TODAY",
                "💎 Активна\n⚠️ истекает сегодня!",
            )


def _insert_random_message(base_text: str, random_message: str, action_prompt: str) -> str:
    if not random_message:
        return base_text

    prompt = action_prompt or ""
    if prompt and prompt in base_text:
        parts = base_text.split(prompt, 1)
        if len(parts) == 2:
            return f"{parts[0]}\n{random_message}\n\n{prompt}{parts[1]}"
        return base_text.replace(prompt, f"\n{random_message}\n\n{prompt}", 1)

    return f"{base_text}\n\n{random_message}"


async def get_main_menu_text(user, texts, db: AsyncSession):

    base_text = texts.MAIN_MENU.format(
        user_name=user.full_name,
        subscription_status=_get_subscription_status(user, texts)
    )

    action_prompt = texts.t("MAIN_MENU_ACTION_PROMPT", "Выберите действие:")

    info_sections: list[str] = []

    try:
        promo_hint = await build_promo_offer_hint(db, user, texts)
        if promo_hint:
            info_sections.append(promo_hint.strip())
    except Exception as hint_error:
        logger.debug(
            "Не удалось построить подсказку промо-предложения для пользователя %s: %s",
            getattr(user, "id", None),
            hint_error,
        )

    try:
        test_access_hint = await build_test_access_hint(db, user, texts)
        if test_access_hint:
            info_sections.append(test_access_hint.strip())
    except Exception as test_error:
        logger.debug(
            "Не удалось построить подсказку тестового доступа для пользователя %s: %s",
            getattr(user, "id", None),
            test_error,
        )

    if info_sections:
        extra_block = "\n\n".join(section for section in info_sections if section)
        if extra_block:
            base_text = _insert_random_message(base_text, extra_block, action_prompt)

    try:
        random_message = await get_random_active_message(db)
        if random_message:
            return _insert_random_message(base_text, random_message, action_prompt)
                
    except Exception as e:
        logger.error(f"Ошибка получения случайного сообщения: {e}")
    
    return base_text


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        handle_back_to_menu,
        F.data == "back_to_menu"
    )
    
    dp.callback_query.register(
        show_service_rules,
        F.data == "menu_rules"
    )

    dp.callback_query.register(
        show_info_menu,
        F.data == "menu_info",
    )

    dp.callback_query.register(
        show_faq_pages,
        F.data == "menu_faq",
    )

    dp.callback_query.register(
        show_faq_page,
        F.data.startswith("menu_faq_page:"),
    )

    dp.callback_query.register(
        show_privacy_policy,
        F.data == "menu_privacy_policy",
    )

    dp.callback_query.register(
        show_privacy_policy,
        F.data.startswith("menu_privacy_policy:"),
    )

    dp.callback_query.register(
        show_public_offer,
        F.data == "menu_public_offer",
    )

    dp.callback_query.register(
        show_public_offer,
        F.data.startswith("menu_public_offer:"),
    )

    dp.callback_query.register(
        show_language_menu,
        F.data == "menu_language"
    )

    dp.callback_query.register(
        process_language_change,
        F.data.startswith("language_select:"),
        StateFilter(None)
    )
