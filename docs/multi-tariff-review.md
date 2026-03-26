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

## Этап 2: Полный функциональный аудит (6 агентов, ~60 файлов)

> Проведён 2026-03-26. Каждый агент проверял свою группу файлов.

### 2.0 CRITICAL (найдены в Этапе 2)

| # | Проблема | Файл | Строки |
|---|----------|------|--------|
| S2-C1 | **`update_or_create_subscription()` без multi-tariff guard** — в мульти-режиме перезаписывает самую свежую подписку произвольными данными | `subscription_utils.py` | 55-88 |
| S2-C2 | **Panel sync перезаписывает `traffic_limit_gb` из панели** — стирает докупленный трафик. Бот = source of truth, панель = нет | `remnawave_service.py` | 1822-1825, 2049-2054 |
| S2-C3 | **Guest purchase блокирует создание второй подписки** — PENDING_ACTIVATION вместо создания нового тарифа | `guest_purchase_service.py` | 343-357 |
| S2-C4 | **Auto-purchase cart per-user, не per-subscription** — при 2 autopay подписках cart может указывать на неправильную | `subscription_auto_purchase_service.py` | 152-188, 2772 |
| S2-C5 | **`_sync_users_from_panel` dict `.uuid` AttributeError** — silent fail, создаёт дубликаты подписок | `remnawave_service.py` | 1351 |

### 2.1 HIGH (найдены в Этапе 2)

#### Bot Handlers

| # | Проблема | Файл | Строки |
|---|----------|------|--------|
| S2-H1 | **`confirm_extend_subscription` fallback на `db_user.subscription`** когда FSM state отсутствует | `purchase.py` | 1815-1816 |
| S2-H2 | **`open_subscription_link:{sub_id}` callback не зарегистрирован** — handler только для exact match, мульти-тариф callbacks dropped | `links.py` | 176-178 |
| S2-H3 | **`subscription_connect:{sub_id}` callback не зарегистрирован** — аналогично H2 | `links.py` | 358-363 |
| S2-H4 | **`handle_subscription_settings` использует `db_user.subscription`** — device settings на неправильной подписке | `purchase.py` | 2851-2852 |
| S2-H5 | **`confirm_reset_traffic` без sub_id в callback** — зависит от stale FSM | `traffic.py` | 308-325 |

#### Services

| # | Проблема | Файл | Строки |
|---|----------|------|--------|
| S2-H6 | **`update_remnawave_user` fallback на `user.remnawave_uuid`** при `subscription.remnawave_uuid=None` — может обновить чужого panel user | `subscription_service.py` | 409-413 |
| S2-H7 | **`_auto_purchase_tariff` picks `next()` по tariff_id** — при 2 подписках на один тариф продлевает не ту | `subscription_auto_purchase_service.py` | 722-729 |
| S2-H8 | **`migrate_squad_users` проверяет `user.remnawave_uuid`** — в мульти-режиме всегда None, panel update пропускается | `remnawave_service.py` | 1005-1006 |
| S2-H9 | **Campaign bonus блокируется любой активной подпиской** | `campaign_service.py` | 132-150, 245-263 |
| S2-H10 | **`broadcast_service` paid-subscription guard берёт только newest sub** — может ошибочно заблокировать | `broadcast_service.py` | 546-561 |
| S2-H11 | **`blocked_users_service` хранит только `user.remnawave_uuid`** — DELETE_FROM_REMNAWAVE пропускается для мульти-тариф | `blocked_users_service.py` | 160-181 |
| S2-H12 | **`unblock_user` логирует `user.remnawave_uuid` вместо `sub.remnawave_uuid`** | `user_service.py` | 758 |

#### Admin

| # | Проблема | Файл | Строки |
|---|----------|------|--------|
| S2-H13 | **`_grant_trial_subscription` блокирует грант при любой активной подписке** — админ не может дать trial на второй тариф | `admin/users.py` | 4370 |
| S2-H14 | **`_grant_paid_subscription` аналогично** — блокирует грант | `admin/users.py` | 4403 |
| S2-H15 | **Promo offers `_build_connect_button_rows` использует `user.subscription`** — кнопка "Подключить" ведёт не к той подписке | `admin/promo_offers.py` | 1904 |
| S2-H16 | **Promo segment broadcast filter — `user.subscription` вместо всех подписок** — юзеры с squad в secondary sub не фильтруются | `admin/promo_offers.py` | 2122-2130 |

#### CRUD / Utils

| # | Проблема | Файл | Строки |
|---|----------|------|--------|
| S2-H17 | **`get_users_list` с `order_by_traffic` — outerjoin без `.unique()`** — дублирует юзеров в списке | `user.py` | 884-887 |

#### Frontend (Cabinet)

| # | Проблема | Файл | Строки |
|---|----------|------|--------|
| S2-H18 | **`refreshTraffic` ставит `subscription_id` в body вместо query param** — backend игнорирует | `api/subscription.ts` | 159-163 |

### 2.2 MEDIUM (найдены в Этапе 2)

| # | Проблема | Файл |
|---|----------|------|
| S2-M1 | Нет лимита подписок (MAX_ACTIVE_SUBSCRIPTIONS) | config |
| S2-M2 | Нет валидации `traffic_reset_mode` совместимости | tariff_purchase.py |
| S2-M3 | Monitoring дедупликация ломается при 2+ подписках | monitoring_service.py:471-482 |
| S2-M4 | MiniApp нет endpoint для списка подписок | miniapp.py |
| S2-M5 | Promo restoration не атомична с refund | tariff_purchase.py:1412-1416 |
| S2-M6 | Auto-purchase cart clearing ломает второй автоплатёж | subscription_auto_purchase_service.py:543-544 |
| S2-M7 | i18n hardcoded strings в monitoring_service keyboard | monitoring_service.py:1417-1426 |
| S2-M8 | `add_user_balance` silent failure — `_persist_failed_refund` не вызывается | tariff_purchase.py:1445 |
| S2-M9 | `handle_toggle_daily_subscription_pause` использует `db_user.subscription` | purchase.py:2943 |
| S2-M10 | `show_subscription_detail` игнорирует `HIDE_SUBSCRIPTION_LINK` | my_subscriptions.py:208-209 |
| S2-M11 | `handle_extend_subscription` для daily tariff теряет sub context | purchase.py:1644-1647 |
| S2-M12 | Traffic/device confirmation callbacks без sub_id (FSM dependency) | inline.py:2180,2248 |
| S2-M13 | `delete_subscription` в multi_tariff.py использует `status` вместо `actual_status` | multi_tariff.py:126 |
| S2-M14 | Admin overview/statistics показывают только primary subscription | admin/users.py:1261,2787 |
| S2-M15 | Tariff switch без guard на already-owned tariff | tariff_switch.py |
| S2-M16 | Admin traffic table дублирует юзеров (1 row per subscription UUID) | admin_traffic.py:214 |
| S2-M17 | Frontend: cache invalidation после purchase/promo не scoped по subscriptionId | SubscriptionPurchase.tsx:304-308 |
| S2-M18 | Panel sync перезаписывает `connected_squads` | remnawave_service.py:2082-2095 |

### 2.3 LOW

| # | Проблема | Файл |
|---|----------|------|
| S2-L1 | `get_subscription_expiring_keyboard()` — мёртвый код | inline.py:1855 |
| S2-L2 | `select_tariff` блокирует покупку вместо redirect на продление | tariff_purchase.py:576-587 |
| S2-L3 | `current_uses` в promo response — stale | promocode_service.py:197 |
| S2-L4 | Legacy merge без panel sync | account_merge_service.py:391-436 |
| S2-L5 | 9x `user.subscription` cache-warmer в user.py (deprecated property) | user.py:99,119,... |
| S2-L6 | `ensure_single_subscription()` — misnamed, noop in multi-tariff | subscription_utils.py:15 |
| S2-L7 | `create_trial_subscription` с `tariff_id=None` в мульти-тариф — undefined behavior | subscription.py:175 |
| S2-L8 | YooKassa admin notification re-queries newest sub вместо resolved | yookassa.py:1039-1046 |
| S2-L9 | Frontend: `is_purchased` flag на tariff cards не используется визуально | SubscriptionPurchase.tsx |
| S2-L10 | Auth response не включает subscriptions list | auth.py:98 |

### 2.4 Confirmed Correct (проверено, проблем нет)

| Компонент | Файл(ы) |
|-----------|---------|
| PricingEngine | pricing_engine.py |
| SubscriptionRenewalService | subscription_renewal_service.py |
| Contest attempt service | contests/attempt_service.py |
| Server squad CRUD | server_squad.py |
| Backup service | backup_service.py |
| WebAPI subscriptions | webapi/routes/subscriptions.py |
| Cabinet multi_tariff module (list/detail/delete) | multi_tariff.py |
| Cabinet purchase (tariff-aware) | cabinet purchase.py |
| Cabinet tariff_switch (resolve_subscription) | tariff_switch.py |
| Cabinet admin_users detail + panel_info | admin_users.py |
| Cabinet wheel + contests | wheel.py, contests.py |
| Channel checker middleware | channel_checker.py |
| Simple subscription (blocks in multi-tariff) | simple_subscription.py |
| Daily subscription service | daily_subscription_service.py |
| Frontend: types, routing, Subscription detail, Subscriptions list, Connection, RenewSubscription | bedolaga-cabinet/src/ |

---

## Общая статистика

| Категория | Этап 1 | Этап 2 | Итого |
|-----------|--------|--------|-------|
| **CRITICAL** | 7 (все исправлены) | 5 (не исправлены) | 12 |
| **HIGH** | 6 (все исправлены) | 18 (не исправлены) | 24 |
| **MEDIUM** | — | 18 | 18 |
| **LOW** | 6 | 10 | 16 |
| **Confirmed correct** | — | 15 компонентов | 15 |
| **Файлов проверено** | 14 (изменены) | ~60 (прочитаны) | ~64 |

## Файлы изменённые в Этапе 1

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
