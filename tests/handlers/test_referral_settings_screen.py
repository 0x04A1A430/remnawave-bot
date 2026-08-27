"""Экран, где пользователь настраивает свои реферальные награды.

Главное здесь — что экрана нет, пока админ ничего не разрешил, и что чужую
подписку выбрать нельзя. Всё остальное — отображение текущего состояния: пункт,
не показывающий, что выбрано сейчас, заставляет угадывать.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.handlers import referral_settings as screen


class _Message:
    def __init__(self):
        self.edit_text = AsyncMock()


def _callback(data: str = 'referral_reward_settings'):
    return SimpleNamespace(data=data, message=_Message(), answer=AsyncMock(), from_user=SimpleNamespace(id=1))


def _user(uid: int = 1, *, preference=None, chosen=None):
    return SimpleNamespace(
        id=uid,
        telegram_id=1000 + uid,
        language='ru',
        referral_reward_preference=preference,
        referral_days_subscription_id=chosen,
    )


def _sub(sub_id: int, *, tariff_id=None, trial=False):
    from datetime import UTC, datetime, timedelta

    return SimpleNamespace(
        id=sub_id, tariff_id=tariff_id, is_trial=trial, end_date=datetime.now(UTC) + timedelta(days=30)
    )


def _raw(handler):
    while hasattr(handler, '__wrapped__'):
        handler = handler.__wrapped__
    return handler


@pytest.fixture
def allowed(monkeypatch):
    monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')
    monkeypatch.setattr(settings, 'REFERRAL_ALLOW_REWARD_KIND_CHOICE', True)
    monkeypatch.setattr(settings, 'REFERRAL_ALLOW_DAYS_TARGET_CHOICE', True)


@pytest.fixture
def subs(monkeypatch):
    store = {'items': [_sub(10, tariff_id=1), _sub(11, tariff_id=2), _sub(12, trial=True)]}

    async def fake_active(_db, _uid):
        return store['items']

    monkeypatch.setattr('app.database.crud.subscription.get_active_subscriptions_by_user_id', fake_active)

    async def fake_names(_db, _subs):
        return {1: 'Про', 2: 'Базовый'}

    monkeypatch.setattr(screen, '_tariff_names', fake_names)
    return store


class TestVisibility:
    def test_button_hidden_until_admin_allows_something(self, monkeypatch):
        """Экран, на котором нечего менять, обещает влияние, которого нет."""
        from app.keyboards.inline import get_referral_keyboard

        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')
        monkeypatch.setattr(settings, 'REFERRAL_ALLOW_REWARD_KIND_CHOICE', False)
        monkeypatch.setattr(settings, 'REFERRAL_ALLOW_DAYS_TARGET_CHOICE', False)

        markup = get_referral_keyboard('ru')
        actions = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert 'referral_reward_settings' not in actions

    @pytest.mark.parametrize(('kind', 'target'), [(True, False), (False, True), (True, True)])
    def test_button_appears_when_anything_is_allowed(self, monkeypatch, kind, target):
        from app.keyboards.inline import get_referral_keyboard

        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')
        monkeypatch.setattr(settings, 'REFERRAL_ALLOW_REWARD_KIND_CHOICE', kind)
        monkeypatch.setattr(settings, 'REFERRAL_ALLOW_DAYS_TARGET_CHOICE', target)

        markup = get_referral_keyboard('ru')
        actions = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert 'referral_reward_settings' in actions

    @pytest.mark.asyncio
    async def test_only_the_allowed_section_is_rendered(self, allowed, subs, monkeypatch):
        monkeypatch.setattr(settings, 'REFERRAL_ALLOW_DAYS_TARGET_CHOICE', False)
        callback = _callback()

        await _raw(screen.show_reward_settings)(callback, db_user=_user(), db=None)

        actions = [
            b.callback_data
            for row in callback.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
            for b in row
        ]
        assert any(a.startswith('ref_pref:') for a in actions)
        assert not any(a.startswith('ref_days_target:') for a in actions)


class TestCurrentStateIsVisible:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('preference', 'marked'),
        [
            # Не выбиравший получает деньги — отметка обязана это показывать,
            # иначе экран расходится с тем, что реально начислят.
            (None, 'ref_pref:money'),
            ('money', 'ref_pref:money'),
            ('days', 'ref_pref:days'),
        ],
    )
    async def test_selected_preference_is_marked(self, allowed, subs, preference, marked):
        callback = _callback()
        await _raw(screen.show_reward_settings)(callback, db_user=_user(preference=preference), db=None)

        rows = callback.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        chosen = [b.callback_data for row in rows for b in row if b.text.startswith('🔘')]
        assert marked in chosen, chosen

    @pytest.mark.asyncio
    async def test_trial_subscriptions_are_not_offered(self, allowed, subs):
        """Положить награду в триал всё равно нельзя — пункт обещал бы обратное."""
        callback = _callback()
        await _raw(screen.show_reward_settings)(callback, db_user=_user(), db=None)

        actions = [
            b.callback_data
            for row in callback.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
            for b in row
        ]
        assert 'ref_days_target:12' not in actions
        assert 'ref_days_target:10' in actions

    @pytest.mark.asyncio
    async def test_says_so_when_there_is_nothing_to_choose(self, allowed, subs):
        subs['items'] = []
        callback = _callback()

        await _raw(screen.show_reward_settings)(callback, db_user=_user(), db=None)

        assert 'нет подписок' in callback.message.edit_text.await_args.args[0]


class TestSaving:
    @pytest.mark.asyncio
    async def test_preference_is_saved(self, allowed, subs):
        user = _user()
        db = SimpleNamespace(commit=AsyncMock())

        await _raw(screen.set_reward_preference)(_callback('ref_pref:days'), db_user=user, db=db)

        assert user.referral_reward_preference == 'days'
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_only_two_options_are_offered(self, allowed, subs):
        """Выбор двоичный: деньги ИЛИ дни, варианта «и то и другое» нет."""
        callback = _callback()
        await _raw(screen.show_reward_settings)(callback, db_user=_user(), db=None)

        actions = [
            b.callback_data
            for row in callback.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
            for b in row
            if b.callback_data.startswith('ref_pref:')
        ]
        assert actions == ['ref_pref:money', 'ref_pref:days'], actions

    @pytest.mark.asyncio
    async def test_unknown_value_is_not_saved(self, allowed, subs):
        """Мусор в callback'е не должен переключать вид награды."""
        user = _user(preference='money')
        db = SimpleNamespace(commit=AsyncMock())
        callback = _callback('ref_pref:any')

        await _raw(screen.set_reward_preference)(callback, db_user=user, db=db)

        assert user.referral_reward_preference == 'money'
        db.commit.assert_not_awaited()
        assert callback.answer.await_args.kwargs.get('show_alert') is True

    @pytest.mark.asyncio
    async def test_a_foreign_subscription_is_refused(self, allowed, subs):
        """Чужому идентификатору не место в БД: проверка при начислении — не единственный рубеж."""
        user = _user()
        db = SimpleNamespace(commit=AsyncMock())
        callback = _callback('ref_days_target:999')

        await _raw(screen.set_days_target)(callback, db_user=user, db=db)

        assert user.referral_days_subscription_id is None
        db.commit.assert_not_awaited()
        assert callback.answer.await_args.kwargs.get('show_alert') is True

    @pytest.mark.asyncio
    async def test_own_subscription_is_saved(self, allowed, subs):
        user = _user()
        db = SimpleNamespace(commit=AsyncMock())

        await _raw(screen.set_days_target)(_callback('ref_days_target:11'), db_user=user, db=db)

        assert user.referral_days_subscription_id == 11

    @pytest.mark.asyncio
    async def test_auto_clears_the_target(self, allowed, subs):
        user = _user(chosen=11)
        db = SimpleNamespace(commit=AsyncMock())

        await _raw(screen.set_days_target)(_callback('ref_days_target:auto'), db_user=user, db=db)

        assert user.referral_days_subscription_id is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ('handler', 'data', 'key'),
        [
            ('set_reward_preference', 'ref_pref:money', 'REFERRAL_ALLOW_REWARD_KIND_CHOICE'),
            ('set_days_target', 'ref_days_target:10', 'REFERRAL_ALLOW_DAYS_TARGET_CHOICE'),
        ],
    )
    async def test_disallowed_setting_is_not_written(self, allowed, subs, monkeypatch, handler, data, key):
        """Запрещённую настройку нельзя записать даже прямым callback'ом."""
        monkeypatch.setattr(settings, key, False)
        user = _user()
        db = SimpleNamespace(commit=AsyncMock())

        await _raw(getattr(screen, handler))(_callback(data), db_user=user, db=db)

        db.commit.assert_not_awaited()


def test_every_handler_is_registered():
    """Обработчик без регистрации — кнопка, которая молча ничего не делает."""
    source = inspect.getsource(screen.register_handlers)
    for name in ('show_reward_settings', 'set_reward_preference', 'set_days_target'):
        assert name in source, name
