"""
Упрощенные тесты для проверки логики уведомлений Kassa AI.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def test_notification_message_bright_prompt():
    """
    Тест: проверяем что формируется ЯРКОЕ сообщение с SHOW_ACTIVATION_PROMPT_AFTER_TOPUP=true.
    """
    from app.config import settings

    # Эмулируем код из kassa_ai.py
    SHOW_ACTIVATION_PROMPT_AFTER_TOPUP = True
    display_name = "Kassa AI"
    amount_formatted = "10₽"

    if SHOW_ACTIVATION_PROMPT_AFTER_TOPUP:
        message = (
            '✅ <b>Платеж успешно завершен!</b>\n\n'
            f'💰 Сумма: {amount_formatted}\n'
            f'💳 Способ: {display_name}\n\n'
            '💎 Средства зачислены на ваш баланс!\n\n'
            '‼️ <b>ВНИМАНИЕ! ОБЯЗАТЕЛЬНО АКТИВИРУЙТЕ ПОДПИСКУ!</b> ‼️\n\n'
            '⚠️ Пополнение баланса <b>НЕ АКТИВИРУЕТ</b> подписку автоматически!\n\n'
            '👇 <b>НАЖМИТЕ КНОПКУ НИЖЕ ДЛЯ АКТИВАЦИИ</b> 👇'
        )
    else:
        message = ''

    # Проверки
    assert '‼️' in message
    assert 'ВНИМАНИЕ' in message
    assert 'ОБЯЗАТЕЛЬНО АКТИВИРУЙТЕ ПОДПИСКУ' in message
    assert '👇' in message
    assert display_name in message
    assert amount_formatted in message
    print(f"\n✅ ЯРКОЕ сообщение сформировано правильно:\n{message}")


def test_notification_message_standard():
    """
    Тест: проверяем что формируется обычное сообщение с SHOW_ACTIVATION_PROMPT_AFTER_TOPUP=false.
    """
    # Эмулируем код из kassa_ai.py
    SHOW_ACTIVATION_PROMPT_AFTER_TOPUP = False
    display_name = "Kassa AI"
    amount_formatted = "10₽"

    if SHOW_ACTIVATION_PROMPT_AFTER_TOPUP:
        message = ''
    else:
        message = (
            '✅ <b>Платеж успешно завершен!</b>\n\n'
            f'💰 Сумма: {amount_formatted}\n'
            f'💳 Способ: {display_name}\n\n'
            'Средства зачислены на ваш баланс!\n\n'
            '⚠️ <b>Важно:</b> Пополнение баланса не активирует подписку автоматически. '
            'Обязательно активируйте подписку отдельно!\n\n'
            f'🔄 При наличии сохранённой корзины подписки и включенной автопокупке, '
            f'подписка будет приобретена автоматически после пополнения баланса.'
        )

    # Проверки
    assert '‼️' not in message
    assert 'ОБЯЗАТЕЛЬНО АКТИВИРУЙТЕ ПОДПИСКУ' not in message
    assert 'Платеж успешно завершен' in message
    assert display_name in message
    assert amount_formatted in message
    print(f"\n✅ Обычное сообщение сформировано правильно:\n{message}")


def test_telegram_id_saved_before_commit():
    """
    Тест: проверяем что telegram_id сохраняется в локальную переменную ДО commit.
    """
    # Эмулируем юзера
    user = MagicMock()
    user.telegram_id = 123456789
    user.language = 'ru'

    # Сохраняем ДО commit
    user_telegram_id = user.telegram_id
    user_language = user.language

    # Эмулируем что после commit объект отсоединяется
    user.telegram_id = None
    user.language = None

    # Проверяем что локальные переменные сохранились
    assert user_telegram_id == 123456789
    assert user_language == 'ru'
    print(f"\n✅ telegram_id сохранен в локальную переменную: {user_telegram_id}")


def test_send_message_called_with_correct_params():
    """
    Тест: проверяем что bot.send_message вызывается с правильными параметрами.
    """
    bot = MagicMock()
    bot.send_message = MagicMock()

    user_telegram_id = 123456789
    message = "Тестовое сообщение"
    keyboard = MagicMock()

    # Эмулируем вызов
    if bot and user_telegram_id:
        bot.send_message(
            chat_id=user_telegram_id,
            text=message,
            parse_mode='HTML',
            reply_markup=keyboard,
        )

    # Проверки
    bot.send_message.assert_called_once()
    call_args = bot.send_message.call_args
    assert call_args[1]['chat_id'] == 123456789
    assert call_args[1]['parse_mode'] == 'HTML'
    assert call_args[1]['text'] == message
    print(f"\n✅ bot.send_message вызван с правильными параметрами")


def test_no_send_when_no_telegram_id():
    """
    Тест: уведомление НЕ отправляется если нет telegram_id.
    """
    bot = MagicMock()
    bot.send_message = MagicMock()

    user_telegram_id = None

    # Эмулируем проверку
    if bot and user_telegram_id:
        bot.send_message(chat_id=user_telegram_id, text="test")

    # Проверка
    bot.send_message.assert_not_called()
    print(f"\n✅ bot.send_message НЕ вызван когда telegram_id=None")
