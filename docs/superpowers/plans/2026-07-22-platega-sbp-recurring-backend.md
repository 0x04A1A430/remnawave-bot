# Platega SBP Recurring — Backend & Bot Implementation Plan (Plan A)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Platega SBP recurring auto-renewal to the bot backend — subscriptions charge on the tariff's cadence and extend the VPN subscription directly via callbacks, plus cancel-on-delete hooks and cabinet backend routes.

**Architecture:** Platega owns the charge schedule (day/week/month/year). We create a Platega subscription bound to a user's tariff, store it in a new `platega_subscriptions` table, and on each charge-callback extend the subscription directly (bypassing balance). Cancel is idempotent and fires from every subscription-deletion path. Mutually exclusive with balance-autopay per subscription.

**Tech Stack:** Python 3.13, aiogram 3.x, SQLAlchemy 2.x async, FastAPI, Alembic, aiohttp, pytest (project `.venv/bin/python -m pytest`), ruff.

**Spec:** `docs/superpowers/specs/2026-07-22-platega-sbp-recurring-design.md`

## Global Constraints

- Feature gated by `settings.is_platega_recurrent_enabled()` (requires `PLATEGA_ENABLED` + merchant/secret + `PLATEGA_RECURRENT_ENABLED`). Default OFF.
- Platega `interval` values: `1`=day, `2`=week, `3`=month, `4`=year (count always 1). Subscription create uses `paymentMethod: 6`.
- Amounts stored in **kopeks** internally; Platega API takes **rubles** (`round(kopeks/100, 2)`, integer if no fractional part).
- Callback bodies are **PascalCase**: `Id`, `Amount`, `Currency`, `Status`, `PaymentMethod`, `Payload`, `SubscriptionId`, `NextChargeAt`.
- Charge callback `Status` ∈ `CONFIRMED` / `CANCELED`. Status callback `Status` ∈ `SUBSCRIPTION_ACTIVATED` / `SUBSCRIPTION_PAST_DUE` / `SUBSCRIPTION_CANCELLED` / `SUBSCRIPTION_FAILED`. Always respond HTTP 200.
- Webhook auth = header `compare_digest` on `X-MerchantId`/`X-Secret` (no HMAC). Reuse existing.
- Datetimes are timezone-aware UTC (`datetime.now(UTC)`, `AwareDateTime()` columns).
- Run tests with `.venv/bin/python -m pytest` (project venv has all deps). Lint: `.venv/bin/python -m ruff check` and `ruff format`.
- Commit conventions: `feat(platega): …` / `test(platega): …`. No Claude attribution in commits.
- Work on branch `feat/platega-sbp-recurring` (already created).

## File Structure

**Create:**
- `app/services/platega_recurrent.py` — pure cadence logic (`resolve_platega_interval`) + status constants.
- `app/database/crud/platega_subscription.py` — CRUD for `platega_subscriptions`.
- `app/cabinet/routes/subscription_modules/platega_recurrent.py` — cabinet user endpoints.
- `migrations/alembic/versions/<rev>_add_platega_subscriptions.py` — table migration.
- Tests: `tests/services/test_platega_recurrent_logic.py`, `tests/services/test_platega_subscription_crud.py`, `tests/services/test_platega_subscription_service.py`, `tests/services/test_platega_subscription_callbacks.py`, `tests/services/test_platega_recurrent_cancel_hooks.py`, `tests/webserver/test_platega_subscription_webhook.py`.

**Modify:**
- `app/database/models.py` — add `PlategaSubscription` model (after `PlategaPayment`, ~:685).
- `app/services/platega_service.py` — add `create_subscription` / `get_subscription` / `list_subscriptions` / `cancel_subscription`.
- `app/services/payment/platega.py` — add subscription mixin methods.
- `app/config.py` — `PLATEGA_RECURRENT_ENABLED` + `is_platega_recurrent_enabled()`.
- `.env.example` — recurrent block.
- `app/webserver/payments.py` — branch subscription callbacks in the Platega block (~:700).
- `app/services/monitoring_service.py` — reconciler in `_monitoring_cycle`.
- Deletion entry points: `app/handlers/admin/users.py`, `app/cabinet/routes/subscription_modules/multi_tariff.py`, `app/cabinet/routes/admin_users.py`, `app/services/subscription_service.py`.
- `app/handlers/subscription/autopay.py` — bot UI flow.
- `app/cabinet/routes/admin_users.py` — `UserSubscriptionInfo` fields + cancel endpoint.

---

## Phase 1 — Data & pure logic

### Task 1: Cadence resolver (`resolve_platega_interval`)

**Files:**
- Create: `app/services/platega_recurrent.py`
- Test: `tests/services/test_platega_recurrent_logic.py`

**Interfaces:**
- Produces: `resolve_platega_interval(period_days: int, is_daily: bool) -> tuple[int, int]` returning `(interval, charge_days)` where `interval ∈ {1,2,3,4}`. Also module constants `PLATEGA_SUBSCRIPTION_METHOD = 6`, and status string sets `CHARGE_SUCCESS = {'CONFIRMED'}`, `CHARGE_FAILED = {'CANCELED'}`, `SUB_ACTIVATED = 'SUBSCRIPTION_ACTIVATED'`, `SUB_PAST_DUE = 'SUBSCRIPTION_PAST_DUE'`, `SUB_CANCELLED = 'SUBSCRIPTION_CANCELLED'`, `SUB_FAILED = 'SUBSCRIPTION_FAILED'`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_platega_recurrent_logic.py
import pytest

from app.services.platega_recurrent import resolve_platega_interval


@pytest.mark.parametrize(
    ('period_days', 'is_daily', 'expected'),
    [
        (1, True, (1, 1)),      # daily tariff -> day
        (30, False, (3, 30)),   # exact month
        (7, False, (2, 7)),     # week
        (360, False, (4, 360)), # year
        (365, False, (4, 365)), # yearly range 350-380
        (31, False, (3, 31)),   # monthly range 28-31
        (14, False, (3, 30)),   # non-mapping -> month @ 30
        (60, False, (3, 30)),
        (90, False, (3, 30)),
        (180, False, (3, 30)),
    ],
)
def test_resolve_platega_interval(period_days, is_daily, expected):
    assert resolve_platega_interval(period_days, is_daily) == expected


def test_is_daily_wins_over_period_days():
    # a daily tariff is always daily regardless of a stray period value
    assert resolve_platega_interval(30, True) == (1, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/services/test_platega_recurrent_logic.py -q`
Expected: FAIL — `ModuleNotFoundError: app.services.platega_recurrent`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/platega_recurrent.py
"""Чистая логика рекуррентных СБП-подписок Platega (без сети и БД)."""

from __future__ import annotations

# Platega paymentMethod для подписки
PLATEGA_SUBSCRIPTION_METHOD = 6

# interval: 1=day, 2=week, 3=month, 4=year
INTERVAL_DAY = 1
INTERVAL_WEEK = 2
INTERVAL_MONTH = 3
INTERVAL_YEAR = 4

# Статусы коллбеков
CHARGE_SUCCESS = {'CONFIRMED'}
CHARGE_FAILED = {'CANCELED'}
SUB_ACTIVATED = 'SUBSCRIPTION_ACTIVATED'
SUB_PAST_DUE = 'SUBSCRIPTION_PAST_DUE'
SUB_CANCELLED = 'SUBSCRIPTION_CANCELLED'
SUB_FAILED = 'SUBSCRIPTION_FAILED'


def resolve_platega_interval(period_days: int, is_daily: bool) -> tuple[int, int]:
    """Возвращает (interval, charge_days) для подписки Platega.

    Platega умеет только day/week/month/year (count=1). Каденс выводится из
    числа дней тарифа; неровные периоды приклеиваются к месяцу по 30-дневной
    цене (см. спеку §3). charge_days задаёт и сумму, и шаг продления.
    """
    if is_daily:
        return INTERVAL_DAY, 1
    if period_days == 7:
        return INTERVAL_WEEK, 7
    if 28 <= period_days <= 31:
        return INTERVAL_MONTH, period_days
    if 350 <= period_days <= 380:
        return INTERVAL_YEAR, period_days
    return INTERVAL_MONTH, 30
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/services/test_platega_recurrent_logic.py -q`
Expected: PASS (12 cases).

- [ ] **Step 5: Commit**

```bash
git add app/services/platega_recurrent.py tests/services/test_platega_recurrent_logic.py
git commit -m "feat(platega): чистая логика каденса рекуррентных СБП-подписок"
```

### Task 2: `PlategaSubscription` model + Alembic migration

**Files:**
- Modify: `app/database/models.py` (after `PlategaPayment`, ~:685)
- Create: `migrations/alembic/versions/<rev>_add_platega_subscriptions.py`
- Test: `tests/services/test_platega_subscription_crud.py` (import-only smoke here; full CRUD in Task 3)

**Interfaces:**
- Produces: `PlategaSubscription` ORM model, table `platega_subscriptions`, columns per spec §5. Status literals used across tasks: `'PENDING' | 'ACTIVE' | 'PAST_DUE' | 'CANCELLED' | 'FAILED'`.

- [ ] **Step 1: Add the model**

Insert after `PlategaPayment` (`app/database/models.py:685`):

```python
class PlategaSubscription(Base):
    __tablename__ = 'platega_subscriptions'

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False, index=True)
    tariff_id = Column(Integer, ForeignKey('tariffs.id'), nullable=True)

    platega_subscription_id = Column(String(255), unique=True, nullable=True, index=True)
    interval = Column(Integer, nullable=False)  # 1=day,2=week,3=month,4=year
    charge_days = Column(Integer, nullable=False)  # шаг продления за одно списание
    amount_kopeks = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default='RUB')

    status = Column(String(20), nullable=False, default='PENDING')  # PENDING/ACTIVE/PAST_DUE/CANCELLED/FAILED
    redirect_url = Column(Text, nullable=True)
    next_charge_at = Column(AwareDateTime(), nullable=True)
    last_charge_at = Column(AwareDateTime(), nullable=True)
    last_charge_external_id = Column(String(255), nullable=True)  # идемпотентность коллбека по charge Id
    charges_success = Column(Integer, nullable=False, default=0)
    charges_failed = Column(Integer, nullable=False, default=0)

    created_at = Column(AwareDateTime(), default=func.now())
    updated_at = Column(AwareDateTime(), default=func.now(), onupdate=func.now())

    user = relationship('User', backref='platega_subscriptions')
    subscription = relationship('Subscription', backref='platega_subscriptions')

    __table_args__ = (Index('ix_platega_subscriptions_user_active', 'user_id', 'status'),)

    @property
    def amount_rubles(self) -> float:
        return self.amount_kopeks / 100
```

Verify `Index` is imported at the top of `models.py` (it is used elsewhere; if not, add to the sqlalchemy import).

- [ ] **Step 2: Create the migration**

Find the current head: `.venv/bin/python -m alembic -c migrations/alembic.ini heads` (or `make migrate-history`). Create `migrations/alembic/versions/<newrev>_add_platega_subscriptions.py` with `down_revision = '<current head>'`:

```python
"""add platega_subscriptions

Revision ID: <newrev>
Revises: <current head>
"""
from alembic import op
import sqlalchemy as sa

revision = '<newrev>'
down_revision = '<current head>'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'platega_subscriptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('subscription_id', sa.Integer(), sa.ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('tariff_id', sa.Integer(), sa.ForeignKey('tariffs.id'), nullable=True),
        sa.Column('platega_subscription_id', sa.String(length=255), nullable=True),
        sa.Column('interval', sa.Integer(), nullable=False),
        sa.Column('charge_days', sa.Integer(), nullable=False),
        sa.Column('amount_kopeks', sa.Integer(), nullable=False),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='RUB'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='PENDING'),
        sa.Column('redirect_url', sa.Text(), nullable=True),
        sa.Column('next_charge_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_charge_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_charge_external_id', sa.String(length=255), nullable=True),
        sa.Column('charges_success', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('charges_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_platega_subscriptions_user_id', 'platega_subscriptions', ['user_id'])
    op.create_index('ix_platega_subscriptions_subscription_id', 'platega_subscriptions', ['subscription_id'])
    op.create_unique_constraint('uq_platega_subscriptions_platega_id', 'platega_subscriptions', ['platega_subscription_id'])
    op.create_index('ix_platega_subscriptions_user_active', 'platega_subscriptions', ['user_id', 'status'])


def downgrade() -> None:
    op.drop_table('platega_subscriptions')
```

- [ ] **Step 3: Smoke test the model import + table name**

```python
# tests/services/test_platega_subscription_crud.py
from app.database.models import PlategaSubscription


def test_model_table_and_columns():
    assert PlategaSubscription.__tablename__ == 'platega_subscriptions'
    cols = set(PlategaSubscription.__table__.columns.keys())
    assert {'platega_subscription_id', 'interval', 'charge_days', 'amount_kopeks', 'status'} <= cols
```

Run: `.venv/bin/python -m pytest tests/services/test_platega_subscription_crud.py -q` → PASS.

- [ ] **Step 4: Verify migration applies on a scratch DB**

Run (against the test Postgres, per project `make migrate` conventions): `.venv/bin/python -m alembic -c migrations/alembic.ini upgrade head` then `downgrade -1` then `upgrade head`. Expected: no errors, table created/dropped/recreated.

- [ ] **Step 5: Commit**

```bash
git add app/database/models.py migrations/alembic/versions/ tests/services/test_platega_subscription_crud.py
git commit -m "feat(platega): модель platega_subscriptions + миграция"
```

### Task 3: CRUD `platega_subscription.py`

**Files:**
- Create: `app/database/crud/platega_subscription.py`
- Test: `tests/services/test_platega_subscription_crud.py` (extend)

**Interfaces:**
- Produces (all `async`, first arg `db: AsyncSession`):
  - `create_platega_subscription(db, *, user_id, subscription_id, tariff_id, interval, charge_days, amount_kopeks, redirect_url, platega_subscription_id, status='PENDING') -> PlategaSubscription`
  - `get_platega_subscription_by_id(db, sub_id: int) -> PlategaSubscription | None`
  - `get_platega_subscription_by_id_for_update(db, sub_id: int) -> PlategaSubscription | None` (`.with_for_update()`)
  - `get_platega_subscription_by_platega_id(db, platega_id: str) -> PlategaSubscription | None`
  - `get_active_platega_subscription_by_subscription(db, subscription_id: int) -> PlategaSubscription | None` (status in ACTIVE/PENDING/PAST_DUE)
  - `update_platega_subscription(db, sub, **fields) -> PlategaSubscription`
  - `list_platega_subscriptions_by_statuses(db, statuses: list[str]) -> list[PlategaSubscription]`

- [ ] **Step 1: Write the failing test** (append to `test_platega_subscription_crud.py`)

```python
import pytest

from app.database.crud import platega_subscription as crud


@pytest.mark.asyncio
async def test_create_and_fetch(db_session, sample_user, sample_subscription):
    created = await crud.create_platega_subscription(
        db_session,
        user_id=sample_user.id,
        subscription_id=sample_subscription.id,
        tariff_id=None,
        interval=3,
        charge_days=30,
        amount_kopeks=19900,
        redirect_url='https://pay.platega.io/s/1',
        platega_subscription_id='sub-1',
    )
    assert created.status == 'PENDING'

    by_platega = await crud.get_platega_subscription_by_platega_id(db_session, 'sub-1')
    assert by_platega is not None and by_platega.id == created.id

    active = await crud.get_active_platega_subscription_by_subscription(db_session, sample_subscription.id)
    assert active is not None and active.id == created.id

    updated = await crud.update_platega_subscription(db_session, created, status='CANCELLED')
    assert updated.status == 'CANCELLED'
    assert await crud.get_active_platega_subscription_by_subscription(db_session, sample_subscription.id) is None
```

Reuse existing DB fixtures. Inspect `tests/conftest.py` for the real fixture names (`db_session`, a user factory, a subscription factory); adapt `sample_user`/`sample_subscription` to whatever the repo provides. If no subscription factory exists, create the `Subscription` inline in the test.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/services/test_platega_subscription_crud.py -q`
Expected: FAIL — `ModuleNotFoundError: app.database.crud.platega_subscription`.

- [ ] **Step 3: Implement the CRUD** (mirror `app/database/crud/platega.py` style)

```python
# app/database/crud/platega_subscription.py
"""CRUD для рекуррентных СБП-подписок Platega."""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PlategaSubscription


logger = structlog.get_logger(__name__)

_ACTIVE_STATUSES = ('PENDING', 'ACTIVE', 'PAST_DUE')


async def create_platega_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    subscription_id: int,
    tariff_id: int | None,
    interval: int,
    charge_days: int,
    amount_kopeks: int,
    redirect_url: str | None,
    platega_subscription_id: str | None,
    status: str = 'PENDING',
) -> PlategaSubscription:
    record = PlategaSubscription(
        user_id=user_id,
        subscription_id=subscription_id,
        tariff_id=tariff_id,
        interval=interval,
        charge_days=charge_days,
        amount_kopeks=amount_kopeks,
        redirect_url=redirect_url,
        platega_subscription_id=platega_subscription_id,
        status=status,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    logger.info('Создана Platega-подписка', platega_subscription_id=platega_subscription_id, user_id=user_id)
    return record


async def get_platega_subscription_by_id(db: AsyncSession, sub_id: int) -> PlategaSubscription | None:
    return await db.get(PlategaSubscription, sub_id)


async def get_platega_subscription_by_id_for_update(db: AsyncSession, sub_id: int) -> PlategaSubscription | None:
    result = await db.execute(
        select(PlategaSubscription).where(PlategaSubscription.id == sub_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def get_platega_subscription_by_platega_id(db: AsyncSession, platega_id: str) -> PlategaSubscription | None:
    result = await db.execute(
        select(PlategaSubscription).where(PlategaSubscription.platega_subscription_id == platega_id)
    )
    return result.scalar_one_or_none()


async def get_active_platega_subscription_by_subscription(
    db: AsyncSession, subscription_id: int
) -> PlategaSubscription | None:
    result = await db.execute(
        select(PlategaSubscription)
        .where(
            PlategaSubscription.subscription_id == subscription_id,
            PlategaSubscription.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(PlategaSubscription.id.desc())
    )
    return result.scalars().first()


async def update_platega_subscription(db: AsyncSession, record: PlategaSubscription, **fields: Any) -> PlategaSubscription:
    for key, value in fields.items():
        setattr(record, key, value)
    await db.commit()
    await db.refresh(record)
    return record


async def list_platega_subscriptions_by_statuses(db: AsyncSession, statuses: list[str]) -> list[PlategaSubscription]:
    result = await db.execute(select(PlategaSubscription).where(PlategaSubscription.status.in_(statuses)))
    return list(result.scalars().all())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/services/test_platega_subscription_crud.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/database/crud/platega_subscription.py tests/services/test_platega_subscription_crud.py
git commit -m "feat(platega): CRUD рекуррентных подписок"
```

---

## Phase 2 — Platega service methods & mixin

### Task 4: `PlategaService` subscription methods

**Files:**
- Modify: `app/services/platega_service.py`
- Test: `tests/services/test_platega_subscription_service.py`

**Interfaces:**
- Produces (methods on `PlategaService`):
  - `create_subscription(self, *, amount: float, currency: str, interval: int, description: str | None = None) -> dict` → `POST` `/transaction/process` (v2 prefix if `api_version=='v2'`). Body: `{'paymentMethod': 6, 'paymentDetails': {'amount': self._format_amount(amount), 'currency': currency, 'interval': interval}, 'description': ...}`. Returns parsed JSON (`transactionId`, `redirect`, `status`).
  - `get_subscription(self, subscription_id: str) -> dict` → `GET /subscription/{id}`.
  - `list_subscriptions(self, *, status=None, date_from=None, date_to=None, page=None, size=None) -> dict` → `GET /subscription` with query params (`from`/`to`).
  - `cancel_subscription(self, subscription_id: str) -> dict` → `POST /subscription/{id}/cancel` (no body).
  - `@staticmethod _format_amount(amount: float) -> int | float` — integer if whole, else `round(amount, 2)`.

- [ ] **Step 1: Write the failing test** (mirror `tests/services/test_payment_service_platega.py` fixtures)

```python
# tests/services/test_platega_subscription_service.py
import pytest

from app.config import settings
from app.services.platega_service import PlategaService


def _configure(monkeypatch, **overrides):
    values = {'PLATEGA_ENABLED': True, 'PLATEGA_MERCHANT_ID': 'm', 'PLATEGA_SECRET': 's',
              'PLATEGA_BASE_URL': 'https://app.platega.io', 'PLATEGA_API_VERSION': 'v1'}
    values.update(overrides)
    for k, v in values.items():
        monkeypatch.setattr(settings, k, v, raising=False)


@pytest.mark.asyncio
async def test_create_subscription_posts_method_6(monkeypatch):
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json=None, params=None):
        captured.update(method=method, endpoint=endpoint, json=json)
        return {'transactionId': 'tx-1', 'redirect': 'https://pay/x', 'status': 'PENDING'}

    monkeypatch.setattr(service, '_request', fake_request)
    res = await service.create_subscription(amount=199.0, currency='RUB', interval=3, description='Тариф')

    assert captured['method'] == 'POST'
    assert captured['endpoint'].endswith('/transaction/process')
    assert captured['json']['paymentMethod'] == 6
    assert captured['json']['paymentDetails'] == {'amount': 199, 'currency': 'RUB', 'interval': 3}
    assert res['transactionId'] == 'tx-1'


@pytest.mark.asyncio
async def test_cancel_subscription_posts_cancel(monkeypatch):
    _configure(monkeypatch)
    service = PlategaService()
    captured = {}

    async def fake_request(method, endpoint, *, json=None, params=None):
        captured.update(method=method, endpoint=endpoint)
        return {'subscriptionId': 'sub-1', 'status': 'cancelled'}

    monkeypatch.setattr(service, '_request', fake_request)
    await service.cancel_subscription('sub-1')
    assert captured['method'] == 'POST'
    assert captured['endpoint'].endswith('/subscription/sub-1/cancel')


def test_format_amount_integer_and_decimal():
    assert PlategaService._format_amount(199.0) == 199
    assert PlategaService._format_amount(149.5) == 149.5
```

Note: verify the real `_request` signature in `platega_service.py` (whether it takes `json=`/`params=`); adapt the `fake_request` params to match so `monkeypatch.setattr` intercepts correctly.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/services/test_platega_subscription_service.py -q`
Expected: FAIL — `AttributeError: 'PlategaService' object has no attribute 'create_subscription'`.

- [ ] **Step 3: Implement** the four methods + `_format_amount` on `PlategaService`, reusing `self._request`, `self.api_version`, and the existing endpoint-versioning pattern from `create_payment`. Subscription read/cancel endpoints are unversioned (`/subscription/...`), create mirrors `create_payment`'s `/v2/transaction/process` vs `/transaction/process` choice.

- [ ] **Step 4: Run to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/platega_service.py tests/services/test_platega_subscription_service.py
git commit -m "feat(platega): методы подписок в PlategaService (create/get/list/cancel)"
```

### Task 5: Mixin — create SBP subscription + mutual exclusion

**Files:**
- Modify: `app/services/payment/platega.py`
- Test: `tests/services/test_platega_subscription_callbacks.py` (create part)

**Interfaces:**
- Consumes: `resolve_platega_interval` (Task 1), `PlategaService.create_subscription` (Task 4), `crud.create_platega_subscription` (Task 3), `Tariff.get_purchasable_price_for_period` (existing).
- Produces on the mixin:
  - `async create_platega_sbp_subscription(self, db, *, user_id: int, subscription, tariff) -> dict` — computes `(interval, charge_days)` from `subscription`/`tariff`, `amount_kopeks = tariff.get_purchasable_price_for_period(charge_days)`; raises `ValueError` if price is `None`; calls `create_subscription`; persists `PlategaSubscription(PENDING)`; sets `subscription.autopay_enabled = False` (mutual exclusion) and commits; returns `{'local_id', 'platega_subscription_id', 'redirect_url', 'status'}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_platega_subscription_callbacks.py
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_create_sbp_subscription_persists_and_disables_autopay(db_session, sample_user, sample_subscription, monkeypatch):
    from app.services.payment.platega import PlategaPaymentMixin
    from app.database.crud import platega_subscription as crud

    sample_subscription.autopay_enabled = True
    tariff = SimpleNamespace(id=5, is_daily=False, get_purchasable_price_for_period=lambda d: 19900)

    class Svc(PlategaPaymentMixin):
        def __init__(self):
            self.platega_service = SimpleNamespace(
                create_subscription=AsyncMock(return_value={'transactionId': 'tx-9', 'redirect': 'https://pay/9', 'status': 'PENDING'})
            )

    res = await Svc().create_platega_sbp_subscription(
        db_session, user_id=sample_user.id, subscription=sample_subscription, tariff=tariff
    )

    assert res['platega_subscription_id'] == 'tx-9'
    assert res['redirect_url'] == 'https://pay/9'
    assert sample_subscription.autopay_enabled is False
    stored = await crud.get_active_platega_subscription_by_subscription(db_session, sample_subscription.id)
    assert stored.interval == 3 and stored.charge_days == 30 and stored.amount_kopeks == 19900
```

Adjust `sample_subscription` construction to the repo's real `Subscription` shape (it needs `id`, `autopay_enabled`, and a `period_days`/tariff link the mixin reads to compute cadence — pass the period via the tariff/subscription as the mixin expects; finalize the exact source of `period_days` when implementing, e.g. `subscription.tariff` period or the last renewal period).

- [ ] **Step 2: Run to verify it fails** → `AttributeError: create_platega_sbp_subscription`.

- [ ] **Step 3: Implement** `create_platega_sbp_subscription` on `PlategaPaymentMixin`. Determine `period_days` from the subscription's current tariff/period (reuse the same source `pricing_engine`/renewal uses; when the tariff is daily use `is_daily=True`). Compute cadence via `resolve_platega_interval`. Guard `amount_kopeks is None → raise ValueError('no price for interval')`. Call `self.platega_service.create_subscription(amount=amount_kopeks/100, currency=settings.PLATEGA_CURRENCY, interval=interval, description=...)`. Persist via `crud.create_platega_subscription(...)`. Set `subscription.autopay_enabled = False` and `await db.commit()`.

- [ ] **Step 4: Run to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/payment/platega.py tests/services/test_platega_subscription_callbacks.py
git commit -m "feat(platega): создание СБП-подписки + выключение balance-autopay"
```

### Task 6: Mixin — callback handler (extend on charge, idempotent)

**Files:**
- Modify: `app/services/payment/platega.py`
- Test: `tests/services/test_platega_subscription_callbacks.py` (extend)

**Interfaces:**
- Consumes: status constants (Task 1), `crud.*` (Task 3), `Subscription.extend_subscription` (existing), `create_transaction` (existing, `TransactionType.SUBSCRIPTION_PAYMENT`, `PaymentMethod.PLATEGA`).
- Produces:
  - `async process_platega_subscription_callback(self, db, payload: dict) -> None` — branches on `payload['Status']`:
    - `SUBSCRIPTION_ACTIVATED` → status `ACTIVE`.
    - `Status in CHARGE_SUCCESS` → idempotency guard on `(platega_subscription_id, payload['Id'])` via a stored last-charge id set; `subscription.extend_subscription(charge_days)`; `next_charge_at = parse(payload['NextChargeAt'])`; `last_charge_at = now`; `charges_success += 1`; `create_transaction(..., type=SUBSCRIPTION_PAYMENT, payment_method=PLATEGA, amount_kopeks, external_id=payload['Id'], description=...)`; notify user. **Balance untouched.**
    - `Status in CHARGE_FAILED` → `PAST_DUE`, `charges_failed += 1`, notify.
    - `SUBSCRIPTION_PAST_DUE` → `PAST_DUE`, notify.
    - `SUBSCRIPTION_CANCELLED` → `CANCELLED`, notify.
    - `SUBSCRIPTION_FAILED` → `FAILED`, notify.
  - Idempotency: compare `record.last_charge_external_id == payload['Id']` (column already defined in Task 2); skip re-processing if equal, else set it before extending.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.asyncio
async def test_confirmed_charge_extends_and_is_idempotent(db_session, sample_user, sample_subscription, monkeypatch):
    from app.services.payment.platega import PlategaPaymentMixin
    from app.database.crud import platega_subscription as crud
    from datetime import UTC, datetime, timedelta

    end0 = datetime.now(UTC) + timedelta(days=2)
    sample_subscription.end_date = end0
    rec = await crud.create_platega_subscription(
        db_session, user_id=sample_user.id, subscription_id=sample_subscription.id, tariff_id=None,
        interval=3, charge_days=30, amount_kopeks=19900, redirect_url=None, platega_subscription_id='ps-1', status='ACTIVE')

    class Svc(PlategaPaymentMixin):
        async def _notify_sbp_recurring(self, *a, **k):  # stub notifications
            return None

    svc = Svc()
    payload = {'Status': 'CONFIRMED', 'Id': 'charge-1', 'Amount': 199, 'Currency': 'RUB',
               'PaymentMethod': 6, 'SubscriptionId': 'ps-1', 'NextChargeAt': '2026-09-01T00:00:00Z'}
    await svc.process_platega_subscription_callback(db_session, payload)
    await db_session.refresh(sample_subscription)
    await db_session.refresh(rec)
    assert sample_subscription.end_date >= end0 + timedelta(days=29)
    assert rec.charges_success == 1

    # replay same charge id -> no double extend
    await svc.process_platega_subscription_callback(db_session, payload)
    await db_session.refresh(rec)
    assert rec.charges_success == 1
```

- [ ] **Step 2: Run to verify it fails** → `AttributeError: process_platega_subscription_callback`.

- [ ] **Step 3: Implement** the handler with a row-lock (`get_platega_subscription_by_platega_id` then re-fetch `_for_update`), the idempotency compare on `last_charge_external_id == payload['Id']` (skip if equal), the extend, counters, `create_transaction`, and a `_notify_sbp_recurring(db, record, kind)` helper (kind ∈ confirmed/failed/past_due/cancelled/activated) that sends the bot message and emits the cabinet WS event. Parse `NextChargeAt` with the repo's ISO helper (guard `None`).

- [ ] **Step 4: Run to verify it passes** → PASS.

- [ ] **Step 5: Add branch tests** for `CANCELED`, `SUBSCRIPTION_PAST_DUE`, `SUBSCRIPTION_CANCELLED`, `SUBSCRIPTION_FAILED`, `SUBSCRIPTION_ACTIVATED` (assert status transitions, no extend on failures). Run → PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/payment/platega.py tests/services/test_platega_subscription_callbacks.py
git commit -m "feat(platega): обработчик коллбеков подписки (продление, идемпотентность, статусы)"
```

### Task 7: Mixin — cancel + by-subscription helper

**Files:**
- Modify: `app/services/payment/platega.py`
- Test: `tests/services/test_platega_recurrent_cancel_hooks.py`

**Interfaces:**
- Produces:
  - `async cancel_platega_sbp_subscription(self, db, *, local_id: int) -> bool` — loads record; if already CANCELLED → return True (idempotent); calls `self.platega_service.cancel_subscription(platega_subscription_id)` (best-effort, log on failure); sets status `CANCELLED`; commits; returns True.
  - `async cancel_platega_recurring_for_subscription(self, db, subscription_id: int) -> None` — finds active record via `crud.get_active_platega_subscription_by_subscription`; if present, calls `cancel_platega_sbp_subscription`; swallow+log exceptions (best-effort — must not raise).

- [ ] **Step 1: Write the failing test**

```python
# tests/services/test_platega_recurrent_cancel_hooks.py
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_cancel_by_subscription_calls_platega_and_marks_cancelled(db_session, sample_user, sample_subscription):
    from app.services.payment.platega import PlategaPaymentMixin
    from app.database.crud import platega_subscription as crud

    rec = await crud.create_platega_subscription(
        db_session, user_id=sample_user.id, subscription_id=sample_subscription.id, tariff_id=None,
        interval=3, charge_days=30, amount_kopeks=19900, redirect_url=None, platega_subscription_id='ps-2', status='ACTIVE')

    class Svc(PlategaPaymentMixin):
        def __init__(self):
            self.platega_service = SimpleNamespace(cancel_subscription=AsyncMock(return_value={'status': 'cancelled'}))

    svc = Svc()
    await svc.cancel_platega_recurring_for_subscription(db_session, sample_subscription.id)
    svc.platega_service.cancel_subscription.assert_awaited_once_with('ps-2')
    await db_session.refresh(rec)
    assert rec.status == 'CANCELLED'

    # idempotent second call — no active record, no raise
    await svc.cancel_platega_recurring_for_subscription(db_session, sample_subscription.id)


@pytest.mark.asyncio
async def test_cancel_best_effort_swallows_platega_error(db_session, sample_user, sample_subscription):
    from app.services.payment.platega import PlategaPaymentMixin
    from app.database.crud import platega_subscription as crud
    rec = await crud.create_platega_subscription(
        db_session, user_id=sample_user.id, subscription_id=sample_subscription.id, tariff_id=None,
        interval=3, charge_days=30, amount_kopeks=19900, redirect_url=None, platega_subscription_id='ps-3', status='ACTIVE')

    class Svc(PlategaPaymentMixin):
        def __init__(self):
            self.platega_service = SimpleNamespace(cancel_subscription=AsyncMock(side_effect=RuntimeError('platega down')))

    await Svc().cancel_platega_recurring_for_subscription(db_session, sample_subscription.id)  # must not raise
    await db_session.refresh(rec)
    assert rec.status == 'CANCELLED'  # local cancel still applied
```

- [ ] **Step 2: Run to verify it fails** → `AttributeError`.

- [ ] **Step 3: Implement** both methods. `cancel_platega_recurring_for_subscription` wraps in `try/except Exception` and logs `error=str(e)` (best-effort); local status is still set to CANCELLED even when the Platega HTTP call fails.

- [ ] **Step 4: Run to verify it passes** → PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/payment/platega.py tests/services/test_platega_recurrent_cancel_hooks.py
git commit -m "feat(platega): отмена СБП-подписки + best-effort хелпер по подписке"
```

---

## Phase 3 — Webhook, reconciler, config

### Task 8: Config gate

**Files:**
- Modify: `app/config.py` (Platega block ~:614-633; helpers ~:2346)
- Modify: `.env.example` (Platega block ~:694)
- Test: `tests/services/test_platega_subscription_service.py` (append a config test)

**Interfaces:**
- Produces: `settings.PLATEGA_RECURRENT_ENABLED: bool = False`; `settings.is_platega_recurrent_enabled() -> bool` (returns `self.is_platega_enabled() and self.PLATEGA_RECURRENT_ENABLED`).

- [ ] **Step 1: Test**

```python
def test_recurrent_gate(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'PLATEGA_ENABLED', True, raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_MERCHANT_ID', 'm', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_SECRET', 's', raising=False)
    monkeypatch.setattr(settings, 'PLATEGA_RECURRENT_ENABLED', False, raising=False)
    assert settings.is_platega_recurrent_enabled() is False
    monkeypatch.setattr(settings, 'PLATEGA_RECURRENT_ENABLED', True, raising=False)
    assert settings.is_platega_recurrent_enabled() is True
```

- [ ] **Step 2: Run → fail** (`AttributeError: is_platega_recurrent_enabled`).

- [ ] **Step 3: Add** the field to the Platega settings block and the helper next to `is_platega_enabled`. Add to `.env.example`: `# Рекуррентные СБП-подписки Platega (автопродление)` + `PLATEGA_RECURRENT_ENABLED=false`.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(platega): гейт PLATEGA_RECURRENT_ENABLED`.

### Task 9: Webhook routing branch

**Files:**
- Modify: `app/webserver/payments.py` (Platega block ~:712-759)
- Test: `tests/webserver/test_platega_subscription_webhook.py`

**Interfaces:**
- Consumes: `process_platega_subscription_callback` (Task 6), existing `process_platega_webhook`.
- Behaviour: after parsing JSON in the existing `platega_webhook`, if `payload.get('PaymentMethod') == 6` OR `'SubscriptionId' in payload` OR `str(payload.get('Status', '')).startswith('SUBSCRIPTION_')` → dispatch `_process_payment_service_callback(payment_service, payload, 'process_platega_subscription_callback')`; else existing `'process_platega_webhook'`. Header auth unchanged. Always 200.

- [ ] **Step 1: Write the failing test** — post a subscription-shaped body to the Platega webhook path, assert it routes to the subscription handler (monkeypatch `payment_service.process_platega_subscription_callback` to record the call) and returns 200; post a one-off body, assert it routes to `process_platega_webhook`; post with wrong secret → 401. Mirror the existing webhook test in `tests/webserver/` for app construction and header setup.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** the branch in `platega_webhook`.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(platega): ветка подписочных коллбеков в /platega-webhook`.

### Task 10: Reconciler

**Files:**
- Modify: `app/services/monitoring_service.py` (`_monitoring_cycle` ~:347)
- Test: `tests/services/test_platega_subscription_service.py` (reconciler unit)

**Interfaces:**
- Produces: `async _reconcile_platega_subscriptions(self, db)` called from `_monitoring_cycle` under `if settings.is_platega_recurrent_enabled():`. Loads PENDING older than N minutes → `get_subscription` → if Platega says active/cancelled/failed, apply; PENDING stuck > 30 min with no Platega record → FAILED. Best-effort, wrapped in try/except.

- [ ] **Step 1: Test** the pure decision: a helper `_platega_reconcile_decision(local_status, remote_status, age_minutes) -> str | None` (pure) returning the new local status or None. Unit-test its table (PENDING+active→ACTIVE; PENDING+age>30+no remote→FAILED; ACTIVE+remote cancelled→CANCELLED).

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** the pure decision fn (in `platega_recurrent.py`) + the `_reconcile_platega_subscriptions` loop wiring it, and the `_monitoring_cycle` call.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(platega): reconciler подписок в monitoring-цикле`.

---

## Phase 4 — Cancel-on-delete hooks

### Task 11: Wire cancel into every deletion/revoke path

**Files:**
- Modify: `app/handlers/admin/users.py` (`confirm_subscription_deletion` ~:6443)
- Modify: `app/cabinet/routes/subscription_modules/multi_tariff.py` (`delete_subscription` :108)
- Modify: `app/cabinet/routes/admin_users.py` (`reset-subscription` :2794 and any admin delete)
- Modify: `app/services/subscription_service.py` (`revoke_subscription` :866)
- Test: `tests/services/test_platega_recurrent_cancel_hooks.py` (extend)

**Interfaces:**
- Consumes: `payment_service.cancel_platega_recurring_for_subscription` (Task 7). Obtain the singleton `PaymentService` the way each module already does (grep each file for how it constructs/imports `PaymentService`; reuse that).

- [ ] **Step 1: Write a test** per entry point that deletes a subscription with an active `PlategaSubscription` and asserts the record ends `CANCELLED`. For the pure-service path (`revoke_subscription`), test directly; for route/handler paths, test at the smallest callable seam (call the deletion function with a stubbed `payment_service`). At minimum, cover `revoke_subscription` and the cabinet user `delete_subscription`.

- [ ] **Step 2: Run → fail** (deletion leaves the Platega subscription ACTIVE).

- [ ] **Step 3: Implement** — in each entry point, before/after the subscription is removed, call `await payment_service.cancel_platega_recurring_for_subscription(db, subscription.id)` (best-effort; never blocks deletion). Gate with `if settings.is_platega_recurrent_enabled():` to avoid overhead when off.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(platega): отмена СБП-автооплаты при удалении/отзыве подписки`.

---

## Phase 5 — Bot UI

### Task 12: Bot autopay-SBP flow

**Files:**
- Modify: `app/handlers/subscription/autopay.py`
- Modify: `app/localization/` (RU/EN texts for the new strings)
- Test: `tests/services/test_platega_subscription_callbacks.py` (notification helper) + a handler smoke test if the repo has handler tests

**Interfaces:**
- Consumes: `create_platega_sbp_subscription`, `cancel_platega_sbp_subscription`, `get_active_platega_subscription_by_subscription`.
- Behaviour: menu item «⚡ Автопродление через СБП» (shown when `is_platega_recurrent_enabled()`); enable → create → send inline button with `redirect_url`; status view (ACTIVE/PENDING/PAST_DUE); «Отменить автооплату» → cancel. Enabling clears balance-autopay (already done in Task 5's create). `_notify_sbp_recurring` (Task 6) sends confirmed/failed messages.

- [ ] **Step 1: Write test** for the notification-text builder / the enable callback at the smallest seam (e.g. a pure function that renders the status text given a `PlategaSubscription`). Assert wording per status.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** the handlers + register callbacks in the module's `register_handlers`, following the existing autopay handler patterns. Add localization keys.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(platega): бот-флоу автопродления через СБП`.

---

## Phase 6 — Cabinet backend routes (this repo)

### Task 13: Cabinet user endpoints

**Files:**
- Create: `app/cabinet/routes/subscription_modules/platega_recurrent.py`
- Modify: the subscription router aggregator that includes `subscription_modules/*` (grep for where `autopay` router is included)
- Test: `tests/cabinet/test_platega_recurrent_routes.py`

**Interfaces:**
- Endpoints (subscription-scoped, mirror `autopay.py` auth/deps):
  - `POST /subscription/platega-recurrent/enable` (`{subscription_id}`) → `{status, redirect_url}`.
  - `GET /subscription/platega-recurrent` (`?subscription_id=`) → `{status, interval, amount_kopeks, next_charge_at, redirect_url}` or `{status: 'none'}`.
  - `POST /subscription/platega-recurrent/cancel` (`{subscription_id}`) → `{status: 'cancelled'}`.
- Guard: 403 if `not is_platega_recurrent_enabled()`.

- [ ] **Step 1: Write failing tests** — call each route via the cabinet test client (mirror `tests/cabinet/test_ticket_cabinet_guards.py` route-call style), stub `payment_service`, assert status codes and payload shape, and the disabled-gate 403.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** the router + include it in the subscription router aggregator.

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(platega): кабинетные эндпоинты СБП-автопродления (юзер)`.

### Task 14: Cabinet admin — status field + cancel endpoint

**Files:**
- Modify: `app/cabinet/routes/admin_users.py` (`_build_subscription_info`/`UserSubscriptionInfo` ~:227/:254; add endpoint)
- Test: `tests/cabinet/test_platega_recurrent_routes.py` (extend)

**Interfaces:**
- `UserSubscriptionInfo` (+ `_build_subscription_info_async`) gains `sbp_recurring_status: str | None` and `sbp_recurring_id: int | None` populated from `get_active_platega_subscription_by_subscription`.
- `POST /{user_id}/subscriptions/{sub_id}/cancel-sbp-recurring` → `cancel_platega_recurring_for_subscription`; admin-permission-guarded; idempotent; returns `{status: 'cancelled'}`.

- [ ] **Step 1: Write failing tests** — admin cancel endpoint cancels an active record and 200s; `get_user_detail` includes `sbp_recurring_status`; permission-denied path 403.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** the field population (use the async builder so it can query) + the admin endpoint (reuse the admin auth dependency already in `admin_users.py`).

- [ ] **Step 4: Run → PASS.**

- [ ] **Step 5: Commit** `feat(platega): админка кабинета — статус и отмена СБП-автооплаты`.

---

## Final verification (after all tasks)

- [ ] `.venv/bin/python -m pytest tests/ -q` → all green.
- [ ] `.venv/bin/python -m ruff check .` and `.venv/bin/python -m ruff format --check .` → clean.
- [ ] Manual: create a subscription (sandbox merchant), confirm binding, simulate `CONFIRMED` callback → subscription extended; simulate `SUBSCRIPTION_CANCELLED` → record CANCELLED; delete subscription → Platega cancel called.

## Notes for the implementer

- **Balance is never touched** by recurring charges — extension is direct via `extend_subscription`. Do not call `add_user_balance`/`subtract_user_balance` in the callback path.
- **Audit** = one `Transaction(SUBSCRIPTION_PAYMENT, PaymentMethod.PLATEGA)` per successful charge (records the renewal payment event; stored negative by convention; balance unchanged) + `charges_success`/`charges_failed` counters on the record.
- **`period_days` source** (Task 5): the subscription's current tariff period / last-purchased period — confirm against how `pricing_engine.calculate_renewal_price` derives the period and reuse the same source so cadence matches what the user actually bought.
- The cabinet **frontend** (React) is a separate plan (Plan B) built against the endpoints from Tasks 13–14.
