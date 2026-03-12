# Gift Subscription from Cabinet — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow authenticated cabinet users to gift VPN subscriptions to others by Telegram username or email, paying from balance or via payment gateway.

**Architecture:** Thin cabinet wrapper around existing `GuestPurchaseService`. New `source`/`buyer_user_id` fields on `GuestPurchase` model distinguish cabinet gifts from landing gifts. Admin toggle via `CABINET_GIFT_ENABLED` branding setting. Frontend page at `/gift` with tariff/period selection, recipient input, payment mode choice.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2.x / Alembic (backend), React 19 / TypeScript / Tailwind CSS / TanStack Query / Zustand / Framer Motion (frontend)

**Repositories:**
- Backend: `/Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot/`
- Frontend: `/Users/ea/Desktop/DEV/bedolaga-cabinet/`

---

## Phase 1: Backend — Model & Migration

### Task 1: Add source/buyer fields to GuestPurchase model

**Files:**
- Modify: `app/database/models.py:3072-3110` (GuestPurchase class)

**Step 1: Add new columns to GuestPurchase model**

In `app/database/models.py`, after the `is_gift` field (line ~3087), add:

```python
source = Column(String(20), nullable=False, default='landing', server_default='landing')  # 'landing' or 'cabinet'
buyer_user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
```

Add relationship after existing relationships (line ~3108):

```python
buyer = relationship('User', foreign_keys=[buyer_user_id], lazy='selectin')
```

**Step 2: Add GIFT_PAYMENT to TransactionType enum**

In `app/database/models.py` at the `TransactionType` enum (line ~129), add:

```python
GIFT_PAYMENT = 'gift_payment'
```

**Step 3: Commit**

```bash
cd /Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot
git add app/database/models.py
git commit -m "feat: add source and buyer_user_id fields to GuestPurchase model"
```

### Task 2: Create Alembic migration

**Files:**
- Create: `migrations/alembic/versions/XXXX_add_gift_cabinet_fields.py`

**Step 1: Generate migration**

```bash
cd /Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot
# Note: requires running PostgreSQL + BOT_TOKEN env var
make migration m="add cabinet gift source and buyer fields"
```

**Step 2: Verify the generated migration**

The migration should contain:
- `op.add_column('guest_purchases', sa.Column('source', sa.String(20), nullable=False, server_default='landing'))`
- `op.add_column('guest_purchases', sa.Column('buyer_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))`
- `op.create_index` on `source` column (for filtering cabinet vs landing)

If autogenerate missed anything, edit manually.

**Step 3: Commit**

```bash
git add migrations/
git commit -m "feat: migration for cabinet gift fields on guest_purchases"
```

---

## Phase 2: Backend — Admin Toggle

### Task 3: Add CABINET_GIFT_ENABLED branding toggle

**Files:**
- Modify: `app/cabinet/routes/branding.py:41,267-276,955-985`

**Step 1: Add constant and schemas**

After line 41 (`LITE_MODE_ENABLED_KEY`), add:

```python
GIFT_ENABLED_KEY = 'CABINET_GIFT_ENABLED'  # Stores "true" or "false"
```

After `LiteModeEnabledUpdate` class (line ~276), add:

```python
class GiftEnabledResponse(BaseModel):
    """Gift feature enabled setting."""
    enabled: bool = False


class GiftEnabledUpdate(BaseModel):
    """Request to update gift feature setting."""
    enabled: bool
```

**Step 2: Add GET/PATCH endpoints**

After the lite-mode endpoints (line ~985), add:

```python
# ============ Gift Feature Routes ============


@router.get('/gift-enabled', response_model=GiftEnabledResponse)
async def get_gift_enabled(
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get gift feature enabled setting. Public endpoint."""
    value = await get_setting_value(db, GIFT_ENABLED_KEY)
    if value is not None:
        enabled = value.lower() == 'true'
        return GiftEnabledResponse(enabled=enabled)
    return GiftEnabledResponse(enabled=False)


@router.patch('/gift-enabled', response_model=GiftEnabledResponse)
async def update_gift_enabled(
    payload: GiftEnabledUpdate,
    admin: User = Depends(require_permission('settings:edit')),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update gift feature enabled setting. Admin only."""
    await set_setting_value(db, GIFT_ENABLED_KEY, str(payload.enabled).lower())
    logger.info('Admin set gift enabled', telegram_id=admin.telegram_id, enabled=payload.enabled)
    return GiftEnabledResponse(enabled=payload.enabled)
```

**Step 3: Commit**

```bash
git add app/cabinet/routes/branding.py
git commit -m "feat: add CABINET_GIFT_ENABLED branding toggle"
```

---

## Phase 3: Backend — Gift API Routes

### Task 4: Create gift schemas

**Files:**
- Create: `app/cabinet/schemas/gift.py`

**Step 1: Write schemas**

```python
"""Schemas for cabinet gift subscription feature."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class GiftConfigTariffPeriod(BaseModel):
    days: int
    price_kopeks: int
    price_label: str
    original_price_kopeks: int | None = None
    discount_percent: int | None = None


class GiftConfigTariff(BaseModel):
    id: int
    name: str
    description: str | None = None
    traffic_limit_gb: int
    device_limit: int
    periods: list[GiftConfigTariffPeriod]


class GiftConfigPaymentMethod(BaseModel):
    method_id: str
    display_name: str
    description: str | None = None
    icon_url: str | None = None
    min_amount_kopeks: int | None = None
    max_amount_kopeks: int | None = None
    sub_options: list[GiftConfigSubOption] | None = None


class GiftConfigSubOption(BaseModel):
    id: str
    name: str


class GiftConfigResponse(BaseModel):
    is_enabled: bool
    tariffs: list[GiftConfigTariff] = []
    payment_methods: list[GiftConfigPaymentMethod] = []
    balance_kopeks: int = 0
    currency_symbol: str = '₽'


class GiftPurchaseRequest(BaseModel):
    tariff_id: int
    period_days: int
    recipient_type: str = Field(pattern=r'^(email|telegram)$')
    recipient_value: str = Field(min_length=1, max_length=255)
    gift_message: str | None = Field(default=None, max_length=1000)
    payment_mode: str = Field(pattern=r'^(balance|gateway)$')
    payment_method: str | None = Field(default=None, max_length=50)

    @model_validator(mode='after')
    def validate_payment(self) -> 'GiftPurchaseRequest':
        if self.payment_mode == 'gateway' and not self.payment_method:
            raise ValueError('payment_method is required for gateway mode')
        return self


class GiftPurchaseResponse(BaseModel):
    """Response for both balance and gateway modes."""
    status: str  # 'delivered', 'pending', 'pending_activation'
    purchase_token: str
    payment_url: str | None = None  # Only for gateway mode


class GiftPurchaseStatusResponse(BaseModel):
    status: str
    is_gift: bool = True
    recipient_contact_value: str | None = None
    gift_message: str | None = None
    tariff_name: str | None = None
    period_days: int | None = None
```

**Step 2: Commit**

```bash
git add app/cabinet/schemas/gift.py
git commit -m "feat: add gift purchase Pydantic schemas"
```

### Task 5: Create gift routes

**Files:**
- Create: `app/cabinet/routes/gift.py`

**Step 1: Write the gift router**

```python
"""Cabinet gift subscription routes."""

from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.cabinet.auth.dependencies import get_current_cabinet_user
from app.cabinet.schemas.gift import (
    GiftConfigResponse,
    GiftConfigSubOption,
    GiftConfigPaymentMethod,
    GiftConfigTariff,
    GiftConfigTariffPeriod,
    GiftPurchaseRequest,
    GiftPurchaseResponse,
    GiftPurchaseStatusResponse,
)
from app.config import settings
from app.database.crud.transaction import create_transaction
from app.database.crud.user import subtract_user_balance
from app.database.models import GuestPurchase, GuestPurchaseStatus, Tariff, TransactionType, User
from app.services.guest_purchase_service import GuestPurchaseService
from app.services.payment_service import PaymentService

from .branding import GIFT_ENABLED_KEY
from ..dependencies import get_cabinet_db, get_setting_value

logger = structlog.get_logger()

router = APIRouter(prefix='/gift', tags=['Cabinet Gift'])

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_TELEGRAM_RE = re.compile(r'^@?[a-zA-Z][a-zA-Z0-9_]{4,31}$')


def _validate_recipient(recipient_type: str, value: str) -> None:
    """Validate recipient contact format."""
    if recipient_type == 'email':
        if not _EMAIL_RE.match(value.strip()):
            raise HTTPException(status_code=400, detail='Invalid email format')
    elif recipient_type == 'telegram':
        clean = value.lstrip('@').strip()
        if not _TELEGRAM_RE.match(clean):
            raise HTTPException(status_code=400, detail='Invalid Telegram username format')


@router.get('/config', response_model=GiftConfigResponse)
async def get_gift_config(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get gift feature config: enabled flag, available tariffs, payment methods, user balance."""
    # Check if gift feature is enabled
    gift_value = await get_setting_value(db, GIFT_ENABLED_KEY)
    is_enabled = gift_value is not None and gift_value.lower() == 'true'

    if not is_enabled:
        return GiftConfigResponse(is_enabled=False, balance_kopeks=user.balance_kopeks)

    # Get active tariffs with period_prices
    from sqlalchemy import select
    result = await db.execute(
        select(Tariff).where(
            Tariff.is_active == True,  # noqa: E712
            Tariff.is_hidden == False,  # noqa: E712
        ).order_by(Tariff.sort_order, Tariff.id)
    )
    tariffs = result.scalars().all()

    config_tariffs = []
    for tariff in tariffs:
        periods = []
        for days_str, price_kopeks in sorted(
            (tariff.period_prices or {}).items(),
            key=lambda x: int(x[0]),
        ):
            days = int(days_str)
            periods.append(GiftConfigTariffPeriod(
                days=days,
                price_kopeks=price_kopeks,
                price_label=f'{price_kopeks / 100:.0f} ₽',
            ))
        if periods:
            config_tariffs.append(GiftConfigTariff(
                id=tariff.id,
                name=tariff.name,
                description=getattr(tariff, 'description', None),
                traffic_limit_gb=tariff.traffic_limit_gb or 0,
                device_limit=tariff.device_limit or 1,
                periods=periods,
            ))

    # Get payment methods (reuse from balance topup config)
    from app.cabinet.routes.balance import _get_available_payment_methods
    payment_methods_raw = await _get_available_payment_methods(db)
    payment_methods = [
        GiftConfigPaymentMethod(
            method_id=m['method_id'],
            display_name=m['display_name'],
            description=m.get('description'),
            icon_url=m.get('icon_url'),
            min_amount_kopeks=m.get('min_amount_kopeks'),
            max_amount_kopeks=m.get('max_amount_kopeks'),
            sub_options=[
                GiftConfigSubOption(id=so['id'], name=so['name'])
                for so in (m.get('sub_options') or [])
            ] if m.get('sub_options') else None,
        )
        for m in payment_methods_raw
    ]

    return GiftConfigResponse(
        is_enabled=True,
        tariffs=config_tariffs,
        payment_methods=payment_methods,
        balance_kopeks=user.balance_kopeks,
        currency_symbol=settings.get_currency_symbol(),
    )


@router.post('/purchase', response_model=GiftPurchaseResponse)
async def create_gift_purchase(
    request: GiftPurchaseRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Create a gift subscription purchase from cabinet."""
    # 1. Check gift feature enabled
    gift_value = await get_setting_value(db, GIFT_ENABLED_KEY)
    if not gift_value or gift_value.lower() != 'true':
        raise HTTPException(status_code=403, detail='Gift feature is disabled')

    # 2. Validate recipient
    _validate_recipient(request.recipient_type, request.recipient_value)

    # 3. Find tariff and validate price
    from sqlalchemy import select
    result = await db.execute(select(Tariff).where(Tariff.id == request.tariff_id, Tariff.is_active == True))  # noqa: E712
    tariff = result.scalar_one_or_none()
    if not tariff:
        raise HTTPException(status_code=404, detail='Tariff not found or inactive')

    price_kopeks = tariff.get_price_for_period(request.period_days)
    if price_kopeks is None:
        raise HTTPException(status_code=400, detail='Invalid period for this tariff')

    # 4. Determine buyer contact info
    buyer_contact_type = 'email' if user.email else 'telegram'
    buyer_contact_value = user.email or (f'@{user.username}' if user.username else str(user.telegram_id or user.id))

    # 5. Create GuestPurchase record
    guest_purchase_service = GuestPurchaseService()
    purchase = await guest_purchase_service.create_purchase(
        db=db,
        landing=None,  # No landing for cabinet gifts
        tariff=tariff,
        period_days=request.period_days,
        amount_kopeks=price_kopeks,
        contact_type=buyer_contact_type,
        contact_value=buyer_contact_value,
        payment_method=request.payment_method or 'balance',
        is_gift=True,
        gift_recipient_type=request.recipient_type,
        gift_recipient_value=request.recipient_value.strip(),
        gift_message=request.gift_message,
        source='cabinet',
        buyer_user_id=user.id,
    )

    # 6. Handle payment mode
    if request.payment_mode == 'balance':
        # Check balance
        if user.balance_kopeks < price_kopeks:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    'code': 'insufficient_funds',
                    'message': f'Insufficient balance. Need {price_kopeks / 100:.0f}, have {user.balance_kopeks / 100:.0f}',
                    'required': price_kopeks,
                    'available': user.balance_kopeks,
                },
            )

        # Deduct balance
        success = await subtract_user_balance(
            db=db,
            user=user,
            amount_kopeks=price_kopeks,
            description=f'Gift: {tariff.name} ({request.period_days}d) → {request.recipient_value}',
        )
        if not success:
            raise HTTPException(status_code=500, detail='Failed to deduct balance')

        # Create transaction
        await create_transaction(
            db=db,
            user_id=user.id,
            type=TransactionType.GIFT_PAYMENT,
            amount_kopeks=price_kopeks,
            description=f'Gift subscription: {tariff.name} ({request.period_days}d) → {request.recipient_value}',
        )

        # Mark as paid and fulfill immediately
        purchase.status = GuestPurchaseStatus.PAID.value
        await db.commit()

        fulfilled = await guest_purchase_service.fulfill_purchase(db, purchase.token)

        return GiftPurchaseResponse(
            status=fulfilled.status if fulfilled else 'failed',
            purchase_token=purchase.token,
        )

    else:
        # Gateway payment — create payment via PaymentService
        # Same pattern as balance topup
        payment_service = PaymentService()
        return_url = f'{settings.get_cabinet_url()}/gift/result?token={purchase.token}'

        # Route to correct payment provider
        payment_result = await _create_gift_payment(
            payment_service=payment_service,
            db=db,
            user=user,
            purchase=purchase,
            payment_method=request.payment_method,
            amount_kopeks=price_kopeks,
            return_url=return_url,
            tariff_name=tariff.name,
            period_days=request.period_days,
        )

        return GiftPurchaseResponse(
            status='pending',
            purchase_token=purchase.token,
            payment_url=payment_result['payment_url'],
        )


async def _create_gift_payment(
    payment_service: PaymentService,
    db: AsyncSession,
    user: User,
    purchase: GuestPurchase,
    payment_method: str | None,
    amount_kopeks: int,
    return_url: str,
    tariff_name: str,
    period_days: int,
) -> dict:
    """Create payment via appropriate payment gateway.

    This mirrors the logic in balance.py topup endpoint but for gift purchases.
    The payment webhook will call fulfill_purchase via the existing guest purchase webhook handler.
    """
    # Implementation will mirror the balance.py topup pattern:
    # Parse payment_method (e.g., 'platega_2' → method='platega', sub_option='2')
    # Call the appropriate PaymentService method
    # Store purchase.token in payment metadata for webhook correlation
    # Return dict with payment_url
    raise HTTPException(
        status_code=501,
        detail='Gateway payment for gifts not yet implemented — use balance mode',
    )


@router.get('/purchase/{token}', response_model=GiftPurchaseStatusResponse)
async def get_gift_purchase_status(
    token: str,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Get gift purchase status. Only accessible by the buyer."""
    from sqlalchemy import select
    result = await db.execute(
        select(GuestPurchase).where(
            GuestPurchase.token == token,
            GuestPurchase.buyer_user_id == user.id,
        )
    )
    purchase = result.scalar_one_or_none()
    if not purchase:
        raise HTTPException(status_code=404, detail='Purchase not found')

    tariff_name = None
    if purchase.tariff_id:
        tariff_result = await db.execute(select(Tariff).where(Tariff.id == purchase.tariff_id))
        tariff = tariff_result.scalar_one_or_none()
        tariff_name = tariff.name if tariff else None

    return GiftPurchaseStatusResponse(
        status=purchase.status,
        is_gift=True,
        recipient_contact_value=purchase.gift_recipient_value,
        gift_message=purchase.gift_message,
        tariff_name=tariff_name,
        period_days=purchase.period_days,
    )
```

**NOTE:** The `_create_gift_payment` function is a stub for gateway mode. It will be implemented in Task 6 by wiring into the existing payment service pattern from `balance.py`. For the MVP, balance mode works end-to-end.

**Step 2: Commit**

```bash
git add app/cabinet/routes/gift.py
git commit -m "feat: add cabinet gift purchase routes"
```

### Task 6: Update GuestPurchaseService.create_purchase for cabinet source

**Files:**
- Modify: `app/services/guest_purchase_service.py:116-160`

**Step 1: Add source and buyer_user_id parameters**

Update the `create_purchase` function signature (line ~116) to accept optional new fields:

```python
async def create_purchase(
    db: AsyncSession,
    landing: LandingPage | None,  # Make nullable for cabinet
    tariff: Tariff,
    period_days: int,
    amount_kopeks: int,
    contact_type: str,
    contact_value: str,
    payment_method: str,
    is_gift: bool = False,
    gift_recipient_type: str | None = None,
    gift_recipient_value: str | None = None,
    gift_message: str | None = None,
    source: str = 'landing',
    buyer_user_id: int | None = None,
    commit: bool = True,
) -> GuestPurchase:
```

In the function body where `GuestPurchase(...)` is constructed, add:

```python
source=source,
buyer_user_id=buyer_user_id,
landing_id=landing.id if landing else None,
```

**Step 2: Commit**

```bash
git add app/services/guest_purchase_service.py
git commit -m "feat: extend create_purchase to support cabinet source"
```

### Task 7: Register gift router

**Files:**
- Modify: `app/cabinet/routes/__init__.py:56-87`

**Step 1: Import and include the gift router**

After line 56 (`from .wheel import router as wheel_router`), add:

```python
from .gift import router as gift_router
```

After line 87 (`router.include_router(wheel_router)`), add:

```python
# Gift routes
router.include_router(gift_router)
```

**Step 2: Commit**

```bash
git add app/cabinet/routes/__init__.py
git commit -m "feat: register gift router in cabinet"
```

---

## Phase 4: Frontend — API & Feature Flag

### Task 8: Create gift API client

**Files:**
- Create: `bedolaga-cabinet/src/api/gift.ts`

**Step 1: Write the API module**

```typescript
import apiClient from './client';

// Types

export interface GiftTariffPeriod {
  days: number;
  price_kopeks: number;
  price_label: string;
  original_price_kopeks: number | null;
  discount_percent: number | null;
}

export interface GiftTariff {
  id: number;
  name: string;
  description: string | null;
  traffic_limit_gb: number;
  device_limit: number;
  periods: GiftTariffPeriod[];
}

export interface GiftPaymentMethodSubOption {
  id: string;
  name: string;
}

export interface GiftPaymentMethod {
  method_id: string;
  display_name: string;
  description: string | null;
  icon_url: string | null;
  min_amount_kopeks: number | null;
  max_amount_kopeks: number | null;
  sub_options: GiftPaymentMethodSubOption[] | null;
}

export interface GiftConfig {
  is_enabled: boolean;
  tariffs: GiftTariff[];
  payment_methods: GiftPaymentMethod[];
  balance_kopeks: number;
  currency_symbol: string;
}

export interface GiftPurchaseRequest {
  tariff_id: number;
  period_days: number;
  recipient_type: 'email' | 'telegram';
  recipient_value: string;
  gift_message?: string;
  payment_mode: 'balance' | 'gateway';
  payment_method?: string;
}

export interface GiftPurchaseResponse {
  status: string;
  purchase_token: string;
  payment_url: string | null;
}

export interface GiftPurchaseStatus {
  status: string;
  is_gift: boolean;
  recipient_contact_value: string | null;
  gift_message: string | null;
  tariff_name: string | null;
  period_days: number | null;
}

// API

export const giftApi = {
  getConfig: async (): Promise<GiftConfig> => {
    const { data } = await apiClient.get<GiftConfig>('/cabinet/gift/config');
    return data;
  },

  createPurchase: async (request: GiftPurchaseRequest): Promise<GiftPurchaseResponse> => {
    const { data } = await apiClient.post<GiftPurchaseResponse>('/cabinet/gift/purchase', request);
    return data;
  },

  getPurchaseStatus: async (token: string): Promise<GiftPurchaseStatus> => {
    const { data } = await apiClient.get<GiftPurchaseStatus>(`/cabinet/gift/purchase/${token}`);
    return data;
  },
};
```

**Step 2: Commit**

```bash
cd /Users/ea/Desktop/DEV/bedolaga-cabinet
git add src/api/gift.ts
git commit -m "feat: add gift subscription API client"
```

### Task 9: Add gift feature flag

**Files:**
- Modify: `bedolaga-cabinet/src/hooks/useFeatureFlags.ts`
- Modify: `bedolaga-cabinet/src/api/branding.ts`

**Step 1: Add branding type and API call**

In `src/api/branding.ts`, after the `EmailAuthEnabled` interface (line ~24), add:

```typescript
export interface GiftEnabled {
  enabled: boolean;
}
```

Add to the `brandingApi` object:

```typescript
getGiftEnabled: async (): Promise<GiftEnabled> => {
  const { data } = await apiClient.get<GiftEnabled>('/cabinet/branding/gift-enabled');
  return data;
},
```

**Step 2: Update useFeatureFlags**

In `src/hooks/useFeatureFlags.ts`, add import and query:

```typescript
import { brandingApi } from '@/api/branding';
```

Inside `useFeatureFlags()`, after the polls query, add:

```typescript
const { data: giftConfig } = useQuery({
  queryKey: ['gift-enabled'],
  queryFn: brandingApi.getGiftEnabled,
  enabled: isAuthenticated,
  staleTime: 60000,
  retry: false,
});
```

Update the return:

```typescript
return {
  referralEnabled: referralTerms?.is_enabled,
  wheelEnabled: wheelConfig?.is_enabled,
  hasContests: (contestsCount?.count ?? 0) > 0,
  hasPolls: (pollsCount?.count ?? 0) > 0,
  giftEnabled: giftConfig?.enabled,
};
```

**Step 3: Commit**

```bash
git add src/hooks/useFeatureFlags.ts src/api/branding.ts
git commit -m "feat: add giftEnabled feature flag"
```

---

## Phase 5: Frontend — Gift Page

### Task 10: Create GiftSubscription page

**Files:**
- Create: `bedolaga-cabinet/src/pages/GiftSubscription.tsx`

**Step 1: Write the page component**

This page reuses patterns from `QuickPurchase.tsx` — period tabs, tariff cards, payment method cards — but adapted for cabinet context (authenticated user, balance payment, glass theme).

Key differences from QuickPurchase:
- No buyer contact field (user is authenticated)
- Payment mode toggle: "From balance" / "Via payment gateway"
- Shows user balance prominently
- Uses cabinet glass theme styling (not landing dark theme)
- No landing-specific features (custom CSS, backgrounds, discount banners)

The page should contain these sections:
1. Page title with gift icon
2. Period pill tabs (from active tariffs)
3. Tariff radio cards (filtered by selected period)
4. Recipient input (email/@telegram with auto-detect)
5. Gift message textarea (optional, 1000 char limit)
6. Payment mode toggle
7. Payment method cards (only when gateway mode selected)
8. Summary card with price, balance info, and "Gift" button

Use existing component patterns:
- Period pills: same `rounded-full px-4 py-2` style as QuickPurchase `PeriodTabs`
- Tariff cards: same radio-button card pattern as QuickPurchase `TariffCard`
- Payment methods: same pattern as QuickPurchase `PaymentMethodCard`
- Input fields: `rounded-xl border border-dark-700/50 bg-dark-800/50` style
- Glass theme: use `getGlassColors(isDark)` for consistent look
- Animations: Framer Motion `motion.div` with stagger

**Important implementation notes:**
- Use `useCurrency()` hook for price formatting
- Use `usePlatform()` for haptic feedback on button clicks
- Use `useTranslation()` with `gift.*` keys
- Use `useMutation` for purchase, handle 402 (insufficient funds) specially
- For balance mode: on success, invalidate `['balance']` query cache
- For gateway mode: redirect to `payment_url`, then poll on `/gift/result`

**Step 2: Commit**

```bash
git add src/pages/GiftSubscription.tsx
git commit -m "feat: add GiftSubscription page"
```

### Task 11: Create GiftResult page (for gateway payments)

**Files:**
- Create: `bedolaga-cabinet/src/pages/GiftResult.tsx`

**Step 1: Write the result page**

Pattern from `PurchaseSuccess.tsx` — polling purchase status every 3 seconds until terminal state.

States to handle:
- `pending` / `paid` — show spinner + "Processing..."
- `delivered` — success screen with confetti/checkmark, show recipient, tariff, period
- `pending_activation` — show that recipient has active subscription, gift pending
- `failed` — error screen with retry suggestion

Read `token` from URL search params: `?token=xxx`

**Step 2: Commit**

```bash
git add src/pages/GiftResult.tsx
git commit -m "feat: add GiftResult page for gateway payment status"
```

### Task 12: Add routes to App.tsx

**Files:**
- Modify: `bedolaga-cabinet/src/App.tsx`

**Step 1: Add lazy imports**

After line 38 (`const Wheel = lazy(...)`) add:

```typescript
const GiftSubscription = lazy(() => import('./pages/GiftSubscription'));
const GiftResult = lazy(() => import('./pages/GiftResult'));
```

**Step 2: Add protected routes**

After the `/wheel` route block (line ~421), add:

```tsx
<Route
  path="/gift"
  element={
    <ProtectedRoute>
      <LazyPage>
        <GiftSubscription />
      </LazyPage>
    </ProtectedRoute>
  }
/>
<Route
  path="/gift/result"
  element={
    <ProtectedRoute>
      <LazyPage>
        <GiftResult />
      </LazyPage>
    </ProtectedRoute>
  }
/>
```

**Step 3: Commit**

```bash
git add src/App.tsx
git commit -m "feat: add /gift and /gift/result routes"
```

---

## Phase 6: Frontend — Navigation

### Task 13: Add gift link to navigation

**Files:**
- Modify: `bedolaga-cabinet/src/components/layout/AppShell/AppShell.tsx:206,337-351`
- Modify: `bedolaga-cabinet/src/components/layout/AppShell/AppHeader.tsx:158-168`

**Step 1: Destructure giftEnabled from useFeatureFlags**

In `AppShell.tsx` line 206, add `giftEnabled`:

```typescript
const { referralEnabled, wheelEnabled, hasContests, hasPolls, giftEnabled } = useFeatureFlags();
```

**Step 2: Add desktop nav link**

After the referral nav link block (line ~351), add:

```tsx
{giftEnabled && (
  <Link
    to="/gift"
    onClick={handleNavClick}
    className={cn(
      'flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
      isActive('/gift')
        ? 'bg-dark-800 text-dark-50'
        : 'text-dark-400 hover:bg-dark-800/50 hover:text-dark-200',
    )}
  >
    <GiftIcon className="h-4 w-4" />
    <span>{t('nav.gift')}</span>
  </Link>
)}
```

Add `GiftIcon` component near the other icon components (line ~30):

```tsx
const GiftIcon = ({ className }: { className?: string }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M21 11.25v8.25a1.5 1.5 0 01-1.5 1.5H5.25a1.5 1.5 0 01-1.5-1.5v-8.25M12 4.875A2.625 2.625 0 109.375 7.5H12m0-2.625V7.5m0-2.625A2.625 2.625 0 1114.625 7.5H12m0 0V21m-8.625-9.75h18c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125h-18c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125z" />
  </svg>
);
```

**Step 3: Pass giftEnabled to AppHeader**

Add `giftEnabled` to `AppHeader` props (line ~406-418):

```tsx
<AppHeader
  ...existing props...
  giftEnabled={giftEnabled}
/>
```

**Step 4: Add to hamburger menu in AppHeader.tsx**

In `AppHeader.tsx`, add to `navItems` array (line ~168), after referral:

```typescript
...(giftEnabled ? [{ path: '/gift', label: t('nav.gift'), icon: GiftIcon }] : []),
```

Add the same `GiftIcon` component to `AppHeader.tsx`.

**Step 5: Commit**

```bash
git add src/components/layout/AppShell/AppShell.tsx src/components/layout/AppShell/AppHeader.tsx
git commit -m "feat: add gift nav link to desktop and mobile navigation"
```

---

## Phase 7: Frontend — Internationalization

### Task 14: Add i18n translations

**Files:**
- Modify: `bedolaga-cabinet/src/locales/ru.json`
- Modify: `bedolaga-cabinet/src/locales/en.json`
- Modify: `bedolaga-cabinet/src/locales/zh.json`
- Modify: `bedolaga-cabinet/src/locales/fa.json`

**Step 1: Add nav key to all locales**

In the `nav` section of each locale, add:

```json
"gift": "Подарить подписку"   // ru
"gift": "Gift subscription"   // en
"gift": "赠送订阅"             // zh
"gift": "اشتراک هدیه"         // fa
```

**Step 2: Add gift section to all locales**

Add `gift` section (Russian example — translate for others):

```json
"gift": {
  "title": "Подарить подписку",
  "subtitle": "Отправьте VPN-подписку в подарок",
  "choosePeriod": "Выберите период",
  "chooseTariff": "Выберите тариф",
  "recipient": "Получатель",
  "recipientPlaceholder": "Email или @telegram",
  "recipientHint": "Введите email или юзернейм в Telegram",
  "giftMessage": "Поздравление",
  "giftMessagePlaceholder": "Добавьте личное сообщение (необязательно)",
  "paymentMode": "Способ оплаты",
  "fromBalance": "С баланса",
  "viaGateway": "Через платёжку",
  "yourBalance": "Ваш баланс",
  "insufficientBalance": "Недостаточно средств",
  "topUpBalance": "Пополнить баланс",
  "total": "Итого",
  "giftButton": "Подарить",
  "sending": "Отправляем подарок...",
  "successTitle": "Подарок отправлен!",
  "successDesc": "Получатель {{contact}} получит уведомление",
  "pendingTitle": "Ожидание оплаты",
  "pendingDesc": "Завершите оплату в платёжной системе",
  "pendingActivationTitle": "Ожидает активации",
  "pendingActivationDesc": "У получателя есть активная подписка. Подарок ожидает активации.",
  "failedTitle": "Ошибка",
  "failedDesc": "Не удалось отправить подарок. Попробуйте снова.",
  "backToGift": "Вернуться",
  "gb": "ГБ",
  "devices": "устройств",
  "paymentMethod": "Способ оплаты",
  "processing": "Обработка..."
}
```

**Step 3: Commit**

```bash
git add src/locales/
git commit -m "feat: add gift subscription i18n translations"
```

---

## Phase 8: Integration & Polish

### Task 15: Wire up gateway payment for gifts (optional MVP+)

**Files:**
- Modify: `app/cabinet/routes/gift.py` (the `_create_gift_payment` stub)
- Modify: Payment webhook handlers to recognize gift purchases

This task wires the payment gateway for gift purchases. For MVP, balance mode is sufficient. Implement this when balance-only is validated.

The approach:
1. In `_create_gift_payment`, parse `payment_method` string (e.g., `platega_2`) into method + sub-option
2. Call `PaymentService.create_*_payment()` with gift-specific metadata including `purchase.token`
3. In the payment webhook handler (e.g., `app/external/payment_webhooks.py`), when payment succeeds, check if metadata contains a gift purchase token
4. If yes, call `guest_purchase_service.fulfill_purchase(db, token)` to deliver the gift

### Task 16: Admin settings UI for gift toggle

**Files:**
- Modify: `bedolaga-cabinet/src/pages/AdminSettings.tsx` (if settings are listed there)

Add a toggle for "Gift subscriptions in cabinet" that calls `PATCH /cabinet/branding/gift-enabled`.

This follows the same pattern as the existing lite-mode or animation toggles in admin settings.

### Task 17: Final verification

**Step 1: Run backend linting**

```bash
cd /Users/ea/Desktop/DEV/remnawave-bedolaga-telegram-bot
make lint
make fix  # if needed
```

**Step 2: Run frontend type check**

```bash
cd /Users/ea/Desktop/DEV/bedolaga-cabinet
npx tsc --noEmit
```

**Step 3: Test the flow manually**

1. Enable gift feature: `PATCH /cabinet/branding/gift-enabled` → `{"enabled": true}`
2. Open cabinet → verify "Gift" appears in nav
3. Go to `/gift` → select tariff, period, enter recipient
4. Purchase with balance → verify success
5. Verify recipient receives notification (Telegram or email)
6. Disable feature → verify nav link disappears

---

## Execution Order & Dependencies

```
Phase 1 (Model)   → Task 1, 2 (sequential)
Phase 2 (Toggle)  → Task 3 (independent)
Phase 3 (Routes)  → Task 4, 5, 6, 7 (sequential, depends on Phase 1)
Phase 4 (API/Flag) → Task 8, 9 (parallel, depends on Phase 2-3)
Phase 5 (Pages)   → Task 10, 11 (parallel, depends on Phase 4)
Phase 6 (Nav)     → Task 13 (depends on Phase 4)
Phase 7 (i18n)    → Task 14 (independent, can run anytime)
Phase 8 (Polish)  → Task 15-17 (depends on all above)
```

**Parallelizable groups:**
- Tasks 1-3 can be done together (model + toggle = independent)
- Tasks 8+9 can be parallelized
- Tasks 10+11+14 can be parallelized
- Task 13 can run with 10+11

**Estimated tasks:** 17 tasks across 8 phases
