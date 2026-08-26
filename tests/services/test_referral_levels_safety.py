"""Предохранители многоуровневой схемы.

Два класса ошибок, каждый из которых стоил бы денег или доверия:

1. **Правка уровня без сброса кэша.** Админ сохраняет новое правило, экран
   показывает новое значение, а начисления до перезапуска идут по старому.
   Расхождение видимого и работающего — худший вид бага в настройках.

2. **Легаси-доначисление на многоуровневой установке.** Диагностика ищет
   отсутствие строки с легаси-причиной и доплачивает по ключам ``REFERRAL_*``.
   В схеме 'levels' награда могла быть выдана по другому поводу или вовсе днями —
   такую пару детектор считает «пропущенной» и заплатит ВТОРОЙ раз, живыми
   деньгами.
"""

from types import SimpleNamespace

import pytest

from app.config import settings
from app.database.crud import referral_reward_level as level_crud
from app.services.referral_diagnostics_service import ReferralDiagnosticsService
from app.services.referral_reward_service import ReferralRewardLevelService


class TestCacheInvalidation:
    @pytest.mark.asyncio
    async def test_upsert_drops_cache(self, monkeypatch):
        ReferralRewardLevelService._cache = {}
        assert ReferralRewardLevelService._cache is not None

        saved = SimpleNamespace(level=1)

        async def fake_get(_db, _level):
            return saved

        async def noop_commit():
            return None

        async def noop_refresh(_obj):
            return None

        monkeypatch.setattr(level_crud, 'get_reward_level', fake_get)
        db = SimpleNamespace(commit=noop_commit, refresh=noop_refresh, add=lambda _o: None)

        await level_crud.upsert_reward_level(db, 1, referrer_percent=15)
        assert ReferralRewardLevelService._cache is None

    @pytest.mark.asyncio
    async def test_delete_drops_cache(self, monkeypatch):
        ReferralRewardLevelService._cache = {}
        deleted = SimpleNamespace(level=2)

        async def fake_get(_db, _level):
            return deleted

        async def noop_commit():
            return None

        async def noop_delete(_obj):
            return None

        monkeypatch.setattr(level_crud, 'get_reward_level', fake_get)
        db = SimpleNamespace(commit=noop_commit, delete=noop_delete)

        assert await level_crud.delete_reward_level(db, 2) is True
        assert ReferralRewardLevelService._cache is None


class TestDiagnosticsGuard:
    @pytest.mark.asyncio
    async def test_detector_refuses_on_levels_scheme(self, monkeypatch):
        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')

        async def explode(*_args, **_kwargs):
            raise AssertionError('детектор не должен ходить в базу на многоуровневой схеме')

        db = SimpleNamespace(execute=explode)
        report = await ReferralDiagnosticsService().check_missing_bonuses(db)

        assert report.unsupported_scheme is True
        assert report.missing_bonuses == []

    @pytest.mark.asyncio
    async def test_apply_refuses_stale_report(self, monkeypatch):
        """Отчёт мог быть построен ДО переключения схемы и пролежать в Redis."""
        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'levels')

        async def explode(*_args, **_kwargs):
            raise AssertionError('доначисление не должно трогать базу на многоуровневой схеме')

        stale = [SimpleNamespace(referral_id=2, referrer_id=1)]
        db = SimpleNamespace(execute=explode)
        report = await ReferralDiagnosticsService().fix_missing_bonuses(db, stale, apply=True)

        assert report.users_fixed == 0

    @pytest.mark.asyncio
    async def test_legacy_scheme_still_runs_detector(self, monkeypatch):
        """Гейт не должен выключать диагностику на обычных установках."""
        monkeypatch.setattr(settings, 'REFERRAL_REWARD_SCHEME', 'legacy')
        reached = []

        async def fake_execute(*_args, **_kwargs):
            reached.append(1)

            class _Empty:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _Empty()

        db = SimpleNamespace(execute=fake_execute)
        report = await ReferralDiagnosticsService().check_missing_bonuses(db)

        assert reached, 'на legacy-схеме детектор обязан работать как раньше'
        assert report.unsupported_scheme is False
