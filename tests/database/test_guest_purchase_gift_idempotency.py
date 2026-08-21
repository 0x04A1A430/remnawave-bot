"""Database level idempotency tests for guest_purchases idempotency_key column and index."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.models import GuestPurchase, GuestPurchaseStatus, Tariff, User
from tests.fixtures.sqlite_memory import memory_session


_TABLES = [
    Tariff.__table__,
    User.__table__,
    GuestPurchase.__table__,
]


def test_guest_purchase_model_has_idempotency_key_column():
    """GuestPurchase model must have idempotency_key column and ux_guest_purchases_idempotency_key index."""
    assert 'idempotency_key' in GuestPurchase.__table__.columns
    col = GuestPurchase.__table__.columns['idempotency_key']
    assert col.nullable is True
    assert col.type.length == 64

    # Check unique index or unique constraint
    indexes = {idx.name: idx for idx in GuestPurchase.__table__.indexes}
    assert 'ux_guest_purchases_idempotency_key' in indexes
    assert indexes['ux_guest_purchases_idempotency_key'].unique is True


@pytest.mark.asyncio
async def test_multiple_null_idempotency_keys_are_allowed(monkeypatch):
    """Multiple legacy guest purchases with NULL idempotency_key must be allowed."""
    async with memory_session(monkeypatch, _TABLES) as db:
        p1 = GuestPurchase(
            token='tok_legacy_1_' + 'x' * 40,
            contact_type='telegram',
            contact_value='@user1',
            period_days=30,
            amount_kopeks=10000,
            status=GuestPurchaseStatus.PENDING.value,
            idempotency_key=None,
        )
        p2 = GuestPurchase(
            token='tok_legacy_2_' + 'x' * 40,
            contact_type='telegram',
            contact_value='@user2',
            period_days=30,
            amount_kopeks=10000,
            status=GuestPurchaseStatus.PENDING.value,
            idempotency_key=None,
        )
        db.add_all([p1, p2])
        await db.commit()

        assert p1.id is not None
        assert p2.id is not None


@pytest.mark.asyncio
async def test_duplicate_non_null_idempotency_key_is_rejected(monkeypatch):
    """Duplicate non-null idempotency_key must trigger uniqueness violation."""
    async with memory_session(monkeypatch, _TABLES) as db:
        p1 = GuestPurchase(
            token='tok_key_1_' + 'x' * 42,
            contact_type='telegram',
            contact_value='@user1',
            period_days=30,
            amount_kopeks=10000,
            status=GuestPurchaseStatus.PENDING.value,
            idempotency_key='test-idempotency-key-12345',
        )
        db.add(p1)
        await db.commit()

        p2 = GuestPurchase(
            token='tok_key_2_' + 'x' * 42,
            contact_type='telegram',
            contact_value='@user2',
            period_days=30,
            amount_kopeks=10000,
            status=GuestPurchaseStatus.PENDING.value,
            idempotency_key='test-idempotency-key-12345',
        )
        db.add(p2)
        with pytest.raises(IntegrityError):
            await db.commit()
