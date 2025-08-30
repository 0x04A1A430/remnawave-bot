# 🚀 Remnawave Bedolaga Bot

<div align="center">

![Logo](./assets/logo2.svg)

**🤖 Современный Telegram-бот для управления VPN подписками через Remnawave API**

*Полнофункциональное решение с управлением пользователями, платежами и администрированием*

[![Docker Image](https://img.shields.io/badge/Docker-fr1ngg/remnawave--bedolaga--telegram--bot-blue?logo=docker&logoColor=white)](https://hub.docker.com/r/fr1ngg/remnawave-bedolaga-telegram-bot)
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
mkdir -p volumes/{postgres,redis} logs data backups

# 4. Запусти всё разом
docker compose up -d

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
# TELEGRAM BOT CONFIGURATION
BOT_TOKEN=
ADMIN_IDS=
SUPPORT_USERNAME=

# DATABASE CONFIGURATION
DATABASE_URL=sqlite+aiosqlite:///./bot.db
REDIS_URL=redis://localhost:6379/0

# REMNAWAVE API CONFIGURATION
REMNAWAVE_API_URL=
REMNAWAVE_API_KEY=

# === NEW: Traffic Selection Mode Settings ===
# Режим выбора трафика:
# "selectable" - пользователи выбирают пакеты трафика (по умолчанию)
# "fixed" - фиксированный лимит трафика для всех подписок, доступно 5/10/25/50/100/250/0 (0 безлимит) гб 
TRAFFIC_SELECTION_MODE=selectable

# Фиксированный лимит трафика в ГБ (используется только в режиме "fixed")
# 0 = безлимит
# для "fixed" обязательно должы быть проставлены цены на пакеты 5/10/25/50/100/250/0 можно постать 0 руб - будет беслпатно
FIXED_TRAFFIC_LIMIT_GB=0

# TRIAL SUBSCRIPTION SETTINGS
TRIAL_DURATION_DAYS=3
TRIAL_TRAFFIC_LIMIT_GB=10
TRIAL_DEVICE_LIMIT=2
TRIAL_SQUAD_UUID=
DEFAULT_TRAFFIC_RESET_STRATEGY=MONTH

# SUBSCRIPTION PRICING (в копейках для точности)
BASE_SUBSCRIPTION_PRICE=50000

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

# REFERRAL SYSTEM SETTINGS
REFERRAL_REGISTRATION_REWARD=5000
REFERRED_USER_REWARD=10000
REFERRAL_COMMISSION_PERCENT=25

# Режим работы кнопки "Подключиться"
# guide - открывает гайд подключения (режим 1)
# miniapp_subscription - открывает ссылку подписки в мини-приложении (режим 2)
# miniapp_custom - открывает заданную ссылку в мини-приложении (режим 3)
CONNECT_BUTTON_MODE=miniapp_subscription
# URL для режима miniapp_custom (обязателен при CONNECT_BUTTON_MODE=miniapp_custom)
# MINIAPP_CUSTOM_URL=

# AUTO-PAYMENT SETTINGS
AUTOPAY_WARNING_DAYS=3,1

# MONITORING SETTINGS
MONITORING_INTERVAL=60
INACTIVE_USER_DELETE_MONTHS=3

TRIAL_WARNING_HOURS=2
ENABLE_NOTIFICATIONS=true
NOTIFICATION_RETRY_ATTEMPTS=3
MONITORING_LOGS_RETENTION_DAYS=30

# PAYMENT SYSTEMS
TELEGRAM_STARS_ENABLED=true
TRIBUTE_ENABLED=false
TRIBUTE_API_KEY=
TRIBUTE_WEBHOOK_SECRET=your_webhook_secret
TRIBUTE_DONATE_LINK=https://t.me/tribute/app?startapp=XXXX
TRIBUTE_WEBHOOK_PATH=/tribute-webhook
TRIBUTE_WEBHOOK_PORT=8081

# === НОВЫЕ НАСТРОЙКИ YOOKASSA ===
# Включение/выключение YooKassa
YOOKASSA_ENABLED=false

# Основные настройки YooKassa (получить в личном кабинете)
YOOKASSA_SHOP_ID=your_shop_id_here
YOOKASSA_SECRET_KEY=your_secret_key_here

# URL для возврата после оплаты (необязательно, по умолчанию t.me/your_bot)
YOOKASSA_RETURN_URL=https://yourdomain.com/payment-success

# Email по умолчанию для чеков (если пользователь не указал свой)
YOOKASSA_DEFAULT_RECEIPT_EMAIL=receipts@yourdomain.com

# Настройки чеков для налоговой
YOOKASSA_VAT_CODE=1
# Коды НДС:
# 1 - НДС не облагается
# 2 - НДС 0%  
# 3 - НДС 10%
# 4 - НДС 20%
# 5 - НДС 10/110
# 6 - НДС 20/120

YOOKASSA_PAYMENT_MODE=full_payment
# Способы расчета:
# full_payment - полная оплата
# partial_payment - частичная оплата  
# advance - аванс
# full_prepayment - полная предоплата
# partial_prepayment - частичная предоплата
# credit - передача в кредит
# credit_payment - оплата кредита

YOOKASSA_PAYMENT_SUBJECT=service
# Предметы расчета:
# commodity - товар
# excise - подакцизный товар  
# job - работа
# service - услуга
# gambling_bet - ставка в азартной игре
# gambling_prize - выигрыш в азартной игре
# lottery - лотерейный билет
# lottery_prize - выигрыш в лотерее
# intellectual_activity - результат интеллектуальной деятельности
# payment - платеж
# agent_commission - агентское вознаграждение
# composite - составной предмет расчета
# another - другое

# Webhook для получения уведомлений от YooKassa
YOOKASSA_WEBHOOK_PATH=/yookassa-webhook
YOOKASSA_WEBHOOK_PORT=8082
YOOKASSA_WEBHOOK_SECRET=ваш_секретный_ключ_для_webhook

WEBHOOK_URL=https://example.com
WEBHOOK_PATH=/webhook

# LOCALIZATION
DEFAULT_LANGUAGE=ru
AVAILABLE_LANGUAGES=ru

# LOGGING
LOG_LEVEL=INFO
LOG_FILE=/tmp/bot.log

# DEVELOPMENT
DEBUG=false

MAINTENANCE_CHECK_INTERVAL=30
MAINTENANCE_AUTO_ENABLE=true
MAINTENANCE_MESSAGE="Ведутся технические работы"
```

</details>

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
├── ⚙️ app-config.json              # Информация для гайда в боте по подключению(Приложения, текста)
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
│   ├── ⌨️ keyboards/             # Интерфейсы
│   │   ├── 🔲 inline.py          # Inline клавиатуры
│   │   ├── 📋 reply.py           # Reply клавиатуры
│   │   └── 👑 admin.py           # Админские клавиатуры
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
│   │   ├── 💳 tribute_service.py          # Tribute платежи
│   │   └── 🌐 remnawave_service.py       # Интеграция с Remnawave
│   │
│   ├── 🛠️ utils/                # Утилиты
│   │   ├── 🎨 decorators.py      # Декораторы
│   │   ├── 📄 formatters.py      # Форматирование данных
│   │   ├── ✅ validators.py      # Валидация
│   │   ├── 📚 pagination.py      # Пагинация
│   │   ├── 👤 user_utils.py      # Утилиты пользователей
│   │   └── 💾 cache.py           # Кеширование
│   │
│   ├── 🛡️ middlewares/           # Middleware
│   │   ├── 🔐 auth.py           # Авторизация
│   │   ├── 📋 logging.py        # Логирование
│   │   └── 🚦 throttling.py     # Ограничение запросов
│   │
│   ├── 🌐 localization/          # Локализация
│   │   ├── 📝 texts.py          # Тексты интерфейса
│   │   └── 🌍 languages/        # Языковые пакеты
│   │
│   └── 🔌 external/              # Внешние API
│       ├── 🌐 remnawave_api.py   # API Remnawave
│       ├── ⭐ telegram_stars.py  # Telegram Stars
│       └── 💳 tribute.py         # Tribute платежи
│
├── 🔄 migrations/                # Миграции БД
│   └── alembic/
│
└── 📋 logs/                      # Логи системы
```

---

## 🐳 Docker развертывание

### 📁 Docker Compose файлы

```
project/
├── docker-compose.yml              # 🚀 Продакшн
├── docker-compose.local.yml        # 🏠 Разработка
├── .env                           # ⚙️ Конфиг
└── .env.example                   # 📝 Пример
```

### 🚀 Продакшн (docker-compose.yml)

<details>
<summary>📄 Показать полный docker-compose.yml</summary>

```yaml
version: '3.8'

services:
  # 🗄️ PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: bedolaga_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-bedolaga_bot}
      POSTGRES_USER: ${POSTGRES_USER:-bedolaga_user}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-secure_password_123}
      POSTGRES_INITDB_ARGS: "--encoding=UTF8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init-scripts:/docker-entrypoint-initdb.d:ro
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    networks:
      - bedolaga_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-bedolaga_user} -d ${POSTGRES_DB:-bedolaga_bot}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # ⚡ Redis Cache
  redis:
    image: redis:7-alpine
    container_name: bedolaga_redis
    restart: unless-stopped
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD:-redis_password_123}
    volumes:
      - redis_data:/data
    ports:
      - "${REDIS_PORT:-6379}:6379"
    networks:
      - bedolaga_network
    healthcheck:
      test: ["CMD", "redis-cli", "--no-auth-warning", "-a", "${REDIS_PASSWORD:-redis_password_123}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 10s
    logging:
      driver: "json-file"
      options:
        max-size: "5m"
        max-file: "3"

  # 🤖 Telegram Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: bedolaga_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER:-bedolaga_user}:${POSTGRES_PASSWORD:-secure_password_123}@postgres:5432/${POSTGRES_DB:-bedolaga_bot}
      REDIS_URL: redis://:${REDIS_PASSWORD:-redis_password_123}@redis:6379/0
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      DEBUG: ${DEBUG:-false}
      HEALTH_CHECK_ENABLED: "true"
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
      - ./backups:/app/backups
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "${WEBHOOK_PORT:-8081}:8081"
    networks:
      - bedolaga_network
    healthcheck:
      test: ["CMD", "python", "-c", "import requests; requests.get('http://localhost:8081/health', timeout=5)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "5"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.bedolaga-webhook.rule=Host(`${WEBHOOK_DOMAIN:-localhost}`) && PathPrefix(`/tribute-webhook`)"
      - "traefik.http.services.bedolaga-webhook.loadbalancer.server.port=8081"

  # 📊 Monitoring (опционально)
  prometheus:
    image: prom/prometheus:latest
    container_name: bedolaga_prometheus
    restart: unless-stopped
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--web.console.libraries=/etc/prometheus/console_libraries'
      - '--web.console.templates=/etc/prometheus/consoles'
      - '--storage.tsdb.retention.time=200h'
      - '--web.enable-lifecycle'
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    ports:
      - "9090:9090"
    networks:
      - bedolaga_network
    profiles:
      - monitoring

  # 📈 Grafana (опционально)
  grafana:
    image: grafana/grafana:latest
    container_name: bedolaga_grafana
    restart: unless-stopped
    environment:
      GF_SECURITY_ADMIN_USER: ${GRAFANA_USER:-admin}
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD:-admin123}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards
      - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources
    ports:
      - "3000:3000"
    networks:
      - bedolaga_network
    profiles:
      - monitoring

# 📦 Volumes
volumes:
  postgres_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ./volumes/postgres
  redis_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ./volumes/redis
  prometheus_data:
    driver: local
  grafana_data:
    driver: local

# 🌐 Networks
networks:
  bedolaga_network:
    driver: bridge
    ipam:
      driver: default
      config:
        - subnet: 172.20.0.0/16
          gateway: 172.20.0.1
    driver_opts:
      com.docker.network.bridge.name: br-bedolaga
```

</details>

### 🏠 Разработка (docker-compose.local.yml)

<details>
<summary>📄 Показать dev конфигурацию</summary>

```yaml
version: '3.8'

services:
  # 🗄️ PostgreSQL для разработки
  postgres-dev:
    image: postgres:15-alpine
    container_name: bedolaga_postgres_dev
    restart: unless-stopped
    environment:
      POSTGRES_DB: bedolaga_bot_dev
      POSTGRES_USER: dev_user
      POSTGRES_PASSWORD: dev_password
    volumes:
      - postgres_dev_data:/var/lib/postgresql/data
    ports:
      - "5433:5432"
    networks:
      - bedolaga_dev_network

  # ⚡ Redis для разработки
  redis-dev:
    image: redis:7-alpine
    container_name: bedolaga_redis_dev
    restart: unless-stopped
    volumes:
      - redis_dev_data:/data
    ports:
      - "6380:6379"
    networks:
      - bedolaga_dev_network

  # 🤖 Bot для разработки
  bot-dev:
    build:
      context: .
      dockerfile: Dockerfile.dev
      args:
        - PYTHON_VERSION=3.11
    container_name: bedolaga_bot_dev
    restart: unless-stopped
    depends_on:
      - postgres-dev
      - redis-dev
    env_file:
      - .env.local
    environment:
      DATABASE_URL: postgresql+asyncpg://dev_user:dev_password@postgres-dev:5432/bedolaga_bot_dev
      REDIS_URL: redis://redis-dev:6379/0
      DEBUG: "true"
      LOG_LEVEL: DEBUG
    volumes:
      - .:/app
      - ./logs:/app/logs
    ports:
      - "8082:8081"
    networks:
      - bedolaga_dev_network
    command: python -m app.main --reload

  # 🔍 Adminer для управления БД
  adminer:
    image: adminer:latest
    container_name: bedolaga_adminer
    restart: unless-stopped
    ports:
      - "8080:8080"
    networks:
      - bedolaga_dev_network
    environment:
      ADMINER_DEFAULT_SERVER: postgres-dev

volumes:
  postgres_dev_data:
  redis_dev_data:

networks:
  bedolaga_dev_network:
    driver: bridge
```

</details>

### 🚀 Команды управления

```bash
# ⚡ Быстрый старт
docker compose up -d

# 📊 С мониторингом
docker compose --profile monitoring up -d

# 🏠 Разработка
docker compose -f docker-compose.local.yml up -d

# 📋 Статус сервисов
docker compose ps

# 📄 Логи
docker compose logs -f bot

# 🔄 Перезапуск
docker compose restart bot

# 🛑 Остановка
docker compose down

# 🧹 Полная очистка
docker compose down -v --remove-orphans
```

### 🔧 Управление данными

```bash
# 💾 Бэкап БД
docker compose exec postgres pg_dump -U bedolaga_user bedolaga_bot > backup_$(date +%Y%m%d_%H%M%S).sql

# 🔄 Восстановление БД
docker compose exec -T postgres psql -U bedolaga_user bedolaga_bot < backup.sql

# 📊 Размер данных
docker system df
docker compose exec postgres du -sh /var/lib/postgresql/data

# 🧹 Очистка логов
docker compose exec bot find /app/logs -name "*.log" -type f -mtime +7 -delete

# 📈 Мониторинг ресурсов
docker stats bedolaga_bot bedolaga_postgres bedolaga_redis
```

---

## 🚀 Производительность

| Пользователей | Память | CPU | Диск | Описание |
|---------------|--------|-----|------|----------|
| **1,000** | 512MB | 1 vCPU | 10GB | ✅ Стартап |
| **10,000** | 2GB | 2 vCPU | 50GB | ✅ Малый бизнес |
| **50,000** | 4GB | 4 vCPU | 100GB | ✅ Средний бизнес |
| **100,000+** | 8GB+ | 8+ vCPU | 200GB+ | 🚀 Enterprise |

### ⚡ Оптимизации производительности

- **🔄 Асинхронная архитектура** - обработка тысяч запросов параллельно
- **⚡ Redis кеширование** - молниеносные ответы на частые запросы
- **🔗 Пул соединений БД** - эффективное использование ресурсов
- **📦 Пагинация** - быстрая загрузка больших списков
- **🛡️ Rate limiting** - защита от злоупотреблений
- **🔄 Graceful shutdown** - безопасные перезагрузки без потери данных

---

## 💎 Продвинутые возможности

### 🎯 **Реальные примеры кода**

**🔄 Автопродление подписок:**
```python
# Из monitoring_service.py - реальная логика автопродления
if user.balance_kopeks >= renewal_cost:
    success = await subtract_user_balance(
        db, user, renewal_cost,
        "Автопродление подписки"
    )
    
    if success:
        await extend_subscription(db, subscription, 30)
        await subscription_service.update_remnawave_user(db, subscription)
        
        if self.bot:
            await self._send_autopay_success_notification(user, renewal_cost, 30)
        
        logger.info(f"💳 Автопродление подписки пользователя {user.telegram_id} успешно")
```

**💰 Реферальные бонусы:**
```python
# Из referral_service.py - начисление комиссии
commission_amount = int(purchase_amount_kopeks * settings.REFERRAL_COMMISSION_PERCENT / 100)

if commission_amount > 0:
    await add_user_balance(
        db, referrer, commission_amount,
        f"Комиссия {settings.REFERRAL_COMMISSION_PERCENT}% с покупки {user.full_name}"
    )
    
    await create_referral_earning(
        db=db,
        user_id=referrer.id,
        referral_id=user_id,
        amount_kopeks=commission_amount,
        reason="referral_commission"
    )
```

**📊 Расчет стоимости подписки:**
```python
# Из subscription_service.py - умный расчет цен
async def calculate_subscription_price(
    self,
    period_days: int,
    traffic_gb: int,
    server_squad_ids: List[int], 
    devices: int,
    db: AsyncSession 
) -> Tuple[int, List[int]]:

    base_price = PERIOD_PRICES.get(period_days, 0)
    traffic_price = TRAFFIC_PRICES.get(traffic_gb, 0)
    
    total_servers_price = 0
    for server_id in server_squad_ids:
        server = await get_server_squad_by_id(db, server_id)
        if server and server.is_available and not server.is_full:
            total_servers_price += server.price_kopeks
    
    devices_price = max(0, devices - 1) * settings.PRICE_PER_DEVICE
    total_price = base_price + traffic_price + total_servers_price + devices_price
    
    logger.info(f"💰 Расчет стоимости новой подписки: {total_price/100}₽")
    return total_price, server_prices
```

**🔔 Система уведомлений:**
```python
# Из monitoring_service.py - умные уведомления
async def _send_trial_ending_notification(self, user: User, subscription: Subscription):
    message = f"""
🎁 <b>Тестовая подписка скоро закончится!</b>

Ваша тестовая подписка истекает через 2 часа.

💎 <b>Не хотите остаться без VPN?</b>
Переходите на полную подписку со скидкой!

🔥 <b>Специальное предложение:</b>
• 30 дней всего за {settings.format_price(settings.PRICE_30_DAYS)}
• Безлимитный трафик
• Все серверы доступны
• Поддержка до 3 устройств
"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="balance_top_up")]
    ])
    
    await self.bot.send_message(user.telegram_id, message, parse_mode="HTML", reply_markup=keyboard)
```

**💳 Платежная система:**
```python
# Из payment_service.py - Telegram Stars
async def create_stars_invoice(self, amount_kopeks: int, description: str) -> str:
    try:
        stars_amount = max(1, amount_kopeks // 100)
        
        invoice_link = await self.bot.create_invoice_link(
            title="Пополнение баланса VPN",
            description=description,
            payload=f"balance_topup_{amount_kopeks}",
            provider_token="", 
            currency="XTR", 
            prices=[LabeledPrice(label="Пополнение", amount=stars_amount)]
        )
        
        logger.info(f"Создан Stars invoice на {stars_amount} звезд")
        return invoice_link
        
    except Exception as e:
        logger.error(f"Ошибка создания Stars invoice: {e}")
        raise
```

### 🔧 **Первичная настройка в боте**

После запуска необходимо:

1. **📡 Синхронизация серверов** (обязательно!)
   - Зайди в бот → **Админ панель** → **Подписки** → **Управление серверами**
   - Нажми **Синхронизация** и дождись завершения
   - Без этого пользователи не смогут выбирать страны!

2. **👥 Синхронизация пользователей** (если есть база)
   - **Админ панель** → **Remnawave** → **Синхронизация**
   - **Синхронизировать всех** → дождись импорта

### 💳 **Настройка Telegram Tribute**

<details>
<summary>🔧 Пошаговая настройка Tribute</summary>

1. **📝 Регистрация**
   - Зарегистрируйся в [Tribute](https://tribute.app)
   - Пройди верификацию

2. **🔗 Создание донат-ссылки**
   - Создай донат ссылку в Tribute
   - Скопируй и вставь в `TRIBUTE_DONATE_LINK`

3. **🌐 Настройка прокси**
   
   **Caddy:**
   ```caddyfile
   https://your-domain.com {
       handle /tribute-webhook* {
           reverse_proxy localhost:8081 {
               header_up Host {host}
               header_up X-Real-IP {remote_host}
           }
       }
       
       handle /webhook-health {
           reverse_proxy localhost:8081/health
       }
   }
   ```
   
   **Nginx:**
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
       
       location /webhook-health {
           proxy_pass http://127.0.0.1:8081/health;
       }
   }
   ```

4. **⚙️ Настройка webhook**
   - В настройках Tribute укажи: `https://your-domain.com/tribute-webhook`
   - Создай API ключ и вставь в `TRIBUTE_API_KEY`
   - Сделай тест в Tribute панели

5. **✅ Проверка**
   - Тестируй пополнение через бота
   - Проверь логи: `docker compose logs -f bot`

</details>

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

## 🛡️ Безопасность

### 🔒 **Защита данных**
- 🔐 Все пароли и ключи в переменных окружения
- 🛡️ SQL Injection защита через SQLAlchemy ORM
- ✅ Валидация всех пользовательских данных
- 🚦 Middleware авторизации и rate limiting
- 📋 Детальное логирование всех операций

### 📊 **Мониторинг безопасности**
- 🔍 Системы алертов при подозрительной активности
- 💾 Автоматическое резервное копирование
- 🏥 Health checks для всех сервисов
- 📈 Мониторинг производительности

---

## 🚀 Roadmap

### ✅ **2.0.0 (Текущая версия)**
- 🏗️ Полное переписывание архитектуры с нуля
- 🎛️ Единая конфигурируемая подписка вместо мультиподписок
- 💳 Интеграция Telegram Stars + Tribute
- 🔄 Продвинутая синхронизация с Remnawave
- 📊 Детальная система мониторинга

### 🎯 **Планы развития**

| Версия | Функция | Статус | ETA | Приоритет |
|--------|---------|--------|-----|-----------|
| **2.1.0** | 💳 ЮKassa интеграция | 🔄 В работе | Q1 2025 | 🔴 High |
| **2.2.0** | 🌐 Web админ-панель | 📋 Планируется | Q2 2025 | 🟡 Medium |
| **2.3.0** | 🌍 Мультиязычность | 💭 Исследование | Q3 2025 | 🟡 Medium |
| **2.4.0** | 🔗 Открытое API | 💭 Исследование | Q4 2025 | 🟢 Low |
| **2.5.0** | 📱 Мобильное приложение | 💭 Концепция | 2026 | 🟢 Low |

### 💡 **Идеи для будущих версий**
- 🎨 Кастомизируемые темы интерфейса
- 🤖 AI-помощник для поддержки
- 📈 Продвинутая аналитика с ML
- 🔔 Push-уведомления
- 💼 Корпоративные тарифы
- 🌐 Мультипанельная поддержка

---

## 🐛 Устранение неполадок

### ❓ **Частые проблемы**

<details>
<summary>🤖 Бот не отвечает</summary>

**Проверь:**
- ✅ Правильность `BOT_TOKEN` в .env
- ✅ Интернет соединение сервера
- ✅ Статус контейнеров: `docker compose ps`

**Диагностика:**
```bash
# Проверка логов
docker compose logs -f bot

# Проверка переменных
docker exec bedolaga_bot env | grep BOT_TOKEN

# Перезапуск
docker compose restart bot
```

</details>

<details>
<summary>🗄️ Ошибки базы данных</summary>

**Симптомы:**
- SQL ошибки в логах
- Бот не сохраняет данные
- Подключение отклонено

**Решение:**
```bash
# Проверка PostgreSQL
docker compose logs postgres

# Проверка подключения
docker exec bedolaga_bot pg_isready -h postgres -p 5432

# Пересоздание БД
docker compose down
docker volume rm project_postgres_data
docker compose up -d
```

</details>

<details>
<summary>🔌 Проблемы с Remnawave API</summary>

**Проверь:**
- ✅ Доступность `REMNAWAVE_API_URL`
- ✅ Валидность `REMNAWAVE_API_KEY`
- ✅ Сетевое подключение

**Диагностика:**
```bash
# Проверка доступности API
curl -I https://your-panel.com

# Тест из контейнера
docker exec bedolaga_bot curl -H "Authorization: Bearer YOUR_TOKEN" https://your-panel.com/api/health

# Проверка синхронизации
docker compose exec bot python -c "
from app.services.remnawave_service import RemnaWaveService
import asyncio
asyncio.run(RemnaWaveService().check_connection())
"
```

</details>

<details>
<summary>💳 Проблемы с Tribute платежами</summary>

**Проверь:**
- ✅ Webhook доступен: `https://your-domain.com/tribute-webhook`
- ✅ API ключ корректен
- ✅ Настройки прокси (Nginx/Caddy)

**Диагностика:**
```bash
# Проверка webhook
curl -X POST https://your-domain.com/tribute-webhook

# Проверка в Tribute панели
# Logs -> Webhook logs -> посмотри статус доставки

# Тест локально
docker exec bedolaga_bot curl http://localhost:8081/health
```

</details>

### 🔧 **Профилактика**

```bash
# 📊 Мониторинг места
df -h
docker system df

# 🧹 Очистка старых логов
find ./logs -name "*.log" -mtime +30 -delete

# 💾 Регулярные бэкапы
0 2 * * * docker compose exec postgres pg_dump -U bedolaga_user bedolaga_bot > /backups/db_$(date +\%Y\%m\%d).sql

# 📈 Мониторинг ресурсов
docker stats --no-stream
```

---

## 🤝 Как помочь проекту

### 💻 **Для разработчиков**

1. **🍴 Fork репозитория**
   ```bash
   git clone https://github.com/YOUR_USERNAME/remnawave-bedolaga-telegram-bot.git
   cd remnawave-bedolaga-telegram-bot
   ```

2. **🌿 Создай feature branch**
   ```bash
   git checkout -b feature/amazing-feature
   ```

3. **💻 Разрабатывай**
   ```bash
   # Используй dev окружение
   docker compose -f docker-compose.local.yml up -d
   
   # Твои изменения...
   
   # Тестируй
   python -m pytest tests/
   ```

4. **📤 Commit и Push**
   ```bash
   git add .
   git commit -m "feat: add amazing feature"
   git push origin feature/amazing-feature
   ```

5. **🔄 Создай Pull Request**

### 🐛 **Для пользователей**

- 🔍 [Сообщай о багах](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues) с подробным описанием
- 💡 [Предлагай идеи](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/discussions) для улучшения
- ⭐ **Ставь звезды** проекту - это мотивирует!
- 📢 **Рассказывай друзьям** о проекте
- 📝 **Улучшай документацию** - исправляй опечатки, добавляй примеры

### 💰 **Для спонсоров**

- 🎯 **Заказывай приоритетные функции** - ускори разработку нужного
- 🏢 **Получи корпоративную поддержку** - персональная помощь
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
- **🐘 [PostgreSQL Docs](https://www.postgresql.org/docs/)** - документация БД

### 💬 **Правила общения**

- 🤝 Будь вежлив и конструктивен
- 🔍 Используй поиск перед созданием issue
- 📝 Предоставляй подробную информацию о проблемах
- 🏷️ Используй правильные теги для issues

---

## 🏆 Статистика и признание

<div align="center">

### 📈 **Рост проекта**

[![Star History Chart](https://api.star-history.com/svg?repos=Fr1ngg/remnawave-bedolaga-telegram-bot&type=Date)](https://star-history.com/#Fr1ngg/remnawave-bedolaga-telegram-bot&Date)

### 📊 **Статистика GitHub**

![GitHub Contributors](https://img.shields.io/github/contributors/Fr1ngg/remnawave-bedolaga-telegram-bot?style=for-the-badge&color=blue)
![GitHub Forks](https://img.shields.io/github/forks/Fr1ngg/remnawave-bedolaga-telegram-bot?style=for-the-badge&color=green)
![GitHub Issues](https://img.shields.io/github/issues/Fr1ngg/remnawave-bedolaga-telegram-bot?style=for-the-badge&color=orange)
![GitHub Last Commit](https://img.shields.io/github/last-commit/Fr1ngg/remnawave-bedolaga-telegram-bot?style=for-the-badge&color=purple)

</div>

### 🏅 **Достижения проекта**

- 🌟 **40+ Stars** на GitHub

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
<td>$15</td>
<td>За веру в проект с самого начала</td>
</tr>
<tr>
<td>🥈</td>
<td><strong>@pilot_737800</strong></td>
<td>₽1,250</td>
<td>За активное тестирование и фидбек</td>
</tr>
<tr>
<td>🥉</td>
<td><strong>@Legacyyy777</strong></td>
<td>₽1,000</td>
<td>За ценные предложения по улучшению</td>
</tr>
</table>

### 🤝 **Contributors**

Огромная благодарность всем, кто делает проект лучше:

- 🐛 **Тестировщикам** - находят баги до пользователей
- 💻 **Разработчикам** - присылают Pull Request'ы
- 💡 **Идейным вдохновителям** - предлагают новые функции

### 🌟 **Особая благодарность**

- **Remnawave Team** - за отличную панель и API

---

<div align="center">

## 📄 Лицензия

Проект распространяется под лицензией **MIT**

```
MIT License

Copyright (c) 2024 Fr1ngg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

---

## 🚀 Заключение

**Bedolaga Bot 2.0.0** - это не просто бот, это **готовое решение для VPN бизнеса**. 

- ⚡ **5 минут до запуска** - быстрее некуда
- 💰 **Автоматизация 99%** - деньги идут сами
- 🔧 **Легкая настройка** - справится даже новичок
- 🆓 **Open Source** - код открыт, развитие прозрачно

### 💪 **Начни свой VPN бизнес уже сегодня!**

```bash
git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot
cp .env.example .env
# Настрой .env
docker compose up -d
# Profit! 💰
```

---

### 💝 **Создано с любовью для Remnawave сообщества**


**Автор:** [@fringg](https://t.me/fringg) - соло-разработчик

*Если проект помог тебе - поставь ⭐, это очень мотивирует!*

---

[![Donate](https://img.shields.io/badge/💝_Поддержать_проект-Telegram-blue?style=for-the-badge)](https://t.me/tribute/app?startapp=duUO)

[⬆️ Наверх](#-remnawave-bedolaga-bot-200)

</div>
