"""Админский редактор уровней реферальных наград.

Проверяется то, что ломается тихо: ответ на callback дважды, ввод в неверных
единицах и создание уровня, который сразу начинает платить недонастроенным
правилом.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.handlers.admin import referral_levels as editor


class _Message:
    def __init__(self):
        self.edit_text = AsyncMock()
        self.answer = AsyncMock()


def _raw(handler):
    """Хендлер без декораторов админки.

    ``admin_required`` проверяет ``isinstance(event, types.CallbackQuery)``, а
    здесь события подставные. Разворачивать декораторы честнее, чем подделывать
    типы aiogram: проверяется логика редактора, а не работа самой обёртки.
    """
    return inspect.unwrap(handler)


def _callback(data: str = 'admin_ref_levels'):
    return SimpleNamespace(data=data, message=_Message(), answer=AsyncMock(), from_user=SimpleNamespace(id=1))


def _level(level=1, **kwargs):
    base = {
        'level': level,
        'is_active': True,
        'reward_mode': 'money',
        'trigger': 'every_topup',
        'referrer_percent': 10,
        'referrer_fixed_kopeks': None,
        'referrer_days': 0,
        'referrer_tariff_id': None,
        'referee_fixed_kopeks': None,
        'referee_days': 0,
        'referee_tariff_id': None,
        'max_payments': 0,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.fixture
def wired(monkeypatch):
    """Подменяет CRUD уровней и тарифов, собирая записи."""
    state = {'levels': [_level(1)], 'saved': []}

    async def fake_get_all(_db, only_active=False):
        return [lvl for lvl in state['levels'] if lvl.is_active or not only_active]

    async def fake_get(_db, level):
        return next((lvl for lvl in state['levels'] if lvl.level == level), None)

    async def fake_upsert(_db, level, **values):
        state['saved'].append({'level': level, **values})
        existing = next((lvl for lvl in state['levels'] if lvl.level == level), None)
        if existing is None:
            existing = _level(level)
            state['levels'].append(existing)
        for key, value in values.items():
            setattr(existing, key, value)
        return existing

    async def fake_delete(_db, level):
        state['levels'] = [lvl for lvl in state['levels'] if lvl.level != level]
        return True

    async def fake_tariffs(_db, include_inactive=False):
        return [SimpleNamespace(id=42, name='Про')]

    monkeypatch.setattr(editor, 'get_all_reward_levels', fake_get_all)
    monkeypatch.setattr(editor, 'get_reward_level', fake_get)
    monkeypatch.setattr(editor, 'upsert_reward_level', fake_upsert)
    monkeypatch.setattr(editor, 'delete_reward_level', fake_delete)
    monkeypatch.setattr(editor, 'get_all_tariffs', fake_tariffs)
    monkeypatch.setattr(settings, 'ADMIN_IDS', [1])
    return state


class TestSingleAnswerPerCallback:
    """Telegram принимает ровно один ответ на callback.

    Хендлер, который подтверждает действие своим текстом и потом перерисовывает
    экран, отвечал бы дважды — второй вызов падает с «query is invalid», и
    пользователь видит зависшую кнопку.
    """

    def test_render_helpers_never_answer(self):
        import ast

        for name in ('_render_levels', '_render_level'):
            tree = ast.parse(inspect.getsource(getattr(editor, name)).lstrip())
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and node.attr == 'answer'
                and isinstance(node.value, ast.Name)
                and node.value.id == 'callback'
            ]
            assert not calls, f'{name} не должен отвечать на callback'

    @pytest.mark.asyncio
    async def test_toggle_active_answers_once(self, wired):
        callback = _callback('admin_ref_lvl_active:1')
        await _raw(editor.toggle_level_active)(callback, db_user=SimpleNamespace(id=1), db=None)
        assert callback.answer.await_count == 1

    @pytest.mark.asyncio
    async def test_mode_cycle_answers_once(self, wired):
        callback = _callback('admin_ref_lvl_mode:1')
        await _raw(editor.cycle_level_mode)(callback, db_user=SimpleNamespace(id=1), db=None)
        assert callback.answer.await_count == 1


class TestActiveBonusSelection:
    """«Выбрать активный бонус/бонусы» — это и есть reward_mode."""

    @pytest.mark.asyncio
    async def test_mode_cycles_money_days_both(self, wired):
        expected = ['days', 'both', 'money']
        for want in expected:
            callback = _callback('admin_ref_lvl_mode:1')
            await _raw(editor.cycle_level_mode)(callback, db_user=SimpleNamespace(id=1), db=None)
            assert wired['saved'][-1]['reward_mode'] == want

    @pytest.mark.asyncio
    async def test_trigger_cycles_all_three(self, wired):
        seen = []
        for _ in range(3):
            callback = _callback('admin_ref_lvl_trigger:1')
            await _raw(editor.cycle_level_trigger)(callback, db_user=SimpleNamespace(id=1), db=None)
            seen.append(wired['saved'][-1]['trigger'])
        assert set(seen) == {'registration', 'first_topup', 'every_topup'}


class TestNewLevelSafety:
    @pytest.mark.asyncio
    async def test_new_level_starts_disabled(self, wired):
        """Включённый при создании уровень начал бы платить пустым правилом."""
        callback = _callback('admin_ref_lvl_add')
        await _raw(editor.add_reward_level)(callback, db_user=SimpleNamespace(id=1), db=None)
        created = wired['saved'][-1]
        assert created['level'] == 2
        assert created['is_active'] is False

    @pytest.mark.asyncio
    async def test_legacy_import_starts_disabled(self, wired, monkeypatch):
        wired['levels'] = []
        monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 25)
        monkeypatch.setattr(settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 100_00)
        monkeypatch.setattr(settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 50_00)

        callback = _callback('admin_ref_lvl_import')
        await _raw(editor.import_legacy_settings)(callback, db_user=SimpleNamespace(id=1), db=None)

        imported = wired['saved'][-1]
        assert imported['is_active'] is False, 'перенос не должен молча начать платить'
        assert imported['referrer_percent'] == 25
        assert imported['referrer_fixed_kopeks'] == 100_00
        assert imported['referee_fixed_kopeks'] == 50_00

    @pytest.mark.asyncio
    async def test_legacy_import_does_not_make_one_off_bonuses_recurring(self, wired, monkeypatch):
        """Фиксированные бонусы классической схемы разовые — за первое пополнение.

        Повод у уровня один на всё правило. Перенос с «каждым пополнением»
        превратил бы оба разовых бонуса в регулярную выплату: на живой базе это
        деньги, которых никто не обещал.
        """
        wired['levels'] = []
        monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 25)
        monkeypatch.setattr(settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 100_00)
        monkeypatch.setattr(settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 50_00)

        callback = _callback('admin_ref_lvl_import')
        await _raw(editor.import_legacy_settings)(callback, db_user=SimpleNamespace(id=1), db=None)

        assert wired['saved'][-1]['trigger'] == 'first_topup'


class TestValueInput:
    @pytest.fixture
    def fsm(self):
        store = {'data': {'referral_level': 1, 'referral_field': 'referrer_fixed_kopeks'}, 'cleared': False}

        async def get_data():
            return store['data']

        async def clear():
            store['cleared'] = True

        return SimpleNamespace(get_data=get_data, clear=clear, store=store)

    @pytest.mark.asyncio
    async def test_money_is_entered_in_rubles_stored_in_kopeks(self, wired, fsm):
        message = SimpleNamespace(text='150,50', answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        await _raw(editor.process_level_value)(message, db_user=SimpleNamespace(id=1), db=None, state=fsm)
        assert wired['saved'][-1]['referrer_fixed_kopeks'] == 15050

    @pytest.mark.asyncio
    async def test_days_are_plain_integers(self, wired, fsm):
        fsm.store['data']['referral_field'] = 'referrer_days'
        message = SimpleNamespace(text='7', answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        await _raw(editor.process_level_value)(message, db_user=SimpleNamespace(id=1), db=None, state=fsm)
        assert wired['saved'][-1]['referrer_days'] == 7

    @pytest.mark.asyncio
    async def test_zero_percent_is_stored_as_null(self, wired, fsm):
        """NULL и 0 значат «не начисляется» — два представления одного состояния спутались бы."""
        fsm.store['data']['referral_field'] = 'referrer_percent'
        message = SimpleNamespace(text='0', answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        await _raw(editor.process_level_value)(message, db_user=SimpleNamespace(id=1), db=None, state=fsm)
        assert wired['saved'][-1]['referrer_percent'] is None

    @pytest.mark.asyncio
    async def test_zero_days_stays_zero_not_null(self, wired, fsm):
        """Колонка дней NOT NULL: запись None уронила бы сохранение."""
        fsm.store['data']['referral_field'] = 'referrer_days'
        message = SimpleNamespace(text='0', answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        await _raw(editor.process_level_value)(message, db_user=SimpleNamespace(id=1), db=None, state=fsm)
        assert wired['saved'][-1]['referrer_days'] == 0

    @pytest.mark.asyncio
    async def test_percent_above_hundred_is_rejected(self, wired, fsm):
        fsm.store['data']['referral_field'] = 'referrer_percent'
        message = SimpleNamespace(text='150', answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        await _raw(editor.process_level_value)(message, db_user=SimpleNamespace(id=1), db=None, state=fsm)
        assert not wired['saved'], 'значение вне диапазона не должно сохраняться'
        assert fsm.store['cleared'] is False, 'ввод остаётся открытым для исправления'

    @pytest.mark.asyncio
    async def test_non_numeric_input_is_rejected(self, wired, fsm):
        message = SimpleNamespace(text='много', answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        await _raw(editor.process_level_value)(message, db_user=SimpleNamespace(id=1), db=None, state=fsm)
        assert not wired['saved']

    @pytest.mark.asyncio
    async def test_negative_input_is_rejected(self, wired, fsm):
        message = SimpleNamespace(text='-5', answer=AsyncMock(), from_user=SimpleNamespace(id=1))
        await _raw(editor.process_level_value)(message, db_user=SimpleNamespace(id=1), db=None, state=fsm)
        assert not wired['saved']


class TestTariffSelection:
    @pytest.mark.asyncio
    async def test_no_tariff_option_stores_null(self, wired):
        callback = _callback('admin_ref_lvl_settariff:1:referrer:0')
        await _raw(editor.set_level_tariff)(callback, db_user=SimpleNamespace(id=1), db=None)
        assert wired['saved'][-1]['referrer_tariff_id'] is None

    @pytest.mark.asyncio
    async def test_referee_side_writes_its_own_column(self, wired):
        callback = _callback('admin_ref_lvl_settariff:1:referee:42')
        await _raw(editor.set_level_tariff)(callback, db_user=SimpleNamespace(id=1), db=None)
        assert wired['saved'][-1] == {'level': 1, 'referee_tariff_id': 42}
