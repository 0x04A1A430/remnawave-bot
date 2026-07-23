# Рекуррентные СБП-платежи Platega — дизайн

**Дата:** 2026-07-22
**Репозитории:** `remnawave-bedolaga-telegram-bot` (бэкенд + бот), `bedolaga-cabinet` (веб-кабинет)
**Статус:** утверждён к реализации

## 1. Цель

Дать пользователю автопродление VPN-подписки через рекуррентные СБП-платежи Platega — по аналогии с сохранёнными картами ЮKassa. Пользователь один раз привязывает СБП, дальше Platega списывает по каденсу тарифа и подписка продлевается автоматически. Отдельный движок продления, независимый от balance-autopay.

## 2. Жёсткие ограничения Platega (данность API, не наш выбор)

Подтверждено по докам и схеме Platega:

- **Единственный рекуррентный механизм — подписки** (`/transaction/process` с `paymentMethod: 6`). Отдельного «списать сохранённый токен по нашей команде» (pull/rebill) у Platega **нет**.
- **Расписание владеет Platega, не мы.** Мы не инициируем списания — Platega списывает сама и присылает callback.
- **Интервал только грубый:** `interval` = `1` день / `2` неделя / `3` месяц / `4` год, count всегда 1. Нативных «раз в 90 дней», «раз в 14 дней» нет.
- **Сумма фиксируется при создании** подписки и Platega списывает её каждый интервал.
- **Подтверждение привязки — в банке.** Создание возвращает `redirect`; у пользователя ~30 минут подтвердить привязку СБП/НСПК, иначе подписка → Failed.
- **HMAC callback'ов не документирован** — только заголовки `X-MerchantId` / `X-Secret`.

Следствие: «списать в конце произвольного периода» невозможно. Мы выбираем ближайший интервал Platega под период тарифа. Для суточного/30/360 совпадает точно; для неровных периодов каденс и сумма приклеиваются к месяцу.

## 3. Доменная модель и маппинг каденса

СБП-привязка Platega = «сохранённый метод автопродления» для конкретной подписки пользователя. Интервал Platega выводится из числа дней тарифа **детерминированной** функцией `resolve_platega_interval(period_days, is_daily) -> (interval, charge_days)`:

| Условие | Platega `interval` | `charge_days` (цена и продление) |
|---|---|---|
| `is_daily` | `1` (day) | 1 |
| `period_days == 7` | `2` (week) | 7 |
| `28 ≤ period_days ≤ 31` | `3` (month) | `period_days` |
| `350 ≤ period_days ≤ 380` | `4` (year) | `period_days` |
| **иначе (14 / 60 / 90 / 180 / …)** | `3` (month) | **30** |

Порядок проверки — сверху вниз (первое совпадение). `charge_days` задаёт и сумму, и шаг продления: `amount = tariff.get_purchasable_price_for_period(charge_days)`, продление `+charge_days`. Для точных тарифов (суточный / ~месяц / ~год) `charge_days` = период тарифа, всё совпадает. Для неровных периодов `charge_days = 30`: списание и продление ежемесячные по цене за 30 дней (иначе Platega спишет 90-дневную цену ежемесячно = переплата ×3). В UI для таких случаев явно пишем «ежемесячно». Диапазоны (28–31, 350–380) покрывают 365-дневные и «календарные» тарифы, чтобы годовой тариф не падал в месячный фолбэк.

Привязка — к **последнему купленному тарифу и его периоду**. При смене тарифа/периода или изменении цены: **отменяем старую Platega-подписку и создаём новую** (интервал/сумма пересчитываются). Требование: у тарифа должна существовать цена за канонический интервал (для month-фолбэка — цена за 30 дней). Если её нет — СБП-автопродление для тарифа недоступно.

## 4. Архитектура и поток данных

```
Включение (бот или кабинет)
  └─ POST /transaction/process {paymentMethod:6, paymentDetails:{amount,currency,interval}, description}
       └─ ← {transactionId (= subscription id), redirect, status:PENDING}
            └─ сохраняем PlategaSubscription(status=PENDING), отдаём redirect пользователю
Пользователь подтверждает привязку в банке (СБП/НСПК)
  └─ callback SUBSCRIPTION_ACTIVATED → PlategaSubscription.status=ACTIVE
Platega списывает по расписанию
  └─ charge callback CONFIRMED → extend_subscription(+charge_days), next_charge_at=NextChargeAt, аудит-Transaction
  └─ charge callback CANCELED   → уведомление, status=PAST_DUE (подписка догорает штатно)
Отмена (пользователь у нас / в банке по e-mail Platega)
  └─ POST /subscription/{id}/cancel  ИЛИ  callback SUBSCRIPTION_CANCELLED → status=CANCELLED, привязка снята
Reconciler (_monitoring_cycle)
  └─ GET /subscription (list) — доводит рассинхрон при потерянных коллбеках
```

**Ключевое отличие от YooKassa-рекуррента:** YooKassa — pull (наш `recurrent_payment_service.py` сам списывает токен, пополняет баланс, Layer A продлевает). Platega — push (Platega списывает, наш callback продлевает **напрямую, мимо баланса**). Это разные движки; `recurrent_payment_service.py` не трогаем.

**Взаимоисключение:** на одной подписке одновременно активен только один движок продления. Включение СБП-автопродления Platega выключает `Subscription.autopay_enabled` (balance-autopay / YooKassa-рекуррент) для этой подписки, и наоборот. Иначе — двойное продление.

## 5. Данные (бэкенд)

Новая таблица `platega_subscriptions` (Alembic-миграция, `Base.metadata` + ревизия):

| Колонка | Тип | Назначение |
|---|---|---|
| `id` | PK | |
| `user_id` | FK users, CASCADE | владелец |
| `subscription_id` | FK subscriptions, CASCADE | продлеваемая подписка |
| `tariff_id` | FK tariffs, nullable | привязанный тариф (для пересоздания при смене цены) |
| `platega_subscription_id` | str, unique, nullable | `transactionId` от Platega (id подписки) |
| `interval` | int | 1/2/3/4 (day/week/month/year) |
| `charge_days` | int | шаг продления за одно списание = `charge_days` из §3 |
| `amount_kopeks` | int | сумма списания |
| `currency` | str | 'RUB' |
| `status` | str | PENDING / ACTIVE / PAST_DUE / CANCELLED / FAILED |
| `redirect_url` | str, nullable | ссылка подтверждения привязки |
| `next_charge_at` | datetime(tz), nullable | из коллбеков/`get` |
| `last_charge_at` | datetime(tz), nullable | |
| `charges_success` / `charges_failed` | int, default 0 | счётчики (аудит/reconciler) |
| `created_at` / `updated_at` | datetime(tz) | |

Индекс `ix_platega_subscriptions_user_active (user_id, status)`. Уникальность `platega_subscription_id`.

`SavedPaymentMethod` **не переиспользуем** (прибит к `yookassa_payment_method_id`, семантика pull-токена). В кабинетном списке «сохранённые методы» обе сущности показываются вместе.

Аудит списаний — в существующий `Transaction` (`payment_method=PaymentMethod.PLATEGA`, тип — платёж за продление, **баланс не трогаем**). Enum `PaymentMethod.PLATEGA` уже есть.

## 6. Компоненты бэкенда

### `app/services/platega_service.py` (расширение)
Новые методы (та же `_request`, base_url, заголовки):
- `create_subscription(*, amount, currency, interval, description) -> dict` → `POST /transaction/process` (`paymentMethod: 6`). Возвращает `transactionId`, `redirect`, `status`.
- `get_subscription(subscription_id) -> dict` → `GET /subscription/{id}`.
- `list_subscriptions(*, status=None, from=None, to=None, page=None, size=None) -> dict` → `GET /subscription`.
- `cancel_subscription(subscription_id) -> dict` → `POST /subscription/{id}/cancel` (идемпотентно, без тела).
- Формат суммы: `round(kopeks/100, 2)`; если дробной части нет — целое (примеры Platega целочисленные). Валидация/лог при дробной.

### `app/services/payment/platega.py` (расширение mixin)
- `create_platega_sbp_subscription(self, db, *, user_id, subscription_id, tariff, period_days)` — вычисляет interval/amount по §3, вызывает `create_subscription`, пишет `PlategaSubscription(PENDING)`, выключает balance-autopay на подписке, возвращает `redirect` + локальную запись.
- `process_platega_subscription_callback(self, db, payload)` — единый вход для обоих коллбеков; ветвление по `Status`:
  - `SUBSCRIPTION_ACTIVATED` → ACTIVE.
  - `CONFIRMED` (charge) → идемпотентность по (`platega_subscription_id`, charge `Id`), `extend_subscription(charge_days)`, `next_charge_at`, `charges_success++`, аудит-Transaction, уведомление.
  - `CANCELED` (charge fail) → PAST_DUE, `charges_failed++`, уведомление.
  - `SUBSCRIPTION_PAST_DUE` → PAST_DUE + уведомление.
  - `SUBSCRIPTION_CANCELLED` → CANCELLED, привязка снята, уведомление.
  - `SUBSCRIPTION_FAILED` → FAILED + уведомление.
- `cancel_platega_sbp_subscription(self, db, *, local_id)` — `cancel_subscription` + status=CANCELLED (идемпотентно).
- Row-lock на `PlategaSubscription` в денежных ветках (по образцу `get_platega_payment_by_id_for_update`).

### CRUD `app/database/crud/platega_subscription.py` (новый)
`create`, `get_by_id` / `_for_update`, `get_by_platega_id`, `get_active_by_subscription`, `get_active_by_user`, `update`, `list_active_ids`.

### Webhook `app/webserver/payments.py` (расширение блока Platega ~700)
Тот же путь `/platega-webhook`, та же header-auth (`compare_digest` merchant+secret). После парсинга тела: если `PaymentMethod == 6` **или** есть `SubscriptionId` **или** `Status` начинается на `SUBSCRIPTION_` → `process_platega_subscription_callback`; иначе — существующий `process_platega_webhook` (разовые). Одна URL для регистрации в ЛК Platega. Ответ 200.

### Конфиг `app/config.py`
- `PLATEGA_RECURRENT_ENABLED: bool = False` — гейт фичи (плюс требует `is_platega_enabled()`).
- Хелпер `is_platega_recurrent_enabled()`.
- Маппинг интервала — чистая тестируемая функция `resolve_platega_interval(period_days, is_daily) -> (interval, charge_days)` (см. §3).
- `.env.example` — новый блок.

### Reconciler `app/services/monitoring_service.py`
В `_monitoring_cycle` (рядом с автоплатежами), под гейтом `PLATEGA_RECURRENT_ENABLED`: пройтись по локальным PENDING/ACTIVE, при подозрении на рассинхрон дёрнуть `get_subscription`/`list_subscriptions` и довести статус (по образцу cispay-ретраев и grace-reconciler). Ловит потерянные коллбеки и «завис в PENDING > N минут → FAILED».

## 7. Бот UI

`app/handlers/subscription/autopay.py` (рядом с существующим меню автоплатежа):
- Пункт «⚡ Автопродление через СБП» на управлении подпиской (виден при `is_platega_recurrent_enabled()`).
- Включить → создать подписку → отдать кнопку-ссылку «Подтвердить привязку в банке» (`redirect`). Статус PENDING до `SUBSCRIPTION_ACTIVATED`.
- Показ статуса (ACTIVE/PENDING/PAST_DUE) и **кнопка «Отменить автооплату»** → `cancel_platega_sbp_subscription` (`POST /subscription/{id}/cancel` в Platega + status=CANCELLED локально). Идемпотентно.
- Уведомления по коллбекам (`CONFIRMED` — «продлено», `CANCELED`/`PAST_DUE` — «списание не прошло»). Тексты в `app/localization`.
- Включение гасит balance-autopay-тоггл (и наоборот) — общий guard.

## 8. Кабинет (`bedolaga-cabinet`)

- **API** `src/api/subscription.ts` (через `bodyWithSubId`/`withSubId`): `enableSbpRecurring`, `getSbpRecurring`, `cancelSbpRecurring`. Экспорт в `src/api/index.ts`. Бэкенд-эндпоинты — в `app/cabinet/routes/subscription_modules/` (новый модуль рядом с `autopay.py`).
- **Типы** `src/types/index.ts` рядом с `SavedCard`: `SbpRecurring { status, interval, amount_kopeks, next_charge_at, redirect_url? }`.
- **UI (пользователь)**:
  - Тоггл «Автопродление через СБП» на `Subscription.tsx` рядом с autopay-блоком (:1034), **взаимоисключение** с autopay в UI + на бэке. Pending-состояние с кнопкой «Подтвердить в банке» → `openPaymentUrl(redirect)` (важно для СБП-хендофа в Telegram WebView).
  - Явная **кнопка «Отменить автооплату»** (когда статус ACTIVE/PENDING) → `cancelSbpRecurring`. СБП-привязка также выводится в списке `SavedCards.tsx` (гейт `recurrent_enabled`) с «Отвязать» → тот же `cancelSbpRecurring`.
- **i18n**: строки во все 4 локали (`ru/en/fa/zh`) под `subscription.*`, `balance.savedCards.*`, `admin.users.*`; `locales.test.ts` держит паритет.
- **WS**: события `sbp_recurring.confirmed` / `.failed` / `.activated` в `WebSocketNotifications.tsx` рядом с `autopay.*`.

### Админка кабинета (раздел юзеров)
- **Бэкенд** (`app/cabinet/routes/admin_users.py`): `_build_subscription_info` / `UserSubscriptionInfo` (~:227/:254) дополняется полями `sbp_recurring_status` и `sbp_recurring_id`, чтобы `get_user_detail` (:698) отдавал статус автооплаты по каждой подписке. Новый эндпоинт **`POST /{user_id}/subscriptions/{sub_id}/cancel-sbp-recurring`** → `cancel_platega_sbp_subscription` (проверка прав админа, идемпотентно).
- **Фронтенд**: в `src/components/admin/userDetail/SubscriptionTab.tsx` — по каждой подписке юзера показать **статус автооплаты** (если включена) и **кнопку «Отменить автооплату»** → новый admin-эндпоинт. В списке `src/pages/AdminUsers.tsx` — индикатор у юзера, если автооплата включена хотя бы на одной подписке. API — в админском клиенте (`src/api/admin*`), инвалидация `['admin-user-detail', userId]`.

## 9. Отмена автосписания при удалении/отзыве подписки

Если подписка удаляется или отзывается, Platega **обязана** перестать списывать — иначе юзер платит за несуществующую подписку. Общий best-effort хелпер `cancel_platega_recurring_for_subscription(db, subscription_id)` (находит активную `PlategaSubscription` по `subscription_id`, вызывает `cancel_platega_sbp_subscription`) вызывается из **каждой** точки удаления/отзыва:

- **Бот, админ:** `app/handlers/admin/users.py` `confirm_subscription_deletion` (~:6443, лог «Админ удалил подписку пользователя» :3475).
- **Кабинет, юзер:** `app/cabinet/routes/subscription_modules/multi_tariff.py:108` `delete_subscription`.
- **Кабинет, админ:** `app/cabinet/routes/admin_users.py:2794` `reset-subscription` (и прочие admin-пути удаления/сброса подписки).
- **Сервис:** `subscription_service.py:866` `revoke_subscription` (общий низкоуровневый отзыв).

Best-effort: провал отмены в Platega **не блокирует** удаление подписки (логируем, reconciler добьёт через `list_subscriptions`). Идемпотентно (повторная отмена уже отменённой — no-op).

## 10. Спецификация коллбеков

Оба коллбека — `POST` на `/platega-webhook`, тело PascalCase:
```json
{ "Id": "<charge/event id>", "Amount": 100, "Currency": "RUB",
  "Status": "<enum>", "PaymentMethod": 6, "Payload": "",
  "SubscriptionId": "<platega subscription id>", "NextChargeAt": "2026-08-09T09:10:00Z" }
```
- По списанию: `Status` ∈ `CONFIRMED` (успех) / `CANCELED` (провал, `NextChargeAt=null`).
- По статусу: `Status` ∈ `SUBSCRIPTION_ACTIVATED` / `SUBSCRIPTION_PAST_DUE` / `SUBSCRIPTION_CANCELLED` / `SUBSCRIPTION_FAILED`.
- Идемпотентность: charge — по (`SubscriptionId`, `Id`); статус — по (`SubscriptionId`, `Status`, переход). Ответ всегда `200`.

## 11. Обработка сбоев и краевые случаи

- **PENDING завис** (юзер не подтвердил привязку за ~30 мин) → reconciler/`get` → FAILED, тоггл сбрасывается.
- **Списание не прошло** (`CANCELED`/`PAST_DUE`) → уведомление; подписка догорает штатно (balance-autopay недоступен — был эксклюзивным). Следующая попытка — на усмотрение Platega; при финальном провале → `SUBSCRIPTION_FAILED`.
- **Отмена в банке** (self-cancel по e-mail Platega) → `SUBSCRIPTION_CANCELLED` → снимаем привязку.
- **Смена тарифа/периода/цены** → cancel + recreate.
- **Потерянные коллбеки** → reconciler через `list_subscriptions`.
- **Дробная сумма** → отправляем 2 знака; при отказе Platega — округление до рубля + лог.
- **Дрейф month vs 30 дней / year vs 360** → продлеваем на `charge_days`, календарный дрейф Platega несущественен для VPN.

## 12. Тестирование

- **Чистые юниты:** `resolve_platega_interval` (все периоды, is_daily, неровные → month), расчёт суммы, формат рублей (целое/дробное).
- **Callback-обработчик:** каждая ветка `Status`, идемпотентность (повторный `CONFIRMED` не продлевает дважды), продление на верное число дней, аудит-Transaction, баланс не тронут.
- **Взаимоисключение:** включение СБП гасит autopay и наоборот.
- **Webhook-роутинг:** подписочное тело → subscription handler, разовое → существующий; header-auth 401 без секрета.
- **Пересоздание** при смене цены (cancel old + create new).
- **Отмена при удалении подписки:** `cancel_platega_recurring_for_subscription` вызывается из каждой точки удаления/отзыва (бот-админ, кабинет-юзер, кабинет-админ, `revoke_subscription`); провал Platega-отмены не блокирует удаление; повторная отмена — no-op.
- **Админ-отмена:** admin-эндпоинт отменяет автооплату юзера; `UserSubscriptionInfo` отдаёт статус; права проверяются.
- **Кабинет:** юнит на утилиту резолва каденса/лейбла; type-check; biome; паритет локалей.
- Полный прогон бэкенда (`pytest`) и `ruff check`/`format` зелёные; кабинет — `npm test`/`type-check`/`biome`.

## 13. Вне scope v1

- Нативное списание раз в 90/180 дней (Platega не умеет — приклеено к month).
- Перенос уже активного balance-autopay на СБП.
- Ретро-миграция существующих подписок.
- Списание сохранённого метода по нашей инициативе (Platega не предоставляет).

## 14. Открытые вопросы

Нет — все решения приняты (модель = привязка к тарифу; каденс из дней тарифа; неровные → month@30д; взаимоисключение с balance-autopay; отдельный push-движок продления).
