import asyncio
import logging
import random
from datetime import datetime, timedelta, time, timezone
from typing import Dict, List, Optional

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud.contest import (
    create_round,
    get_active_round_by_template,
    list_templates,
    upsert_template,
)
from app.database.database import AsyncSessionLocal
from app.database.models import ContestTemplate

logger = logging.getLogger(__name__)

# Slugs for games
GAME_QUEST = "quest_buttons"
GAME_LOCKS = "lock_hack"
GAME_CIPHER = "letter_cipher"
GAME_SERVER = "server_lottery"
GAME_BLITZ = "blitz_reaction"
GAME_EMOJI = "emoji_guess"
GAME_ANAGRAM = "anagram"


DEFAULT_TEMPLATES = [
    {
        "slug": GAME_QUEST,
        "name": "Квест-кнопки",
        "description": "Найди секретную кнопку 3×3",
        "prize_days": 1,
        "max_winners": 3,
        "attempts_per_user": 1,
        "times_per_day": 2,
        "schedule_times": "10:00,18:00",
        "payload": {"rows": 3, "cols": 3},
    },
    {
        "slug": GAME_LOCKS,
        "name": "Кнопочный взлом",
        "description": "Найди взломанную кнопку среди 20 замков",
        "prize_days": 5,
        "max_winners": 1,
        "attempts_per_user": 1,
        "times_per_day": 2,
        "schedule_times": "09:00,19:00",
        "payload": {"buttons": 20},
    },
    {
        "slug": GAME_CIPHER,
        "name": "Шифр букв",
        "description": "Расшифруй слово по номерам",
        "prize_days": 1,
        "max_winners": 1,
        "attempts_per_user": 1,
        "times_per_day": 2,
        "schedule_times": "12:00,20:00",
        "payload": {"words": ["VPN", "SERVER", "PROXY", "XRAY"]},
    },
    {
        "slug": GAME_SERVER,
        "name": "Сервер-лотерея",
        "description": "Угадай доступный сервер",
        "prize_days": 7,
        "max_winners": 1,
        "attempts_per_user": 1,
        "times_per_day": 1,
        "schedule_times": "15:00",
        "payload": {"flags": ["🇸🇪","🇸🇬","🇺🇸","🇷🇺","🇩🇪","🇯🇵","🇧🇷","🇦🇺","🇨🇦","🇫🇷"]},
    },
    {
        "slug": GAME_BLITZ,
        "name": "Блиц-реакция",
        "description": "Нажми кнопку за 10 секунд",
        "prize_days": 1,
        "max_winners": 1,
        "attempts_per_user": 1,
        "times_per_day": 2,
        "schedule_times": "11:00,21:00",
        "payload": {"timeout_seconds": 10},
    },
    {
        "slug": GAME_EMOJI,
        "name": "Угадай сервис по эмодзи",
        "description": "Определи сервис по эмодзи",
        "prize_days": 1,
        "max_winners": 1,
        "attempts_per_user": 1,
        "times_per_day": 1,
        "schedule_times": "13:00",
        "payload": {"pairs": [{"question": "🔐📡🌐", "answer": "VPN"}]},
    },
    {
        "slug": GAME_ANAGRAM,
        "name": "Анаграмма дня",
        "description": "Собери слово из букв",
        "prize_days": 1,
        "max_winners": 1,
        "attempts_per_user": 1,
        "times_per_day": 1,
        "schedule_times": "17:00",
        "payload": {"words": ["SERVER", "XRAY", "VPN"]},
    },
]


class ContestRotationService:
    def __init__(self) -> None:
        self.bot: Optional[Bot] = None
        self._task: Optional[asyncio.Task] = None
        self._interval_seconds = 60

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def set_bot(self, bot: Bot) -> None:
        self.bot = bot

    async def start(self) -> None:
        await self.stop()

        if not settings.is_contests_enabled():
            logger.info("Сервис игр отключён настройками")
            return

        await self._ensure_default_templates()

        self._task = asyncio.create_task(self._loop())
        logger.info("🎲 Сервис ротационных конкурсов запущен")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _ensure_default_templates(self) -> None:
        async with AsyncSessionLocal() as db:
            for tpl in DEFAULT_TEMPLATES:
                try:
                    await upsert_template(db, **tpl)
                except Exception as exc:
                    logger.error("Не удалось создать шаблон %s: %s", tpl["slug"], exc)

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    await self._tick()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.error("Ошибка в ротации конкурсов: %s", exc)
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            logger.info("Сервис ротации конкурсов остановлен")
            raise

    def _parse_times(self, times_str: Optional[str]) -> List[time]:
        if not times_str:
            return []
        times: List[time] = []
        for part in times_str.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                hh, mm = part.split(":")
                times.append(time(int(hh), int(mm)))
            except Exception:
                continue
        return times

    async def _tick(self) -> None:
        async with AsyncSessionLocal() as db:
            templates = await list_templates(db)
            now_local = datetime.now().astimezone(timezone.utc)
            for tpl in templates:
                times = self._parse_times(tpl.schedule_times) or []
                for slot in times[: tpl.times_per_day]:
                    starts_at_local = now_local.replace(
                        hour=slot.hour, minute=slot.minute, second=0, microsecond=0
                    )
                    if starts_at_local > now_local:
                        starts_at_local -= timedelta(days=1)
                    ends_at_local = starts_at_local + timedelta(hours=tpl.cooldown_hours)
                    if not (starts_at_local <= now_local <= ends_at_local):
                        continue

                    exists = await get_active_round_by_template(db, tpl.id)
                    if exists:
                        continue
                    payload = self._build_payload_for_template(tpl)
                    round_obj = await create_round(
                        db,
                        template=tpl,
                        starts_at=starts_at_local.replace(tzinfo=None),
                        ends_at=ends_at_local.replace(tzinfo=None),
                        payload=payload,
                    )
                    logger.info("Создан раунд %s для шаблона %s", round_obj.id, tpl.slug)

    def _build_payload_for_template(self, tpl: ContestTemplate) -> Dict:
        payload = tpl.payload or {}
        if tpl.slug == GAME_QUEST:
            rows = payload.get("rows", 3)
            cols = payload.get("cols", 3)
            total = rows * cols
            secret_idx = random.randint(0, total - 1)
            return {"rows": rows, "cols": cols, "secret_idx": secret_idx}
        if tpl.slug == GAME_LOCKS:
            total = payload.get("buttons", 20)
            secret_idx = random.randint(0, max(0, total - 1))
            return {"total": total, "secret_idx": secret_idx}
        if tpl.slug == GAME_CIPHER:
            words = payload.get("words") or ["VPN"]
            word = random.choice(words)
            codes = [str(ord(ch.upper()) - 64) for ch in word if ch.isalpha()]
            return {"question": "-".join(codes), "answer": word.upper()}
        if tpl.slug == GAME_SERVER:
            flags = payload.get("flags") or ["🇸🇪","🇸🇬","🇺🇸","🇷🇺","🇩🇪","🇯🇵","🇧🇷","🇦🇺","🇨🇦","🇫🇷"]
            secret_idx = random.randint(0, len(flags) - 1)
            return {"flags": flags, "secret_idx": secret_idx}
        if tpl.slug == GAME_BLITZ:
            return {"timeout_seconds": payload.get("timeout_seconds", 10)}
        if tpl.slug == GAME_EMOJI:
            pairs = payload.get("pairs") or [{"question": "🔐📡🌐", "answer": "VPN"}]
            pair = random.choice(pairs)
            return pair
        if tpl.slug == GAME_ANAGRAM:
            words = payload.get("words") or ["SERVER"]
            word = random.choice(words).upper()
            shuffled = "".join(random.sample(word, len(word)))
            return {"letters": shuffled, "answer": word}
        return payload


contest_rotation_service = ContestRotationService()
