"""Кабинетные эндпоинты уровней реферальных наград.

Проверяется то, что дорого стоит: приём мусорного режима, частичная правка,
затирающая чужие поля, и молчаливое согласие переключить схему, залоченную в
``.env`` (запись легла бы в БД, но после перезапуска победил бы файл — админ
считал бы схему переключённой).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.config import settings


def test_referral_level_routes_registered(registered_paths):
    assert '/cabinet/admin/partners/referral-levels' in registered_paths
    assert '/cabinet/admin/partners/referral-levels/{level}' in registered_paths
    assert '/cabinet/admin/partners/referral-scheme' in registered_paths


@pytest.fixture
def wired(monkeypatch):
    from app.cabinet.routes import admin_partners

    state = {'saved': [], 'deleted': []}

    async def fake_get_all(_db, only_active=False):
        return []

    async def fake_upsert(_db, level, **values):
        state['saved'].append({'level': level, **values})
        return SimpleNamespace(level=level)

    async def fake_delete(_db, level):
        state['deleted'].append(level)
        return True

    monkeypatch.setattr(admin_partners, 'get_all_reward_levels', fake_get_all)
    monkeypatch.setattr(admin_partners, 'upsert_reward_level', fake_upsert)
    monkeypatch.setattr(admin_partners, 'delete_reward_level', fake_delete)
    return state


def _db_returning(value):
    """Сессия, у которой любой SELECT возвращает заданный scalar."""

    class _Result:
        def scalar_one_or_none(self):
            return value

        def all(self):
            return []

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())
    return db


class TestValidation:
    @pytest.mark.asyncio
    async def test_unknown_reward_mode_rejected(self, wired):
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralRewardLevelUpdateRequest

        with pytest.raises(HTTPException) as excinfo:
            await admin_partners.upsert_referral_level(
                1,
                ReferralRewardLevelUpdateRequest(reward_mode='everything'),
                admin=SimpleNamespace(id=1),
                db=_db_returning(None),
            )
        assert excinfo.value.status_code == 400
        assert not wired['saved']

    @pytest.mark.asyncio
    async def test_unknown_trigger_rejected(self, wired):
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralRewardLevelUpdateRequest

        with pytest.raises(HTTPException) as excinfo:
            await admin_partners.upsert_referral_level(
                1,
                ReferralRewardLevelUpdateRequest(trigger='whenever'),
                admin=SimpleNamespace(id=1),
                db=_db_returning(None),
            )
        assert excinfo.value.status_code == 400

    @pytest.mark.asyncio
    async def test_unknown_tariff_rejected(self, wired):
        """Тариф-призрак означал бы дни, которым некуда лечь."""
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralRewardLevelUpdateRequest

        with pytest.raises(HTTPException) as excinfo:
            await admin_partners.upsert_referral_level(
                1,
                ReferralRewardLevelUpdateRequest(referrer_tariff_id=999),
                admin=SimpleNamespace(id=1),
                db=_db_returning(None),
            )
        assert excinfo.value.status_code == 400
        assert not wired['saved']


class TestPartialUpdate:
    @pytest.mark.asyncio
    async def test_only_sent_fields_are_written(self, wired):
        """Экран правит поля по одному; отправка всего объекта затирала бы правки из бота."""
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralRewardLevelUpdateRequest

        await admin_partners.upsert_referral_level(
            2,
            ReferralRewardLevelUpdateRequest(referrer_days=7),
            admin=SimpleNamespace(id=1),
            db=_db_returning(None),
        )

        assert wired['saved'] == [{'level': 2, 'referrer_days': 7}]

    @pytest.mark.asyncio
    async def test_explicit_null_tariff_is_written(self, wired):
        """«Без тарифа» — осмысленное значение, а не отсутствие поля."""
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralRewardLevelUpdateRequest

        await admin_partners.upsert_referral_level(
            1,
            ReferralRewardLevelUpdateRequest(referrer_tariff_id=None),
            admin=SimpleNamespace(id=1),
            db=_db_returning(None),
        )

        assert wired['saved'] == [{'level': 1, 'referrer_tariff_id': None}]


class TestSchemeSwitch:
    @pytest.mark.asyncio
    async def test_env_pinned_scheme_conflicts(self, wired, monkeypatch):
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralSchemeUpdateRequest

        service = SimpleNamespace(set_value=AsyncMock(), is_env_locked=lambda key: True)
        monkeypatch.setattr(admin_partners, 'bot_configuration_service', service)

        with pytest.raises(HTTPException) as excinfo:
            await admin_partners.update_referral_scheme(
                ReferralSchemeUpdateRequest(scheme='levels'),
                admin=SimpleNamespace(id=1),
                db=_db_returning(None),
            )

        assert excinfo.value.status_code == 409
        service.set_value.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_scheme_rejected(self, wired, monkeypatch):
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralSchemeUpdateRequest

        service = SimpleNamespace(set_value=AsyncMock(), is_env_locked=lambda key: False)
        monkeypatch.setattr(admin_partners, 'bot_configuration_service', service)

        with pytest.raises(HTTPException) as excinfo:
            await admin_partners.update_referral_scheme(
                ReferralSchemeUpdateRequest(scheme='pyramid'),
                admin=SimpleNamespace(id=1),
                db=_db_returning(None),
            )

        assert excinfo.value.status_code == 400
        service.set_value.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_switch_persists(self, wired, monkeypatch):
        from app.cabinet.routes import admin_partners
        from app.cabinet.schemas.referral import ReferralSchemeUpdateRequest

        service = SimpleNamespace(set_value=AsyncMock(), is_env_locked=lambda key: False)
        monkeypatch.setattr(admin_partners, 'bot_configuration_service', service)
        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'legacy')

        db = _db_returning(None)
        await admin_partners.update_referral_scheme(
            ReferralSchemeUpdateRequest(scheme='levels'), admin=SimpleNamespace(id=1), db=db
        )

        service.set_value.assert_awaited_once_with(db, 'REFERRAL_REWARD_SCHEME', 'levels')


class TestDeletion:
    @pytest.mark.asyncio
    async def test_missing_level_returns_404(self, wired, monkeypatch):
        from app.cabinet.routes import admin_partners

        async def fake_delete(_db, _level):
            return False

        monkeypatch.setattr(admin_partners, 'delete_reward_level', fake_delete)

        with pytest.raises(HTTPException) as excinfo:
            await admin_partners.remove_referral_level(7, admin=SimpleNamespace(id=1), db=_db_returning(None))
        assert excinfo.value.status_code == 404


class TestTermsEndpointUnderLevels:
    """Публичные условия программы обязаны отвечать при включённой схеме.

    Ветка levels выполняется только когда схема переключена, поэтому опечатка в
    ней невидима на обычной установке и проявляется ровно в момент включения
    фичи — то есть у того, кто её включил, страница условий отдаёт 500.
    Линтер здесь не помощник: F821 в проекте отключён глобально.
    """

    @pytest.mark.asyncio
    async def test_levels_scheme_returns_terms(self, monkeypatch):
        from app.cabinet.routes import referral as route

        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')

        async def fake_all(_db):
            return {}

        monkeypatch.setattr(
            'app.services.referral_reward_service.ReferralRewardLevelService.get_all',
            classmethod(lambda cls, db: fake_all(db)),
        )

        response = await route.get_referral_terms(db=AsyncMock(), user=None)
        assert response.scheme == 'levels'
        assert response.level_descriptions == []

    @pytest.mark.asyncio
    async def test_language_follows_the_caller_when_known(self, monkeypatch):
        from app.cabinet.routes import referral as route

        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')
        captured = {}

        async def fake_all(_db):
            return {}

        async def fake_describe(_db, tariff_names=None, language=None):
            captured['language'] = language
            return []

        monkeypatch.setattr(
            'app.services.referral_reward_service.ReferralRewardLevelService.get_all',
            classmethod(lambda cls, db: fake_all(db)),
        )
        monkeypatch.setattr('app.services.referral_reward_service.describe_active_levels', fake_describe)

        await route.get_referral_terms(db=AsyncMock(), user=SimpleNamespace(language='en'))
        assert captured['language'] == 'en'

    @pytest.mark.asyncio
    async def test_legacy_scheme_still_public(self, monkeypatch):
        from app.cabinet.routes import referral as route

        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'legacy')
        response = await route.get_referral_terms(db=AsyncMock(), user=None)
        assert response.scheme == 'legacy'


class TestLegacyImportEndpoint:
    """Перенос старых настроек в уровень 1 — паритет с админкой бота."""

    @pytest.mark.asyncio
    async def test_creates_level_one_disabled_on_first_topup(self, wired, monkeypatch):
        from app.cabinet.routes import admin_partners

        monkeypatch.setattr(settings, 'REFERRAL_COMMISSION_PERCENT', 25)
        monkeypatch.setattr(settings, 'REFERRAL_INVITER_BONUS_KOPEKS', 100_00)
        monkeypatch.setattr(settings, 'REFERRAL_FIRST_TOPUP_BONUS_KOPEKS', 50_00)
        monkeypatch.setattr(settings, 'REFERRAL_MAX_COMMISSION_PAYMENTS', 3)

        async def no_level(_db, _level):
            return None

        monkeypatch.setattr(admin_partners, 'get_reward_level', no_level)

        await admin_partners.import_legacy_referral_settings(admin=SimpleNamespace(id=1), db=_db_returning(None))

        imported = wired['saved'][-1]
        assert imported['is_active'] is False, 'перенос не должен молча начать платить'
        # Фиксированные бонусы классической схемы разовые: повод «каждое
        # пополнение» превратил бы их в регулярную выплату.
        assert imported['trigger'] == 'first_topup'
        assert imported['referrer_percent'] == 25
        assert imported['referrer_fixed_kopeks'] == 100_00
        assert imported['referee_fixed_kopeks'] == 50_00
        assert imported['max_payments'] == 3

    @pytest.mark.asyncio
    async def test_refuses_when_level_one_exists(self, wired, monkeypatch):
        from app.cabinet.routes import admin_partners

        async def existing(_db, _level):
            return SimpleNamespace(level=1)

        monkeypatch.setattr(admin_partners, 'get_reward_level', existing)

        with pytest.raises(HTTPException) as excinfo:
            await admin_partners.import_legacy_referral_settings(admin=SimpleNamespace(id=1), db=_db_returning(None))
        assert excinfo.value.status_code == 409
        assert not wired['saved'], 'существующее правило не должно затираться переносом'
