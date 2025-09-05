# 🚀 Remnawave Bedolaga Bot

<div align="center">

![Logo](./assets/logo2.svg)

**🤖 Современный Telegram-бот для управления VPN подписками через Remnawave API**

*Полнофункциональное решение с управлением пользователями, платежами и администрированием*

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue?logo=postgresql&logoColor=white)](https://postgresql.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/Fr1ngg/remnawave-bedolaga-telegram-bot?style=social)](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/stargazers)

[🚀 Быстрый старт](#-быстрый-старт) • [📖 Функционал](#-функционал) • [🐳 Docker](#-docker-развертывание) • [💬 Поддержка](#-поддержка-и-сообщество)

</div>

---

## 🧪 [Тестирование бота](https://t.me/FringVPN_bot)

## 💬 **[Bedolaga Chat](https://t.me/+wTdMtSWq8YdmZmVi)** - Для общения, вопросов, предложений

---

## 🌟 Почему Bedolaga?

Бот Бедолага не добрый и не милый.
Он просто делает вашу работу вместо вас, принимает оплату, выдаёт подписки, интегрируется с Remnawave и тихо ненавидит всех, кто ещё не подключил его.

Вы хотите продавать VPN — Бедолага позволит это делать.
Вы хотите спать — он позволит и это.

### ⚡ **Полная автоматизация VPN бизнеса**
- 🎯 **Готовое решение** - разверни за 5 минут, начни продавать сегодня
- 💰 **Многоканальные платежи** - Telegram Stars + Tribute + ЮKassa
- 🔄 **Автоматизация 99%** - от регистрации до продления подписок
- 📊 **Детальная аналитика** - полная картина вашего бизнеса
  
### 🎛️ **Гибкость конфигурации**
- 🌍 **Умный выбор серверов** - автоматический пропуск при одном сервере, мультивыбор при нескольких
- 📱 **Управление устройствами** - от 1 до неограниченного количества
- 📊 **Режимы продажи трафика** - фиксированный лимит или выбор пакетов
- 🎁 **Промо-система** - коды на деньги, дни подписки, триал-периоды
- 🔧 **Гибкие тарифы** - от 5GB до безлимита, от 14 дней до года

### 💪 **Enterprise готовность**
- 🏗️ **Современная архитектура** - AsyncIO, PostgreSQL, Redis
- 🔒 **Безопасность** - интеграция с системой защиты панели через куки-аутентификацию
- 📈 **Масштабируемость** - от стартапа до крупного бизнеса
- 🔧 **Мониторинг** - автоматическое управление режимом тех. работ
- 🛡️ **Защита панели** - поддержка [remnawave-reverse-proxy](https://github.com/eGamesAPI/remnawave-reverse-proxy)

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
docker compose up -d

# 5. Проверь статус
docker compose logs 
```

---

## ⚙️ Конфигурация

### 🔧 Основные параметры

| Настройка | Где взять | Пример |
|-----------|-----------|---------|
| 🤖 **BOT_TOKEN** | [@BotFather](https://t.me/BotFather) | `1234567890:AABBCCdd...` |
| 🔑 **REMNAWAVE_API_KEY** | Твоя Remnawave панель | `eyJhbGciOiJIUzI1...` |
| 🌐 **REMNAWAVE_API_URL** | URL твоей панели | `https://panel.example.com` |
| 🛡️ **REMNAWAVE_SECRET_KEY** | Ключ защиты панели | `secret_name:secret_value` |
| 👑 **ADMIN_IDS** | Твой Telegram ID | `123456789,987654321` |

### 🛡️ Защита панели Remnawave

Для панелей, защищенных через [remnawave-reverse-proxy](https://github.com/eGamesAPI/remnawave-reverse-proxy):

```env
# Для панелей установленных скриптом eGames
REMNAWAVE_SECRET_KEY=XXXXXXX:DDDDDDDD

# Или если ключ и значение одинаковые
REMNAWAVE_SECRET_KEY=secret_key_name
```

### 📊 Режимы продажи трафика

#### **Выбираемые пакеты** (по умолчанию)
```env
TRAFFIC_SELECTION_MODE=selectable
TRAFFIC_PACKAGES_CONFIG="5:2000:false,10:3500:false,25:7000:false,50:11000:true,100:15000:true,250:17000:false,500:19000:false,1000:19500:true,0:20000:true"
```

#### **Фиксированный лимит**
```env
TRAFFIC_SELECTION_MODE=fixed
FIXED_TRAFFIC_LIMIT_GB=100  # 0 = безлимит
TRAFFIC_PACKAGES_CONFIG="100:15000:true" # Указать цену обязательно для FIXED_TRAFFIC_LIMIT_GB 
```

### 💰 Система ценообразования

Цена подписки рассчитывается по формуле:
**Базовая цена + Стоимость трафика + Доп. устройства + Доп. серверы**

**Пример расчета для подписки на 180 дней:**
- Базовый период: 400₽
- Трафик безлимит: 200₽/мес × 6 мес = 1200₽
- 4 устройства: 50₽/мес × 6 мес = 300₽
- 2 сервера: 100₽/мес × 6 мес = 1200₽
- **Итого: 3100₽**

```env
# Базовая цена подписки
BASE_SUBSCRIPTION_PRICE=0

# Цены за периоды (в копейках)
PRICE_14_DAYS=7000
PRICE_30_DAYS=9900
PRICE_60_DAYS=25900
PRICE_90_DAYS=36900
PRICE_180_DAYS=69900
PRICE_360_DAYS=109900

# Выводимые пакеты трафика и их цены в копейках
TRAFFIC_PACKAGES_CONFIG="5:2000:false,10:3500:false,25:7000:false,50:11000:true,100:15000:true,250:17000:false,500:19000:false,1000:19500:true,0:20000:true"

# Цена за дополнительное устройство
PRICE_PER_DEVICE=5000

# Настройка доступных периодов
AVAILABLE_SUBSCRIPTION_PERIODS=30,90,180
AVAILABLE_RENEWAL_PERIODS=30,90,180
```

### 📱 Управление устройствами

```env
# Бесплатные устройства в триал подписке
TRIAL_DEVICE_LIMIT=1

# Бесплатные устройства в платной подписке
DEFAULT_DEVICE_LIMIT=3

# Максимум устройств для покупки (0 = без лимита)
MAX_DEVICES_LIMIT=15
```

### 🛡️ Мониторинг и техническое обслуживание

```env
# Автоматический режим тех. работ
MAINTENANCE_MODE=false
MAINTENANCE_AUTO_ENABLE=true
MAINTENANCE_CHECK_INTERVAL=30

# Интервал проверки состояния панели (секунды)
MONITORING_INTERVAL=60

# Сообщение для пользователей
MAINTENANCE_MESSAGE=Ведутся технические работы. Сервис временно недоступен.
```

<details>
<summary>🔧 Полная конфигурация .env</summary>

```env
# ===============================================
# 🤖 REMNAWAVE BEDOLAGA BOT CONFIGURATION
# ===============================================

# ===== TELEGRAM BOT =====
BOT_TOKEN=
ADMIN_IDS=
SUPPORT_USERNAME=@support

# ===== DATABASE =====
# Для Docker используйте PostgreSQL:
DATABASE_URL=postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
# Для локального запуска без Docker используйте SQLite: sqlite+aiosqlite:///./bot.db 

REDIS_URL=redis://redis:6379/0

# Пароли для Docker (PostgreSQL/Redis)
POSTGRES_DB=remnawave_bot
POSTGRES_USER=remnawave_user
POSTGRES_PASSWORD=secure_password_123

# ===== REMNAWAVE API =====
REMNAWAVE_API_URL=https://panel.example.com
REMNAWAVE_API_KEY=
# Для панелей установленных скриптом eGames прописывать ключ в формате XXXXXXX:DDDDDDDD
REMNAWAVE_SECRET_KEY=your_secret_key_here

# ========= ПОДПИСКИ =========
# ===== ТРИАЛ ПОДПИСКА =====
TRIAL_DURATION_DAYS=3
TRIAL_TRAFFIC_LIMIT_GB=10
TRIAL_DEVICE_LIMIT=1
TRIAL_SQUAD_UUID=

# ===== ПЛАТНАЯ ПОДПИСКА =====
# Сколько устройств доступно по дефолту при покупке платной подписки
DEFAULT_DEVICE_LIMIT=3

# Максимум устройств доступных к покупке (0 = Нет лимита)
MAX_DEVICES_LIMIT=15

# Дефолт параметры для подписок выданных через админку
DEFAULT_TRAFFIC_LIMIT_GB=100

# ===== ГЛОБАЛЬНЫЙ ПАРАМЕТР ДЛЯ ВСЕХ ПОДПИСОК =====
DEFAULT_TRAFFIC_RESET_STRATEGY=MONTH

# ===== НАСТРОЙКИ ТРАФИКА =====
# Режим выбора трафика:
# "selectable" - пользователи выбирают пакеты трафика (по умолчанию)
# "fixed" - фиксированный лимит трафика для всех подписок
TRAFFIC_SELECTION_MODE=selectable

# Фиксированный лимит трафика в ГБ (используется только в режиме "fixed")
# 0 = безлимит
FIXED_TRAFFIC_LIMIT_GB=100

# ===== ПЕРИОДЫ ПОДПИСКИ =====
# Доступные периоды подписки (через запятую)
# Возможные значения: 14,30,60,90,180,360
AVAILABLE_SUBSCRIPTION_PERIODS=30,90,180
AVAILABLE_RENEWAL_PERIODS=30,90,180

# ===== ЦЕНЫ (в копейках) =====
BASE_SUBSCRIPTION_PRICE=0

# Цены за периоды
PRICE_14_DAYS=7000
PRICE_30_DAYS=9900
PRICE_60_DAYS=25900
PRICE_90_DAYS=36900
PRICE_180_DAYS=69900
PRICE_360_DAYS=109900

# Выводимые пакеты трафика и их цены в копейках
TRAFFIC_PACKAGES_CONFIG="5:2000:false,10:3500:false,25:7000:false,50:11000:true,100:15000:true,250:17000:false,500:19000:false,1000:19500:true,0:20000:true"

# Цена за дополнительное устройство (DEFAULT_DEVICE_LIMIT идет бесплатно!)
PRICE_PER_DEVICE=5000

# ===== РЕФЕРАЛЬНАЯ СИСТЕМА =====
REFERRAL_MINIMUM_TOPUP_KOPEKS=10000
REFERRAL_FIRST_TOPUP_BONUS_KOPEKS=10000
REFERRAL_INVITER_BONUS_KOPEKS=10000
REFERRAL_COMMISSION_PERCENT=25

# ===== АВТОПРОДЛЕНИЕ =====
AUTOPAY_WARNING_DAYS=3,1
DEFAULT_AUTOPAY_DAYS_BEFORE=3
MIN_BALANCE_FOR_AUTOPAY_KOPEKS=10000

# ===== ПЛАТЕЖНЫЕ СИСТЕМЫ =====

# Telegram Stars (работает автоматически)
TELEGRAM_STARS_ENABLED=true
TELEGRAM_STARS_RATE_RUB=1.3

# Tribute (https://tribute.app)
TRIBUTE_ENABLED=false
TRIBUTE_API_KEY=
TRIBUTE_WEBHOOK_SECRET=your_webhook_secret
TRIBUTE_DONATE_LINK=
TRIBUTE_WEBHOOK_PATH=/tribute-webhook
TRIBUTE_WEBHOOK_PORT=8081

# YooKassa (https://yookassa.ru)
YOOKASSA_ENABLED=false
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=
YOOKASSA_DEFAULT_RECEIPT_EMAIL=receipts@yourdomain.com

# Настройки чеков для налоговой
YOOKASSA_VAT_CODE=1
YOOKASSA_PAYMENT_MODE=full_payment
YOOKASSA_PAYMENT_SUBJECT=service

# Webhook настройки
YOOKASSA_WEBHOOK_PATH=/yookassa-webhook
YOOKASSA_WEBHOOK_PORT=8082
YOOKASSA_WEBHOOK_SECRET=your_webhook_secret

# ===== НАСТРОЙКИ ОПИСАНИЙ ПЛАТЕЖЕЙ =====
PAYMENT_SERVICE_NAME=Интернет-сервис
PAYMENT_BALANCE_DESCRIPTION=Пополнение баланса
PAYMENT_SUBSCRIPTION_DESCRIPTION=Оплата подписки
PAYMENT_BALANCE_TEMPLATE={service_name} - {description}
PAYMENT_SUBSCRIPTION_TEMPLATE={service_name} - {description}

# ===== ИНТЕРФЕЙС И UX =====

# Режим работы кнопки "Подключиться"
# guide - открывает гайд подключения (режим 1)
# miniapp_subscription - открывает ссылку подписки в мини-приложении (режим 2)
# miniapp_custom - открывает заданную ссылку в мини-приложении (режим 3)
CONNECT_BUTTON_MODE=guide

# URL для режима miniapp_custom (обязателен при CONNECT_BUTTON_MODE=miniapp_custom)
MINIAPP_CUSTOM_URL=

# ===== МОНИТОРИНГ И УВЕДОМЛЕНИЯ =====
MONITORING_INTERVAL=60
INACTIVE_USER_DELETE_MONTHS=3

# Уведомления
TRIAL_WARNING_HOURS=2
ENABLE_NOTIFICATIONS=true
NOTIFICATION_RETRY_ATTEMPTS=3
MONITORING_LOGS_RETENTION_DAYS=30
NOTIFICATION_CACHE_HOURS=24

# ===== РЕЖИМ ТЕХНИЧЕСКИХ РАБОТ =====
MAINTENANCE_MODE=false
MAINTENANCE_CHECK_INTERVAL=30
MAINTENANCE_AUTO_ENABLE=true
MAINTENANCE_MESSAGE=Ведутся технические работы. Сервис временно недоступен. Попробуйте позже.

# ===== ЛОКАЛИЗАЦИЯ =====
DEFAULT_LANGUAGE=ru
AVAILABLE_LANGUAGES=ru,en

# ===== ЛОГИРОВАНИЕ =====
LOG_LEVEL=INFO
LOG_FILE=logs/bot.log

# ===== РАЗРАБОТКА =====
DEBUG=false
WEBHOOK_URL=
WEBHOOK_PATH=/webhook

# ===== ДОПОЛНИТЕЛЬНЫЕ НАСТРОЙКИ =====
# Конфигурация приложений для гайда подключения
APP_CONFIG_PATH=app-config.json
ENABLE_DEEP_LINKS=true
APP_CONFIG_CACHE_TTL=3600
```

</details>

---

## ⭐ Функционал

<table>
<tr>
<td width="50%" valign="top">

### 👤 **Для пользователей**

🛒 **Умная покупка подписок**
- 📅 Гибкие периоды (14-360 дней)
- 📊 Выбор трафика или фиксированный лимит
- 🌍 Автоматический выбор серверов
- 📱 Настройка количества устройств

🧪 **Тестовая подписка**
- Настраиваемый триал-период
- Уведомления об истечении
- Плавный переход на платную версию

💰 **Удобные платежи**
- ⭐ Telegram Stars 
- 💳 Tribute 
- 💳 YooKassa
- 🎁 Реферальные бонусы
- Детальная история транзакций 

📱 **Управление подписками**
- 📈 Статистика использования в реальном времени
- 🔄 Автопродление с баланса
- 🔄 Управление трафиком
- 🌍 Переключение серверов на лету
- 📱 Управление устройствами + сброс HWID

🎁 **Бонусная система**
- 🎫 Промокоды на деньги/дни/триал
- 👥 Реферальная программа с комиссиями
- 🔔 Своевременные уведомления

</td>
<td width="50%" valign="top">

### ⚙️ **Для администраторов**

📊 **Мощная аналитика**
- 👥 Детальная статистика пользователей
- 💰 Анализ подписок и платежей
- 🖥️ Мониторинг серверов Remnawave
- 📈 Финансовые отчеты и тренды

👥 **Управление пользователями**
- 🔍 Поиск и редактирование профилей
- 💰 Управление балансами
- 📱 **НОВОЕ**: Изменение лимита устройств (1-X)
- 📊 **НОВОЕ**: Настройка лимитов трафика (0-10000 ГБ)
- 🌍 **НОВОЕ**: Мультивыбор серверов
- 🔄 **НОВОЕ**: Сброс HWID устройств
- 🚫 Блокировка/разблокировка/удаление

🎫 **Промо-система**
- 🎁 Создание промокодов (деньги/дни/длинный триал)
- 📊 Детальная статистика использования
- ⚙️ Полное редактирование промокодов

🖥️ **Умный мониторинг**
- 💚 Состояние Remnawave панели в реальном времени
- 🔄 Автоматическая синхронизация данных
- 🌐 Управление сквадами с актуальным статусом
- 🚧 **Автоматический режим тех. работ**
- 📋 Логи и диагностика

📨 **Коммуникации**
- 📢 Рассылки по сегментам
- 🔔 Автоуведомления о продлении
- 💬 Система поддержки с HTML разметкой
- 📝 Настройка правил сервиса

</td>
</tr>
</table>

---

## 🆕 Недавние обновления

### 🛠️ **Управление серверами**
- **Мультивыбор серверов**: Админы могут назначать пользователям несколько серверов через удобный переключатель
- **Умная логика отображения**: Показывает активные серверы + уже назначенные пользователю неактивные серверы
- **Автоматический пропуск**: При наличии только одного сервера шаг выбора автоматически пропускается
- **Синхронизация в реальном времени**: Все изменения серверов автоматически синхронизируются с Remnawave панелью

### 📱 **Управление устройствами**
- **Изменение лимита устройств**: Админы могут менять количество разрешенных устройств (1-X)
- **Быстрые кнопки**: Предустановленные значения 1, 2, 3, 5, 10 устройств
- **Сброс HWID**: Функция сброса привязанных устройств пользователя
- **Гибкая настройка**: Отдельные лимиты для триал и платных подписок

### 📊 **Управление трафиком**
- **Настройка лимитов трафика**: Возможность установки лимита от 0 ГБ до 10000 ГБ
- **Безлимитный режим**: Опция установки безлимитного трафика (0 ГБ)
- **Быстрые настройки**: Предустановленные значения 50, 100, 500, 1000 ГБ
- **Два режима продажи**: Фиксированный трафик или выбираемые пакеты

---

## 🐳 Docker развертывание

### 📄 docker-compose.yml

```yaml
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
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
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
      - ./logs:/app/logs:rw
      - ./data:/app/data:rw
      - /etc/timezone:/etc/timezone:ro
      - /etc/localtime:/etc/localtime:ro
    ports:
      - "${TRIBUTE_WEBHOOK_PORT:-8081}:8081"
      - "${YOOKASSA_WEBHOOK_PORT:-8082}:8082"
    networks:
      - bot_network
    user: "1000:1000"
    command: >
      bash -c "
        mkdir -p /app/logs /app/data &&
        python main.py
      "

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

### 🚀 Команды управления

```bash
# Быстрый старт
docker compose up -d

# Статус сервисов
docker compose ps

# Логи
docker compose logs -f

# Перезапуск
docker compose restart

# Остановка
docker compose down

# Полная очистка
docker compose down -v --remove-orphans
```

---

## 🔧 Первичная настройка

После запуска необходимо:

1. **📡 Синхронизация серверов** (обязательно!)
   - Зайди в бот → **Админ панель** → **Подписки** → **Управление серверами**
   - Нажми **Синхронизация** и дождись завершения
   - Без этого пользователи не смогут выбирать страны!

2. **👥 Синхронизация пользователей** (если есть база)
   - **Админ панель** → **Remnawave** → **Синхронизация**
   - **Синхронизировать всех** → дождись импорта

3. **💳 Настройка платежных систем**
   - **Telegram Stars**: Работает автоматически
   - **Tribute**: Настрой webhook на `https://your-domain.com/tribute-webhook`
   - **YooKassa**: Настрой webhook на `https://your-domain.com/yookassa-webhook`

---

## 🚀 Производительность

| Пользователей | Память | CPU | Диск | Описание |
|---------------|--------|-----|------|----------|
| **1,000** | 512MB | 1 vCPU | 10GB | ✅ Стартап |
| **10,000** | 2GB | 2 vCPU | 50GB | ✅ Малый бизнес |
| **50,000** | 4GB | 4 vCPU | 100GB | ✅ Средний бизнес |
| **100,000+** | 8GB+ | 8+ vCPU | 200GB+ | 🚀 Enterprise |

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
├── 🎯 main.py                    # Точка входа
├── 📦 requirements.txt           # Зависимости
├── ⚙️ .env.example               # Конфиг
├── ⚙️ app-config.json            # Информация для гайда подключения
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
│   │   ├── 🌟 stars_payment.py   # Start платежи
│   │   ├── common.py         
│   │   ├── 💬 support.py         # Техподдержка
│   │   └── 👑 admin/             # Админ панель
│   │       ├── 📊 statistics.py  # Статистика
│   │       ├── 👥 users.py       # Управление юзерами
│   │       ├── 🎫 promocodes.py  # Управление промокодами
│   │       ├── 🚧 maintenance.py # Тех работы
│   │       ├── 📨 messages.py    # Рассылки
│   │       ├── ⚙️ main.py        # Админское меню
│   │       ├── 📖 rules.py       # Правила
│   │       ├── 🙋 referrals.py   # Правила
│   │       ├── 🌎 servers.py     # Сервера
│   │       ├── 📱 subscriptions.py  # Подписки
│   │       ├── 🔍 monitoring.py  # Мониторинг
│   │       └── 🔗 remnawave.py   # Система RemnaWave
│   │
│   ├── 🗄️ database/                  # База данных
│   │   ├── 📊 models.py              # Модели SQLAlchemy
│   │   ├── 🔗 database.py            # Подключение к БД
│   │   ├── 🔄 universal_migration.py # Миграции
│   │   └── 📝 crud/                  # CRUD операции
│   │       ├── 👤 user.py            # Операции с пользователями
│   │       ├── 📋 subscription.py    # Операции с подписками
│   │       ├── 💰 transaction.py     # Операции с транзакциями
│   │       ├── 📜 rules.py           # Правила сервиса
│   │       ├── 💳 yookassa.py        # YooKassa операции
│   │       ├── 🌐 server_squad.py    # Серверы и сквады
│   │       ├── 🎁 promocode.py       # Промокоды
│   │       └── 👥 referral.py        # Рефералы
│   │
│   ├── 🔧 services/                   # Бизнес-логика
│   │   ├── 👤 user_service.py         # Сервис пользователей
│   │   ├── 📋 subscription_service.py # Сервис подписок
│   │   ├── 💰 payment_service.py      # Платежи
│   │   ├── 🎁 promocode_service.py    # Промокоды
│   │   ├── 🚧 maintenance_service.py  # Промокоды
│   │   ├── 👥 referral_service.py     # Рефералы
│   │   ├── 🔍 monitoring_service.py   # Мониторинг
│   │   ├── 🎖️ tribute_service.py      # Tribute платежи
│   │   ├── 💳 yookassa_service.py     # YooKassa платежи
│   │   └── 🌐 remnawave_service.py    # Интеграция с RemnaWave
│   │
│   ├── 🛠️ utils/                     # Утилиты
│   │   ├── 🎨 decorators.py          # Декораторы
│   │   ├── 📝 formatters.py          # Форматирование данных
│   │   ├── ✅ validators.py          # Валидация
│   │   ├── ✅ subscription_utils.py  # Проверка подписок
│   │   ├── 📄 pagination.py          # Пагинация
│   │   ├── 📄 pricing_utils.py       # Цены
│   │   ├── 👤 user_utils.py          # Утилиты для пользователей
│   │   └── ⚡ cache.py                # Кеширование
│   │
│   ├── 🛡️ middlewares/                # Middleware
│   │   ├── 🔐 auth.py                 # Авторизация
│   │   ├── 📋 logging.py              # Логирование
│   │   ├── 🚧 maintenance.py          # тех работы
│   │   ├── 🔐 subscription_checker.py # тех работы
│   │   └── ⏱️ throttling.py           # Ограничение запросов
│   │
│   ├── 🌐 localization/          # Локализация
│   │   ├── 📝 texts.py           # Тексты интерфейса
│   │   └── 🗣️ languages/
│   │
│   ├── ⌨️ keyboards/             # Клавиатуры
│   │   ├── 🔗 inline.py          # Inline клавиатуры
│   │   ├── 💬 reply.py           # Reply клавиатуры
│   │   └── 👑 admin.py           # Админские клавиатуры
│   │
│   └── 🔌 external/               # Внешние API
│       ├── 🌐 remnawave_api.py    # RemnaWave API
│       ├── ⭐ telegram_stars.py   # Telegram Stars
│       ├── 💳 yookassa_webhook.py # YooKassa webhook
│       ├── 🌐 webhook_server.py   # Webhook сервер
│       └── 🎖️ tribute.py          # Tribute платежи
│
├── 🔄 migrations/                # Миграции БД
└── 📋 logs/                      # Логи системы
```

---

## 🐛 Устранение неполадок

### 🏥 Health Checks
- **Основной**: `http://localhost:8081/health`
- **YooKassa**: `http://localhost:8082/health`
- **Tribute**: `http://localhost:8081/tribute-webhook`

### 🔧 Полезные команды
```bash
# Просмотр логов в реальном времени
docker compose logs -f bot

# Статус всех контейнеров
docker compose ps

# Перезапуск только бота
docker compose restart bot

# Проверка базы данных
docker compose exec postgres pg_isready -U remnawave_user

# Подключение к базе данных
docker compose exec postgres psql -U remnawave_user -d remnawave_bot

# Проверка использования ресурсов
docker stats

# Очистка логов Docker
docker system prune
```

### 🚨 Частые проблемы и решения

| Проблема | Диагностика | Решение |
|----------|-------------|---------|
| **Бот не отвечает** | `docker logs remnawave_bot` | Проверь `BOT_TOKEN` и интернет |
| **Ошибки БД** | `docker compose ps postgres` | Проверь статус PostgreSQL |
| **Webhook не работает** | Проверь порты 8081/8082 | Настрой прокси-сервер правильно |
| **API недоступен** | Проверь логи бота | Проверь `REMNAWAVE_API_URL` и ключ |
| **Мониторинг не работает** | Админ панель → Мониторинг | Проверь `MAINTENANCE_AUTO_ENABLE` |
| **Платежи не проходят** | Проверь webhook'и | Настрой URL в платежных системах |

### 🔧 Настройка webhook'ов

#### 🌐 Через Nginx
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    # Для Tribute
    location /tribute-webhook {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # Для YooKassa
    location /yookassa-webhook {
        proxy_pass http://127.0.0.1:8082;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    # Health check
    location /health {
        proxy_pass http://127.0.0.1:8081/health;
    }
}
```

#### ⚡ Через Caddy
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

---

## 💡 Использование

### 👤 **Для пользователей**

1. **🚀 Старт** → Найди бота и нажми `/start`
2. **📋 Правила** → Прими правила сервиса 
3. **💰 Баланс** → "💰 Баланс" → пополни через Stars/Tribute/YooKassa
4. **🛒 Подписка** → "🛒 Купить подписку" → выбор тарифа → оплата
5. **📱 Управление** → "📋 Мои подписки" → конфигурация → получение ссылки
6. **👥 Рефералы** → "👥 Рефералы" → поделись ссылкой

### ⚙️ **Для администраторов**

Доступ через **"⚙️ Админ панель"**:

- **📦 Подписки** → настройка серверов, цен, синхронизация
- **👥 Пользователи** → поиск, редактирование, блокировка, управление устройствами
- **🎁 Промокоды** → создание бонусов, статистика применения
- **📨 Рассылки** → уведомления по сегментам с HTML разметкой
- **🖥 Remnawave** → мониторинг панели, синхронизация, диагностика
- **📊 Статистика** → детальная аналитика бизнеса и финансов

---

## 🛡️ Безопасность

### 🔐 Защита панели RemnaWave

Бот поддерживает интеграцию с системой защиты панели через куки-аутентификацию:

```env
# Для защищенных панелей
REMNAWAVE_SECRET_KEY=secret_name:secret_value

# Для панелей eGames скрипта  
REMNAWAVE_SECRET_KEY=XXXXXXX:DDDDDDDD
```

Совместимость с [remnawave-reverse-proxy](https://github.com/eGamesAPI/remnawave-reverse-proxy) для скрытия панели от несанкционированного доступа.

### 🔒 Дополнительные меры безопасности

- **Валидация всех входящих данных**
- **Rate limiting для защиты от спама**  
- **Шифрование чувствительных данных**
- **Автоматическое управление сессиями**
- **Мониторинг подозрительной активности**

---

## 🤝 Как помочь проекту

- 🔍 [**Сообщай о багах**](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues) с подробным описанием
- 💡 [**Предлагай идеи**](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/discussions) для улучшения
- ⭐ **Ставь звезды** проекту - это мотивирует разработку!
- 📢 **Рассказывай друзьям** о проекте
- 💝 **[Поддержи разработку](https://t.me/tribute/app?startapp=duUO)** - помоги проекту расти

---

## 💬 Поддержка и сообщество

### 📞 **Контакты**

- **💬 Telegram:** [@fringg](https://t.me/fringg) - вопросы по разработке (только по делу!)
- **💬 Telegram Group:** [Bedolaga Chat](https://t.me/+wTdMtSWq8YdmZmVi) - общение, вопросы, предложения, баги
- **🐛 Issues:** [GitHub Issues](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues) - баги и предложения

### 📚 **Полезные ресурсы**

- **📖 [Remnawave Docs](https://docs.remna.st)** - документация панели
- **🤖 [Telegram Bot API](https://core.telegram.org/bots/api)** - API ботов  
- **🐳 [Docker Guide](https://docs.docker.com/get-started/)** - обучение Docker
- **🛡️ [Reverse Proxy](https://github.com/eGamesAPI/remnawave-reverse-proxy)** - защита панели

---

## 💝 Благодарности

### 🌟 **Топ спонсоры проекта**

<table align="center">
<tr>
<th>🏆 Место</th>
<th>👤 Спонсор</th>
<th>💰 Сумма</th>
<th>💬 Благодарность</th>
</tr>

<tr>
<td>🥇</td>
<td><strong>Илья (@ispanec_nn)</strong></td>
<td>$30</td>
<td>За веру в проект с самого начала</td>
</tr>

<tr>
<td>🥈</td>
<td><strong>@Legacyyy777</strong></td>
<td>₽2,400</td>
<td>За ценные предложения по улучшению</td>
</tr>

<tr>
<td>🥉</td>
<td><strong>@pilot_737800</strong></td>
<td>₽2,250</td>
<td>За активное тестирование и фидбек</td>
</tr>

<tr>
<td>4</td>
<td><strong>@fso404</strong></td>
<td>₽1,000</td>
<td>За поддержку и доверие</td>
</tr>

<tr>
<td>5</td>
<td><strong>@PhiLin58</strong></td>
<td>₽300</td>
<td>За участие в развитии</td>
</tr>

</table>

### 🌟 **Особая благодарность**

- **Remnawave Team** - за отличную панель и стабильный API
- **Сообщество Bedolaga** - за активное тестирование и обратную связь
- **Всем пользователям** - за доверие и использование бота

---

## 📋 Roadmap

### 🚧 **В разработке**

- 🌎 Вебпанель
- 🌍 **Мультиязычность** - полная поддержка английского языка
- 📊 **Расширенная аналитика** - больше метрик и графиков  
- 🔄 **API для интеграций** - подключение внешних сервисов

---

<div align="center">

## 📄 Лицензия

Проект распространяется под лицензией **MIT**

[📜 Посмотреть лицензию](LICENSE)

---

## 🚀 Начни уже сегодня!

<table align="center">
<tr>
<td align="center">
<h3>🧪 Протестируй бота</h3>
<a href="https://t.me/FringVPN_bot">
<img src="https://img.shields.io/badge/Telegram-Тестовый_бот-blue?style=for-the-badge&logo=telegram" alt="Test Bot">
</a>
</td>
<td align="center">
<h3>💬 Присоединись к сообществу</h3>
<a href="https://t.me/+wTdMtSWq8YdmZmVi">
<img src="https://img.shields.io/badge/Telegram-Bedolaga_Chat-blue?style=for-the-badge&logo=telegram" alt="Community">
</a>
</td>
</tr>
<tr>
<td align="center">
<h3>⭐ Поставь звезду</h3>
<a href="https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot">
<img src="https://img.shields.io/badge/GitHub-Звезда-yellow?style=for-the-badge&logo=github" alt="Star">
</a>
</td>
<td align="center">
<h3>💝 Поддержи проект</h3>
<a href="https://t.me/tribute/app?startapp=duUO">
<img src="https://img.shields.io/badge/Tribute-Донат-green?style=for-the-badge&logo=heart" alt="Donate">
</a>
</td>
</tr>
</table>

---

**Made with ❤️ by [@fringg](https://t.me/fringg)**

</div>
│
