<img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 15 13" src="https://github.com/user-attachments/assets/91098622-1bce-4f27-afef-60a3c5b5061f" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 22" src="https://github.com/user-attachments/assets/46b87e75-b420-4ac6-91b9-8c7e9bcffb2a" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 39" src="https://github.com/user-attachments/assets/ca97811f-ca00-4133-a120-1c11f0efa0fc" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 45" src="https://github.com/user-attachments/assets/258e1adb-2c39-4126-82a7-7791b56d42db" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 53" src="https://github.com/user-attachments/assets/073455fc-f42d-4d70-839d-59042add2d94" /><img width="906" height="316" alt="Снимок экрана 2025-08-05 в 03 16 00" src="https://github.com/user-attachments/assets/2034dde8-a48b-4149-a23f-b788aa40e0b1" /><img width="906" height="366" alt="Снимок экрана 2025-08-05 в 03 16 18" src="https://github.com/user-attachments/assets/3a3d1e0a-92fc-4573-a48c-f36481d6d0de" /><img width="906" height="842" alt="Снимок экрана 2025-08-05 в 03 17 24" src="https://github.com/user-attachments/assets/8b407f69-6861-4810-822e-c3f7b8f63629" /><img width="906" height="274" alt="Снимок экрана 2025-08-05 в 03 17 43" src="https://github.com/user-attachments/assets/923a945a-5ef8-4dcb-9804-fffc37ab8887" /><img width="936" height="364" alt="Снимок экрана 2025-08-05 в 03 20 03" src="https://github.com/user-attachments/assets/1faecdfe-f80c-4ac2-ad38-81a30fc6623d" />

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

Сервис контроля истечения сроков действия подписки(Уведомляет об истечении за указанный в настройках срок), уведомления с предложением продления подписи. (NEW)

Интеграция с RemnaWave API для управления подписками и пользователями RemnaWave

История платежей(Не работает, в доработке) и управление платежами (подтверждение, отклонение)


#Требования

Pip

Python 3.8+

PostgreSQL, SQLite или другая поддерживаемая SQL-база данных

Токен Telegram-бота

URL и токен RemnaWave API

Ссылки на подписку из ремны формата SUB_PUBLIC_DOMAIN=sub.example.com/sub

#Установка

1) Клонируйте репозиторий:

    git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot
    cd remnawave-bedolaga-telegram-bot

2) Установите python3 python pip

        sudo apt install pip
        sudo apt install python3

3) Установите зависимости:

        pip install -r requirements.txt

4) Создайте файл .env в корне проекта и заполните его необходимыми переменными окружения. Пример:

        BOT_TOKEN=ваш_telegram_bot_token
        REMNAWAVE_URL=https://your-remnawave-url.ru
        REMNAWAVE_MODE=remote/local
        REMNAWAVE_TOKEN=ваш_remnawave_token
        SUBSCRIPTION_BASE_URL=https://
        DATABASE_URL=sqlite+aiosqlite:///bot.db
        ADMIN_IDS=123456789,987654321
        DEFAULT_LANGUAGE=ru
        SUPPORT_USERNAME=support
        TRIAL_ENABLED=true
        TRIAL_DURATION_DAYS=3
        TRIAL_TRAFFIC_GB=2
        TRIAL_SQUAD_UUID=19bd5bde-5eea-4368-809c-6ba1ffb93897
        TRIAL_PRICE=0.0

5) Запустите бота:
   
1) Хлебный - создание службы автозапуска, проверка файлов, запуск бота 

       chmod +x run.sh
       ./run.sh

Если создали службу через скрипт, то запустить бота можно командой

            sudo systemctl start remnawave-bot

Выключить

        sudo systemctl stop remnawave-bot

3) Для мужчин (Службу там поднять самому, докерфайл собрать или под скрином развернуть - уже твое дело)

       python main.py
   
#Конфигурация

BOT_TOKEN — токен Telegram бота от BotFather.

REMNAWAVE_URL — URL API RemnaWave.

REMNAWAVE_MODE=remote  

REMNAWAVE_TOKEN — токен доступа к API RemnaWave.

DATABASE_URL — строка подключения к базе данных.

SUBSCRIPTION_BASE_URL=https://sub.example.com (без / на конце)

ADMIN_IDS — через запятую Telegram ID администраторов.

SUPPORT_USERNAME — ник поддержки, без @ указывать

Параметры тестовой подписки (включение, длительность, трафик, UUID squad, цена).

TRIAL_ENABLED=true/false

TRIAL_DURATION_DAYS=3 (дни)

TRIAL_TRAFFIC_GB=2 

TRIAL_SQUAD_UUID=(УКазать UUID сквада из панели!)

TRIAL_PRICE=0.0(не трогать)

Monitor Service Settings (дополнительные настройки)

MONITOR_CHECK_INTERVAL=3600 (Запуск службы проверки)

MONITOR_DAILY_CHECK_HOUR=10 (Разовый чек в определенный промежуток дня)

MONITOR_WARNING_DAYS=2 (За сколько дней слать уведомления)


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

subscription_monitor.py - сервис мониторинга сроков истечения подписок 

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

Мониторинг подписок (Проверка статуса службы, принудитедьный запуск, деактивация истекщих подписок(на случай падения базы), персональный тест(можно отправить уведомления юзеру по tg id)

Просмотр краткой статистики

#ToDo

1) Код колхозный и не без вайбкодинга тут обошлось, но будет допиливаться, текущая реализация работает - уже хорошо
2) Подключить различные шлюзы для пополнения баланса 
3) Дописать службу для оповещения об истечении срока подписки и контроля
4) Синхранизацию с Remnawave между пользователями по тг id
5) Полнофункциональную панель упарвления 
