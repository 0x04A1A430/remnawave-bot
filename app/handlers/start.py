import logging
from datetime import datetime
from aiogram import Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.states import RegistrationStates
from app.database.crud.user import (
    get_user_by_telegram_id, create_user, get_user_by_referral_code
)
from app.database.models import UserStatus
from app.keyboards.inline import (
    get_rules_keyboard, get_main_menu_keyboard
)
from app.localization.texts import get_texts
from app.services.referral_service import process_referral_registration
from app.utils.user_utils import generate_unique_referral_code

logger = logging.getLogger(__name__)


async def handle_potential_referral_code(
    message: types.Message, 
    state: FSMContext, 
    db: AsyncSession
):
    current_state = await state.get_state()
    logger.info(f"🔍 REFERRAL CHECK: Проверка сообщения '{message.text}' в состоянии {current_state}")
    
    if current_state not in [
        RegistrationStates.waiting_for_rules_accept.state,
        RegistrationStates.waiting_for_referral_code.state,
        None 
    ]:
        return False
    
    user = await get_user_by_telegram_id(db, message.from_user.id)
    if user and user.status == UserStatus.ACTIVE.value:
        return False
    
    potential_code = message.text.strip()
    if len(potential_code) < 4 or len(potential_code) > 20:
        return False
    
    referrer = await get_user_by_referral_code(db, potential_code)
    if not referrer:
        await message.answer(
            "❌ Неверный реферальный код.\n\n"
            "💡 Если у вас есть реферальный код, убедитесь что он введен правильно.\n"
            "⏭️ Для продолжения регистрации без реферального кода используйте команду /start"
        )
        return True 
    
    data = await state.get_data() or {}
    data['referral_code'] = potential_code
    data['referrer_id'] = referrer.id
    await state.set_data(data)
    
    await message.answer("✅ Реферальный код принят!")
    logger.info(f"✅ Реферальный код {potential_code} применен для пользователя {message.from_user.id}")
    
    if current_state != RegistrationStates.waiting_for_referral_code.state:
        language = data.get('language', 'ru')
        texts = get_texts(language)
        
        await message.answer(
            texts.RULES_TEXT,
            reply_markup=get_rules_keyboard(language)
        )
        await state.set_state(RegistrationStates.waiting_for_rules_accept)
        logger.info("📋 Правила отправлены после ввода реферального кода")
    else:
        await complete_registration(message, state, db)
    
    return True 


async def cmd_start(message: types.Message, state: FSMContext, db: AsyncSession, db_user=None):
    logger.info(f"🚀 START: Обработка /start от {message.from_user.id}")
    
    referral_code = None
    if len(message.text.split()) > 1:
        potential_code = message.text.split()[1]
        referral_code = potential_code
        logger.info(f"🔎 Найден реферальный код: {referral_code}")
    
    if referral_code:
        await state.set_data({'referral_code': referral_code})
    
    user = db_user if db_user else await get_user_by_telegram_id(db, message.from_user.id)
    
    if user and user.status != UserStatus.DELETED.value:
        logger.info(f"✅ Активный пользователь найден: {user.telegram_id}")
        
        profile_updated = False
        
        if user.username != message.from_user.username:
            old_username = user.username
            user.username = message.from_user.username
            logger.info(f"📝 Username обновлен: '{old_username}' → '{user.username}'")
            profile_updated = True
        
        if user.first_name != message.from_user.first_name:
            old_first_name = user.first_name
            user.first_name = message.from_user.first_name
            logger.info(f"📝 Имя обновлено: '{old_first_name}' → '{user.first_name}'")
            profile_updated = True
        
        if user.last_name != message.from_user.last_name:
            old_last_name = user.last_name
            user.last_name = message.from_user.last_name
            logger.info(f"📝 Фамилия обновлена: '{old_last_name}' → '{user.last_name}'")
            profile_updated = True
        
        user.last_activity = datetime.utcnow()
        
        if profile_updated:
            user.updated_at = datetime.utcnow()
            await db.commit()
            await db.refresh(user)
            logger.info(f"💾 Профиль пользователя {user.telegram_id} обновлен")
        else:
            await db.commit()
        
        texts = get_texts(user.language)
        
        if referral_code and not user.referred_by_id:
            await message.answer("ℹ️ Вы уже зарегистрированы в системе. Реферальная ссылка не может быть применена.")
        
        has_active_subscription = user.subscription is not None
        subscription_is_active = False
        
        if user.subscription:
            subscription_is_active = user.subscription.is_active
        
        await message.answer(
            texts.MAIN_MENU.format(
                user_name=user.full_name,
                subscription_status=_get_subscription_status(user, texts)
            ),
            reply_markup=get_main_menu_keyboard(
                language=user.language,
                is_admin=settings.is_admin(user.telegram_id),
                has_had_paid_subscription=user.has_had_paid_subscription,
                has_active_subscription=has_active_subscription,
                subscription_is_active=subscription_is_active,
                balance_kopeks=user.balance_kopeks 
            )
        )
        await state.clear()
        return
    
    if user and user.status == UserStatus.DELETED.value:
        logger.info(f"🔄 Удаленный пользователь {user.telegram_id} начинает повторную регистрацию")
        
        try:
            from app.services.user_service import UserService
            from app.database.models import (
                Subscription, Transaction, PromoCodeUse, 
                ReferralEarning, SubscriptionServer
            )
            from sqlalchemy import delete
            
            if user.subscription:
                await db.execute(
                    delete(SubscriptionServer).where(
                        SubscriptionServer.subscription_id == user.subscription.id
                    )
                )
                logger.info(f"🗑️ Удалены записи SubscriptionServer")
            
            if user.subscription:
                await db.delete(user.subscription)
                logger.info(f"🗑️ Удалена подписка пользователя")
            
            await db.execute(
                delete(PromoCodeUse).where(PromoCodeUse.user_id == user.id)
            )
            
            await db.execute(
                delete(ReferralEarning).where(ReferralEarning.user_id == user.id)
            )
            await db.execute(
                delete(ReferralEarning).where(ReferralEarning.referral_id == user.id)
            )
            
            await db.execute(
                delete(Transaction).where(Transaction.user_id == user.id)
            )
            
            user.status = UserStatus.ACTIVE.value
            user.balance_kopeks = 0
            user.remnawave_uuid = None
            user.has_had_paid_subscription = False
            user.referred_by_id = None
            
            user.username = message.from_user.username
            user.first_name = message.from_user.first_name
            user.last_name = message.from_user.last_name
            user.updated_at = datetime.utcnow()
            user.last_activity = datetime.utcnow()
            
            from app.utils.user_utils import generate_unique_referral_code
            user.referral_code = await generate_unique_referral_code(db, user.telegram_id)
            
            await db.commit()
            
            logger.info(f"✅ Пользователь {user.telegram_id} подготовлен к восстановлению")
            
        except Exception as e:
            logger.error(f"❌ Ошибка подготовки к восстановлению: {e}")
            await db.rollback()
    else:
        logger.info(f"🆕 Новый пользователь, начинаем регистрацию")
    
    language = 'ru'
    texts = get_texts(language)
    
    data = await state.get_data() or {}
    data['language'] = language
    await state.set_data(data)
    logger.info(f"💾 Установлен русский язык по умолчанию")
    
    await message.answer(
        texts.RULES_TEXT,
        reply_markup=get_rules_keyboard(language)
    )
    logger.info(f"📋 Правила отправлены")
    
    await state.set_state(RegistrationStates.waiting_for_rules_accept)
    current_state = await state.get_state()
    logger.info(f"📊 Установлено состояние: {current_state}")


async def process_rules_accept(
    callback: types.CallbackQuery, 
    state: FSMContext, 
    db: AsyncSession
):
    
    logger.info(f"📋 RULES: Начало обработки правил")
    logger.info(f"📊 Callback data: {callback.data}")
    logger.info(f"👤 User: {callback.from_user.id}")
    
    current_state = await state.get_state()
    logger.info(f"📊 Текущее состояние: {current_state}")
    
    try:
        await callback.answer()
        
        data = await state.get_data()
        language = data.get('language', 'ru')
        texts = get_texts(language)
        
        if callback.data == 'rules_accept':
            logger.info(f"✅ Правила приняты пользователем {callback.from_user.id}")
            
            try:
                await callback.message.delete()
                logger.info(f"🗑️ Сообщение с правилами удалено")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось удалить сообщение с правилами: {e}")
                try:
                    await callback.message.edit_text(
                        "✅ Правила приняты! Завершаем регистрацию...",
                        reply_markup=None
                    )
                except:
                    pass
            
            if data.get('referral_code'):
                logger.info(f"🎫 Найден реферальный код из deep link: {data['referral_code']}")
                
                referrer = await get_user_by_referral_code(db, data['referral_code'])
                if referrer:
                    data['referrer_id'] = referrer.id
                    await state.set_data(data)
                    logger.info(f"✅ Реферер найден: {referrer.id}")
                
                await complete_registration_from_callback(callback, state, db)
            else:
                try:
                    await callback.message.answer(
                        "У вас есть реферальный код? Введите его или нажмите 'Пропустить'",
                        reply_markup=get_referral_code_keyboard(language)
                    )
                    await state.set_state(RegistrationStates.waiting_for_referral_code)
                    logger.info(f"🔍 Ожидание ввода реферального кода")
                except Exception as e:
                    logger.error(f"Ошибка при показе вопроса о реферальном коде: {e}")
                    await complete_registration_from_callback(callback, state, db)
                    
        else:
            logger.info(f"❌ Правила отклонены пользователем {callback.from_user.id}")
            
            try:
                rules_required_text = getattr(texts, 'RULES_REQUIRED', 
                                             "Для использования бота необходимо принять правила сервиса.")
                await callback.message.edit_text(
                    rules_required_text,
                    reply_markup=get_rules_keyboard(language)
                )
            except Exception as e:
                logger.error(f"Ошибка при показе сообщения об отклонении правил: {e}")
                await callback.message.edit_text(
                    "Для использования бота необходимо принять правила сервиса.",
                    reply_markup=get_rules_keyboard(language)
                )
        
        logger.info(f"✅ Правила обработаны для пользователя {callback.from_user.id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки правил: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка. Попробуйте еще раз.", show_alert=True)
        
        try:
            data = await state.get_data()
            language = data.get('language', 'ru')
            await callback.message.answer(
                "Произошла ошибка. Попробуйте принять правила еще раз:",
                reply_markup=get_rules_keyboard(language)
            )
            await state.set_state(RegistrationStates.waiting_for_rules_accept)
        except:
            pass


async def process_referral_code_input(
    message: types.Message, 
    state: FSMContext, 
    db: AsyncSession
):
    
    logger.info(f"🎫 REFERRAL: Обработка реферального кода: {message.text}")
    
    data = await state.get_data()
    language = data.get('language', 'ru')
    texts = get_texts(language)
    
    referral_code = message.text.strip()
    
    referrer = await get_user_by_referral_code(db, referral_code)
    if referrer:
        data['referrer_id'] = referrer.id
        await state.set_data(data)
        await message.answer("✅ Реферальный код применен!")
        logger.info(f"✅ Реферальный код применен")
    else:
        await message.answer("❌ Неверный реферальный код")
        logger.info(f"❌ Неверный реферальный код")
        return
    
    await complete_registration(message, state, db)


async def process_referral_code_skip(
    callback: types.CallbackQuery, 
    state: FSMContext, 
    db: AsyncSession
):
    
    logger.info(f"⭐️ SKIP: Пропуск реферального кода от пользователя {callback.from_user.id}")
    await callback.answer()
    
    try:
        await callback.message.delete()
        logger.info(f"🗑️ Сообщение с вопросом о реферальном коде удалено")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось удалить сообщение с вопросом о реферальном коде: {e}")
        try:
            await callback.message.edit_text(
                "✅ Завершаем регистрацию...",
                reply_markup=None
            )
        except:
            pass
    
    await complete_registration_from_callback(callback, state, db)



async def complete_registration_from_callback(
    callback: types.CallbackQuery,
    state: FSMContext, 
    db: AsyncSession
):
    
    logger.info(f"🏁 COMPLETE: Завершение регистрации для пользователя {callback.from_user.id}")
    
    existing_user = await get_user_by_telegram_id(db, callback.from_user.id)
    
    if existing_user and existing_user.status == UserStatus.ACTIVE.value:
        logger.warning(f"⚠️ Пользователь {callback.from_user.id} уже активен! Показываем главное меню.")
        texts = get_texts(existing_user.language)
        
        data = await state.get_data()
        if data.get('referral_code') and not existing_user.referred_by_id:
            await callback.message.answer("ℹ️ Вы уже зарегистрированы в системе. Реферальная ссылка не может быть применена.")
        
        has_active_subscription = existing_user.subscription is not None
        subscription_is_active = False
        
        if existing_user.subscription:
            subscription_is_active = existing_user.subscription.is_active
        
        user_name = existing_user.full_name
        user_telegram_id = existing_user.telegram_id
        user_language = existing_user.language
        has_had_paid_subscription = existing_user.has_had_paid_subscription
        balance_kopeks = existing_user.balance_kopeks
        
        try:
            await callback.message.answer(
                texts.MAIN_MENU.format(
                    user_name=user_name,
                    subscription_status=_get_subscription_status(existing_user, texts)
                ),
                reply_markup=get_main_menu_keyboard(
                    language=user_language,
                    is_admin=settings.is_admin(user_telegram_id),
                    has_had_paid_subscription=has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=balance_kopeks
                )
            )
        except Exception as e:
            logger.error(f"Ошибка при показе главного меню существующему пользователю: {e}")
            await callback.message.answer(f"Добро пожаловать, {user_name}!")
        
        await state.clear()
        return
    
    data = await state.get_data()
    language = data.get('language', 'ru')
    texts = get_texts(language)
    
    referrer_id = data.get('referrer_id')
    if not referrer_id and data.get('referral_code'):
        referrer = await get_user_by_referral_code(db, data['referral_code'])
        if referrer:
            referrer_id = referrer.id
    
    if existing_user and existing_user.status == UserStatus.DELETED.value:
        logger.info(f"🔄 Восстанавливаем удаленного пользователя {callback.from_user.id}")
        
        existing_user.username = callback.from_user.username
        existing_user.first_name = callback.from_user.first_name
        existing_user.last_name = callback.from_user.last_name
        existing_user.language = language
        existing_user.referred_by_id = referrer_id
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.balance_kopeks = 0
        existing_user.has_had_paid_subscription = False
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user)
        
        user = existing_user
        logger.info(f"✅ Пользователь {callback.from_user.id} восстановлен")
        
    elif not existing_user:
        logger.info(f"🆕 Создаем нового пользователя {callback.from_user.id}")
        
        referral_code = await generate_unique_referral_code(db, callback.from_user.id)
        
        user = await create_user(
            db=db,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
            language=language,
            referred_by_id=referrer_id,
            referral_code=referral_code 
        )
    else:
        logger.info(f"🔄 Обновляем существующего пользователя {callback.from_user.id}")
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.language = language
        if referrer_id and not existing_user.referred_by_id:
            existing_user.referred_by_id = referrer_id
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user)
        user = existing_user
    
    if referrer_id:
        try:
            await process_referral_registration(db, user.id, referrer_id)
            bonus_message = f"🎉 Вы получили {settings.REFERRED_USER_REWARD/100}₽ за регистрацию по реферальной ссылке!"
            await callback.message.answer(bonus_message)
        except Exception as e:
            logger.error(f"Ошибка при обработке реферальной регистрации: {e}")
    
    await state.clear()
    
    has_active_subscription = False 
    subscription_is_active = False
    user_name = user.full_name
    balance_kopeks = user.balance_kopeks
    user_telegram_id = user.telegram_id
    user_language = user.language
    has_had_paid_subscription = user.has_had_paid_subscription
    
    try:
        await callback.message.answer(
            texts.MAIN_MENU.format(
                user_name=user_name,
                subscription_status=_get_subscription_status_simple(texts)
            ),
            reply_markup=get_main_menu_keyboard(
                language=user_language,
                is_admin=settings.is_admin(user_telegram_id),
                has_had_paid_subscription=has_had_paid_subscription,
                has_active_subscription=has_active_subscription,
                subscription_is_active=subscription_is_active,
                balance_kopeks=balance_kopeks
            )
        )
        logger.info(f"✅ Главное меню отправлено для пользователя {user_telegram_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке главного меню: {e}")
        try:
            balance_rubles = balance_kopeks / 100
            await callback.message.answer(
                f"Добро пожаловать, {user_name}!\n"
                f"Баланс: {balance_rubles:.2f} ₽\n"
                f"Подписка: Нет активной подписки",
                reply_markup=get_main_menu_keyboard(
                    language=user_language,
                    is_admin=settings.is_admin(user_telegram_id),
                    has_had_paid_subscription=has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=balance_kopeks
                )
            )
            logger.info(f"✅ Fallback главное меню отправлено для пользователя {user_telegram_id}")
        except Exception as fallback_error:
            logger.error(f"⛔ Критическая ошибка при отправке fallback меню: {fallback_error}")
            try:
                await callback.message.answer(f"Добро пожаловать, {user_name}! Регистрация завершена.")
                logger.info(f"✅ Простое приветствие отправлено для пользователя {user_telegram_id}")
            except Exception as final_error:
                logger.error(f"⛔ Критическая ошибка при отправке простого сообщения: {final_error}")
    
    logger.info(f"✅ Регистрация завершена для пользователя: {user_telegram_id}")


async def complete_registration(
    message: types.Message, 
    state: FSMContext, 
    db: AsyncSession
):
    
    logger.info(f"🏁 COMPLETE: Завершение регистрации для пользователя {message.from_user.id}")
    
    existing_user = await get_user_by_telegram_id(db, message.from_user.id)
    
    if existing_user and existing_user.status == UserStatus.ACTIVE.value:
        logger.warning(f"⚠️ Пользователь {message.from_user.id} уже активен! Показываем главное меню.")
        texts = get_texts(existing_user.language)
        
        data = await state.get_data()
        if data.get('referral_code') and not existing_user.referred_by_id:
            await message.answer("ℹ️ Вы уже зарегистрированы в системе. Реферальная ссылка не может быть применена.")
        
        has_active_subscription = existing_user.subscription is not None
        subscription_is_active = False
        
        if existing_user.subscription:
            subscription_is_active = existing_user.subscription.is_active
        
        user_name = existing_user.full_name
        user_telegram_id = existing_user.telegram_id
        user_language = existing_user.language
        has_had_paid_subscription = existing_user.has_had_paid_subscription
        balance_kopeks = existing_user.balance_kopeks
        
        try:
            await message.answer(
                texts.MAIN_MENU.format(
                    user_name=user_name,
                    subscription_status=_get_subscription_status(existing_user, texts)
                ),
                reply_markup=get_main_menu_keyboard(
                    language=user_language,
                    is_admin=settings.is_admin(user_telegram_id),
                    has_had_paid_subscription=has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=balance_kopeks
                )
            )
        except Exception as e:
            logger.error(f"Ошибка при показе главного меню существующему пользователю: {e}")
            await message.answer(f"Добро пожаловать, {user_name}!")
        
        await state.clear()
        return
    
    data = await state.get_data()
    language = data.get('language', 'ru')
    texts = get_texts(language)
    
    referrer_id = data.get('referrer_id')
    if not referrer_id and data.get('referral_code'):
        referrer = await get_user_by_referral_code(db, data['referral_code'])
        if referrer:
            referrer_id = referrer.id
    
    if existing_user and existing_user.status == UserStatus.DELETED.value:
        logger.info(f"🔄 Восстанавливаем удаленного пользователя {message.from_user.id}")
        
        existing_user.username = message.from_user.username
        existing_user.first_name = message.from_user.first_name
        existing_user.last_name = message.from_user.last_name
        existing_user.language = language
        existing_user.referred_by_id = referrer_id
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.balance_kopeks = 0
        existing_user.has_had_paid_subscription = False
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user)
        
        user = existing_user
        logger.info(f"✅ Пользователь {message.from_user.id} восстановлен")
        
    elif not existing_user:
        logger.info(f"🆕 Создаем нового пользователя {message.from_user.id}")
        
        referral_code = await generate_unique_referral_code(db, message.from_user.id)
        
        user = await create_user(
            db=db,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            language=language,
            referred_by_id=referrer_id,
            referral_code=referral_code
        )
    else:
        logger.info(f"🔄 Обновляем существующего пользователя {message.from_user.id}")
        existing_user.status = UserStatus.ACTIVE.value
        existing_user.language = language
        if referrer_id and not existing_user.referred_by_id:
            existing_user.referred_by_id = referrer_id
        
        from datetime import datetime
        existing_user.updated_at = datetime.utcnow()
        existing_user.last_activity = datetime.utcnow()
        
        await db.commit()
        await db.refresh(existing_user)
        user = existing_user
    
    if referrer_id:
        try:
            await process_referral_registration(db, user.id, referrer_id)
            bonus_message = f"🎉 Вы получили {settings.REFERRED_USER_REWARD/100}₽ за регистрацию по реферальной ссылке!"
            await message.answer(bonus_message)
        except Exception as e:
            logger.error(f"Ошибка при обработке реферальной регистрации: {e}")
    
    await state.clear()
    
    has_active_subscription = False
    subscription_is_active = False
    user_name = user.full_name
    balance_kopeks = user.balance_kopeks
    user_telegram_id = user.telegram_id
    user_language = user.language
    has_had_paid_subscription = user.has_had_paid_subscription
    
    try:
        await message.answer(
            texts.MAIN_MENU.format(
                user_name=user_name,
                subscription_status=_get_subscription_status_simple(texts)
            ),
            reply_markup=get_main_menu_keyboard(
                language=user_language,
                is_admin=settings.is_admin(user_telegram_id),
                has_had_paid_subscription=has_had_paid_subscription,
                has_active_subscription=has_active_subscription,
                subscription_is_active=subscription_is_active,
                balance_kopeks=balance_kopeks
            )
        )
        logger.info(f"✅ Главное меню отправлено для пользователя {user_telegram_id}")
    except Exception as e:
        logger.error(f"Ошибка при отправке главного меню: {e}")
        try:
            balance_rubles = balance_kopeks / 100
            await message.answer(
                f"Добро пожаловать, {user_name}!\n"
                f"Баланс: {balance_rubles:.2f} ₽\n"
                f"Подписка: Нет активной подписки",
                reply_markup=get_main_menu_keyboard(
                    language=user_language,
                    is_admin=settings.is_admin(user_telegram_id),
                    has_had_paid_subscription=has_had_paid_subscription,
                    has_active_subscription=has_active_subscription,
                    subscription_is_active=subscription_is_active,
                    balance_kopeks=balance_kopeks
                )
            )
            logger.info(f"✅ Fallback главное меню отправлено для пользователя {user_telegram_id}")
        except Exception as fallback_error:
            logger.error(f"⛔ Критическая ошибка при отправке fallback меню: {fallback_error}")
            try:
                await message.answer(f"Добро пожаловать, {user_name}! Регистрация завершена.")
                logger.info(f"✅ Простое приветствие отправлено для пользователя {user_telegram_id}")
            except:
                pass
    
    logger.info(f"✅ Регистрация завершена для пользователя: {user_telegram_id}")


def _get_subscription_status(user, texts):
    if not user or not hasattr(user, 'subscription'):
        return getattr(texts, 'SUBSCRIPTION_NONE', 'Нет активной подписки')
    
    if not user.subscription:
        return getattr(texts, 'SUBSCRIPTION_NONE', 'Нет активной подписки')
    
    subscription = user.subscription
    
    from datetime import datetime
    current_time = datetime.utcnow()
    
    if hasattr(subscription, 'end_date') and subscription.end_date <= current_time:
        return f"🔴 Истекла\n📅 {subscription.end_date.strftime('%d.%m.%Y')}"
    
    if hasattr(subscription, 'end_date'):
        days_left = (subscription.end_date - current_time).days
    else:
        days_left = 0
    
    is_trial = getattr(subscription, 'is_trial', False)
    
    if is_trial:
        if days_left > 1:
            return f"🎁 Тестовая подписка\n📅 до {subscription.end_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
        elif days_left == 1:
            return "🎁 Тестовая подписка\n⚠️ истекает завтра!"
        else:
            return "🎁 Тестовая подписка\n⚠️ истекает сегодня!"
    else: 
        if days_left > 7:
            return f"💎 Активна\n📅 до {subscription.end_date.strftime('%d.%m.%Y')} ({days_left} дн.)"
        elif days_left > 1:
            return f"💎 Активна\n⚠️ истекает через {days_left} дн."
        elif days_left == 1:
            return "💎 Активна\n⚠️ истекает завтра!"
        else:
            return "💎 Активна\n⚠️ истекает сегодня!"



def _get_subscription_status_simple(texts):
    return getattr(texts, 'SUBSCRIPTION_NONE', 'Нет активной подписки')


def get_referral_code_keyboard(language: str):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⭐️ Пропустить",
            callback_data="referral_skip"
        )]
    ])


def register_handlers(dp: Dispatcher):
    
    logger.info("🔧 === НАЧАЛО регистрации обработчиков start.py ===")
    
    dp.message.register(
        cmd_start,
        Command("start")
    )
    logger.info("✅ Зарегистрирован cmd_start")
    
    dp.callback_query.register(
        process_rules_accept,
        F.data.in_(["rules_accept", "rules_decline"]),
        StateFilter(RegistrationStates.waiting_for_rules_accept)
    )
    logger.info("✅ Зарегистрирован process_rules_accept")
    
    dp.callback_query.register(
        process_referral_code_skip,
        F.data == "referral_skip",
        StateFilter(RegistrationStates.waiting_for_referral_code)
    )
    logger.info("✅ Зарегистрирован process_referral_code_skip")
    
    dp.message.register(
        process_referral_code_input,
        StateFilter(RegistrationStates.waiting_for_referral_code)
    )
    logger.info("✅ Зарегистрирован process_referral_code_input")
    
    dp.message.register(
        handle_potential_referral_code,
        StateFilter(
            RegistrationStates.waiting_for_rules_accept,
            RegistrationStates.waiting_for_referral_code
        )
    )
    logger.info("✅ Зарегистрирован handle_potential_referral_code")
    
    logger.info("🔧 === КОНЕЦ регистрации обработчиков start.py ===")
