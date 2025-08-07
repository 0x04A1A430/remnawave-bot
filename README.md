<img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 15 13" src="https://github.com/user-attachments/assets/91098622-1bce-4f27-afef-60a3c5b5061f" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 22" src="https://github.com/user-attachments/assets/46b87e75-b420-4ac6-91b9-8c7e9bcffb2a" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 39" src="https://github.com/user-attachments/assets/ca97811f-ca00-4133-a120-1c11f0efa0fc" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 45" src="https://github.com/user-attachments/assets/258e1adb-2c39-4126-82a7-7791b56d42db" /><img width="906" height="496" alt="Снимок экрана 2025-08-05 в 03 14 53" src="https://github.com/user-attachments/assets/073455fc-f42d-4d70-839d-59042add2d94" /><img width="906" height="316" alt="Снимок экрана 2025-08-05 в 03 16 00" src="https://github.com/user-attachments/assets/2034dde8-a48b-4149-a23f-b788aa40e0b1" /><img width="894" height="317" alt="Снимок экрана 2025-08-05 в 15 32 32" src="https://github.com/user-attachments/assets/a96337cf-f58a-488e-9600-c94a92bdbfc2" /><img width="906" height="366" alt="Снимок экрана 2025-08-05 в 03 16 18" src="https://github.com/user-attachments/assets/3a3d1e0a-92fc-4573-a48c-f36481d6d0de" /><img width="906" height="842" alt="Снимок экрана 2025-08-05 в 03 17 24" src="https://github.com/user-attachments/assets/8b407f69-6861-4810-822e-c3f7b8f63629" /><img width="906" height="274" alt="Снимок экрана 2025-08-05 в 03 17 43" src="https://github.com/user-attachments/assets/923a945a-5ef8-4dcb-9804-fffc37ab8887" /><img width="936" height="364" alt="Снимок экрана 2025-08-05 в 03 20 03" src="https://github.com/user-attachments/assets/1faecdfe-f80c-4ac2-ad38-81a30fc6623d" /><img width="892" height="486" alt="Снимок экрана 2025-08-07 в 07 43 47" src="https://github.com/user-attachments/assets/0dd6cb8e-fd2f-4a98-8920-aadceee09fd0" /><img width="892" height="762" alt="Снимок экрана 2025-08-07 в 07 44 20" src="https://github.com/user-attachments/assets/d7c95e3e-cf04-40bc-9422-d7289447625d" /><img width="892" height="823" alt="Снимок экрана 2025-08-07 в 07 46 45" src="https://github.com/user-attachments/assets/9ab2c378-0abc-447d-9e95-a3ab8dab2f18" />
<img width="892" height="501" alt="Снимок экрана 2025-08-07 в 07 42 07" src="https://github.com/user-attachments/assets/839c02da-4461-4127-894a-772e66175e23" /><img width="892" height="805" alt="Снимок экрана 2025-08-07 в 07 57 01" src="https://github.com/user-attachments/assets/bc35f79d-0b0d-4c81-8623-696b708642ad" /><img width="892" height="834" alt="Снимок экрана 2025-08-07 в 07 41 09" src="https://github.com/user-attachments/assets/d4731a79-0171-4254-aa78-2e4c7305829b" /><img width="631" height="606" alt="Снимок экрана 2025-08-06 в 18 48 31" src="https://github.com/user-attachments/assets/c44548b0-f27b-4f67-b3c0-2c002ae33979" />



#Описание

RemnaWave Bedolaga Telegram Bot — это многофункциональный бот для управления подписками(Для каждой подписки возможно назначить свой сквад со своими инбаундами - нововведение Remnawave 2.0.0+), балансом, промокодами, тестовой подпиской и рассылками пользователям через Telegram. 

Бот интегрирован с системой RemnaWave версии 2.0.8

#Основные возможности

Мультиязычный интерфейс (на данный момент русский и английский языки)

Создание и покупка подписок с управлением трафиком, длительностью и ценой

Бесплатная тестовая подписка с заданными ограничениями(срок, лимит трафика, назначение сквада)

Пополнение баланса: 1) Через саппорт в ручную 2) Отправка заявки с суммой админу (С возможность подтвердить/отклонить заявку)

Управление балансом пользователей (пополнение, списание)

Промокоды со скидками и ограничениями по использованию

Полноценная админ-панель с контролем пользователей, созданием подписок(Подтягивает UUID сквада из панели), платежей и статистикой

Рассылка сообщений отдельным пользователям и всем сразу

Сервис контроля истечения сроков действия подписки(Уведомляет об истечении за указанный в настройках срок), уведомления с предложением продления подписи. (NEW)

Интеграция с RemnaWave API для управления подписками и пользователями RemnaWave

Полная синхранизация Remnawave <--> Bot - Перенос подписок из панели Remnawave в бот по Telegram id

Управление системой Remnawave (NEW)

Управление платежами (подтверждение, отклонение) + История платежей(Все действия с балансом и подписками в постраничной истории)


#Требования

Pip

Python 3.8+

PostgreSQL, SQLite или другая поддерживаемая SQL-база данных

Токен Telegram-бота

URL и токен RemnaWave API

Ссылки на подписку из ремны формата SUB_PUBLIC_DOMAIN=sub.example.com/sub

#Установка

1. Клонируйте репозиторий:

    git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot
    cd remnawave-bedolaga-telegram-bot

2. Создайте файл .env в корне проекта и заполните его необходимыми переменными окружения. Пример:

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
        MONITOR_CHECK_INTERVAL=3600
        MONITOR_DAILY_CHECK_HOUR=10
        MONITOR_WARNING_DAYS=2

  
4. Соберите образ (Makefile Dockerfile docker-compose):

       make build

5. Запуск:

Запуск минимальной конфигурации (бот + база данных):
    
    make up-min

Или запуск с Redis:
    
    make up

Или запуск со всеми сервисами включая Nginx:
    
    make up-full

5. Управление

Просмотр логов:

       make logs-bot

Статус сервисов:
    
        make status

Перезапуск:
    
        make restart

Остановка:
    
        make down
   
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

/start - запуск

#Синхронизация подписок 

Вы можете перенести свои существующие подписки из панели Remnawave прямо в бота всего одним кликом.
Для этого в админ панеле реализован соостветствующий пункт: Админ панель - Система Remnawave - Синхронизация с Remnawave - Импорт всех по Telegram ID. После нажатия подтянет всех пользователей в бота, подпискам из панели будет назначено имя "Старая подписка" - такую подписку невозможно продлить. 

ДОПОЛНИТЕЛЬНО:
Реализована возможность зачистки импортированных из панели подписок по тг айди Админ панель - Система Remnawave - Синхронизация с Remnawave - Просмотрт планов - Удалалить импортированные

Остальное трогать без понимания кода - не рекомендую.

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

Просмотр статистики

Управление систеой Remnawave (Ноды, пользователи, синхронизация и импорт подписок из базы Remnawave в бот) 

#ToDo

Код колхозный и не без вайбкодинга тут обошлось, но будет допиливаться, текущая реализация работает - уже хорошо
1) Дописать службу для оповещения об истечении срока подписки и контроля - Done v1.1.0
2) Подключить различные шлюзы для пополнения баланса 
3) Синхранизацию с Remnawave между пользователями по тг id
4) Полнофункциональную панель упарвления
5) Добавить возможность удаление промокодов - In progress 
6) Доработать алгоритм удаления подписок ибо удаление(А НЕ деактивация) сейчас - скроект эту подписку у всех юзеров которые ее купили, так что удаляйте на свой страх и риск я предупредил) - In progress 
8) Отправка уведомлений административных в другие чаты-топики
9) Рефка (как по мне беспонтовая штука, сервера нормальные хостите, сервис нормальный делайте и будут клиенты - не ебите мозги, но если будет не лень, то допилю)
