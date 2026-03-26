# MULTI_TARIFF_ENABLED — Code Review Report

> **Дата**: 2026-03-26
> **Версия**: после фиксов, до функционального тестирования
> **Scope**: 64 файла с `multi_tariff` логикой, ~650k токенов анализа

---

## Архитектурная справка

**ТЗ** предполагало один Remnawave-юзер на бот-юзера с агрегированными параметрами.
**Реализация** пошла другим путём: один Remnawave-юзер **на подписку** (`_create_or_update_remnawave_user_multi`). Каждая подписка получает свой `remnawave_uuid`, `subscription_url`, `subscription_crypto_link`.

Это валидный альтернативный подход. ТЗ (`bedolaga-multi-tariff-tz.md`) нуждается в обновлении.

---

## Этап 1: Code Review — найденные и исправленные проблемы

### CRITICAL (7 проблем, все исправлены)

| # | Проблема | Файл | Строки | Фикс |
|---|----------|------|--------|------|
| C1 | **NameError в PromoCodeService** — `subscription_id` не передавался в `_apply_promocode_effects()`. При мульти-подписках + промокод `SUBSCRIPTION_DAYS` = crash в runtime | `promocode_service.py` | 86, 194, 279 | Добавлен параметр `subscription_id`, проброс из `activate_promocode()` |
| C2 | **TypeError в PromoCodeService** — `_apply_promocode_effects()` возвращал `dict` вместо `str`, ломал `result_description +=` конкатенацию | `promocode_service.py` | 282, 287-295 | Dict returns заменены на `raise ValueError` / `raise _SelectSubscriptionRequired` |
| C3 | **Savepoint без commit в PromoCodeService** — `promo_use` удалялся через `begin_nested` без `commit()`, при retry юзер получал `already_used_by_user` навсегда | `promocode_service.py` | 100-102, 111-113 | Заменено на `delete` + `commit` |
| C4 | **Cabinet status/autopay/renewal — операции на случайной подписке** — при мульти-тариф без `subscription_id` fallback на `user.subscription` вместо `resolve_subscription()` | `status.py`, `autopay.py`, `renewal.py` | Множ. | Заменено на `resolve_subscription()` |
| C5 | **Cabinet devices — `MultipleResultsFound` crash** — 3 POST endpoint'а (`/devices`, `/devices/purchase`, `/devices/reduce`) использовали `scalar_one_or_none()` без фильтра по sub_id | `devices.py` | 66-74, 291-299, 1016-1024 | Resolve → lock by resolved.id + `FOR UPDATE` |
| C6 | **Webhook IDOR** — fallback search возвращал подписку другого юзера, downstream handlers мутировали чужие данные | `remnawave_webhook_service.py` | 409 | `return user, fallback1_sub` → `return user, None` |
| C7 | **monitoring_service.py — реальная клавиатура уведомлений hardcoded** — `get_subscription_expiring_keyboard()` в `inline.py` оказалась мёртвым кодом, настоящая клавиатура строится inline в monitoring_service | `monitoring_service.py` | 1412-1422 | Добавлен `f'se:{subscription.id}'` + dynamic button text |

### HIGH (6 проблем, все исправлены)

| # | Проблема | Файл | Строки | Фикс |
|---|----------|------|--------|------|
| H1 | **Race condition при покупке** — `submit_purchase()` не использовал `FOR UPDATE` на subscription row. Две конкурентные покупки могли создать дубликаты | `subscription_purchase_service.py` | 1022-1048 | `FOR UPDATE` + `populate_existing=True` на **обеих** ветках (existing sub и new sub) |
| H2 | **MiniApp не передаёт `subscription_id`** — 8 endpoint'ов вызывали `_ensure_paid_subscription(user)` без ID, в мульти-тариф выбиралась произвольная подписка | `miniapp.py` | 3666, 5086, 5177, 5576, 5597, 5831, 5986, 7050 | Добавлен `subscription_id=payload.subscription_id` |
| H3 | **Уведомление об истечении без `sub_id`** — кнопка "Продлить" использовала `subscription_extend` без идентификатора, юзер не мог продлить конкретную подписку | `inline.py` + `monitoring_service.py` | 1867 / 1412 | `se:{subscription_id}` в мульти-тариф, legacy в single |
| H4 | **Refund без recovery** — при неудаче `add_user_balance` после списания деньги терялись, только CRITICAL log | `tariff_purchase.py` + `models.py` | 1396-1449 | `TransactionType.FAILED_REFUND`, `_persist_failed_refund()` через отдельную сессию. Query: `SELECT * FROM transactions WHERE type='failed_refund' AND is_completed=false` |
| H5 | **Account merge без панельной синки** — после переноса подписок панель показывала старый `telegramId` | `account_merge_service.py` | 264-297 | `_sync_transferred_subscriptions_to_panel()` обновляет description через прямой API |
| H6 | **Webhook `scalar_one_or_none()` без `.limit(1)`** — при дубликатах `remnawave_uuid` мог crash `MultipleResultsFound` | `remnawave_webhook_service.py` | 396, 357 | Добавлен `.limit(1)` на оба fallback query |

### Дополнительные исправления (найдены при верификации)

| # | Проблема | Файл | Фикс |
|---|----------|------|------|
| V1 | Мёртвая ветка `else` в promo: `target_sub = None` | `promocode_service.py:320-323` | Заменена на `raise ValueError('no_subscription_for_days')` |
| V2 | `renewal.py` `subscription_id` без `Query()` аннотации | `renewal.py:42` | Добавлен `Query(None, description='...')` |
| V3 | Unused import `get_subscription_by_user_id` | `renewal.py:16` | Удалён |
| V4 | Unused import `get_subscription_by_id_for_user` | `status.py:22` | Удалён |
| V5 | Unused imports `settings`, `get_subscription_by_id_for_user` | `autopay.py:6,13` | Удалены |
| V6 | Missing `AsyncSession` TYPE_CHECKING import | `helpers.py:8` | Добавлен через `TYPE_CHECKING` guard |

---

## Что корректно реализовано (confirmed)

- **DB миграции** (0050, 0051): partial unique index, composite indexes, FK RESTRICT
- **selectinload миграция**: все 30+ мест `User.subscription` → `User.subscriptions` — ноль пропусков
- **IDOR защита в bot handlers**: `_resolve_and_store_sub()` + `get_subscription_by_id_for_user()`
- **PricingEngine**: чистый, per-subscription, без single-sub assumptions
- **SubscriptionRenewalService**: `FOR UPDATE`, subscription-centric
- **WheelService, BroadcastService**: корректно обрабатывают мульти-подписки
- **Cabinet modules** (traffic, devices, servers, tariff_switch): `resolve_subscription()`
- **FastAPI session safety**: `get_current_cabinet_user` и `get_cabinet_db` — одна сессия (dependency caching)

---

## Статистика фиксов

```
14 files changed, 294 insertions(+), 126 deletions(-)
```

| Категория | Файлов | Изменений |
|-----------|--------|-----------|
| Cabinet routes | 5 | status, autopay, renewal, devices, helpers |
| Services | 5 | promocode, webhook, purchase, merge, monitoring |
| Handlers | 1 | tariff_purchase |
| Models | 1 | TransactionType.FAILED_REFUND |
| Keyboards | 1 | inline.py |
| MiniApp API | 1 | miniapp.py |

---

## Этап 2: Функциональное тестирование и оставшиеся MEDIUM/LOW

### 2.1 Оставшиеся MEDIUM проблемы (не исправлены)

| # | Проблема | Файл(ы) | Риск | Рекомендация |
|---|----------|---------|------|--------------|
| M1 | **Нет лимита подписок** — юзер может купить неограниченное количество тарифов | Все точки создания | Abuse | Добавить `MAX_ACTIVE_SUBSCRIPTIONS` в config + проверку при покупке |
| M2 | **Нет валидации `traffic_reset_mode`** — несовместимые режимы сброса трафика не блокируются | `tariff_purchase.py` | Data integrity | Валидация при покупке: сравнить с existing subs |
| M3 | **Monitoring дедупликация ломается** — при 2+ подписках уведомление о 3-дневном пороге пропускается если другая подписка в 1-дневном | `monitoring_service.py:471-482` | Missed notifications | Переработать дедупликацию per-subscription |
| M4 | **Campaign бонус блокируется** — любая активная подписка блокирует бонус, даже для другого тарифа | `campaign_service.py:144-150` | Feature gap | Проверять по `tariff_id`, не по наличию подписки |
| M5 | **MiniApp нет endpoint для списка подписок** — `POST /subscription` возвращает одну | `miniapp.py` | UX gap | Добавить `GET /subscriptions` как в Cabinet |
| M6 | **Promo restoration не атомична с refund** — краш между commits = потеря промо-скидки | `tariff_purchase.py:1412-1416` | Data loss (low prob) | `commit=False` на `add_user_balance`, единый commit |
| M7 | **Auto-purchase cart clearing** — после первого автоплатежа очищается cart, ломает второй | `subscription_auto_purchase_service.py:543-544` | Multi-sub autopay | Per-subscription cart keys |
| M8 | **i18n hardcoded strings в monitoring_service** — кнопки на русском вместо `texts.t(...)` | `monitoring_service.py:1417-1426` | i18n | Использовать `texts.t(...)` |
| M9 | **`add_user_balance` silent failure** — возвращает `False` без exception, `_persist_failed_refund` не вызывается | `tariff_purchase.py:1445` | Silent money loss | Проверять return value |

### 2.2 Оставшиеся LOW проблемы

| # | Проблема | Файл |
|---|----------|------|
| L1 | `get_subscription_expiring_keyboard()` — мёртвый код (никем не вызывается) | `inline.py:1855` |
| L2 | `select_tariff` блокирует покупку вместо redirect на продление | `tariff_purchase.py:576-587` |
| L3 | `sync_users_from_panel` перезаписывает `traffic_limit_gb` из панели в мульти-режиме | `remnawave_service.py:1814-1825` |
| L4 | `current_uses` в promo response — stale (pre-increment значение) | `promocode_service.py:197` |
| L5 | Stale callbacks `subscription_settings`/`toggle_daily_pause` → `db_user.subscription` | `purchase.py:2841,2933` |
| L6 | Legacy single-tariff merge без panel sync | `account_merge_service.py:391-436` |

### 2.3 Чеклист функционального тестирования

#### Bot (Telegram)

| # | Сценарий | Ожидание | Проверить |
|---|----------|----------|-----------|
| B1 | Покупка первого тарифа | Подписка создана, Remnawave юзер создан | `subscription.remnawave_uuid` заполнен |
| B2 | Покупка второго тарифа (другого) | Вторая подписка создана, свой Remnawave юзер | 2 записи в subscriptions, 2 UUID |
| B3 | Попытка купить тот же тариф | Сообщение "Тариф уже активен, продлите" | Partial unique index отрабатывает |
| B4 | "Мои подписки" — список | Показывает обе подписки с кнопками управления | Кнопки `sm:`, `se:`, `sl:` |
| B5 | Продление конкретной подписки | Продлевается именно выбранная | `end_date` обновлён только у неё |
| B6 | Докупка трафика для конкретной подписки | Трафик добавлен именно к выбранной | `purchased_traffic_gb` |
| B7 | Докупка устройств для конкретной подписки | `device_limit` увеличен у выбранной | Remnawave user updated |
| B8 | Уведомление об истечении одной подписки | Кнопка "Продлить" ведёт к конкретной подписке | `se:{sub_id}` callback |
| B9 | Истечение одной подписки, вторая активна | Вторая продолжает работать | Status check |
| B10 | Промокод `SUBSCRIPTION_DAYS` при 2+ подписках | Предложение выбрать подписку | `select_subscription` response |
| B11 | Промокод `BALANCE` при 2+ подписках | Баланс пополнен без выбора подписки | Не зависит от подписок |
| B12 | Autopay при 2+ подписках | Каждая подписка с autopay продлевается отдельно | Проверить по каждой |
| B13 | Конкурентные покупки (2 запроса одновременно) | Только одна операция проходит | `FOR UPDATE` lock |

#### Cabinet (WebApp)

| # | Сценарий | Ожидание | Проверить |
|---|----------|----------|-----------|
| W1 | `GET /subscriptions` — список подписок | Возвращает все подписки юзера | JSON array |
| W2 | `GET /subscription/info?subscription_id=X` | Информация по конкретной подписке | Ownership validated |
| W3 | `GET /subscription/info` (без ID) | Возвращает подписку с max days_left | `resolve_subscription()` |
| W4 | `PATCH /subscription/autopay?subscription_id=X` | Autopay для конкретной подписки | Другие подписки не затронуты |
| W5 | `GET /subscription/renewal-options?subscription_id=X` | Цены для конкретной подписки | Tariff prices |
| W6 | `POST /subscription/renew` с `subscription_id` | Продление конкретной подписки | Balance deducted, period extended |
| W7 | `GET /subscription/connection-link?subscription_id=X` | Link от конкретной подписки | Каждая подписка — свой URL |
| W8 | `POST /devices/purchase?subscription_id=X` | Устройства для конкретной подписки | FOR UPDATE lock, ownership |
| W9 | `GET /subscription/app-config?subscription_id=X` | Deep links для конкретной подписки | Correct URL/crypto link |
| W10 | IDOR: `subscription_id` чужой подписки | HTTP 404 | `get_subscription_by_id_for_user` |

#### MiniApp API

| # | Сценарий | Ожидание | Проверить |
|---|----------|----------|-----------|
| A1 | `/subscription/autopay` с `subscriptionId` | Autopay для конкретной подписки | `_ensure_paid_subscription` + `_validate_subscription_id` |
| A2 | `/subscription/renewal/options` с `subscriptionId` | Цены для конкретной подписки | Tariff-aware |
| A3 | `/subscription/settings` с `subscriptionId` | Настройки конкретной подписки | Ownership |
| A4 | `/subscription/servers` с `subscriptionId` | Серверы конкретной подписки | Squad list |
| A5 | `/subscription/traffic` с `subscriptionId` | Трафик конкретной подписки | Traffic limit |
| A6 | `/subscription/devices` с `subscriptionId` | Устройства конкретной подписки | Device limit |
| A7 | `/subscription/traffic-topup` с `subscriptionId` | Докупка для конкретной подписки | Balance deducted |
| A8 | IDOR: `subscriptionId` чужой подписки | HTTP 403 `subscription_mismatch` | `_validate_subscription_id` |

#### Edge Cases

| # | Сценарий | Ожидание |
|---|----------|----------|
| E1 | Юзер с 0 подписок, все endpoint'ы | Graceful 404 / empty response |
| E2 | Юзер с 1 подпиской, без `subscription_id` | Работает как single-tariff (backward compat) |
| E3 | Account merge: оба юзера с подписками | Все подписки на primary, panel synced |
| E4 | Webhook с `remnawave_uuid` от перенесённой подписки | Subscription resolved, status updated |
| E5 | Webhook с unknown `remnawave_uuid` | `(user, None)`, no crash |
| E6 | `MULTI_TARIFF_ENABLED=false` | Всё работает как раньше, single subscription |
| E7 | Суточные подписки + мульти-тариф | Daily charge per-subscription |
| E8 | Failed refund persistence | Transaction с `type='failed_refund'` в БД |

---

## Файлы изменённые в этом ревью

```
app/cabinet/routes/subscription_modules/autopay.py
app/cabinet/routes/subscription_modules/devices.py
app/cabinet/routes/subscription_modules/helpers.py
app/cabinet/routes/subscription_modules/renewal.py
app/cabinet/routes/subscription_modules/status.py
app/database/models.py
app/handlers/subscription/tariff_purchase.py
app/keyboards/inline.py
app/services/account_merge_service.py
app/services/monitoring_service.py
app/services/promocode_service.py
app/services/remnawave_webhook_service.py
app/services/subscription_purchase_service.py
app/webapi/routes/miniapp.py
```
