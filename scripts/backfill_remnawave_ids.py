#!/usr/bin/env python
"""One-shot CLI for the Remnawave 3.0.0 identity backfill.

Migration 0104 only ADDS the ``remnawave_id`` columns; it cannot fill them,
because resolving a panel account requires talking to the panel.  This script is
that step, and on an existing install it must run before the bot is trusted:
until it does, every pre-upgrade row has ``remnawave_id IS NULL`` and the grace
reader treats a NULL identity as a hard data fault.

Usage:
    python -m scripts.backfill_remnawave_ids            # dry run, writes nothing
    python -m scripts.backfill_remnawave_ids --apply    # persist

A dry run is the default on purpose: read the report, confirm ``unresolved`` and
``conflicts`` are what you expect, and only then re-run with --apply.  The apply
pass refuses to commit if any two bot rows claim the same panel account.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.database.database import AsyncSessionLocal
from app.services.remnawave_identity_backfill import backfill_remnawave_ids


def _print_report(report) -> None:
    data = report.as_dict()
    print()
    print('=' * 62)
    print('  DRY RUN — ничего не записано' if data['dry_run'] else '  APPLIED')
    print('=' * 62)
    print(f'  пользователей в панели      : {data["panel_users"]}')
    print(f'  подписок к резолву          : {data["subscriptions_total"]}')
    print(f'  подписок связано            : {data["subscriptions_resolved"]}')
    print(f'  пользователей связано       : {data["users_resolved"]}')
    print(f'  grace-сессий связано        : {data["grace_sessions_resolved"]}')
    print()
    if data['by_strategy']:
        print('  по стратегии сопоставления:')
        for strategy, count in sorted(data['by_strategy'].items(), key=lambda kv: -kv[1]):
            print(f'    {strategy:<32} {count}')
        print()
    if report.conflicts:
        print(f'  !! КОНФЛИКТЫ: {len(report.conflicts)} — коммита не будет, пока не разберёте')
        for line in report.conflicts[:20]:
            print(f'     {line}')
        if len(report.conflicts) > 20:
            print(f'     ...и ещё {len(report.conflicts) - 20}')
        print()
    if report.unresolved:
        print(f'  не разрешено: {len(report.unresolved)}')
        by_reason: dict[str, int] = {}
        for row in report.unresolved:
            by_reason[row.reason] = by_reason.get(row.reason, 0) + 1
        for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f'    {reason:<40} {count}')
        print()
        print('  первые 20 строк:')
        for row in report.unresolved[:20]:
            print(f'    {row.kind} #{row.row_id}: {row.reason} (uuid={row.remnawave_uuid})')
        print()
    print(f'  ИТОГ: {"полный" if report.complete else "ЧАСТИЧНЫЙ — см. выше"}')
    print('=' * 62)


async def _run(apply: bool) -> int:
    async with AsyncSessionLocal() as db:
        report = await backfill_remnawave_ids(db, dry_run=not apply)
    _print_report(report)
    if report.conflicts:
        return 2
    return 0 if report.complete else 1


def main() -> int:
    parser = argparse.ArgumentParser(description='Backfill numeric Remnawave panel ids (3.0.0 migration)')
    parser.add_argument('--apply', action='store_true', help='persist changes (default is a dry run)')
    args = parser.parse_args()
    return asyncio.run(_run(args.apply))


if __name__ == '__main__':
    sys.exit(main())
