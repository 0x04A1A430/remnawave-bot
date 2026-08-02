"""Matching rules for the Remnawave 3.0.0 identity backfill.

A wrong match here silently points a subscription at another person's VPN
account, so every rule that *refuses* to match matters as much as the ones that
succeed.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.remnawave_identity_backfill import (
    _match_subscription,
    _PanelIndex,
    backfill_remnawave_ids,
)


def _patch_roster(monkeypatch, panel_users):
    """Replace the panel sweep and the configuration check with local data."""
    import app.services.remnawave_identity_backfill as mod

    async def fake_roster(_service):
        return panel_users

    class _Svc:
        is_configured = True
        configuration_error = None

        def _refresh_configuration(self):
            return None

    monkeypatch.setattr(mod, '_load_panel_roster', fake_roster)
    monkeypatch.setattr(mod, 'RemnaWaveService', _Svc)


def panel_user(user_id, *, short_uuid=None, username=None, telegram_id=None, email=None):
    return SimpleNamespace(
        id=user_id,
        short_uuid=short_uuid,
        username=username,
        telegram_id=telegram_id,
        email=email,
    )


def subscription(sub_id=1, *, user_id=1, short_uuid=None, uuid='legacy-uuid', short_id=None):
    return SimpleNamespace(
        id=sub_id,
        user_id=user_id,
        remnawave_short_uuid=short_uuid,
        remnawave_uuid=uuid,
        remnawave_short_id=short_id,
    )


def bot_user(user_id=1, *, telegram_id=None, email=None, full_name='Test', username=None):
    return SimpleNamespace(
        id=user_id,
        telegram_id=telegram_id,
        email=email,
        full_name=full_name,
        username=username,
    )


def test_short_uuid_is_the_exact_key():
    index = _PanelIndex([panel_user(42, short_uuid='abc123', telegram_id=555)])
    matched, strategy = _match_subscription(subscription(short_uuid='abc123'), bot_user(telegram_id=555), index, {})
    assert strategy == 'short_uuid'
    assert matched.id == 42


def test_stored_short_uuid_unknown_to_panel_does_not_fall_back():
    """The panel user was deleted.

    Falling through to telegram_id would re-link the subscription to a
    *different* panel account of the same person, quietly handing them someone
    else's traffic counters and squads.
    """
    index = _PanelIndex([panel_user(7, short_uuid='other', telegram_id=555)])
    matched, strategy = _match_subscription(subscription(short_uuid='gone'), bot_user(telegram_id=555), index, {})
    assert matched is None
    assert strategy == 'short_uuid_not_found_in_panel'


def test_unique_telegram_id_resolves_when_no_short_uuid():
    index = _PanelIndex([panel_user(9, short_uuid='s9', telegram_id=555)])
    matched, strategy = _match_subscription(subscription(), bot_user(telegram_id=555), index, {})
    assert strategy == 'telegram_id_unique'
    assert matched.id == 9


def test_multi_tariff_ambiguity_is_refused_without_a_discriminator():
    index = _PanelIndex(
        [
            panel_user(1, short_uuid='s1', username='user_555_aaa', telegram_id=555),
            panel_user(2, short_uuid='s2', username='user_555_bbb', telegram_id=555),
        ]
    )
    matched, strategy = _match_subscription(subscription(), bot_user(telegram_id=555), index, {})
    assert matched is None
    assert strategy == 'ambiguous_telegram_id'


def test_multi_tariff_short_id_suffix_disambiguates():
    index = _PanelIndex(
        [
            panel_user(1, username='user_555_aaa', telegram_id=555),
            panel_user(2, username='user_555_bbb', telegram_id=555),
        ]
    )
    matched, strategy = _match_subscription(subscription(short_id='bbb'), bot_user(telegram_id=555), index, {})
    assert strategy == 'telegram_id_plus_short_id'
    assert matched.id == 2


def test_email_only_user_resolves_by_email():
    index = _PanelIndex([panel_user(11, email='A@Example.COM')])
    matched, strategy = _match_subscription(
        subscription(), bot_user(telegram_id=None, email='a@example.com'), index, {}
    )
    assert strategy == 'email_unique'
    assert matched.id == 11


def test_duplicate_email_is_refused():
    index = _PanelIndex([panel_user(11, email='a@e.com'), panel_user(12, email='a@e.com')])
    matched, strategy = _match_subscription(subscription(), bot_user(telegram_id=None, email='a@e.com'), index, {})
    assert matched is None
    assert strategy == 'ambiguous_email'


def test_no_surviving_identifier_is_reported_not_guessed():
    index = _PanelIndex([panel_user(1, short_uuid='s1', telegram_id=999)])
    matched, strategy = _match_subscription(subscription(), bot_user(telegram_id=None, email=None), index, {})
    assert matched is None
    assert strategy == 'no_surviving_identifier'


def test_already_claimed_panel_user_is_not_reused_via_username():
    """Two subscriptions must never converge on one panel account."""
    index = _PanelIndex([panel_user(5, username='user_555', telegram_id=None)])
    matched, _ = _match_subscription(subscription(), bot_user(telegram_id=None, email=None), index, claimed={5: '1'})
    assert matched is None


@pytest.mark.parametrize('telegram_id', [555, None])
def test_index_tolerates_missing_fields(telegram_id):
    """Panel rows legitimately have null telegramId/email; indexing must not crash."""
    index = _PanelIndex([panel_user(1, short_uuid=None, username=None, telegram_id=telegram_id, email=None)])
    assert index.by_short_uuid == {}
    assert index.by_username == {}


def test_exact_only_defers_rows_without_a_short_uuid():
    """Pass 1 must not consume weak matches.

    Otherwise a low-numbered dead row can claim, via telegram_id, the panel
    account that a later row identifies exactly by shortUuid — linking the dead
    subscription to a live panel user and leaving the live one NULL.
    """
    index = _PanelIndex([panel_user(77, short_uuid='abc123', telegram_id=555)])
    matched, strategy = _match_subscription(
        subscription(sub_id=10), bot_user(telegram_id=555), index, {}, exact_only=True
    )
    assert matched is None
    assert strategy == 'no_exact_key'

    # ...and the exact row still wins it in the same pass.
    matched, strategy = _match_subscription(
        subscription(sub_id=20, short_uuid='abc123'), bot_user(telegram_id=555), index, {}, exact_only=True
    )
    assert strategy == 'short_uuid'
    assert matched.id == 77


def test_exact_only_still_reports_a_dead_short_uuid():
    """A stored shortUuid the panel does not know is a verdict, not a deferral."""
    index = _PanelIndex([panel_user(7, short_uuid='other')])
    matched, strategy = _match_subscription(
        subscription(short_uuid='gone'), bot_user(telegram_id=555), index, {}, exact_only=True
    )
    assert matched is None
    assert strategy == 'short_uuid_not_found_in_panel'


# ── Integration: the two-pass loop, claim priming and conflict rollback ──────
#
# These exercise `backfill_remnawave_ids` against a real (in-memory) database,
# because the defects they guard against live in the CALLER, not in
# `_match_subscription`: pass ordering, priming `claimed` from already-persisted
# rows, and refusing to commit a run that produced conflicts.

from app.database.models import GraceAccessSessionModel, Subscription as SubModel, User as UserModel
from tests.fixtures.sqlite_memory import memory_session


async def _seed(session, *, subs, owners=None):
    """`subs` = [(sub_id, short_uuid, legacy_uuid)]; `owners` = {sub_id: user_id}."""
    owners = owners or {}
    for user_id in sorted({*owners.values(), 1}):
        session.add(UserModel(id=user_id, telegram_id=550 + user_id, remnawave_uuid=f'legacy-{user_id}'))
    for sub_id, short_uuid, uuid in subs:
        session.add(
            SubModel(
                id=sub_id,
                user_id=owners.get(sub_id, 1),
                status='active',
                end_date=datetime(2030, 1, 1, tzinfo=UTC),
                remnawave_short_uuid=short_uuid,
                remnawave_uuid=uuid,
                remnawave_short_id=f'sid{sub_id}',
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_exact_match_wins_over_a_lower_numbered_weak_match(monkeypatch):
    """The regression this was written for.

    Sub 10 lost its shortUuid (crud/subscription.py:776 clears it) and its panel
    account is gone; sub 20 is the live row. Processing in id order used to let
    sub 10 claim panel 77 by telegram_id, stranding the live row on NULL.
    """
    async with memory_session(
        monkeypatch, [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    ) as db:
        await _seed(db, subs=[(10, None, 'dead-uuid'), (20, 'abc123', 'live-uuid')])
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='abc123', username='u', telegram_id=555)])

        report = await backfill_remnawave_ids(db, dry_run=False)

        sub10 = await db.get(SubModel, 10)
        sub20 = await db.get(SubModel, 20)
        assert sub20.remnawave_id == 77, 'the exactly-identified row must win the panel account'
        assert sub10.remnawave_id is None, 'the dead row must be left unresolved, not linked'
        assert report.conflicts == []


@pytest.mark.asyncio
async def test_rerun_does_not_reassign_an_already_persisted_panel_id(monkeypatch):
    """`claimed` must be primed from rows a previous run already wrote."""
    async with memory_session(
        monkeypatch, [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    ) as db:
        await _seed(db, subs=[(10, None, 'dead-uuid'), (20, 'abc123', 'live-uuid')])
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='abc123', username='u', telegram_id=555)])
        await backfill_remnawave_ids(db, dry_run=False)  # pass 1 gives 77 to sub 20

        report = await backfill_remnawave_ids(db, dry_run=False)  # pass 2

        sub10 = await db.get(SubModel, 10)
        assert sub10.remnawave_id is None, 'a re-run must not hand out an owned panel id'
        assert report.conflicts == []


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(monkeypatch):
    async with memory_session(
        monkeypatch, [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    ) as db:
        await _seed(db, subs=[(20, 'abc123', 'live-uuid')])
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='abc123', username='u', telegram_id=555)])

        report = await backfill_remnawave_ids(db, dry_run=True)

        assert report.subscriptions_resolved == 1
        db.expunge_all()
        assert (await db.get(SubModel, 20)).remnawave_id is None


@pytest.mark.asyncio
async def test_conflict_between_different_users_rolls_back(monkeypatch):
    """Один панельный аккаунт, на который претендуют РАЗНЫЕ пользователи.

    Здесь минимум одна запись неверна и указала бы подписку на чужой VPN, —
    коммитить «кто первый успел» нельзя, откатываем весь прогон.
    """
    async with memory_session(
        monkeypatch, [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    ) as db:
        await _seed(db, subs=[(10, 'dup', 'uuid-a'), (20, 'dup', 'uuid-b')], owners={10: 1, 20: 2})
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='dup', username='u', telegram_id=555)])

        report = await backfill_remnawave_ids(db, dry_run=False)

        assert report.conflicts, 'разные пользователи на один аккаунт — это конфликт'
        db.expunge_all()
        assert (await db.get(SubModel, 10)).remnawave_id is None
        assert (await db.get(SubModel, 20)).remnawave_id is None


@pytest.mark.asyncio
async def test_sibling_rows_of_one_user_are_not_a_conflict(monkeypatch):
    """Штатное состояние single-tariff, а не ошибка.

    Истёк триал — вставляется НОВАЯ строка, старая сохраняет тот же
    `remnawave_short_uuid`, потому что панельный аккаунт у пользователя один.
    Считать это конфликтом означало откатывать весь бэкфил на любой
    инсталляции, которая когда-либо работала в single-tariff, то есть сделать
    обязательный шаг миграции невыполнимым.
    """
    async with memory_session(
        monkeypatch, [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    ) as db:
        await _seed(db, subs=[(10, 'dup', 'uuid-a'), (20, 'dup', 'uuid-b')])
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='dup', username='u', telegram_id=555)])

        report = await backfill_remnawave_ids(db, dry_run=False)

        assert report.conflicts == [], 'подписки одного пользователя — не конфликт'
        db.expunge_all()
        # Колонка частично уникальна, поэтому id получает ровно одна строка.
        ids = {(await db.get(SubModel, 10)).remnawave_id, (await db.get(SubModel, 20)).remnawave_id}
        assert ids == {77, None}
        # Идентичность пользователя сохранена — в single-tariff она каноническая.
        assert (await db.get(UserModel, 1)).remnawave_id == 77


@pytest.mark.asyncio
async def test_multi_tariff_never_fills_the_user_level_column(monkeypatch):
    """В multi-tariff `users.remnawave_id` обязан остаться пустым.

    Идентичность там живёт на подписке, а колонка на User исторически пуста —
    именно поэтому около десятка фолбэков `sub.remnawave_id or
    user.remnawave_id` безопасно пропускали панель. Заполнив её, бэкфил оживил
    бы их, и подписка с неразрезолвленным id начала бы адресовать панельный
    аккаунт СОСЕДНЕГО тарифа.

    Гейта по `users.remnawave_uuid` мало: инсталляция, начинавшая в
    single-tariff и переключённая на multi, несёт эту колонку заполненной.
    """
    from app.config import Settings

    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: True)
    async with memory_session(
        monkeypatch, [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    ) as db:
        await _seed(db, subs=[(20, 'abc123', 'legacy-uuid')])
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='abc123', username='u', telegram_id=551)])

        await backfill_remnawave_ids(db, dry_run=False)

        db.expunge_all()
        assert (await db.get(SubModel, 20)).remnawave_id == 77, 'подписка обязана быть привязана'
        assert (await db.get(UserModel, 1)).remnawave_id is None, 'колонка на User должна остаться пустой'


@pytest.mark.asyncio
async def test_rerun_does_not_invent_a_conflict_for_a_sibling(monkeypatch):
    """Повторный прогон — штатный сценарий, а не исключение.

    Первый apply всегда завершается «частично» (у сиблинга нет id), и оператор
    по инструкции перезапускает. `claimed` приминговывался уже записанными id,
    а владелец — нет, поэтому сиблинг выглядел как претензия ДРУГОГО
    пользователя, объявлялся конфликтом и откатывал весь прогон.
    """
    tables = [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    async with memory_session(monkeypatch, tables) as db:
        await _seed(db, subs=[(10, 'dup', 'uuid-a'), (20, 'dup', 'uuid-b')])
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='dup', username='u', telegram_id=551)])

        first = await backfill_remnawave_ids(db, dry_run=False)
        assert first.conflicts == []

        second = await backfill_remnawave_ids(db, dry_run=False)

        assert second.conflicts == [], 'сиблинг уже записанной строки — не конфликт'
        db.expunge_all()
        ids = {(await db.get(SubModel, 10)).remnawave_id, (await db.get(SubModel, 20)).remnawave_id}
        assert ids == {77, None}, 'повторный прогон не должен ничего терять'


@pytest.mark.asyncio
async def test_live_subscription_wins_the_panel_id_not_the_expired_one(monkeypatch):
    """id достаётся живой строке, а не самой старой.

    По `order_by(id)` его получал конвертированный триал, и тогда grace-сессия
    текущей подписки оставалась без идентичности (для ридера грейса это жёсткий
    data fault), а вебхук резолвил события на мёртвую строку.
    """
    tables = [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    async with memory_session(monkeypatch, tables) as db:
        await _seed(db, subs=[(10, 'dup', 'uuid-a'), (20, 'dup', 'uuid-b')])
        # 10 — истёкший триал, 20 — текущая подписка.
        (await db.get(SubModel, 10)).status = 'expired'
        (await db.get(SubModel, 20)).status = 'active'
        await db.commit()
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='dup', username='u', telegram_id=551)])

        await backfill_remnawave_ids(db, dry_run=False)

        db.expunge_all()
        assert (await db.get(SubModel, 20)).remnawave_id == 77, 'живая подписка обязана получить id'
        assert (await db.get(SubModel, 10)).remnawave_id is None


@pytest.mark.asyncio
async def test_multi_tariff_treats_a_shared_panel_account_as_a_real_conflict(monkeypatch):
    """В multi-tariff у каждой подписки СВОЙ аккаунт — совпадение это порча данных.

    Проглотив его как «сиблинга», бэкфил молча привязал бы одну строку к чужому
    аккаунту и забрал бы у второй последний ключ.
    """
    from app.config import Settings

    monkeypatch.setattr(Settings, 'is_multi_tariff_enabled', lambda self: True)
    tables = [UserModel.__table__, SubModel.__table__, GraceAccessSessionModel.__table__]
    async with memory_session(monkeypatch, tables) as db:
        await _seed(db, subs=[(10, 'dup', 'uuid-a'), (20, 'dup', 'uuid-b')])
        _patch_roster(monkeypatch, [panel_user(77, short_uuid='dup', username='u', telegram_id=551)])

        report = await backfill_remnawave_ids(db, dry_run=False)

        assert report.conflicts, 'в multi-tariff общий аккаунт — конфликт, а не норма'
        db.expunge_all()
        assert (await db.get(SubModel, 10)).remnawave_id is None
        assert (await db.get(SubModel, 20)).remnawave_id is None
