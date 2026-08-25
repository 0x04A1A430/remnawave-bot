<div>
  <img src="https://raw.githubusercontent.com/cy7su/remnawave-bot/main/.github/assets/logo.png">
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

### Telegram bot for the [Remnawave](https://github.com/remnawave/remnawave) panel: VPN subscription sales, user cabinet, 15+ payment systems, referral program and promo codes

#

> [!WARNING]
>
> ### SECURITY
> Before launching, make sure to change the default values in `.env`:
> `POSTGRES_PASSWORD`, `BOT_TOKEN`, payment system keys and webhook secrets.
> The bot warns about insecure defaults at startup — do not ignore these warnings.
>
> Never publish your `.env` file or commit it to the repository.

> [!IMPORTANT]
> All configuration is done through the `.env` file — create one and fill in your values.
> Minimum to start: `BOT_TOKEN` (get it from [@BotFather](https://t.me/BotFather)), `ADMIN_IDS`,
> `REMNAWAVE_API_URL` and `REMNAWAVE_API_KEY` from your Remnawave panel.

## File Overview

- [**`docker-compose.yml`**](../docker-compose.yml) — production stack: bot + PostgreSQL + Redis

- [**`Makefile`**](../Makefile) — management commands:
  - <ins>**`make up`** — start the stack in background</ins>
  - **`make up-follow`** — start with streaming logs
  - **`make down`** — stop and remove containers
  - **`make migrate`** — apply Alembic migrations
  - **`make test`** — run tests (pytest)
  - **`make lint` / `make format` / `make fix`** — ruff checks and auto-fixes

- [**`app/`**](../app) — source code:
  - `handlers/` — Telegram handlers (admin, subscriptions, payments, cabinet)
  - `services/` — business logic: payments, subscriptions, broadcasts, backups
  - `cabinet/` — web user cabinet (FastAPI)
  - `webapi/` — REST API for external integrations
  - `external/` — Remnawave API and payment system clients
  - `database/` — models and CRUD (SQLAlchemy 2.0, async)

- [**`migrations/`**](../migrations) — Alembic migrations

- [**`locales/`**](../locales) — bot interface localization

## Support

Support the project by starring this repository (top right of this page)

Bot on Telegram: [@xilarobot](https://t.me/xilarobot)

## License

This project is licensed under [MIT](./LICENSE)
