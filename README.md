<div align="center">

<img src="https://github.com/user-attachments/assets/17ad0128-231d-4553-9f4b-ce0644da796c" alt="Bedolaga Bot" width="200" />

# Bedolaga Bot

**Telegram-бот для автоматизации VPN-бизнеса на базе [Remnawave](https://github.com/remnawave/backend)**

Принимает оплату, выдаёт подписки, управляет пользователями — пока вы спите.

[![Python 3.13+](https://img.shields.io/badge/Python-3.13+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docs.bedolagam.ru/getting-started/docker-deployment)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)

[Документация](https://docs.bedolagam.ru) · [Тестировать бота](https://t.me/zero_ping_vpn_bot?start=Git) · [Чат сообщества](https://t.me/+wTdMtSWq8YdmZmVi)

</div>

---

## Что такое Bedolaga?

Bedolaga — полнофункциональная платформа для продажи VPN-подписок через Telegram. Бот интегрируется с панелью [Remnawave](https://github.com/remnawave/backend) и берёт на себя весь цикл: от регистрации пользователя до автопродления подписки.

> **[Bedolaga Cabinet](https://github.com/BEDOLAGA-DEV/bedolaga-cabinet)** — веб-кабинет, который существенно расширяет возможности бота: личный кабинет пользователя, OAuth-авторизация, лендинги, расширенная админ-панель с аналитикой продаж и управлением ролями.

---

## Возможности

<table>
<tr>
<td width="50%" valign="top">

### Подписки и тарифы

- Гибкие тарифные планы (от 14 дней до года)
- Трафик: безлимит, фиксированный лимит или пакеты
- Управление устройствами (1–20 на подписку)
- Автовыбор сервера или ручной выбор
- Пробный период с конвертацией в платный
- Умная корзина — сохраняет выбор при недостатке баланса
- Автопродление за 3 дня до окончания
- Подарочные подписки

</td>
<td width="50%" valign="top">

### Платежи

- **14 платёжных провайдеров** одновременно
- Единый баланс: пополнение любым способом → покупка с баланса
- Автопокупка подписки после пополнения
- Рекуррентные платежи (сохранённые карты)
- Фискализация через НалоGo (для самозанятых)
- Проверка статуса платежей
- Поддержка гостевых покупок через лендинги

</td>
</tr>
<tr>
<td width="50%" valign="top">

### Маркетинг и продвижение

- Промокоды (деньги, дни подписки, триалы)
- Реферальная программа с выводом средств
- Рассылки по сегментам пользователей
- Кастомные лендинги с аналитикой
- Конкурсы и ежедневные игры с призами
- Персональные предложения и скидки
- Маркетинговые кампании с трекингом

</td>
<td width="50%" valign="top">

### Администрирование

- Панель управления прямо в Telegram
- Управление пользователями, подписками, платежами
- Уведомления в топики: покупки, продления, пополнения
- Автобэкапы БД с восстановлением из бота
- Режим техработ (авто-детект недоступности панели)
- Мониторинг трафика и аномалий
- Партнёрская программа
- RBAC: роли и гранулярные права доступа

</td>
</tr>
</table>

---

## Платёжные провайдеры

<div align="center">

| Провайдер | Методы | | Провайдер | Методы |
|:---|:---|:---:|:---|:---|
| **Telegram Stars** | Звёзды Telegram | | **CloudPayments** | Карты, 3D-Secure |
| **YooKassa** | Карты, СБП | | **Freekassa** | NSPK СБП, карты |
| **CryptoBot** | USDT, TON, BTC, ETH | | **Kassa AI** | СБП, карты, SberPay |
| **PayPalych** | Карты, СБП | | **RioPay** | Карты |
| **Platega** | Карты, СБП, крипто | | **SeverPay** | СБП, карты |
| **WATA** | Карты | | **Tribute** | Telegram |
| **Heleket** | Криптовалюта | | **MulenPay** | Карты |

</div>

> Все провайдеры работают параллельно через единый веб-сервер. Подробная настройка каждого — в [документации](https://docs.bedolagam.ru/bot/payments).

---

## Быстрый старт

```bash
git clone https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot
cp .env.example .env   # заполните переменные
docker compose up -d
```

Подробнее: **[Развёртывание →](https://docs.bedolagam.ru/getting-started/docker-deployment)**

---

## Стек

| Компонент | Технология |
|:---|:---|
| Язык | Python 3.13, полностью async |
| Telegram | aiogram 3.x |
| База данных | PostgreSQL + SQLAlchemy 2.x + Alembic |
| Web-сервер | FastAPI (webhook, платежи, Cabinet API) |
| Логирование | structlog |
| Контейнеризация | Docker + Docker Compose |
| Линтер | ruff |

---

## Bedolaga Cabinet

<div align="center">

[![Cabinet](https://img.shields.io/badge/Репозиторий-Cabinet-6366f1?style=for-the-badge&logo=react&logoColor=white)](https://github.com/BEDOLAGA-DEV/bedolaga-cabinet)

</div>

Веб-кабинет на React + TypeScript, расширяющий бота:

- **Личный кабинет** — подписки, баланс, устройства, реферальная программа
- **OAuth-авторизация** — Google, Yandex, Discord, VK, Telegram OIDC
- **Лендинги** — кастомные страницы для привлечения клиентов
- **Админ-панель** — аналитика продаж, RBAC, управление контентом
- **Подарочные подписки** — покупка и отправка подписок другим пользователям

---

## Документация

| Раздел | Описание |
|:---|:---|
| [Быстрый старт](https://docs.bedolagam.ru/getting-started/quickstart) | Развёртывание за 5 минут |
| [Настройка платежей](https://docs.bedolagam.ru/bot/payments) | 14 провайдеров, webhook, фискализация |
| [Подписки и тарифы](https://docs.bedolagam.ru/bot/subscriptions) | Конфигурация планов и трафика |
| [Реферальная программа](https://docs.bedolagam.ru/bot/referral-program) | Партнёрка и вывод средств |
| [Cabinet](https://docs.bedolagam.ru/cabinet/overview) | Настройка веб-кабинета |
| [Промо-система](https://docs.bedolagam.ru/bot/promo-system) | Промокоды, предложения, скидки |
| [API Reference](https://docs.bedolagam.ru/api-reference/overview) | REST API для внешних интеграций |

**Полная документация: [docs.bedolagam.ru](https://docs.bedolagam.ru)**

---

## Сообщество

[![Telegram Chat](https://img.shields.io/badge/Telegram-Чат_сообщества-26A5E4?style=flat-square&logo=telegram&logoColor=white)](https://t.me/+wTdMtSWq8YdmZmVi)
[![GitHub Issues](https://img.shields.io/badge/GitHub-Баг--репорты-181717?style=flat-square&logo=github&logoColor=white)](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/issues)

- **Баги и предложения** — [GitHub Issues](https://github.com/BEDOLAGA-DEV/remnawave-bedolaga-telegram-bot/issues)
- **Вопросы и обсуждения** — [Telegram-чат](https://t.me/+wTdMtSWq8YdmZmVi)
- **Тестирование** — [@zero_ping_vpn_bot](https://t.me/zero_ping_vpn_bot?start=Git)

---

## Лицензия

[MIT](LICENSE) — используйте свободно для личных и коммерческих проектов.
