import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, User as TgUser, Message
from sqlalchemy.ext.asyncio import AsyncSession
from app.handlers.subscription.purchase import save_cart_and_redirect_to_topup, return_to_saved_cart, clear_saved_cart
from app.handlers.subscription.autopay import handle_subscription_cancel
from app.database.models import User, Subscription

@pytest.fixture
def mock_callback_query():
    callback = AsyncMock(spec=CallbackQuery)
    callback.message = AsyncMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    callback.data = "subscription_confirm"
    return callback

@pytest.fixture
def mock_user():
    user = AsyncMock(spec=User)
    user.id = 12345
    user.telegram_id = 12345
    user.language = "ru"
    user.balance_kopeks = 10000
    user.subscription = None
    user.has_had_paid_subscription = False
    return user

@pytest.fixture
def mock_db():
    db = AsyncMock(spec=AsyncSession)
    return db

@pytest.fixture
def mock_state():
    state = AsyncMock(spec=FSMContext)
    state.get_data = AsyncMock(return_value={
        'period_days': 30,
        'countries': ['ru'],
        'devices': 2,
        'traffic_gb': 10,
        'total_price': 50000
    })
    state.set_data = AsyncMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    return state

@pytest.mark.asyncio
async def test_save_cart_and_redirect_to_topup(mock_callback_query, mock_state, mock_user, mock_db):
    """Тест сохранения корзины и перенаправления к пополнению"""
    # Мокаем все зависимости
    with patch('app.handlers.subscription.purchase.user_cart_service') as mock_cart_service, \
         patch('app.handlers.subscription.purchase.get_payment_methods_keyboard_with_cart') as mock_keyboard_func, \
         patch('app.localization.texts.get_texts') as mock_get_texts:

        # Подготовим моки
        mock_cart_service.save_user_cart = AsyncMock(return_value=True)
        mock_keyboard = AsyncMock()
        mock_keyboard_func.return_value = mock_keyboard

        # Подготовим тексты
        mock_texts = AsyncMock()
        mock_texts.format_price = lambda x: f"{x/100} ₽"
        mock_get_texts.return_value = mock_texts

        missing_amount = 40000  # 50000 - 10000 = 40000

        # Вызываем функцию
        await save_cart_and_redirect_to_topup(mock_callback_query, mock_state, mock_user, missing_amount)

        # Проверяем, что данные были сохранены в корзину
        mock_cart_service.save_user_cart.assert_called_once()
        args, kwargs = mock_cart_service.save_user_cart.call_args
        saved_user_id, saved_cart_data = args

        assert saved_user_id == mock_user.id
        assert saved_cart_data['period_days'] == 30
        assert saved_cart_data['countries'] == ['ru']
        assert saved_cart_data['devices'] == 2
        assert saved_cart_data['traffic_gb'] == 10
        assert saved_cart_data['total_price'] == 50000
        assert saved_cart_data['saved_cart'] is True
        assert saved_cart_data['missing_amount'] == missing_amount
        assert saved_cart_data['return_to_cart'] is True
        assert saved_cart_data['user_id'] == mock_user.id

        # Проверяем, что сообщение было отредактировано
        mock_callback_query.message.edit_text.assert_called_once()

        # В этой функции нет вызова callback.answer()
        # mock_callback_query.answer не должен быть вызван
        mock_callback_query.answer.assert_not_called()

@pytest.mark.asyncio
async def test_return_to_saved_cart_success(mock_callback_query, mock_state, mock_user, mock_db):
    """Тест возврата к сохраненной корзине с достаточным балансом"""
    # Подготовим данные корзины
    cart_data = {
        'period_days': 30,
        'countries': ['ru', 'us'],
        'devices': 3,
        'traffic_gb': 20,
        'total_price': 30000,  # Меньше, чем баланс пользователя (50000)
        'saved_cart': True,
        'user_id': mock_user.id
    }

    # Мокаем все зависимости
    with patch('app.handlers.subscription.purchase.user_cart_service') as mock_cart_service, \
         patch('app.handlers.subscription.purchase._get_available_countries') as mock_get_countries, \
         patch('app.handlers.subscription.purchase.format_period_description') as mock_format_period, \
         patch('app.localization.texts.get_texts') as mock_get_texts, \
         patch('app.handlers.subscription.purchase.get_subscription_confirm_keyboard_with_cart') as mock_keyboard_func:

        # Подготовим моки
        mock_cart_service.get_user_cart = AsyncMock(return_value=cart_data)
        mock_get_countries.return_value = [{'uuid': 'ru', 'name': 'Russia'}, {'uuid': 'us', 'name': 'USA'}]
        mock_format_period.return_value = "30 дней"
        mock_keyboard = AsyncMock()
        mock_keyboard_func.return_value = mock_keyboard

        # Подготовим тексты
        mock_texts = AsyncMock()
        mock_texts.format_price = lambda x: f"{x/100} ₽"
        mock_get_texts.return_value = mock_texts

        # Увеличиваем баланс пользователя, чтобы его хватило
        mock_user.balance_kopeks = 50000

        # Вызываем функцию
        await return_to_saved_cart(mock_callback_query, mock_state, mock_user, mock_db)

        # Проверяем, что данные были загружены из корзины и установлены в FSM
        mock_state.set_data.assert_called_once_with(cart_data)

        # Проверяем, что сообщение было отредактировано
        mock_callback_query.message.edit_text.assert_called_once()

        # В успешном сценарии вызывается callback.answer()
        mock_callback_query.answer.assert_called_once()

@pytest.mark.asyncio
async def test_return_to_saved_cart_insufficient_funds(mock_callback_query, mock_state, mock_user, mock_db):
    """Тест возврата к сохраненной корзине с недостаточным балансом"""
    # Подготовим данные корзины
    cart_data = {
        'period_days': 30,
        'countries': ['ru', 'us'],
        'devices': 3,
        'traffic_gb': 20,
        'total_price': 50000,  # Больше, чем баланс пользователя (10000)
        'saved_cart': True,
        'user_id': mock_user.id
    }

    # Мокаем все зависимости
    with patch('app.handlers.subscription.purchase.user_cart_service') as mock_cart_service, \
         patch('app.localization.texts.get_texts') as mock_get_texts, \
         patch('app.handlers.subscription.purchase.get_insufficient_balance_keyboard_with_cart') as mock_keyboard_func:

        # Подготовим моки
        mock_cart_service.get_user_cart = AsyncMock(return_value=cart_data)
        mock_keyboard = AsyncMock()
        mock_keyboard_func.return_value = mock_keyboard

        # Подготовим тексты
        mock_texts = AsyncMock()
        mock_texts.format_price = lambda x: f"{x/100} ₽"
        mock_texts.t = lambda key, default: default
        mock_get_texts.return_value = mock_texts

        # Баланс пользователя меньше стоимости подписки
        mock_user.balance_kopeks = 10000

        # Вызываем функцию
        await return_to_saved_cart(mock_callback_query, mock_state, mock_user, mock_db)

        # Проверяем, что FSM не был изменен (данные не установлены)
        mock_state.set_data.assert_not_called()

        # Проверяем, что сообщение было отредактировано с сообщением о недостатке средств
        mock_callback_query.message.edit_text.assert_called_once()

        # В этой функции в сценарии недостатка средств вызова callback.answer() не происходит
        # (ответ отправляется через return до вызова callback.answer())
        mock_callback_query.answer.assert_not_called()

@pytest.mark.asyncio
async def test_clear_saved_cart(mock_callback_query, mock_state, mock_user, mock_db):
    """Тест очистки сохраненной корзины"""
    # Мокаем все зависимости
    with patch('app.handlers.subscription.purchase.user_cart_service') as mock_cart_service, \
         patch('app.handlers.menu.show_main_menu') as mock_show_main_menu:

        mock_cart_service.delete_user_cart = AsyncMock(return_value=True)
        mock_show_main_menu.return_value = AsyncMock()

        # Вызываем функцию
        await clear_saved_cart(mock_callback_query, mock_state, mock_user, mock_db)

        # Проверяем, что корзина удалена из сервиса
        mock_cart_service.delete_user_cart.assert_called_once_with(mock_user.id)

        # Проверяем, что FSM очищен
        mock_state.clear.assert_called_once()

        # Проверяем, что вызван answer
        mock_callback_query.answer.assert_called_once()

@pytest.mark.asyncio
async def test_handle_subscription_cancel_clears_saved_cart(mock_callback_query, mock_state, mock_user, mock_db):
    """Отмена покупки должна очищать сохраненную корзину"""
    mock_clear_draft = AsyncMock()
    mock_show_main_menu = AsyncMock()

    with patch('app.handlers.subscription.autopay.user_cart_service') as mock_cart_service, \
         patch('app.handlers.subscription.autopay.clear_subscription_checkout_draft', new=mock_clear_draft), \
         patch('app.localization.texts.get_texts', return_value=MagicMock()) as _, \
         patch('app.handlers.menu.show_main_menu', new=mock_show_main_menu):

        mock_cart_service.delete_user_cart = AsyncMock(return_value=True)

        await handle_subscription_cancel(mock_callback_query, mock_state, mock_user, mock_db)

        mock_state.clear.assert_called_once()
        mock_clear_draft.assert_awaited_once_with(mock_user.id)
        mock_cart_service.delete_user_cart.assert_awaited_once_with(mock_user.id)
        mock_show_main_menu.assert_awaited_once_with(mock_callback_query, mock_user, mock_db)
        mock_callback_query.answer.assert_called_once_with("❌ Покупка отменена")

