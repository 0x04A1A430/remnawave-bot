<div>
  <img src="./.github/assets/logo.png" width="200" height="200" align="left">
  <h1>
   <p align="left">xilarobot</p>
  <a href="https://www.github.com/cy7su/remnawave-bot/releases/"><img alt="downloads"
    src="https://img.shields.io/github/downloads/cy7su/remnawave-bot/total?labelColor=161616&color=242424" height="21" align="left"/></a>
  </a>
  <br>
  <a href="https://www.github.com/cy7su/remnawave-bot/releases/latest"><img alt="downloads@latest"
    src="https://img.shields.io/github/downloads/cy7su/remnawave-bot/latest/total?labelColor=161616&color=242424" height="21" align="left"/></a>
  </a>
  <br>
  <a href="https://www.github.com/cy7su/remnawave-bot/releases/"><img alt="release"
    src="https://img.shields.io/github/v/release/cy7su/remnawave-bot?labelColor=161616&color=242424" height="21" align="left"/></a>
  </a>
  <br>
  <a href="https://www.github.com/cy7su/remnawave-bot/tree/main"><img alt="code size"
    src="https://img.shields.io/github/languages/code-size/cy7su/remnawave-bot?labelColor=161616&color=242424" height="21" align="left"/></a>
  </a>
  <br>
</div>

### Telegram-бот для панели [Remnawave](https://github.com/remnawave/remnawave): продажа VPN-подписок, личный кабинет, приём платежей через 15+ платёжных систем, реферальная программа и промокоды

#

> [!WARNING]
>
> ### БЕЗОПАСНОСТЬ
> Перед запуском обязательно смените значения по умолчанию в `.env`:
> `POSTGRES_PASSWORD`, `BOT_TOKEN`, ключи платёжных систем и секреты вебхуков.
> Бот при старте предупреждает об небезопасных дефолтах — не игнорируйте эти предупреждения.
>
> Никогда не публикуйте `.env` и не коммитьте его в репозиторий.

> [!IMPORTANT]
> Все настройки выполняются через файл [`.env`](./.env.example) — скопируйте его и заполните своими значениями.
> Минимум для старта: `BOT_TOKEN` (получите у [@BotFather](https://t.me/BotFather)), `ADMIN_IDS`,
> `REMNAWAVE_API_URL` и `REMNAWAVE_API_KEY` из панели Remnawave.

## Краткие описания файлов

- [**`docker-compose.yml`**](./docker-compose.yml) — продакшен-стек: бот + PostgreSQL + Redis

- [**`docker-compose.local.yml`**](./docker-compose.local.yml) — локальная разработка с монтированием исходников

- [**`Makefile`**](./Makefile) — команды управления:
  - <ins>**`make up`** — запуск стека в фоне</ins>
  - **`make up-follow`** — запуск с потоковым выводом логов
  - **`make down`** — остановка и удаление контейнеров
  - **`make migrate`** — применение миграций Alembic
  - **`make test`** — запуск тестов (pytest)
  - **`make lint` / `make format` / `make fix`** — проверки и автоисправления ruff

- [**`app/`**](./app) — исходный код:
  - `handlers/` — хендлеры Telegram (админка, подписки, платежи, кабинет)
  - `services/` — бизнес-логика: платежи, подписки, рассылки, резервные копии
  - `cabinet/` — веб-личный кабинет пользователя (FastAPI)
  - `webapi/` — REST API для внешних интеграций
  - `external/` — клиенты Remnawave API и платёжных систем
  - `database/` — модели и CRUD (SQLAlchemy 2.0, async)

- [**`migrations/`**](./migrations) — миграции Alembic

- [**`locales/`**](./locales) — локализация интерфейса бота

## Поддержка проекта

Вы можете поддержать проект, поставив :star: этому репозиторию (сверху справа страницы)

Бот в Telegram: [@xilarobot](https://t.me/xilarobot)

## Лицензия

Проект распространяется под лицензией [MIT](./LICENSE)
