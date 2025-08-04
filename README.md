#Описание

RemnaWave Telegram Bot — это многофункциональный бот для управления подписками(Для каждой подписки возможно назначить свой сквад со своими инбаундами - нововведение Remnawave 2.0.0+), балансом, промокодами, тестовой подпиской и рассылками пользователям через Telegram. 

Бот интегрирован с системой RemnaWave версии 2.0.8

#Основные возможности

Мультиязычный интерфейс (на данный момент русский и английский языки)

Создание и покупка подписок с управлением трафиком, длительностью и ценой

Бесплатная тестовая подписка с ограничениями

Пополнение баланса: 1) Через саппорт в ручную 2) Отправка заявки с суммой админу (С возможность подтвердить/отклонить заявку)

Управление балансом пользователей (пополнение, списание)

Промокоды со скидками и ограничениями по использованию

Полноценная админ-панель с контролем пользователей, созданием подписок(Подтягивает UUID сквада из панели), платежей и статистикой

Рассылка сообщений отдельным пользователям и всем сразу

Интеграция с RemnaWave API для управления подписками и пользователями RemnaWave

История платежей(Не работает, в доработке) и управление платежами (подтверждение, отклонение)


#Требования

Pip

Python 3.8+

PostgreSQL, SQLite или другая поддерживаемая SQL-база данных

Токен Telegram-бота

URL и токен RemnaWave API


#Установка

1) Клонируйте репозиторий:

    git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot
    cd remnawave-bedolaga-telegram-bot

2) Настройте handlers.py! В нем механизм подмены поддомена адреса подписки обязательно заменить на свой! Строка 997+ 

           # Fallback  Замена поддомена у ссылки подписки 
            config = kwargs.get('config')
            if config and 'adminka.' in config.REMNAWAVE_URL:
                base_url = config.REMNAWAVE_URL.replace('adminka.', 'sub.')

3) В remnawave_api строки 187-206 заменить поддомен панели на свой!

            # Формируем правильную ссылку на основе base_url
            # Заменяем adminka на sub в URL
            if 'adminka.' in self.base_url:
                sub_url = self.base_url.replace('adminka.', 'sub.')
            else:
                sub_url = self.base_url
                
            subscription_url = f"{sub_url}/sub/{short_uuid}"
            logger.info(f"Generated subscription URL: {subscription_url}")
            
            return subscription_url

            except Exception as e:
            logger.error(f"Failed to get subscription URL: {e}")
            # Fallback с заменой домена
            if 'adminka.' in self.base_url:
                sub_url = self.base_url.replace('adminka.', 'sub.')
            else:
                sub_url = self.base_url
            return f"{sub_url}/sub/{short_uuid}"

4) Создайте виртуальное окружение и активируйте его:

        suo apt install python3
        python3 -m venv venv
        source venv/bin/activate

5) Установите зависимости:

        pip install -r requirements.txt

6) Создайте файл .env в корне проекта и заполните его необходимыми переменными окружения. Пример:

        BOT_TOKEN=ваш_telegram_bot_token
        REMNAWAVE_URL=https://your-remnawave-url.ru
        REMNAWAVE_MODE=remote/local
        REMNAWAVE_TOKEN=ваш_remnawave_token
        DATABASE_URL=sqlite+aiosqlite:///bot.db
        ADMIN_IDS=123456789,987654321
        DEFAULT_LANGUAGE=ru
        SUPPORT_USERNAME=support
        TRIAL_ENABLED=true
        TRIAL_DURATION_DAYS=3
        TRIAL_TRAFFIC_GB=2
        TRIAL_SQUAD_UUID=19bd5bde-5eea-4368-809c-6ba1ffb93897
        TRIAL_PRICE=0.0

7) Запустите бота:
   
1) Хлебный - создание службы автозапуска, проверка файлов, запуск бота 

       chmod +x run.sh
       ./run.sh

2) Для мужчин (Службу там поднять самому, докерфайл собрать или под скрином развернуть - уже твое дело)

       python main.py
   
#Конфигурация

BOT_TOKEN — токен Telegram бота от BotFather.

REMNAWAVE_URL — URL API RemnaWave.

REMNAWAVE_MODE=remote  

REMNAWAVE_TOKEN — токен доступа к API RemnaWave.

DATABASE_URL — строка подключения к базе данных.

ADMIN_IDS — через запятую Telegram ID администраторов.

SUPPORT_USERNAME — ник поддержки, без @ указывать

Параметры тестовой подписки (включение, длительность, трафик, UUID squad, цена).

TRIAL_ENABLED=true/false

TRIAL_DURATION_DAYS=3 (дни)

TRIAL_TRAFFIC_GB=2 

TRIAL_SQUAD_UUID=(УКазать UUID сквада из панели!)

TRIAL_PRICE=0.0(не трогать)


#Использование

/start

#Структура проекта

main.py — главный файл запуска.

handlers.py — основные обработчики команд и действий пользователя.

admin_handlers.py — обработчики команд и действий администраторов.

database.py — модели и методы работы с базой данных (SQLAlchemy).

remnawave_api.py — интеграция с API RemnaWave.

keyboards.py — генерация клавиатур Telegram.

translations.py — локализация и переводы.

utils.py — вспомогательные функции.

middlewares.py — промежуточные слои для обработки сообщений и запросов.

.env — файл конфигурации с переменными окружения.

requirements.txt — список зависимостей Python.

run.sh — скрипт установки и управления ботом (опционально).

#Администрирование

Для входа в админ-панель используйте кнопку "⚙️ Админ панель" в главном меню, если вы указаны как администратор в ADMIN_IDS.

В админ-панели доступны:

Управление подписками (создание, редактирование, список, удаление, включение/отключение)

Управление пользователями (просмотр списка, баланс)

Управление балансом (пополнение пользователей через tg id)

Управление промокодами (создание, список)

Одобрение или отклонение платежей (Приходит запрос администраторам)

Отправка сообщений пользователям или массовая рассылка

Просмотр краткой статистики

#ToDo

1) Код колхозный и не без вайбкодинга тут обошлось, но будет допиливаться, текущая реализация работает - уже хорошо
2) Подключить различные шлюзы для пополнения баланса 
3) Дописать службу для оповещения об истечении срока подписки и контроля
4) Синхранизацию с Remnawave между пользователями по тг id
5) Полнофункциональную панель упарвления 
