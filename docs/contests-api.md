# Contests API (admin)

Админский REST API для конкурсов: ежедневные игры и реферальные конкурсы. Авторизация как в остальных методах — `X-API-Key` или Bearer.

## Ежедневные игры (`/contests/daily`)

- `GET /contests/daily/templates?enabled_only=false` — список шаблонов игр.
- `GET /contests/daily/templates/{id}` — получить шаблон.
- `PATCH /contests/daily/templates/{id}` — обновить поля: `name`, `description`, `prize_type`, `prize_value`, `max_winners`, `attempts_per_user`, `times_per_day`, `schedule_times`, `cooldown_hours`, `payload` (dict), `is_enabled`.
- `POST /contests/daily/templates/{id}/start-round` — запустить раунд вручную. Тело:
  ```json
  {
    "starts_at": "2025-12-15T09:00:00+03:00",
    "ends_at": "2025-12-15T13:00:00+03:00",
    "cooldown_hours": 4,
    "payload": {"secret_idx": 3},
    "force": true
  }
  ```
  Если `force=true`, активный раунд этого шаблона завершается перед созданием нового.
- `GET /contests/daily/rounds?status_filter=active|finished|any&template_id&limit&offset` — список раундов.
- `GET /contests/daily/rounds/{id}` — получить раунд.
- `POST /contests/daily/rounds/{id}/finish` — завершить раунд.
- `GET /contests/daily/rounds/{id}/attempts?winners_only=false&limit&offset` — попытки (с данными пользователя).

## Реферальные конкурсы (`/contests/referral`)

- `GET /contests/referral?contest_type&limit&offset` — список конкурсов.
- `POST /contests/referral` — создать конкурс:
  ```json
  {
    "title": "Рефералы декабрь",
    "contest_type": "referral_paid",
    "start_at": "2025-12-20T10:00:00+03:00",
    "end_at": "2025-12-27T10:00:00+03:00",
    "daily_summary_time": "12:00:00",
    "timezone": "Europe/Moscow",
    "prize_text": "🥇 5000 ₽, 🥈 3000 ₽",
    "is_active": true,
    "created_by": 1
  }
  ```
- `GET /contests/referral/{id}/detailed-stats` — детальная статистика конкурса с разбивкой по участникам (total_participants, total_invited, total_paid_amount, total_unpaid, participants).
- `PATCH /contests/referral/{id}` — частичное обновление (те же поля + `final_summary_sent`, `is_active`, `daily_summary_times` с несколькими временами через запятую).
- `POST /contests/referral/{id}/toggle?is_active=true|false` — быстро включить/остановить.
- `GET /contests/referral/{id}/events?limit&offset` — события (referrer/referral, тип, суммы).
- `DELETE /contests/referral/{id}` — удалить завершённый конкурс.

## Даты и часовые пояса

- Поля `datetime` можно передавать с TZ; сервер переводит в UTC (tzinfo убирается).
- Если TZ не указан, используется `settings.TIMEZONE`.

## Тег в OpenAPI

Все методы сгруппированы под тегом `contests` в Swagger/Redoc после перезапуска web-api.
