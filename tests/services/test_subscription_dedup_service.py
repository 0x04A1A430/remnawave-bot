"""
Тест разовой чистки дублей тарифных подписок (subscription_dedup_service).

Проверяем, что лишние ИСТЁКШИЕ/отключённые дубли удаляются И из БД, И из панели
(тем же delete_remnawave_user, что и штатное удаление подписки), живые/одиночные
не трогаются, а если панель не подтвердила удаление — строка в БД остаётся
(консистентность БД↔панель), повторит на следующем старте.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.database.models import SubscriptionStatus
from app.services import subscription_dedup_service as dedup


def _sub(sub_id, user_id, tariff_id, status, days_from_now, uuid='u'):
    s = MagicMock()
    s.id = sub_id
    s.user_id = user_id
    s.tariff_id = tariff_id
    s.status = status
    s.end_date = datetime.now(UTC) + timedelta(days=days_from_now)
    s.is_trial = False
    s.remnawave_uuid = uuid
    return s


def _patch(monkeypatch, subs, *, delete_result=True):
    db = AsyncMock()
    db.commit = AsyncMock()
    deleted: list = []
    db.delete = AsyncMock(side_effect=lambda obj: deleted.append(obj))

    result = MagicMock()
    result.scalars.return_value.all.return_value = subs
    db.execute = AsyncMock(return_value=result)

    acm = MagicMock()
    acm.__aenter__ = AsyncMock(return_value=db)
    acm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(dedup, 'AsyncSessionLocal', MagicMock(return_value=acm))

    svc = MagicMock()
    svc.delete_remnawave_user = AsyncMock(return_value=delete_result)
    monkeypatch.setattr(dedup, 'SubscriptionService', MagicMock(return_value=svc))

    return deleted, svc


async def test_collapses_report_scenario(monkeypatch):
    subs = [
        # user 1, тариф 1: active + 2 истёкших → остаётся active
        _sub(1, 1, 1, SubscriptionStatus.ACTIVE.value, 14, uuid='a1'),
        _sub(2, 1, 1, SubscriptionStatus.EXPIRED.value, -14, uuid='e2'),
        _sub(3, 1, 1, SubscriptionStatus.EXPIRED.value, -59, uuid='e3'),
        # user 1, тариф 2: 2 истёкших → остаётся самый свежий
        _sub(4, 1, 2, SubscriptionStatus.EXPIRED.value, -1, uuid='e4'),
        _sub(5, 1, 2, SubscriptionStatus.EXPIRED.value, -30, uuid='e5'),
        # user 2, тариф 1: одна active → не трогаем
        _sub(6, 2, 1, SubscriptionStatus.ACTIVE.value, 20, uuid='a6'),
    ]
    deleted, svc = _patch(monkeypatch, subs)

    stats = await dedup._run_dedupe()

    deleted_ids = {s.id for s in deleted}
    assert deleted_ids == {2, 3, 5}  # удалены лишние истёкшие
    panel_uuids = {c.args[0] for c in svc.delete_remnawave_user.call_args_list}
    assert panel_uuids == {'e2', 'e3', 'e5'}  # и из панели — те же
    assert stats == {'removed_db': 3, 'removed_panel': 3}


async def test_never_removes_alive_even_if_outranked_by_date(monkeypatch):
    subs = [
        _sub(1, 1, 1, SubscriptionStatus.EXPIRED.value, 30, uuid='e1'),  # дата позже, но истёкшая
        _sub(2, 1, 1, SubscriptionStatus.ACTIVE.value, 1, uuid='a2'),  # активная, дата раньше
    ]
    deleted, svc = _patch(monkeypatch, subs)

    await dedup._run_dedupe()

    assert {s.id for s in deleted} == {1}  # удалён истёкший дубль
    assert svc.delete_remnawave_user.call_args_list[0].args[0] == 'e1'


async def test_panel_failure_keeps_db_row(monkeypatch):
    subs = [
        _sub(1, 1, 1, SubscriptionStatus.ACTIVE.value, 10, uuid='a1'),
        _sub(2, 1, 1, SubscriptionStatus.EXPIRED.value, -5, uuid='e2'),
    ]
    deleted, svc = _patch(monkeypatch, subs, delete_result=False)  # панель не подтвердила

    stats = await dedup._run_dedupe()

    assert deleted == []  # строку в БД НЕ удалили — консистентно с панелью
    assert stats == {'removed_db': 0, 'removed_panel': 0}
    svc.delete_remnawave_user.assert_awaited_once()  # попытка была


async def test_single_rows_untouched(monkeypatch):
    subs = [
        _sub(1, 1, 1, SubscriptionStatus.EXPIRED.value, -5, uuid='e1'),
        _sub(2, 1, 2, SubscriptionStatus.ACTIVE.value, 10, uuid='a2'),
    ]
    deleted, svc = _patch(monkeypatch, subs)

    await dedup._run_dedupe()

    assert deleted == []
    svc.delete_remnawave_user.assert_not_called()
