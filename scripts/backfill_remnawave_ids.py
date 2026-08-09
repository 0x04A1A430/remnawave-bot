#!/usr/bin/env python
"""One-shot CLI for the numeric Remnawave identity backfill.

Runs the same background service that the bot starts on startup
(``app.services.remnawave_id_backfill_service``) explicitly, so an operator can
re-run it after a deployment without waiting for the next restart or talking to
the running process.

Usage:
    python -m scripts.backfill_remnawave_ids

The service resolves the numeric panel id for every ``User``/``Subscription``
row that still has a ``remnawave_uuid`` but no ``remnawave_id``.  It is a no-op
once the panel is in v3 mode (``REMNAWAVE_USE_USER_ID``) and every row is
already linked.  Never raises — per-row failures are logged and skipped.
"""

import asyncio
import sys

import structlog

from app.config import settings
from app.services.remnawave_id_backfill_service import backfill_remnawave_ids
from app.services.system_settings_service import bot_configuration_service


logger = structlog.get_logger(__name__)


async def _run() -> int:
    await bot_configuration_service.initialize()
    if settings.REMNAWAVE_USE_USER_ID:
        print('Режим v3: панель уже отдаёт числовые id; backfill не требуется.')
        return 0
    summary = await backfill_remnawave_ids()
    print('backfill panel ids завершён')
    for key, value in summary.items():
        print(f'  {key:<10} {value}')
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == '__main__':
    sys.exit(main())
