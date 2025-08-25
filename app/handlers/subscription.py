import logging
from datetime import datetime, timedelta
from aiogram import Dispatcher, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
import json
import os
from typing import Dict, List, Any, Tuple, Optional

from app.config import settings, PERIOD_PRICES, TRAFFIC_PRICES
from app.states import SubscriptionStates
from app.database.crud.subscription import (
    get_subscription_by_user_id, create_trial_subscription, 
    create_paid_subscription, extend_subscription,
    add_subscription_traffic, add_subscription_devices,
    add_subscription_squad, update_subscription_autopay,
    add_subscription_servers  
)
from app.database.crud.user import subtract_user_balance
from app.database.crud.transaction import create_transaction, get_user_transactions
from app.database.models import (
    User, TransactionType, SubscriptionStatus, 
    SubscriptionServer  
)
from app.keyboards.inline import (
    get_subscription_keyboard, get_trial_keyboard,
    get_subscription_period_keyboard, get_traffic_packages_keyboard,
    get_countries_keyboard, get_devices_keyboard,
    get_subscription_confirm_keyboard, get_autopay_keyboard,
    get_autopay_days_keyboard, get_back_keyboard,
    get_extend_subscription_keyboard, get_add_traffic_keyboard,
    get_add_devices_keyboard, get_reset_traffic_confirm_keyboard,
    get_manage_countries_keyboard,
    get_device_selection_keyboard, get_connection_guide_keyboard,
    get_app_selection_keyboard, get_specific_app_keyboard,
    get_subscription_settings_keyboard, get_extend_subscription_keyboard_with_prices
)
from app.localization.texts import get_texts
from app.services.remnawave_service import RemnaWaveService
from app.services.subscription_service import SubscriptionService
from app.services.referral_service import process_referral_purchase

logger = logging.getLogger(__name__)


async def show_subscription_info(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    await db.refresh(db_user)
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription:
        await callback.message.edit_text(
            texts.SUBSCRIPTION_NONE,
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    from app.database.crud.subscription import check_and_update_subscription_status
    subscription = await check_and_update_subscription_status(db, subscription)
    
    subscription_service = SubscriptionService()
    await subscription_service.sync_subscription_usage(db, subscription)
    
    await db.refresh(subscription)
    
    current_time = datetime.utcnow()
    
    if subscription.status == "expired" or subscription.end_date <= current_time:
        actual_status = "expired"
        status_display = "Истекла"
        status_emoji = "🔴"
    elif subscription.status == "active" and subscription.end_date > current_time:
        if subscription.is_trial:
            actual_status = "trial_active"
            status_display = "Тестовая"
            status_emoji = "🎁"
        else:
            actual_status = "paid_active"
            status_display = "Активна"
            status_emoji = "💎"
    else:
        actual_status = "unknown"
        status_display = "Неизвестно"
        status_emoji = "❓"
    
    if subscription.end_date <= current_time:
        days_left = 0
        time_left_text = "истёк"
        warning_text = "" 
    else:
        delta = subscription.end_date - current_time
        days_left = delta.days
        hours_left = delta.seconds // 3600
        
        if days_left > 1:
            time_left_text = f"{days_left} дн."
            warning_text = ""
        elif days_left == 1:
            time_left_text = f"{days_left} дн."
            warning_text = "\n⚠️ истекает завтра!"
        elif hours_left > 0:
            time_left_text = f"{hours_left} ч."
            warning_text = "\n⚠️ истекает сегодня!"
        else:
            minutes_left = (delta.seconds % 3600) // 60
            time_left_text = f"{minutes_left} мин."
            warning_text = "\n🔴 истекает через несколько минут!"
    
    subscription_type = "Триал" if subscription.is_trial else "Платная"
    
    if subscription.traffic_limit_gb == 0:
        traffic_used_display = f"∞ (безлимит) / {subscription.traffic_used_gb:.1f} ГБ"
    else:
        traffic_used_display = f"{subscription.traffic_used_gb:.1f} / {subscription.traffic_limit_gb} ГБ"
    
    devices_used = await get_current_devices_count(db_user)
    

    message = f"""👤 {db_user.full_name}
━━━━━━━━━━━━━━━━━
💰 Баланс: {settings.format_price(db_user.balance_kopeks)}
📱 Подписка: {status_emoji} {status_display}{warning_text}
━━━━━━━━━━━━━━━━━

📱 Информация о подписке
🎭 Тип: {subscription_type}
📅 Действует до: {subscription.end_date.strftime("%d.%m.%Y %H:%M")}
⏰ Осталось: {time_left_text}
📈 Трафик: {traffic_used_display}
🌍 Серверы: {len(subscription.connected_squads)} стран
📱 Устройства: {devices_used} / {subscription.device_limit}"""
    
    if hasattr(subscription, 'subscription_url') and subscription.subscription_url:
        if actual_status in ['trial_active', 'paid_active']:
            message += f"\n\n🔗 <b>Ссылка для подключения:</b>\n<code>{subscription.subscription_url}</code>"
            message += f"\n\n📱 Скопируйте ссылку и добавьте в ваше VPN приложение"
    
    await callback.message.edit_text(
        message,
        reply_markup=get_subscription_keyboard(
            db_user.language,
            has_subscription=True,
            is_trial=subscription.is_trial,
            subscription=subscription
        ),
        parse_mode="HTML"
    )
    await callback.answer()

async def get_current_devices_count(db_user: User) -> str:
    try:
        if not db_user.remnawave_uuid:
            return "—"
        
        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()
        
        async with service.api as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')
            
            if response and 'response' in response:
                total_devices = response['response'].get('total', 0)
                return str(total_devices)
            else:
                return "—"
                
    except Exception as e:
        logger.error(f"Ошибка получения количества устройств: {e}")
        return "—"


async def get_subscription_cost(subscription, db: AsyncSession) -> int:
    try:
        if subscription.is_trial:
            return 0
        
        from app.config import TRAFFIC_PRICES, PERIOD_PRICES, settings
        from app.services.subscription_service import SubscriptionService
        
        subscription_service = SubscriptionService()
        
        base_cost = PERIOD_PRICES.get(30, 0)
        
        try:
            servers_cost, _ = await subscription_service.get_countries_price_by_uuids(
                subscription.connected_squads, db
            )
        except AttributeError:
            servers_cost, _ = await get_countries_price_by_uuids_fallback(
                subscription.connected_squads, db
            )
        
        traffic_cost = TRAFFIC_PRICES.get(subscription.traffic_limit_gb, 0)
        devices_cost = max(0, subscription.device_limit - 1) * settings.PRICE_PER_DEVICE
        
        total_cost = base_cost + servers_cost + traffic_cost + devices_cost
        
        logger.info(f"📊 Месячная стоимость конфигурации подписки {subscription.id}:")
        logger.info(f"   📅 Базовый тариф (30 дней): {base_cost/100}₽")
        logger.info(f"   🌍 Серверы: {servers_cost/100}₽")
        logger.info(f"   📊 Трафик: {traffic_cost/100}₽")
        logger.info(f"   📱 Устройства: {devices_cost/100}₽")
        logger.info(f"   💎 ИТОГО: {total_cost/100}₽")
        
        return total_cost
        
    except Exception as e:
        logger.error(f"⚠ Ошибка расчета стоимости подписки: {e}")
        return 0


async def show_trial_offer(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)
    
    if db_user.subscription or db_user.has_had_paid_subscription:
        await callback.message.edit_text(
            texts.TRIAL_ALREADY_USED,
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    trial_text = texts.TRIAL_AVAILABLE.format(
        days=settings.TRIAL_DURATION_DAYS,
        traffic=settings.TRIAL_TRAFFIC_LIMIT_GB,
        devices=settings.TRIAL_DEVICE_LIMIT
    )
    
    await callback.message.edit_text(
        trial_text,
        reply_markup=get_trial_keyboard(db_user.language)
    )
    await callback.answer()


async def activate_trial(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    
    if db_user.subscription or db_user.has_had_paid_subscription:
        await callback.message.edit_text(
            texts.TRIAL_ALREADY_USED,
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    try:
        subscription = await create_trial_subscription(db, db_user.id)
        
        await db.refresh(db_user)
        
        subscription_service = SubscriptionService()
        remnawave_user = await subscription_service.create_remnawave_user(
            db, subscription
        )
        
        await db.refresh(db_user)
        
        if remnawave_user and hasattr(subscription, 'subscription_url') and subscription.subscription_url:
            trial_success_text = f"{texts.TRIAL_ACTIVATED}\n\n"
            trial_success_text += f"🔗 <b>Ваша ссылка для подключения:</b>\n"
            trial_success_text += f"<code>{subscription.subscription_url}</code>\n\n"
            trial_success_text += f"📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве"
    
            connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔗 Подключиться", callback_data="subscription_connect")
                ],
                [
                    InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")
                ],
                [
                    InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_menu")
                ]
            ])
    
            await callback.message.edit_text(
                trial_success_text,
                reply_markup=connect_keyboard,
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"{texts.TRIAL_ACTIVATED}\n\n⚠️ Ссылка генерируется, попробуйте перейти в раздел 'Моя подписка' через несколько секунд.",
                reply_markup=get_back_keyboard(db_user.language)
            )
        
        logger.info(f"✅ Активирована тестовая подписка для пользователя {db_user.telegram_id}")
        
    except Exception as e:
        logger.error(f"Ошибка активации триала: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await callback.answer()


async def start_subscription_purchase(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    texts = get_texts(db_user.language)
    
    await callback.message.edit_text(
        texts.BUY_SUBSCRIPTION_START,
        reply_markup=get_subscription_period_keyboard(db_user.language)
    )
    
    initial_data = {
        'period_days': None,
        'countries': [],
        'devices': 1,
        'total_price': 0
    }
    
    if settings.is_traffic_fixed():
        initial_data['traffic_gb'] = settings.get_fixed_traffic_limit()
    else:
        initial_data['traffic_gb'] = None
    
    await state.set_data(initial_data)
    await state.set_state(SubscriptionStates.selecting_period)
    await callback.answer()



async def handle_add_countries(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or subscription.is_trial:
        await callback.answer("❌ Эта функция доступна только для платных подписок", show_alert=True)
        return
    
    countries = await _get_available_countries()
    current_countries = subscription.connected_squads
    
    current_countries_names = []
    for country in countries:
        if country['uuid'] in current_countries:
            current_countries_names.append(country['name'])
    
    text = "🌍 <b>Управление странами подписки</b>\n\n"
    text += f"📍 <b>Текущие страны ({len(current_countries)}):</b>\n"
    if current_countries_names:
        text += "\n".join(f"• {name}" for name in current_countries_names)
    else:
        text += "Нет подключенных стран"
    
    text += "\n\n💡 <b>Инструкция:</b>\n"
    text += "✅ - страна подключена\n"
    text += "➕ - будет добавлена (платно)\n"
    text += "➖ - будет отключена (бесплатно)\n"
    text += "⚪ - не выбрана\n\n"
    text += "⚠️ <b>Важно:</b> Повторное подключение отключенных стран будет платным!"
    
    await state.update_data(countries=current_countries.copy())
    
    await callback.message.edit_text(
        text,
        reply_markup=get_manage_countries_keyboard(
            countries, 
            current_countries.copy(), 
            current_countries, 
            db_user.language
        ),
        parse_mode="HTML"
    )
    
    await callback.answer()

async def get_countries_price_by_uuids_fallback(country_uuids: List[str], db: AsyncSession) -> Tuple[int, List[int]]:
    try:
        from app.database.crud.server_squad import get_server_squad_by_uuid
        
        total_price = 0
        prices_list = []
        
        for country_uuid in country_uuids:
            try:
                server = await get_server_squad_by_uuid(db, country_uuid)
                if server and server.is_available and not server.is_full:
                    price = server.price_kopeks
                    total_price += price
                    prices_list.append(price)
                else:
                    default_price = 1000
                    total_price += default_price
                    prices_list.append(default_price)
            except Exception:
                default_price = 1000
                total_price += default_price
                prices_list.append(default_price)
        
        return total_price, prices_list
        
    except Exception as e:
        logger.error(f"Ошибка fallback функции: {e}")
        default_prices = [1000] * len(country_uuids)
        return sum(default_prices), default_prices

async def handle_manage_country(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    logger.info(f"🔍 Управление страной: {callback.data}")
    
    country_uuid = callback.data.split('_')[2] 
    
    subscription = db_user.subscription
    if not subscription or subscription.is_trial:
        await callback.answer("❌ Только для платных подписок", show_alert=True)
        return
    
    data = await state.get_data()
    current_selected = data.get('countries', subscription.connected_squads.copy())
    
    if country_uuid in current_selected:
        current_selected.remove(country_uuid)
        action = "removed"
    else:
        current_selected.append(country_uuid)
        action = "added"
    
    logger.info(f"🔍 Страна {country_uuid} {action}")
    
    await state.update_data(countries=current_selected)
    
    countries = await _get_available_countries()
    
    try:
        await callback.message.edit_reply_markup(
            reply_markup=get_manage_countries_keyboard(
                countries, 
                current_selected, 
                subscription.connected_squads, 
                db_user.language
            )
        )
        logger.info(f"✅ Клавиатура обновлена")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обновления клавиатуры: {e}")
    
    await callback.answer()

async def apply_countries_changes(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    logger.info(f"🔍 Применение изменений стран")
    
    data = await state.get_data()
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    selected_countries = data.get('countries', [])
    current_countries = subscription.connected_squads
    
    added = [c for c in selected_countries if c not in current_countries]
    removed = [c for c in current_countries if c not in selected_countries]
    
    if not added and not removed:
        await callback.answer("⚠️ Изменения не обнаружены", show_alert=True)
        return
    
    logger.info(f"🔍 Добавлено: {added}, Удалено: {removed}")
    
    countries = await _get_available_countries()
    cost = 0
    added_names = []
    removed_names = []
    
    added_server_prices = []
    added_server_ids = []
    
    for country in countries:
        if country['uuid'] in added:
            cost += country['price_kopeks']
            added_names.append(country['name'])
            added_server_prices.append(country['price_kopeks'])
        if country['uuid'] in removed:
            removed_names.append(country['name'])
    
    if cost > 0 and db_user.balance_kopeks < cost:
        await callback.answer(
            f"❌ Недостаточно средств!\nТребуется: {texts.format_price(cost)}\nУ вас: {texts.format_price(db_user.balance_kopeks)}", 
            show_alert=True
        )
        return
    
    try:
        if added and cost > 0:
            success = await subtract_user_balance(
                db, db_user, cost, 
                f"Добавление стран: {', '.join(added_names)}"
            )
            if not success:
                await callback.answer("❌ Ошибка списания средств", show_alert=True)
                return
            
            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=cost,
                description=f"Добавление стран к подписке: {', '.join(added_names)}"
            )
        
        if added:
            from app.database.crud.server_squad import get_server_ids_by_uuids, add_user_to_servers
            from app.database.crud.subscription import add_subscription_servers
            
            added_server_ids = await get_server_ids_by_uuids(db, added)
            
            if added_server_ids:
                await add_subscription_servers(db, subscription, added_server_ids, added_server_prices)
                await add_user_to_servers(db, added_server_ids)
                
                logger.info(f"📊 Добавлены серверы с ценами: {list(zip(added_server_ids, added_server_prices))}")
        
        subscription.connected_squads = selected_countries
        subscription.updated_at = datetime.utcnow()
        await db.commit()
        
        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)
        
        if cost > 0:
            try:
                await process_referral_purchase(
                    db=db,
                    user_id=db_user.id,
                    purchase_amount_kopeks=cost,
                    transaction_id=None
                )
            except Exception as e:
                logger.error(f"Ошибка обработки реферальной покупки: {e}")
        
        await db.refresh(subscription)
        
        success_text = "✅ <b>Страны успешно обновлены!</b>\n\n"
        
        if added_names:
            success_text += f"➕ <b>Добавлены страны:</b>\n"
            success_text += "\n".join(f"• {name}" for name in added_names)
            if cost > 0:
                success_text += f"\n💰 Списано: {texts.format_price(cost)}"
            success_text += "\n"
        
        if removed_names:
            success_text += f"\n➖ <b>Отключены страны:</b>\n"
            success_text += "\n".join(f"• {name}" for name in removed_names)
            success_text += "\nℹ️ Повторное подключение будет платным\n"
        
        success_text += f"\n🌍 <b>Активных стран:</b> {len(selected_countries)}"
        
        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language),
            parse_mode="HTML"
        )
        
        await state.clear()
        logger.info(f"✅ Пользователь {db_user.telegram_id} обновил страны. Добавлено: {len(added)}, удалено: {len(removed)}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка применения изменений: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await callback.answer()


async def handle_add_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    from app.config import settings
    
    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный и не может быть изменен", show_alert=True)
        return
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or subscription.is_trial:
        await callback.answer("⌛ Эта функция доступна только для платных подписок", show_alert=True)
        return
    
    if subscription.traffic_limit_gb == 0:
        await callback.answer("⌛ У вас уже безлимитный трафик", show_alert=True)
        return
    
    current_traffic = subscription.traffic_limit_gb
    
    await callback.message.edit_text(
        f"📈 <b>Добавить трафик к подписке</b>\n\n"
        f"Текущий лимит: {texts.format_traffic(current_traffic)}\n"
        f"Выберите дополнительный трафик:",
        reply_markup=get_add_traffic_keyboard(db_user.language)
    )
    
    await callback.answer()


async def handle_add_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or subscription.is_trial:
        await callback.answer("❌ Эта функция доступна только для платных подписок", show_alert=True)
        return
    
    current_devices = subscription.device_limit
    
    await callback.message.edit_text(
        f"📱 <b>Добавить устройства к подписке</b>\n\n"
        f"Текущий лимит: {current_devices} устройств\n"
        f"Выберите количество дополнительных устройств:",
        reply_markup=get_add_devices_keyboard(current_devices, db_user.language)
    )
    
    await callback.answer()


async def handle_extend_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or subscription.is_trial:
        await callback.answer("❌ Продление доступно только для платных подписок", show_alert=True)
        return
    
    if subscription.days_left > 3:
        await callback.answer("❌ Продление доступно за 3 дня до окончания подписки", show_alert=True)
        return
    
    subscription_service = SubscriptionService()
    
    renewal_prices = {}
    for days in [30, 90, 180]:
        price = await subscription_service.calculate_renewal_price(subscription, days, db)
        renewal_prices[days] = price
    
    await callback.message.edit_text(
        f"⏰ <b>Продление подписки</b>\n\n"
        f"Осталось дней: {subscription.days_left}\n\n"
        f"<b>Ваша текущая конфигурация:</b>\n"
        f"🌍 Серверов: {len(subscription.connected_squads)}\n"
        f"📊 Трафик: {texts.format_traffic(subscription.traffic_limit_gb)}\n"
        f"📱 Устройств: {subscription.device_limit}\n\n"
        f"<b>Выберите период продления:</b>\n"
        f"📅 30 дней - {texts.format_price(renewal_prices[30])}\n"
        f"📅 90 дней - {texts.format_price(renewal_prices[90])}\n"
        f"📅 180 дней - {texts.format_price(renewal_prices[180])}\n\n"
        f"💡 <i>Цена включает все ваши текущие серверы и настройки</i>",
        reply_markup=get_extend_subscription_keyboard_with_prices(db_user.language, renewal_prices),
        parse_mode="HTML"
    )
    
    await callback.answer()


async def handle_reset_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    from app.config import settings
    
    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный и не может быть сброшен", show_alert=True)
        return
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or subscription.is_trial:
        await callback.answer("⌛ Эта функция доступна только для платных подписок", show_alert=True)
        return
    
    if subscription.traffic_limit_gb == 0:
        await callback.answer("⌛ У вас безлимитный трафик", show_alert=True)
        return
    
    reset_price = PERIOD_PRICES[30]
    
    if db_user.balance_kopeks < reset_price:
        await callback.answer("⌛ Недостаточно средств на балансе", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"🔄 <b>Сброс трафика</b>\n\n"
        f"Использовано: {texts.format_traffic(subscription.traffic_used_gb)}\n"
        f"Лимит: {texts.format_traffic(subscription.traffic_limit_gb)}\n\n"
        f"Стоимость сброса: {texts.format_price(reset_price)}\n\n"
        "После сброса счетчик использованного трафика станет равным 0.",
        reply_markup=get_reset_traffic_confirm_keyboard(reset_price, db_user.language)
    )
    
    await callback.answer()



async def confirm_add_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    from app.config import settings
    
    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный", show_alert=True)
        return
    
    traffic_gb = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    price = TRAFFIC_PRICES[traffic_gb]
    
    if db_user.balance_kopeks < price:
        await callback.answer("⌛ Недостаточно средств на балансе", show_alert=True)
        return
    
    try:
        success = await subtract_user_balance(
            db, db_user, price,
            f"Добавление {traffic_gb} ГБ трафика"
        )
        
        if not success:
            await callback.answer("⌛ Ошибка списания средств", show_alert=True)
            return
        
        if traffic_gb == 0: 
            subscription.traffic_limit_gb = 0
        else:
            await add_subscription_traffic(db, subscription, traffic_gb)
        
        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)
        
        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f"Добавление {traffic_gb} ГБ трафика"
        )
        
        try:
            await process_referral_purchase(
                db=db,
                user_id=db_user.id,
                purchase_amount_kopeks=price,
                transaction_id=None
            )
        except Exception as e:
            logger.error(f"Ошибка обработки реферальной покупки: {e}")
        
        await db.refresh(db_user)
        await db.refresh(subscription)
        
        success_text = f"✅ Трафик успешно добавлен!\n\n"
        if traffic_gb == 0:
            success_text += "🎉 Теперь у вас безлимитный трафик!"
        else:
            success_text += f"📈 Добавлено: {traffic_gb} ГБ\n"
            success_text += f"Новый лимит: {texts.format_traffic(subscription.traffic_limit_gb)}"
        
        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language)
        )
        
        logger.info(f"✅ Пользователь {db_user.telegram_id} добавил {traffic_gb} ГБ трафика")
        
    except Exception as e:
        logger.error(f"Ошибка добавления трафика: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await callback.answer()


async def confirm_add_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    devices_count = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    price = devices_count * settings.PRICE_PER_DEVICE
    
    if db_user.balance_kopeks < price:
        await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    try:
        success = await subtract_user_balance(
            db, db_user, price,
            f"Добавление {devices_count} устройств"
        )
        
        if not success:
            await callback.answer("❌ Ошибка списания средств", show_alert=True)
            return
        
        await add_subscription_devices(db, subscription, devices_count)
        
        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)
        
        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f"Добавление {devices_count} устройств"
        )
        
        try:
            await process_referral_purchase(
                db=db,
                user_id=db_user.id,
                purchase_amount_kopeks=price,
                transaction_id=None
            )
        except Exception as e:
            logger.error(f"Ошибка обработки реферальной покупки: {e}")
        
        await db.refresh(db_user)
        await db.refresh(subscription)
        
        await callback.message.edit_text(
            f"✅ Устройства успешно добавлены!\n\n"
            f"📱 Добавлено: {devices_count} устройств\n"
            f"Новый лимит: {subscription.device_limit} устройств",
            reply_markup=get_back_keyboard(db_user.language)
        )
        
        logger.info(f"✅ Пользователь {db_user.telegram_id} добавил {devices_count} устройств")
        
    except Exception as e:
        logger.error(f"Ошибка добавления устройств: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await callback.answer()


async def confirm_extend_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    days = int(callback.data.split('_')[2])
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    subscription_service = SubscriptionService()
    price = await subscription_service.calculate_renewal_price(subscription, days, db)
    
    if db_user.balance_kopeks < price:
        await callback.answer("❌ Недостаточно средств на балансе", show_alert=True)
        return
    
    try:
        success = await subtract_user_balance(
            db, db_user, price,
            f"Продление подписки на {days} дней"
        )
        
        if not success:
            await callback.answer("❌ Ошибка списания средств", show_alert=True)
            return
        
        await extend_subscription(db, subscription, days)
        
        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)
        
        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=price,
            description=f"Продление подписки на {days} дней"
        )
        
        try:
            await process_referral_purchase(
                db=db,
                user_id=db_user.id,
                purchase_amount_kopeks=price,
                transaction_id=None
            )
        except Exception as e:
            logger.error(f"Ошибка обработки реферальной покупки: {e}")
        
        await db.refresh(db_user)
        await db.refresh(subscription)
        
        await callback.message.edit_text(
            f"✅ Подписка успешно продлена!\n\n"
            f"⏰ Добавлено: {days} дней\n"
            f"Действует до: {subscription.end_date.strftime('%d.%m.%Y %H:%M')}\n\n"
            f"💰 Списано: {texts.format_price(price)}",
            reply_markup=get_back_keyboard(db_user.language)
        )
        
        logger.info(f"✅ Пользователь {db_user.telegram_id} продлил подписку на {days} дней за {price/100}₽")
        
    except Exception as e:
        logger.error(f"Ошибка продления подписки: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await callback.answer()


def get_extend_subscription_keyboard_with_prices(language: str, prices: dict) -> InlineKeyboardMarkup:
    texts = get_texts(language)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"📅 30 дней - {texts.format_price(prices[30])}", 
                callback_data="extend_period_30"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📅 90 дней - {texts.format_price(prices[90])}", 
                callback_data="extend_period_90"
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📅 180 дней - {texts.format_price(prices[180])}", 
                callback_data="extend_period_180"
            )
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
        ]
    ])


async def confirm_reset_traffic(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    from app.config import settings
    
    if settings.is_traffic_fixed():
        await callback.answer("⚠️ В текущем режиме трафик фиксированный", show_alert=True)
        return
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    reset_price = PERIOD_PRICES[30] 
    
    if db_user.balance_kopeks < reset_price:
        await callback.answer("⌛ Недостаточно средств на балансе", show_alert=True)
        return
    
    try:
        success = await subtract_user_balance(
            db, db_user, reset_price,
            "Сброс трафика"
        )
        
        if not success:
            await callback.answer("⌛ Ошибка списания средств", show_alert=True)
            return
        
        subscription.traffic_used_gb = 0.0
        subscription.updated_at = datetime.utcnow()
        await db.commit()
        
        subscription_service = SubscriptionService()
        remnawave_service = RemnaWaveService()
        
        user = db_user
        if user.remnawave_uuid:
            async with remnawave_service.api as api:
                await api.reset_user_traffic(user.remnawave_uuid)
        
        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=reset_price,
            description="Сброс трафика"
        )
        
        await db.refresh(db_user)
        await db.refresh(subscription)
        
        await callback.message.edit_text(
            f"✅ Трафик успешно сброшен!\n\n"
            f"🔄 Использованный трафик обнулен\n"
            f"📊 Лимит: {texts.format_traffic(subscription.traffic_limit_gb)}",
            reply_markup=get_back_keyboard(db_user.language)
        )
        
        logger.info(f"✅ Пользователь {db_user.telegram_id} сбросил трафик")
        
    except Exception as e:
        logger.error(f"Ошибка сброса трафика: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await callback.answer()



async def select_period(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    period_days = int(callback.data.split('_')[1])
    texts = get_texts(db_user.language)
    
    data = await state.get_data()
    data['period_days'] = period_days
    data['total_price'] = PERIOD_PRICES[period_days]
    
    if settings.is_traffic_fixed():
        fixed_traffic_price = TRAFFIC_PRICES.get(settings.get_fixed_traffic_limit(), 0)
        data['total_price'] += fixed_traffic_price
    
    await state.set_data(data)
    
    if settings.is_traffic_selectable():
        await callback.message.edit_text(
            texts.SELECT_TRAFFIC,
            reply_markup=get_traffic_packages_keyboard(db_user.language)
        )
        await state.set_state(SubscriptionStates.selecting_traffic)
    else:
        countries = await _get_available_countries()
        await callback.message.edit_text(
            texts.SELECT_COUNTRIES,
            reply_markup=get_countries_keyboard(countries, [], db_user.language)
        )
        await state.set_state(SubscriptionStates.selecting_countries)
    
    await callback.answer()

async def get_subscription_info_text(subscription, texts, db_user, db: AsyncSession):
    
    devices_used = await get_current_devices_count(db_user)
    countries_info = await _get_countries_info(subscription.connected_squads)
    countries_text = ", ".join([c['name'] for c in countries_info]) if countries_info else "Нет"
    
    subscription_url = getattr(subscription, 'subscription_url', None) or "Генерируется..."
    
    if subscription.is_trial:
        status_text = "🎁 Тестовая"
        type_text = "Триал"
    else:
        if subscription.is_active:
            status_text = "✅ Оплачена"
        else:
            status_text = "⌛ Истекла"
        type_text = "Платная подписка"
    
    if subscription.traffic_limit_gb == 0:
        if settings.is_traffic_fixed():
            traffic_text = "∞ Безлимитный"
        else:
            traffic_text = "∞ Безлимитный"
    else:
        if settings.is_traffic_fixed():
            traffic_text = f"{subscription.traffic_limit_gb} ГБ"
        else:
            traffic_text = f"{subscription.traffic_limit_gb} ГБ"
    
    subscription_cost = await get_subscription_cost(subscription, db)
    
    info_text = texts.SUBSCRIPTION_INFO.format(
        status=status_text,
        type=type_text,
        end_date=subscription.end_date.strftime("%d.%m.%Y %H:%M"),
        days_left=max(0, subscription.days_left),
        traffic_used=texts.format_traffic(subscription.traffic_used_gb),
        traffic_limit=traffic_text,
        countries_count=len(subscription.connected_squads),
        devices_used=devices_used,
        devices_limit=subscription.device_limit,
        autopay_status="✅ Включен" if subscription.autopay_enabled else "⌛ Выключен"
    )
    
    if subscription_cost > 0:
        info_text += f"\n💰 <b>Стоимость подписки в месяц:</b> {texts.format_price(subscription_cost)}"
    
    if subscription_url and subscription_url != "Генерируется...":
        info_text += f"\n\n🔗 <b>Ссылка для подключения:</b>\n<code>{subscription_url}</code>"
        info_text += f"\n\n📱 Скопируйте ссылку и добавьте в ваше VPN приложение"
    
    return info_text

def format_traffic_display(traffic_gb: int, is_fixed_mode: bool = None) -> str:
    if is_fixed_mode is None:
        is_fixed_mode = settings.is_traffic_fixed()
    
    if traffic_gb == 0:
        if is_fixed_mode:
            return "Безлимитный"
        else:
            return "Безлимитный"
    else:
        if is_fixed_mode:
            return f"{traffic_gb} ГБ"
        else:
            return f"{traffic_gb} ГБ"

async def select_traffic(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    
    traffic_gb = int(callback.data.split('_')[1])
    texts = get_texts(db_user.language)
    
    data = await state.get_data()
    data['traffic_gb'] = traffic_gb
    data['total_price'] += TRAFFIC_PRICES[traffic_gb]
    await state.set_data(data)
    
    countries = await _get_available_countries()
    
    await callback.message.edit_text(
        texts.SELECT_COUNTRIES,
        reply_markup=get_countries_keyboard(countries, [], db_user.language)
    )
    
    await state.set_state(SubscriptionStates.selecting_countries)
    await callback.answer()


async def select_country(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    country_uuid = callback.data.split('_')[1]
    data = await state.get_data()
    
    selected_countries = data.get('countries', [])
    if country_uuid in selected_countries:
        selected_countries.remove(country_uuid)
    else:
        selected_countries.append(country_uuid)
    
    countries = await _get_available_countries()
    
    base_price = PERIOD_PRICES[data['period_days']] + TRAFFIC_PRICES[data['traffic_gb']]
    
    try:
        subscription_service = SubscriptionService()
        countries_price, _ = await subscription_service.get_countries_price_by_uuids(selected_countries, db)
    except AttributeError:
        logger.warning("Используем fallback функцию для расчета цен стран")
        countries_price, _ = await get_countries_price_by_uuids_fallback(selected_countries, db)
    
    data['countries'] = selected_countries
    data['total_price'] = base_price + countries_price
    await state.set_data(data)
    
    await callback.message.edit_reply_markup(
        reply_markup=get_countries_keyboard(countries, selected_countries, db_user.language)
    )
    await callback.answer()


async def countries_continue(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    
    data = await state.get_data()
    texts = get_texts(db_user.language)
    
    if not data.get('countries'):
        await callback.answer("⚠️ Выберите хотя бы одну страну!", show_alert=True)
        return
    
    await callback.message.edit_text(
        texts.SELECT_DEVICES,
        reply_markup=get_devices_keyboard(1, db_user.language)
    )
    
    await state.set_state(SubscriptionStates.selecting_devices)
    await callback.answer()


async def select_devices(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User
):
    if not callback.data.startswith("devices_") or callback.data == "devices_continue":
        await callback.answer("❌ Некорректный запрос", show_alert=True)
        return
    
    try:
        devices = int(callback.data.split('_')[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Некорректное количество устройств", show_alert=True)
        return
    
    data = await state.get_data()
    
    base_price = (
        PERIOD_PRICES[data['period_days']] + 
        TRAFFIC_PRICES[data['traffic_gb']]
    )
    
    countries = await _get_available_countries()
    countries_price = sum(
        c['price_kopeks'] for c in countries 
        if c['uuid'] in data['countries']
    )
    
    devices_price = (devices - 1) * settings.PRICE_PER_DEVICE
    
    data['devices'] = devices
    data['total_price'] = base_price + countries_price + devices_price
    await state.set_data(data)
    
    await callback.message.edit_reply_markup(
        reply_markup=get_devices_keyboard(devices, db_user.language)
    )
    await callback.answer()

async def devices_continue(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    if not callback.data == "devices_continue":
        await callback.answer("⌛ Некорректный запрос", show_alert=True)
        return
    
    data = await state.get_data()
    texts = get_texts(db_user.language)
    
    countries = await _get_available_countries()
    selected_countries_names = []
    
    try:
        subscription_service = SubscriptionService()
        countries_price, _ = await subscription_service.get_countries_price_by_uuids(data['countries'], db)
    except AttributeError:
        logger.warning("Используем fallback функцию для расчета цен стран")
        countries_price, _ = await get_countries_price_by_uuids_fallback(data['countries'], db)
    
    for country in countries:
        if country['uuid'] in data['countries']:
            selected_countries_names.append(country['name'])
    
    base_price = PERIOD_PRICES[data['period_days']]
    
    if settings.is_traffic_fixed():
        traffic_price = TRAFFIC_PRICES.get(data['traffic_gb'], 0)
    else:
        traffic_price = TRAFFIC_PRICES.get(data['traffic_gb'], 0)
    
    devices_price = (data['devices'] - 1) * settings.PRICE_PER_DEVICE
    total_price = base_price + traffic_price + countries_price + devices_price
    
    data['total_price'] = total_price
    await state.set_data(data)
    
    if settings.is_traffic_fixed():
        if data['traffic_gb'] == 0:
            traffic_display = "Безлимитный"
        else:
            traffic_display = f"{data['traffic_gb']} ГБ"
    else:
        if data['traffic_gb'] == 0:
            traffic_display = "Безлимитный"
        else:
            traffic_display = f"{data['traffic_gb']} ГБ"
    
    summary_text = f"""
📋 <b>Сводка заказа</b>

📅 <b>Период:</b> {data['period_days']} дней
📊 <b>Трафик:</b> {traffic_display}
🌍 <b>Страны:</b> {", ".join(selected_countries_names)}
📱 <b>Устройства:</b> {data['devices']}

💰 <b>Общая стоимость:</b> {texts.format_price(total_price)}

Подтверждаете покупку?
"""
    
    await callback.message.edit_text(
        summary_text,
        reply_markup=get_subscription_confirm_keyboard(db_user.language),
        parse_mode="HTML"
    )
    
    await state.set_state(SubscriptionStates.confirming_purchase)
    await callback.answer()


async def confirm_purchase(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    data = await state.get_data()
    texts = get_texts(db_user.language)
    
    countries = await _get_available_countries()
    
    base_price = PERIOD_PRICES[data['period_days']]
    
    countries_price = 0
    server_prices = []
    for country in countries:
        if country['uuid'] in data['countries']:
            countries_price += country['price_kopeks']
            server_prices.append(country['price_kopeks'])
    
    devices_price = (data['devices'] - 1) * settings.PRICE_PER_DEVICE
    
    if settings.is_traffic_fixed():
        traffic_price = TRAFFIC_PRICES.get(settings.get_fixed_traffic_limit(), 0)
        final_traffic_gb = settings.get_fixed_traffic_limit()
    else:
        traffic_price = TRAFFIC_PRICES.get(data['traffic_gb'], 0)
        final_traffic_gb = data['traffic_gb']
    
    final_price = base_price + traffic_price + countries_price + devices_price
    
    if db_user.balance_kopeks < final_price:
        await callback.message.edit_text(
            texts.INSUFFICIENT_BALANCE,
            reply_markup=get_back_keyboard(db_user.language)
        )
        await callback.answer()
        return
    
    try:
        success = await subtract_user_balance(
            db, db_user, final_price,
            f"Покупка подписки на {data['period_days']} дней"
        )
        
        if not success:
            await callback.message.edit_text(
                texts.INSUFFICIENT_BALANCE,
                reply_markup=get_back_keyboard(db_user.language)
            )
            await callback.answer()
            return
        
        existing_subscription = db_user.subscription
        
        if existing_subscription and existing_subscription.is_trial:
            logger.info(f"🔄 Обновляем триальную подписку пользователя {db_user.telegram_id}")
            
            existing_subscription.is_trial = False
            existing_subscription.status = SubscriptionStatus.ACTIVE.value
            
            existing_subscription.traffic_limit_gb = final_traffic_gb
            existing_subscription.device_limit = data['devices']
            existing_subscription.connected_squads = data['countries']
            
            existing_subscription.extend_subscription(data['period_days'])
            existing_subscription.updated_at = datetime.utcnow()
            
            await db.commit()
            await db.refresh(existing_subscription)
            subscription = existing_subscription
            
            logger.info(f"✅ Триальная подписка обновлена до платной. Новая дата окончания: {subscription.end_date}")
            
        else:
            logger.info(f"🆕 Создаем новую платную подписку для пользователя {db_user.telegram_id}")
            subscription = await create_paid_subscription_with_traffic_mode(
                db=db,
                user_id=db_user.id,
                duration_days=data['period_days'],
                device_limit=data['devices'],
                connected_squads=data['countries'],
                traffic_gb=final_traffic_gb
            )
        
        from app.utils.user_utils import mark_user_as_had_paid_subscription
        await mark_user_as_had_paid_subscription(db, db_user)
        
        from app.database.crud.server_squad import get_server_ids_by_uuids, add_user_to_servers
        from app.database.crud.subscription import add_subscription_servers
        
        server_ids = await get_server_ids_by_uuids(db, data['countries'])
        
        if server_ids:
            await add_subscription_servers(db, subscription, server_ids, server_prices)
            await add_user_to_servers(db, server_ids)
            
            logger.info(f"📊 Сохранены цены серверов: {server_prices}")
            logger.info(f"📊 Обновлены счетчики пользователей для серверов: {server_ids}")
        
        await db.refresh(db_user)
        
        subscription_service = SubscriptionService()
        
        if db_user.remnawave_uuid:
            logger.info(f"📝 Обновляем существующего RemnaWave пользователя {db_user.remnawave_uuid}")
            remnawave_user = await subscription_service.update_remnawave_user(db, subscription)
        else:
            logger.info(f"🆕 Создаем нового RemnaWave пользователя для {db_user.telegram_id}")
            remnawave_user = await subscription_service.create_remnawave_user(db, subscription)
            
        if not remnawave_user:
            logger.error(f"❌ Не удалось создать/обновить RemnaWave пользователя для {db_user.telegram_id}")
            logger.info(f"🔄 Fallback: принудительное создание нового RemnaWave пользователя")
            remnawave_user = await subscription_service.create_remnawave_user(db, subscription)
        
        await create_transaction(
            db=db,
            user_id=db_user.id,
            type=TransactionType.SUBSCRIPTION_PAYMENT,
            amount_kopeks=final_price,
            description=f"Подписка на {data['period_days']} дней"
        )
        
        try:
            await process_referral_purchase(
                db=db,
                user_id=db_user.id,
                purchase_amount_kopeks=final_price,
                transaction_id=None 
            )
        except Exception as e:
            logger.error(f"Ошибка обработки реферальной покупки: {e}")
        
        await db.refresh(db_user)
        await db.refresh(subscription)
        
        if remnawave_user and hasattr(subscription, 'subscription_url') and subscription.subscription_url:
            success_text = f"{texts.SUBSCRIPTION_PURCHASED}\n\n"
            success_text += f"🔗 <b>Ваша ссылка для подключения:</b>\n"
            success_text += f"<code>{subscription.subscription_url}</code>\n\n"
            success_text += f"📱 Нажмите кнопку ниже, чтобы получить инструкцию по настройке VPN на вашем устройстве"
    
            connect_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="🔗 Подключиться", callback_data="subscription_connect")
                ],
                [
                    InlineKeyboardButton(text="📱 Моя подписка", callback_data="menu_subscription")
                ],
                [
                    InlineKeyboardButton(text="⬅️ В главное меню", callback_data="back_to_menu")
                ]
            ])
    
            await callback.message.edit_text(
                success_text,
                reply_markup=connect_keyboard,
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                f"{texts.SUBSCRIPTION_PURCHASED}\n\n⚠️ Ссылка генерируется, перейдите в раздел 'Моя подписка' через несколько секунд.",
                reply_markup=get_back_keyboard(db_user.language)
            )
        
        logger.info(f"✅ Пользователь {db_user.telegram_id} купил подписку на {data['period_days']} дней")
        
    except Exception as e:
        logger.error(f"Ошибка покупки подписки: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await state.clear()
    await callback.answer()

async def create_paid_subscription_with_traffic_mode(
    db: AsyncSession,
    user_id: int,
    duration_days: int,
    device_limit: int,
    connected_squads: List[str],
    traffic_gb: Optional[int] = None 
):
    from app.config import settings
    from app.database.crud.subscription import create_paid_subscription
    
    if traffic_gb is None:
        if settings.is_traffic_fixed():
            traffic_limit_gb = settings.get_fixed_traffic_limit()
        else:
            traffic_limit_gb = 0 
    else:
        traffic_limit_gb = traffic_gb
    
    subscription = await create_paid_subscription(
        db=db,
        user_id=user_id,
        duration_days=duration_days,
        traffic_limit_gb=traffic_limit_gb,
        device_limit=device_limit,
        connected_squads=connected_squads
    )
    
    logger.info(f"📋 Создана подписка с трафиком: {traffic_limit_gb} ГБ (режим: {settings.TRAFFIC_SELECTION_MODE})")
    
    return subscription


async def handle_subscription_settings(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or subscription.is_trial:
        await callback.answer("⚠️ Настройки доступны только для платных подписок", show_alert=True)
        return
    
    devices_used = await get_current_devices_count(db_user)
    
    settings_text = f"""
⚙️ <b>Настройки подписки</b>

📊 <b>Текущие параметры:</b>
🌍 Стран: {len(subscription.connected_squads)}
📈 Трафик: {texts.format_traffic(subscription.traffic_used_gb)} / {texts.format_traffic(subscription.traffic_limit_gb)}
📱 Устройства: {devices_used} / {subscription.device_limit}

Выберите что хотите изменить:
"""
    
    await callback.message.edit_text(
        settings_text,
        reply_markup=get_subscription_settings_keyboard(db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_autopay_menu(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    subscription = db_user.subscription
    if not subscription:
        await callback.answer("⚠️ У вас нет активной подписки!", show_alert=True)
        return
    
    status = "включен" if subscription.autopay_enabled else "выключен"
    days = subscription.autopay_days_before
    
    text = f"💳 <b>Автоплатеж</b>\n\n"
    text += f"📊 <b>Статус:</b> {status}\n"
    text += f"⏰ <b>Списание за:</b> {days} дн. до окончания\n\n"
    text += "Выберите действие:"
    
    await callback.message.edit_text(
        text,
        reply_markup=get_autopay_keyboard(db_user.language)
    )
    await callback.answer()


async def toggle_autopay(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    subscription = db_user.subscription
    enable = callback.data == "autopay_enable"
    
    await update_subscription_autopay(db, subscription, enable)
    
    status = "включен" if enable else "выключен"
    await callback.answer(f"✅ Автоплатеж {status}!")
    
    await handle_autopay_menu(callback, db_user, db)


async def show_autopay_days(
    callback: types.CallbackQuery,
    db_user: User
):
    
    await callback.message.edit_text(
        "⏰ Выберите за сколько дней до окончания списывать средства:",
        reply_markup=get_autopay_days_keyboard(db_user.language)
    )
    await callback.answer()


async def set_autopay_days(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    days = int(callback.data.split('_')[2])
    subscription = db_user.subscription
    
    await update_subscription_autopay(
        db, subscription, subscription.autopay_enabled, days
    )
    
    await callback.answer(f"✅ Установлено {days} дней!")
    
    await handle_autopay_menu(callback, db_user, db)

async def handle_subscription_config_back(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    current_state = await state.get_state()
    texts = get_texts(db_user.language)
    
    if current_state == SubscriptionStates.selecting_traffic.state:
        await callback.message.edit_text(
            texts.BUY_SUBSCRIPTION_START,
            reply_markup=get_subscription_period_keyboard(db_user.language)
        )
        await state.set_state(SubscriptionStates.selecting_period)
        
    elif current_state == SubscriptionStates.selecting_countries.state:
        if settings.is_traffic_selectable():
            await callback.message.edit_text(
                texts.SELECT_TRAFFIC,
                reply_markup=get_traffic_packages_keyboard(db_user.language)
            )
            await state.set_state(SubscriptionStates.selecting_traffic)
        else:
            await callback.message.edit_text(
                texts.BUY_SUBSCRIPTION_START,
                reply_markup=get_subscription_period_keyboard(db_user.language)
            )
            await state.set_state(SubscriptionStates.selecting_period)
        
    elif current_state == SubscriptionStates.selecting_devices.state:
        countries = await _get_available_countries()
        data = await state.get_data()
        selected_countries = data.get('countries', [])
        
        await callback.message.edit_text(
            texts.SELECT_COUNTRIES,
            reply_markup=get_countries_keyboard(countries, selected_countries, db_user.language)
        )
        await state.set_state(SubscriptionStates.selecting_countries)
        
    else:
        from app.handlers.menu import show_main_menu
        await show_main_menu(callback, db_user, db)
        await state.clear()
    
    await callback.answer()

async def handle_subscription_cancel(
    callback: types.CallbackQuery,
    state: FSMContext,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)
    
    await state.clear()
    
    from app.handlers.menu import show_main_menu
    await show_main_menu(callback, db_user, db)
    
    await callback.answer("❌ Покупка отменена")

async def _get_available_countries():
    from app.utils.cache import cache
    from app.database.database import AsyncSessionLocal
    from app.database.crud.server_squad import get_available_server_squads
    
    cached_countries = await cache.get("available_countries")
    if cached_countries:
        return cached_countries
    
    try:
        async with AsyncSessionLocal() as db:
            available_servers = await get_available_server_squads(db)
        
        countries = []
        for server in available_servers:
            countries.append({
                "uuid": server.squad_uuid,
                "name": server.display_name, 
                "price_kopeks": server.price_kopeks,
                "country_code": server.country_code,
                "is_available": server.is_available and not server.is_full
            })
        
        if not countries:
            logger.info("🔄 Серверов в БД нет, получаем из RemnaWave...")
            from app.services.remnawave_service import RemnaWaveService
            
            service = RemnaWaveService()
            squads = await service.get_all_squads()
            
            for squad in squads:
                squad_name = squad["name"]
                
                if not any(flag in squad_name for flag in ["🇳🇱", "🇩🇪", "🇺🇸", "🇫🇷", "🇬🇧", "🇮🇹", "🇪🇸", "🇨🇦", "🇯🇵", "🇸🇬", "🇦🇺"]):
                    name_lower = squad_name.lower()
                    if "netherlands" in name_lower or "нидерланды" in name_lower or "nl" in name_lower:
                        squad_name = f"🇳🇱 {squad_name}"
                    elif "germany" in name_lower or "германия" in name_lower or "de" in name_lower:
                        squad_name = f"🇩🇪 {squad_name}"
                    elif "usa" in name_lower or "сша" in name_lower or "america" in name_lower or "us" in name_lower:
                        squad_name = f"🇺🇸 {squad_name}"
                    else:
                        squad_name = f"🌐 {squad_name}"
                
                countries.append({
                    "uuid": squad["uuid"],
                    "name": squad_name,
                    "price_kopeks": 1000, 
                    "is_available": True
                })
        
        await cache.set("available_countries", countries, 300)
        return countries
        
    except Exception as e:
        logger.error(f"Ошибка получения списка стран: {e}")
        fallback_countries = [
            {"uuid": "default-free", "name": "🆓 Бесплатный сервер", "price_kopeks": 0, "is_available": True},
        ]
        
        await cache.set("available_countries", fallback_countries, 60)
        return fallback_countries

async def _get_countries_info(squad_uuids):
    countries = await _get_available_countries()
    return [c for c in countries if c['uuid'] in squad_uuids]

async def handle_reset_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or subscription.is_trial:
        await callback.answer("❌ Эта функция доступна только для платных подписок", show_alert=True)
        return
    
    if not db_user.remnawave_uuid:
        await callback.answer("❌ UUID пользователя не найден", show_alert=True)
        return
    
    try:
        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()
        
        async with service.api as api:
            response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')
            
            if response and 'response' in response:
                devices_info = response['response']
                total_devices = devices_info.get('total', 0)
                devices_list = devices_info.get('devices', [])
                
                if total_devices == 0:
                    await callback.answer("ℹ️ У вас нет подключенных устройств", show_alert=True)
                    return
                
                devices_text = "\n".join([
                    f"• {device.get('platform', 'Unknown')} - {device.get('deviceModel', 'Unknown')}"
                    for device in devices_list[:5]
                ])
                
                if len(devices_list) > 5:
                    devices_text += f"\n... и еще {len(devices_list) - 5}"
                
                confirm_text = f"🔄 <b>Сброс устройств</b>\n\n"
                confirm_text += f"📊 Всего подключено: {total_devices} устройств\n\n"
                confirm_text += f"<b>Подключенные устройства:</b>\n{devices_text}\n\n"
                confirm_text += "⚠️ <b>Внимание!</b> Все устройства будут отключены и вам потребуется заново настроить VPN на каждом устройстве.\n\n"
                confirm_text += "Продолжить?"
                
                await callback.message.edit_text(
                    confirm_text,
                    reply_markup=get_reset_devices_confirm_keyboard(db_user.language),
                    parse_mode="HTML"
                )
            else:
                await callback.answer("❌ Ошибка получения информации об устройствах", show_alert=True)
                
    except Exception as e:
        logger.error(f"Ошибка получения списка устройств: {e}")
        await callback.answer("❌ Ошибка получения информации об устройствах", show_alert=True)
    
    await callback.answer()

async def handle_add_country_to_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    
    logger.info(f"🔍 handle_add_country_to_subscription вызван для {db_user.telegram_id}")
    logger.info(f"🔍 Callback data: {callback.data}")
    
    current_state = await state.get_state()
    logger.info(f"🔍 Текущее состояние: {current_state}")
    
    country_uuid = callback.data.split('_')[1]
    data = await state.get_data()
    logger.info(f"🔍 Данные состояния: {data}")
    
    selected_countries = data.get('countries', [])
    countries = await _get_available_countries()
    
    if country_uuid in selected_countries:
        selected_countries.remove(country_uuid)
        logger.info(f"🔍 Удалена страна: {country_uuid}")
    else:
        selected_countries.append(country_uuid)
        logger.info(f"🔍 Добавлена страна: {country_uuid}")
    
    total_price = 0
    for country in countries:
        if country['uuid'] in selected_countries and country['uuid'] not in db_user.subscription.connected_squads:
            total_price += country['price_kopeks']
    
    data['countries'] = selected_countries
    data['total_price'] = total_price
    await state.set_data(data)
    
    logger.info(f"🔍 Новые выбранные страны: {selected_countries}")
    logger.info(f"🔍 Общая стоимость: {total_price}")
    
    try:
        from app.keyboards.inline import get_manage_countries_keyboard
        await callback.message.edit_reply_markup(
            reply_markup=get_manage_countries_keyboard(countries, selected_countries, db_user.subscription.connected_squads, db_user.language)
        )
        logger.info(f"✅ Клавиатура обновлена")
    except Exception as e:
        logger.error(f"❌ Ошибка обновления клавиатуры: {e}")
    
    await callback.answer()


async def confirm_add_countries_to_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession,
    state: FSMContext
):
    
    data = await state.get_data()
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    selected_countries = data.get('countries', [])
    current_countries = subscription.connected_squads
    
    new_countries = [c for c in selected_countries if c not in current_countries]
    removed_countries = [c for c in current_countries if c not in selected_countries]
    
    if not new_countries and not removed_countries:
        await callback.answer("⚠️ Изменения не обнаружены", show_alert=True)
        return
    
    countries = await _get_available_countries()
    total_price = 0
    new_countries_names = []
    removed_countries_names = []
    
    for country in countries:
        if country['uuid'] in new_countries:
            total_price += country['price_kopeks']
            new_countries_names.append(country['name'])
        if country['uuid'] in removed_countries:
            removed_countries_names.append(country['name'])
    
    if new_countries and db_user.balance_kopeks < total_price:
        await callback.message.edit_text(
            f"❌ Недостаточно средств на балансе!\n\n"
            f"💰 Требуется: {texts.format_price(total_price)}\n"
            f"💳 У вас: {texts.format_price(db_user.balance_kopeks)}",
            reply_markup=get_back_keyboard(db_user.language)
        )
        await state.clear()
        await callback.answer()
        return
    
    try:
        if new_countries and total_price > 0:
            success = await subtract_user_balance(
                db, db_user, total_price,
                f"Добавление стран к подписке: {', '.join(new_countries_names)}"
            )
            
            if not success:
                await callback.answer("❌ Ошибка списания средств", show_alert=True)
                return
            
            await create_transaction(
                db=db,
                user_id=db_user.id,
                type=TransactionType.SUBSCRIPTION_PAYMENT,
                amount_kopeks=total_price,
                description=f"Добавление стран к подписке: {', '.join(new_countries_names)}"
            )
        
        subscription.connected_squads = selected_countries
        subscription.updated_at = datetime.utcnow()
        await db.commit()
        
        subscription_service = SubscriptionService()
        await subscription_service.update_remnawave_user(db, subscription)
        
        if new_countries and total_price > 0:
            try:
                await process_referral_purchase(
                    db=db,
                    user_id=db_user.id,
                    purchase_amount_kopeks=total_price,
                    transaction_id=None
                )
            except Exception as e:
                logger.error(f"Ошибка обработки реферальной покупки: {e}")
        
        await db.refresh(db_user)
        await db.refresh(subscription)
        
        success_text = "✅ Страны успешно обновлены!\n\n"
        
        if new_countries_names:
            success_text += f"➕ Добавлены страны:\n{chr(10).join(f'• {name}' for name in new_countries_names)}\n"
            if total_price > 0:
                success_text += f"💰 Списано: {texts.format_price(total_price)}\n"
        
        if removed_countries_names:
            success_text += f"\n➖ Отключены страны:\n{chr(10).join(f'• {name}' for name in removed_countries_names)}\n"
            success_text += "ℹ️ Повторное подключение будет платным\n"
        
        success_text += f"\n🌍 Активных стран: {len(selected_countries)}"
        
        await callback.message.edit_text(
            success_text,
            reply_markup=get_back_keyboard(db_user.language)
        )
        
        logger.info(f"✅ Пользователь {db_user.telegram_id} обновил страны подписки. Добавлено: {len(new_countries)}, убрано: {len(removed_countries)}")
        
    except Exception as e:
        logger.error(f"Ошибка обновления стран подписки: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await state.clear()
    await callback.answer()

async def confirm_reset_devices(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    
    texts = get_texts(db_user.language)
    
    if not db_user.remnawave_uuid:
        await callback.answer("❌ UUID пользователя не найден", show_alert=True)
        return
    
    try:
        from app.services.remnawave_service import RemnaWaveService
        service = RemnaWaveService()
        
        async with service.api as api:
            devices_response = await api._make_request('GET', f'/api/hwid/devices/{db_user.remnawave_uuid}')
            
            if not devices_response or 'response' not in devices_response:
                await callback.answer("❌ Ошибка получения списка устройств", show_alert=True)
                return
            
            devices_list = devices_response['response'].get('devices', [])
            
            if not devices_list:
                await callback.answer("ℹ️ У вас нет подключенных устройств", show_alert=True)
                return
            
            logger.info(f"🔍 Найдено {len(devices_list)} устройств для сброса")
            
            success_count = 0
            failed_count = 0
            
            for device in devices_list:
                device_hwid = device.get('hwid')
                if device_hwid:
                    try:
                        delete_data = {
                            "userUuid": db_user.remnawave_uuid,
                            "hwid": device_hwid
                        }
                        
                        await api._make_request('POST', '/api/hwid/devices/delete', data=delete_data)
                        success_count += 1
                        logger.info(f"✅ Устройство {device_hwid} удалено")
                        
                    except Exception as device_error:
                        failed_count += 1
                        logger.error(f"❌ Ошибка удаления устройства {device_hwid}: {device_error}")
                else:
                    failed_count += 1
                    logger.warning(f"⚠️ У устройства нет HWID: {device}")
            
            if success_count > 0:
                if failed_count == 0:
                    await callback.message.edit_text(
                        f"✅ <b>Устройства успешно сброшены!</b>\n\n"
                        f"🔄 Сброшено: {success_count} устройств\n"
                        f"📱 Теперь вы можете заново подключить свои устройства\n\n"
                        f"💡 Используйте ссылку из раздела 'Моя подписка' для повторного подключения",
                        reply_markup=get_back_keyboard(db_user.language),
                        parse_mode="HTML"
                    )
                    logger.info(f"✅ Пользователь {db_user.telegram_id} успешно сбросил {success_count} устройств")
                else:
                    await callback.message.edit_text(
                        f"⚠️ <b>Частичный сброс устройств</b>\n\n"
                        f"✅ Удалено: {success_count} устройств\n"
                        f"❌ Не удалось удалить: {failed_count} устройств\n\n"
                        f"Попробуйте еще раз или обратитесь в поддержку.",
                        reply_markup=get_back_keyboard(db_user.language),
                        parse_mode="HTML"
                    )
                    logger.warning(f"⚠️ Частичный сброс у пользователя {db_user.telegram_id}: {success_count}/{len(devices_list)}")
            else:
                await callback.message.edit_text(
                    f"❌ <b>Не удалось сбросить устройства</b>\n\n"
                    f"Попробуйте еще раз позже или обратитесь в техподдержку.\n\n"
                    f"Всего устройств: {len(devices_list)}",
                    reply_markup=get_back_keyboard(db_user.language),
                    parse_mode="HTML"
                )
                logger.error(f"❌ Не удалось сбросить ни одного устройства у пользователя {db_user.telegram_id}")
        
    except Exception as e:
        logger.error(f"Ошибка сброса устройств: {e}")
        await callback.message.edit_text(
            texts.ERROR,
            reply_markup=get_back_keyboard(db_user.language)
        )
    
    await callback.answer()

async def handle_connect_subscription(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or not subscription.subscription_url:
        await callback.answer("❌ У вас нет активной подписки или ссылка еще генерируется", show_alert=True)
        return
    
    connect_mode = settings.CONNECT_BUTTON_MODE
    
    if connect_mode == "miniapp_subscription":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Открыть подписку", 
                    web_app=types.WebAppInfo(url=subscription.subscription_url)
                )
            ],
            [
                InlineKeyboardButton(text="📋 Показать ссылку", callback_data="open_subscription_link")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
            ]
        ])
        
        await callback.message.edit_text(
            f"""
🔗 <b>Подключить подписку</b>

📱 Нажмите кнопку ниже, чтобы открыть подписку в мини-приложении Telegram:
            """,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    elif connect_mode == "miniapp_custom":
        if not settings.MINIAPP_CUSTOM_URL:
            await callback.answer("❌ Кастомная ссылка для мини-приложения не настроена", show_alert=True)
            return
            
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Открыть приложение", 
                    web_app=types.WebAppInfo(url=settings.MINIAPP_CUSTOM_URL)
                )
            ],
            [
                InlineKeyboardButton(text="📋 Показать ссылку подписки", callback_data="open_subscription_link")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
            ]
        ])
        
        await callback.message.edit_text(
            f"""
🚀 <b>Подключить подписку</b>

📱 Нажмите кнопку ниже, чтобы открыть приложение:

📋 <b>Ссылка подписки:</b>
<code>{subscription.subscription_url}</code>
            """,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    else:
        device_text = f"""
📱 <b>Подключить подписку</b>

🔗 <b>Ссылка подписки:</b>
<code>{subscription.subscription_url}</code>

💡 <b>Выберите ваше устройство</b> для получения подробной инструкции по настройке:
        """
        
        await callback.message.edit_text(
            device_text,
            reply_markup=get_device_selection_keyboard(db_user.language),
            parse_mode="HTML"
        )
    
    await callback.answer()


async def handle_device_guide(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    device_type = callback.data.split('_')[2] 
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    if not subscription or not subscription.subscription_url:
        await callback.answer("❌ Ссылка подписки недоступна", show_alert=True)
        return
    
    apps = get_apps_for_device(device_type, db_user.language)
    
    if not apps:
        await callback.answer("❌ Приложения для этого устройства не найдены", show_alert=True)
        return
    
    featured_app = next((app for app in apps if app.get('isFeatured', False)), apps[0])
    
    guide_text = f"""
📱 <b>Настройка для {get_device_name(device_type, db_user.language)}</b>

🔗 <b>Ссылка подписки:</b>
<code>{subscription.subscription_url}</code>

📋 <b>Рекомендуемое приложение:</b> {featured_app['name']}

<b>Шаг 1 - Установка:</b>
{featured_app['installationStep']['description'][db_user.language]}

<b>Шаг 2 - Добавление подписки:</b>
{featured_app['addSubscriptionStep']['description'][db_user.language]}

<b>Шаг 3 - Подключение:</b>
{featured_app['connectAndUseStep']['description'][db_user.language]}

💡 <b>Как подключить:</b>
1. Установите приложение по ссылке выше
2. Скопируйте ссылку подписки (нажмите на неё)
3. Откройте приложение и вставьте ссылку
4. Подключитесь к серверу
"""
    
    await callback.message.edit_text(
        guide_text,
        reply_markup=get_connection_guide_keyboard(
            subscription.subscription_url,
            featured_app,
            db_user.language
        ),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_app_selection(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    device_type = callback.data.split('_')[2] 
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    apps = get_apps_for_device(device_type, db_user.language)
    
    if not apps:
        await callback.answer("❌ Приложения для этого устройства не найдены", show_alert=True)
        return
    
    app_text = f"""
📱 <b>Приложения для {get_device_name(device_type, db_user.language)}</b>

Выберите приложение для подключения:
"""
    
    await callback.message.edit_text(
        app_text,
        reply_markup=get_app_selection_keyboard(device_type, apps, db_user.language),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_specific_app_guide(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    _, device_type, app_id = callback.data.split('_') 
    texts = get_texts(db_user.language)
    subscription = db_user.subscription
    
    apps = get_apps_for_device(device_type, db_user.language)
    app = next((a for a in apps if a['id'] == app_id), None)
    
    if not app:
        await callback.answer("❌ Приложение не найдено", show_alert=True)
        return
    
    guide_text = f"""
📱 <b>{app['name']} - {get_device_name(device_type, db_user.language)}</b>

🔗 <b>Ссылка подписки:</b>
<code>{subscription.subscription_url}</code>

<b>Шаг 1 - Установка:</b>
{app['installationStep']['description'][db_user.language]}

<b>Шаг 2 - Добавление подписки:</b>
{app['addSubscriptionStep']['description'][db_user.language]}

<b>Шаг 3 - Подключение:</b>
{app['connectAndUseStep']['description'][db_user.language]}
"""
    
    if 'additionalAfterAddSubscriptionStep' in app:
        additional = app['additionalAfterAddSubscriptionStep']
        guide_text += f"""

<b>{additional['title'][db_user.language]}:</b>
{additional['description'][db_user.language]}
"""
    
    await callback.message.edit_text(
        guide_text,
        reply_markup=get_specific_app_keyboard(
            subscription.subscription_url,
            app,
            device_type,
            db_user.language
        ),
        parse_mode="HTML"
    )
    await callback.answer()


async def handle_open_subscription_link(
    callback: types.CallbackQuery,
    db_user: User,
    db: AsyncSession
):
    subscription = db_user.subscription
    
    if not subscription or not subscription.subscription_url:
        await callback.answer("❌ Ссылка подписки недоступна", show_alert=True)
        return
    
    link_text = f"""
🔗 <b>Ссылка подписки:</b>

<code>{subscription.subscription_url}</code>

📱 <b>Как использовать:</b>
1. Нажмите на ссылку выше чтобы её скопировать
2. Откройте ваше VPN приложение
3. Найдите функцию "Добавить подписку" или "Import"
4. Вставьте скопированную ссылку

💡 Если ссылка не скопировалась, выделите её вручную и скопируйте.
"""
    
    await callback.message.edit_text(
        link_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔗 Подключиться", callback_data="subscription_connect")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_subscription")
            ]
        ]),
        parse_mode="HTML"
    )
    await callback.answer()


def load_app_config() -> Dict[str, Any]:
    try:
        from app.config import settings
        config_path = settings.get_app_config_path()
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки конфига приложений: {e}")
        return {}


def get_apps_for_device(device_type: str, language: str = "ru") -> List[Dict[str, Any]]:
    config = load_app_config()
    
    device_mapping = {
        'ios': 'ios',
        'android': 'android', 
        'windows': 'pc',
        'mac': 'pc',
        'tv': 'tv'
    }
    
    config_key = device_mapping.get(device_type, device_type)
    return config.get(config_key, [])


def get_device_name(device_type: str, language: str = "ru") -> str:
    if language == "en":
        names = {
            'ios': 'iPhone/iPad',
            'android': 'Android',
            'windows': 'Windows',
            'mac': 'macOS',
            'tv': 'Android TV'
        }
    else:
        names = {
            'ios': 'iPhone/iPad',
            'android': 'Android',
            'windows': 'Windows',
            'mac': 'macOS',
            'tv': 'Android TV'
        }
    
    return names.get(device_type, device_type)


def create_deep_link(app: Dict[str, Any], subscription_url: str) -> str:
    from app.config import settings
    
    return subscription_url


def get_reset_devices_confirm_keyboard(language: str = "ru") -> InlineKeyboardMarkup:
    texts = get_texts(language)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, сбросить все устройства", 
                callback_data="confirm_reset_devices"
            )
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="menu_subscription")
        ]
    ])


def register_handlers(dp: Dispatcher):
    
    dp.callback_query.register(
        show_subscription_info,
        F.data == "menu_subscription"
    )
    
    dp.callback_query.register(
        show_trial_offer,
        F.data == "menu_trial"
    )
    
    dp.callback_query.register(
        activate_trial,
        F.data == "trial_activate"
    )
    
    dp.callback_query.register(
        start_subscription_purchase,
        F.data.in_(["menu_buy", "subscription_upgrade"])
    )
    
    
    dp.callback_query.register(
        handle_add_countries,
        F.data == "subscription_add_countries"
    )
    
    dp.callback_query.register(
        handle_add_traffic,
        F.data == "subscription_add_traffic"
    )
    
    dp.callback_query.register(
        handle_add_devices,
        F.data == "subscription_add_devices"
    )
    
    dp.callback_query.register(
        handle_extend_subscription,
        F.data == "subscription_extend"
    )
    
    dp.callback_query.register(
        handle_reset_traffic,
        F.data == "subscription_reset_traffic"
    )
    
    dp.callback_query.register(
        confirm_add_traffic,
        F.data.startswith("add_traffic_")
    )
    
    dp.callback_query.register(
        confirm_add_devices,
        F.data.startswith("add_devices_")
    )
    
    dp.callback_query.register(
        confirm_extend_subscription,
        F.data.startswith("extend_period_")
    )
    
    dp.callback_query.register(
        confirm_reset_traffic,
        F.data == "confirm_reset_traffic"
    )

    dp.callback_query.register(
        handle_reset_devices,
        F.data == "subscription_reset_devices"
    )
    
    dp.callback_query.register(
        confirm_reset_devices,
        F.data == "confirm_reset_devices"
    )
    
    
    dp.callback_query.register(
        select_period,
        F.data.startswith("period_"),
        SubscriptionStates.selecting_period
    )
    
    dp.callback_query.register(
        select_traffic,
        F.data.startswith("traffic_"),
        SubscriptionStates.selecting_traffic
    )
    
    dp.callback_query.register(
        select_devices,
        F.data.startswith("devices_") & ~F.data.in_(["devices_continue"]),
        SubscriptionStates.selecting_devices
    )
    
    dp.callback_query.register(
        devices_continue,
        F.data == "devices_continue",
        SubscriptionStates.selecting_devices
    )
    
    dp.callback_query.register(
        confirm_purchase,
        F.data == "subscription_confirm",
        SubscriptionStates.confirming_purchase
    )
    
    
    dp.callback_query.register(
        handle_autopay_menu,
        F.data == "subscription_autopay"
    )
    
    dp.callback_query.register(
        toggle_autopay,
        F.data.in_(["autopay_enable", "autopay_disable"])
    )
    
    dp.callback_query.register(
        show_autopay_days,
        F.data == "autopay_set_days"
    )

    dp.callback_query.register(
        handle_subscription_config_back,
        F.data == "subscription_config_back"
    )
    
    dp.callback_query.register(
        handle_subscription_cancel,
        F.data == "subscription_cancel"
    )
    
    dp.callback_query.register(
        set_autopay_days,
        F.data.startswith("autopay_days_")
    )

    dp.callback_query.register(
        select_country,
        F.data.startswith("country_"),
        SubscriptionStates.selecting_countries
    )
    
    dp.callback_query.register(
        countries_continue,
        F.data == "countries_continue",
        SubscriptionStates.selecting_countries
    )

    dp.callback_query.register(
        handle_manage_country,
        F.data.startswith("country_manage_")
    )
    
    dp.callback_query.register(
        apply_countries_changes,
        F.data == "countries_apply"
    )

    dp.callback_query.register(
        handle_connect_subscription,
        F.data == "subscription_connect"
    )
    
    dp.callback_query.register(
        handle_device_guide,
        F.data.startswith("device_guide_")
    )
    
    dp.callback_query.register(
        handle_app_selection,
        F.data.startswith("app_list_")
    )
    
    dp.callback_query.register(
        handle_specific_app_guide,
        F.data.startswith("app_")
    )
    
    dp.callback_query.register(
        handle_open_subscription_link,
        F.data == "open_subscription_link"
    )

    dp.callback_query.register(
        handle_subscription_settings,
        F.data == "subscription_settings"
    )
