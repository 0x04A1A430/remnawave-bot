"""Пачка проверок: CRUD, нарезка целей по 10, оценка времени, статус из задач, драйвер ≤3 параллельно, отмена."""

from __future__ import annotations

import pytest

from app.database.crud import reachability as crud


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------- CRUD


async def test_batch_crud_roundtrip(session_factory) -> None:
    async with session_factory() as db:
        batch = await crud.create_batch(
            db,
            status='pending',
            started_by_user_id=None,
            scope={'kind': 'problems', 'host_refs': ['h1', 'h2']},
            request={'units': ['mts|цфо|on'], 'dpi': 'on', 'probes': {'tcp': True, 'sni': True}, 'sni_hosts': []},
            total_targets=2,
            estimated_kopeks=1280,
        )
        await db.commit()

        loaded = await crud.get_batch(db, batch.id)
        assert loaded.scope['kind'] == 'problems' and loaded.total_targets == 2 and loaded.jobs == []
        assert (await crud.get_active_batch(db)).id == batch.id
        items, total = await crud.list_batches(db)
        assert total == 1 and items[0].id == batch.id
        assert [item.id for item in await crud.list_unfinished_batches(db)] == [batch.id]

        await crud.update_batch(db, loaded, status='done')
        await db.commit()
        assert await crud.get_active_batch(db) is None
        assert await crud.list_unfinished_batches(db) == []


async def test_jobs_for_batch_are_ordered_and_carry_legs(session_factory) -> None:
    from tests.services.reachability.test_jobs import EU, make_job

    async with session_factory() as db:
        batch = await crud.create_batch(
            db, status='pending', started_by_user_id=None, scope={'kind': 'manual', 'host_refs': []},
            request={}, total_targets=2, estimated_kopeks=None,
        )
        await db.commit()
        batch_id = batch.id
    ids = [await make_job(session_factory, 'probe', {'target': 'x'}, [EU], ['mts|пфо|on'], batch_id=batch_id) for _ in range(2)]
    async with session_factory() as db:
        jobs = await crud.jobs_for_batch(db, batch_id)
        assert [job.id for job in jobs] == ids and all(job.legs == [] for job in jobs)
        assert (await crud.get_batch(db, batch_id)).jobs[0].id == ids[0]
