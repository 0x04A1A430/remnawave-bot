"""Пачка проверок: чистые правила — нарезка целей по лимиту API, оценка времени, статус и цена из задач.

Одна кнопка «Проверить серверы» = ⌈N/10⌉ задач probe (API берёт не больше 10 целей за запрос),
которые идут не более трёх одновременно (лимит API на пробы с несколькими целями).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any

from app.database.crud.reachability import TERMINAL_STATUSES
from app.services.reachability.requests import MAX_PROBE_TARGETS


BATCH_PARALLEL = 3
# Полный флот в 15 симок проходит одну пробу за 10–15 минут: база плюс доля на каждую симку.
MINUTES_PER_ROUND_BASE = 3.0
MINUTES_PER_UNIT = 0.8

STATUS_DONE = 'done'
STATUS_FAILED = 'failed'
STATUS_CANCELLED = 'cancelled'


def chunk_targets[T](items: Sequence[T], size: int = MAX_PROBE_TARGETS) -> list[list[T]]:
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def estimate_batch_minutes(chunks: int, units: int, parallel: int = BATCH_PARALLEL) -> int:
    """Примерное время всей пачки: раунды по ``parallel`` чашек, раунд длится по числу симок."""
    rounds = max(1, math.ceil(chunks / parallel))
    return max(1, rounds * round(MINUTES_PER_ROUND_BASE + MINUTES_PER_UNIT * max(1, units)))


def batch_status_from_jobs(jobs: Iterable[Any], *, cancelling: bool) -> str | None:
    """None — пачка ещё идёт; иначе итог: отменена, не удалась целиком или завершена."""
    statuses = [job.status for job in jobs]
    if any(status not in TERMINAL_STATUSES for status in statuses):
        return None
    if cancelling or STATUS_CANCELLED in statuses:
        return STATUS_CANCELLED
    if statuses and all(status == STATUS_FAILED for status in statuses):
        return STATUS_FAILED
    return STATUS_DONE


def batch_cost_kopeks(jobs: Iterable[Any]) -> int | None:
    costs = [job.cost_kopeks for job in jobs if job.cost_kopeks is not None]
    return sum(costs) if costs else None


def batch_done_targets(jobs: Iterable[Any]) -> int:
    return sum(len(job.targets or []) for job in jobs if job.status in TERMINAL_STATUSES)
