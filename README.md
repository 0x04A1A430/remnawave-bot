# 🚀 Remnawave Bedolaga Bot

<div align="center">

![Logo](./assets/logo2.svg)

**🤖 Современный Telegram-бот для управления VPN подписками через Remnawave API**

*Полнофункциональное решение с управлением пользователями, платежами и администрированием*

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue?logo=postgresql&logoColor=white)](https://postgresql.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/Fr1ngg/remnawave-bedolaga-telegram-bot?style=social)](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/stargazers)

[🚀 Быстрый старт](#-быстрый-старт) • [📖 Функционал](#-функционал) • [🐳 Docker](#-docker-развертывание) • [💬 Поддержка](#-поддержка)

</div>

---

## 🧪 ([Тестирование бота](https://t.me/FringVPN_bot))

## 💬 **[Bedolaga Chat](https://t.me/+wTdMtSWq8YdmZmVi)** - Для общения, вопросов, предложений

## 🌟 Почему Bedolagа?
Бот Бедолага не добрый и не милый.
Он просто делает вашу работу вместо вас, принимает оплату, выдаёт подписки, интегрируется с Remnawave и тихо ненавидит всех, кто ещё не подключил его.

Вы хотите продавать VPN — Бедолага позволит это делать.
Вы хотите спать — он позволит и это.

### ⚡ **Полная автоматизация VPN бизнеса**
- 🎯 **Готовое решение** - разверни за 5 минут, начни продавать сегодня
- 💰 **Многоканальные платежи** - Telegram Stars + Tribute + ЮKassa
- 🔄 **Автоматизация 99%** - от регистрации до продления подписок
- 📊 **Детальная аналитика**
  
### 🎛️ **Гибкость конфигурации**
- 🌍 **Выбор стран** - пользователи сами выбирают нужные локации
- 📱 **Управление устройствами** - от 1 до 10 шт
- 📊 **Гибкие тарифы** - от 5GB до безлимита, от 14 дней до года
- 🎁 **Промо-система** - коды на деньги, дни подписки, триал-периоды
- 3 режима показа ссылки подписки: 1) С гайдом по подключению прямо в боте(тянущий данные приложений и ссылок на скачку из app-config.json) 2) Обычное открытие ссылки подписки в миниапе 3) Интеграция сабпейджа maposia - кастомно прописать ссылку можно
- Возможность переключаться между пакетной продажей трафика и фиксированной(Пропуская шаг выбора пакета трафика при оформлении/настройки подписки юзера)
- Возможность задать доступные дни для покупки первой подписки и при продлении

### 💪 **Enterprise готовность**
- 🏗️ **Современная архитектура** - AsyncIO, PostgreSQL, Redis
- 🔒 **Безопасность** - шифрование, валидация, rate limiting
- 📈 **Масштабируемость** 
- 🔧 **Мониторинг** - Prometheus, Grafana, health checks
- 🔧 **Режим технических работ** - Ручное включение + Мониторинг системы, который в случае падении панели Remnawave переведет бота в режим технических работ и обратно - отключит его, если панель поднимется.

---

## 🚀 Быстрый старт

### 🐳 Docker запуск

```bash
# 1. Скачай репозиторий
git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot

# 2. Настрой конфиг
cp .env.example .env
nano .env  # Заполни токены и настройки

# 3. Создай необходимые директории
mkdir -p logs data

# 4. Запусти всё разом
docker compose up -d --build

# 5. Проверь статус
docker compose logs -f bot
```

### ⚙️ Минимальная настройка (2 минуты)

| Настройка | Где взять | Пример |
|-----------|-----------|---------|
| 🤖 **BOT_TOKEN** | [@BotFather](https://t.me/BotFather) | `1234567890:AABBCCdd...` |
| 🔑 **REMNAWAVE_API_KEY** | Твоя Remnawave панель | `eyJhbGciOiJIUzI1...` |
| 🌐 **REMNAWAVE_API_URL** | URL твоей панели | `https://panel.example.com` |
| 👑 **ADMIN_IDS** | Твой Telegram ID | `123456789,987654321` |

<details>
<summary>🔧 Полная конфигурация .env</summary>

```env
# ===== ОБЯЗАТЕЛЬНЫЕ НАСТРОЙКИ =====

# Токен вашего бота (получить у @BotFather)
BOT_TOKEN=

# Ваш Telegram ID (узнать у @userinfobot)
ADMIN_IDS=

# URL панели Remnawave (например: https://your-panel.com)
REMNAWAVE_API_URL=

# API ключ из панели Remnawave
REMNAWAVE_API_KEY=

# Пароль для базы данных (придумайте сложный)
POSTGRES_PASSWORD=

# ===== ПЛАТЕЖНЫЕ СИСТЕМЫ =====

# Telegram Stars (работает автоматически)
TELEGRAM_STARS_ENABLED=true

# Tribute (https://tribute.app)
TRIBUTE_ENABLED=false
TRIBUTE_API_KEY=
TRIBUTE_DONATE_LINK=
TRIBUTE_WEBHOOK_SECRET=your_webhook_secret
TRIBUTE_WEBHOOK_PORT=8081

# YooKassa (https://yookassa.ru)
YOOKASSA_ENABLED=false
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_DEFAULT_RECEIPT_EMAIL=receipts@yourdomain.com
YOOKASSA_WEBHOOK_PORT=8082

# ===== НАСТРОЙКИ ТРАФИКА =====
# Режим выбора трафика:
# "selectable" - пользователи выбирают пакеты трафика (по умолчанию)
# "fixed" - фиксированный лимит трафика для всех подписок
TRAFFIC_SELECTION_MODE=selectable

# Фиксированный лимит трафика в ГБ (используется только в режиме "fixed")
# 0 = безлимит
FIXED_TRAFFIC_LIMIT_GB=0

# ===== ЦЕНЫ (в копейках) =====
PRICE_14_DAYS=5000
PRICE_30_DAYS=9900  
PRICE_60_DAYS=18900
PRICE_90_DAYS=26900
PRICE_180_DAYS=49900
PRICE_360_DAYS=89900

PRICE_TRAFFIC_5GB=2000
PRICE_TRAFFIC_10GB=4000
PRICE_TRAFFIC_25GB=6000
PRICE_TRAFFIC_50GB=10000
PRICE_TRAFFIC_100GB=15000
PRICE_TRAFFIC_250GB=20000
PRICE_TRAFFIC_UNLIMITED=25000

PRICE_PER_DEVICE=5000

# ===== ТРИАЛ ПОДПИСКА =====
TRIAL_DURATION_DAYS=3
TRIAL_TRAFFIC_LIMIT_GB=10
TRIAL_DEVICE_LIMIT=2
TRIAL_SQUAD_UUID=

# ===== РЕФЕРАЛЬНАЯ СИСТЕМА =====
REFERRAL_REGISTRATION_REWARD=5000
REFERRED_USER_REWARD=10000
REFERRAL_COMMISSION_PERCENT=25

# ===== ДОПОЛНИТЕЛЬНЫЕ НАСТРОЙКИ =====
# Доступные периоды подписки (через запятую)
AVAILABLE_SUBSCRIPTION_PERIODS=14,30,60,90,180,360
AVAILABLE_RENEWAL_PERIODS=30,90,180

# Режим работы кнопки "Подключиться"
# guide - открывает гайд подключения (режим 1)
# miniapp_subscription - открывает ссылку подписки в мини-приложении (режим 2)
# miniapp_custom - открывает заданную ссылку в мини-приложении (режим 3)
CONNECT_BUTTON_MODE=miniapp_subscription

# Автопродление - за сколько дней предупреждать
AUTOPAY_WARNING_DAYS=3,1

# Мониторинг
MONITORING_INTERVAL=60
TRIAL_WARNING_HOURS=2
ENABLE_NOTIFICATIONS=true

# База данных
POSTGRES_DB=remnawave_bot
POSTGRES_USER=remnawave_user

# Логи
LOG_LEVEL=INFO
DEBUG=false

# Поддержка
SUPPORT_USERNAME=

# Домен для webhook'ов (только если используете Tribute/YooKassa)
WEBHOOK_DOMAIN=your-domain.com
```

</details>

---

## 🐳 Docker развертывание

### 📄 docker-compose.yml

```yaml
version: '3.8'

services:
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-remnawave_bot}
      POSTGRES_USER: ${POSTGRES_USER:-remnawave_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-secure_password_123}
      POSTGRES_INITDB_ARGS: "--encoding=UTF8"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-remnawave_user} -d ${POSTGRES_DB:-remnawave_bot}"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 30s

  redis:
    image: redis:7-alpine
    container_name: remnawave_bot_redis
    restart: unless-stopped
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 10s
      retries: 3

  bot:
    build: .
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-remnawave_user}:${POSTGRES_PASSWORD:-secure_password_123}@postgres:5432/${POSTGRES_DB:-remnawave_bot}
      REDIS_URL: redis://redis:6379/0
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "${TRIBUTE_WEBHOOK_PORT:-8081}:8081"
      - "${YOOKASSA_WEBHOOK_PORT:-8082}:8082"
    networks:
      - bot_network
    healthcheck:
      test: ["CMD", "wget", "--no-verbose", "--tries=1", "--spider", "http://localhost:8081/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

volumes:
  postgres_data:
  redis_data:

networks:
  bot_network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
```

### 🔧 Настройка webhook'ов (для Tribute/YooKassa)

#### Через Nginx

```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location /tribute-webhook {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /yookassa-webhook {
        proxy_pass http://127.0.0.1:8082;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /health {
        proxy_pass http://127.0.0.1:8081/health;
    }
}
```

#### Через Caddy

```caddyfile
your-domain.com {
    handle /tribute-webhook* {
        reverse_proxy localhost:8081
    }
    
    handle /yookassa-webhook* {
        reverse_proxy localhost:8082
    }
    
    handle /health {
        reverse_proxy localhost:8081/health
    }
}
```

### 🚀 Команды управления

```bash
# Быстрый старт
docker compose up -d --build

# Статус сервисов
docker compose ps

# Логи
docker compose logs -f bot

# Перезапуск
docker compose restart bot

# Остановка
docker compose down

# Полная очистка
docker compose down -v --remove-orphans
```

---

## ⭐ Функционал

<table>
<tr>
<td width="50%" valign="top">

### 👤 **Для пользователей**

🛒 **Умная покупка подписок**
- 📅 Выбор периода (14-360 дней)
- 📊 Настройка трафика (5GB - безлимит)
- 🌍 Выбор стран через сквады
- 📱 Количество устройств (1-10)

💰 **Удобные платежи**
- ⭐ Telegram Stars 
- 💳 Tribute (автопополнение)
- 💳 YooKassa
- 🎁 Реферальные бонусы

📱 **Управление подписками**
- 📈 Просмотр статистики использования
- 🔄 Автопродление с баланса
- 🔄 Сброс/увеличение трафика
- 🌍 Смена стран на лету

🎁 **Бонусная система**
- 🎫 Промокоды на деньги/дни
- 👥 Реферальная программа 
- 🆓 Бесплатный триал
- 🔔 Ежедневные уведомления

</td>
<td width="50%" valign="top">

### ⚙️ **Для администраторов**

📊 **Мощная аналитика**
- 👥 Детальная статистика пользователей
- 💰 Анализ подписок и платежей
- 🖥️ Мониторинг серверов Remnawave
- 📈 Финансовые отчеты

👥 **Управление пользователями**
- 🔍 Поиск и редактирование профилей
- 💰 Управление балансами
- 🚫 Блокировка/разблокировка
- 📋 Массовые операции

🎫 **Промо-система**
- 🎁 Создание промокодов (деньги/дни)
- 📊 Статистика использования
- 🔄 Массовая генерация
- ⚙️ Гибкие условия активации

🖥️ **Мониторинг системы**
- 💚 Состояние Remnawave панели
- 🔄 Синхронизация данных
- 🌐 Управление сквадами
- 📋 Логи и диагностика

📨 **Коммуникации**
- 📢 Рассылки по сегментам
- 🔔 Автоуведомления о продлении
- 💬 Система поддержки
- 📝 Настройка правил сервиса

</td>
</tr>
</table>

---

## 🔧 Первичная настройка в боте

После запуска необходимо:

1. **📡 Синхронизация серверов** (обязательно!)
   - Зайди в бот → **Админ панель** → **Подписки** → **Управление серверами**
   - Нажми **Синхронизация** и дождись завершения
   - Без этого пользователи не смогут выбирать страны!

2. **👥 Синхронизация пользователей** (если есть база)
   - **Админ панель** → **Remnawave** → **Синхронизация**
   - **Синхронизировать всех** → дождись импорта

### 💳 Настройка платежных систем

#### Telegram Stars
Работает автоматически после указания `BOT_TOKEN`.

#### Tribute
1. Зарегистрируйся на https://tribute.app
2. Создай донат-ссылку
3. Получи API ключ
4. Настрой webhook в Tribute: `https://your-domain.com/tribute-webhook`

#### YooKassa
1. Зарегистрируйся в ЮKassa
2. Получи Shop ID и Secret Key
3. Настрой webhook в YooKassa: `https://your-domain.com/yookassa-webhook`

---

## 💡 Использование

### 👤 **Для пользователей**

1. **🚀 Старт** → Найди бота и нажми `/start`
2. **📋 Правила** → Прими правила сервиса 
3. **💰 Баланс** → "💰 Баланс" → пополни через Stars/Tribute
4. **🛒 Подписка** → "🛒 Купить подписку" → выбор тарифа → оплата
5. **📱 Управление** → "📋 Мои подписки" → конфигурация → получение ссылки
6. **👥 Рефералы** → "👥 Рефералы" → поделись ссылкой

### ⚙️ **Для администраторов**

Доступ через **"⚙️ Админ панель"**:

- **📦 Подписки** → настройка серверов, цен, синхронизация
- **👥 Пользователи** → поиск, редактирование, блокировка
- **🎁 Промокоды** → создание бонусов, статистика
- **📨 Рассылки** → уведомления по сегментам
- **🖥 Remnawave** → мониторинг панели, синхронизация
- **📊 Статистика** → детальная аналитика бизнеса

---

## 🚀 Производительность

| Пользователей | Память | CPU | Диск | Описание |
|---------------|--------|-----|------|----------|
| **1,000** | 512MB | 1 vCPU | 10GB | ✅ Стартап |
| **10,000** | 2GB | 2 vCPU | 50GB | ✅ Малый бизнес |
| **50,000** | 4GB | 4 vCPU | 100GB | ✅ Средний бизнес |
| **100,000+** | 8GB+ | 8+ vCPU | 200GB+ | 🚀 Enterprise |

---

## 🐛 Устранение неполадок

### Health Checks
- Основной: `http://localhost:8081/health`
- YooKassa: `http://localhost:8082/health`

### Полезные команды
```bash
# Просмотр логов
docker compose logs -f bot

# Статус контейнеров
docker compose ps

# Перезапуск бота
docker compose restart bot

# Проверка базы данных
docker compose exec postgres pg_isready -U remnawave_user
```

### Частые проблемы

| Проблема | Решение |
|----------|---------|
| Бот не отвечает | Проверь `BOT_TOKEN` и интернет |
| Ошибки БД | Проверь статус PostgreSQL контейнера |
| Webhook не работает | Проверь настройки прокси-сервера |
| API Remnawave недоступен | Проверь `REMNAWAVE_API_URL` и ключ |

---

## 🏗️ Архитектура

### 💪 Современный стек технологий

- **🐍 Python 3.11+** с AsyncIO - максимальная производительность
- **🗄️ PostgreSQL 15+** - надежное хранение данных
- **⚡ Redis** - быстрое кеширование и сессии
- **🐳 Docker** - простое развертывание в любой среде
- **🔗 SQLAlchemy ORM** - безопасная работа с БД
- **🚀 aiogram 3** - современная Telegram Bot API

### 📁 Структура проекта

```
bedolaga_bot/
├── 🎯 main.py                     # Точка входа
├── 📦 requirements.txt            # Зависимости
├── ⚙️ .env.example               # Конфиг
├── ⚙️ app-config.json            # Информация для гайда в боте по подключению
│
├── 📱 app/
│   ├── 🤖 bot.py                 # Инициализация бота
│   ├── ⚙️ config.py              # Настройки
│   ├── 🎛️ states.py              # FSM состояния
│   │
│   ├── 🎮 handlers/              # Обработчики событий
│   │   ├── 🏠 start.py           # Регистрация и старт
│   │   ├── 🛒 subscription.py    # Подписки
│   │   ├── 💰 balance.py         # Баланс и платежи
│   │   ├── 🎁 promocode.py       # Промокоды
│   │   ├── 👥 referral.py        # Реферальная система
│   │   ├── 💬 support.py         # Техподдержка
│   │   └── 👑 admin/             # Админ панель
│   │       ├── 📊 statistics.py  # Статистика
│   │       ├── 👥 users.py       # Управление юзерами
│   │       ├── 🎫 promocodes.py  # Управление промокодами
│   │       ├── 📨 messages.py    # Рассылки
│   │       ├── 🔍 monitoring.py  # Мониторинг
│   │       └── 🔗 remnawave.py   # Система RemnaWave
│   │
│   ├── 🗄️ database/             # База данных
│   │   ├── 📊 models.py          # Модели SQLAlchemy
│   │   ├── 🔗 database.py        # Подключение к БД
│   │   └── 📝 crud/              # CRUD операции
│   │
│   ├── 🔧 services/             # Бизнес-логика
│   │   ├── 👤 user_service.py             # Сервис пользователей
│   │   ├── 📋 subscription_service.py     # Сервис подписок
│   │   ├── 💰 payment_service.py          # Платежи
│   │   ├── 🎁 promocode_service.py        # Промокоды
│   │   ├── 👥 referral_service.py         # Рефералы
│   │   ├── 🔍 monitoring_service.py       # Мониторинг
│   │   └── 🌐 remnawave_service.py       # Интеграция с Remnawave
│   │
│   ├── 🛠️ utils/                # Утилиты
│   ├── 🛡️ middlewares/           # Middleware
│   ├── 🌐 localization/          # Локализация
│   └── 🔌 external/              # Внешние API
│
├── 🔄 migrations/                # Миграции БД
└── 📋 logs/                      # Логи системы
```

---

## 🤝 Как помочь проекту

- 🔍 [Сообщай о багах](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues) с подробным описанием
- 💡 [Предлагай идеи](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/discussions) для улучшения
- ⭐ **Ставь звезды** проекту - это мотивирует!
- 📢 **Рассказывай друзьям** о проекте
- 💝 **[Поддержи разработку](https://t.me/tribute/app?startapp=duUO)** - помоги проекту расти

---

## 💬 Поддержка и сообщество

### 📞 **Контакты**

- **💬 Telegram:** [@fringg](https://t.me/fringg) - вопросы по разработке (только по делу!)
- **💬 Telegram Group:** [Bedolaga Chat](https://t.me/+wTdMtSWq8YdmZmVi) - Для общения, вопросов, предложений, багов
- **🐛 Issues:** [GitHub Issues](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues) - баги и предложения

### 📚 **Полезные ресурсы**

- **📖 [Remnawave Docs](https://docs.remna.st)** - документация панели
- **🤖 [Telegram Bot API](https://core.telegram.org/bots/api)** - API ботов
- **🐳 [Docker Guide](https://docs.docker.com/get-started/)** - обучение Docker

---

## 💝 Благодарности

### 🌟 **Топ спонсоры проекта**

<table align="center">
<tr>
<th>🥇 Место</th>
<th>👤 Спонсор</th>
<th>💰 Сумма</th>
<th>💬 От себя благодарю</th>
</tr>
<tr>
<td>🥇</td>
<td><strong>Илья (@ispanec_nn)</strong></td>
<td>$30</td>
<td>За веру в проект с самого начала</td>
</tr>
<tr>
<td>🥈</td>
<td><strong>@pilot_737800</strong></td>
<td>₽2,250</td>
<td>За активное тестирование и фидбек</td>
</tr>
<tr>
<td>🥉</td>
<td><strong>@Legacyyy777</strong></td>
<td>₽1,000</td>
<td>За ценные предложения по улучшению</td>
</tr>
</table>

### 🌟 **Особая благодарность**

- **Remnawave Team** - за отличную панель и API

---

<div align="center">

## 📄 Лицензия

Проект распространяется под лицензией **MIT**

---
