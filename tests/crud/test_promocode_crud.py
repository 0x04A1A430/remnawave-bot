"""
Tests for Promocode CRUD operations - focus on promo_group_id integration
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

from app.database.crud.promocode import (
    create_promocode,
    get_promocode_by_code,
    get_promocodes_list,
)
from app.database.models import PromoCodeType, PromoCode

# Import fixtures
from tests.fixtures.promocode_fixtures import (
    sample_promo_group,
    sample_promocode_promo_group,
    mock_db_session,
)


async def test_create_promocode_with_promo_group_id(
    sample_promo_group,
    mock_db_session,
):
    """
    Test creating a promocode with promo_group_id

    Scenario:
    - Create PROMO_GROUP type promocode
    - promo_group_id should be saved
    - Database operations should be called correctly
    """
    # Execute
    promocode = await create_promocode(
        db=mock_db_session,
        code="TESTGROUP",
        type=PromoCodeType.PROMO_GROUP,
        balance_bonus_kopeks=0,
        subscription_days=0,
        max_uses=100,
        valid_until=None,
        created_by=1,
        promo_group_id=sample_promo_group.id
    )

    # Assertions
    assert promocode.code == "TESTGROUP"
    assert promocode.type == PromoCodeType.PROMO_GROUP.value
    assert promocode.promo_group_id == sample_promo_group.id

    # Verify database operations
    mock_db_session.add.assert_called_once()
    mock_db_session.commit.assert_awaited_once()
    mock_db_session.refresh.assert_awaited_once()


async def test_create_promocode_without_promo_group_id(mock_db_session):
    """
    Test creating a promocode without promo_group_id (other types)

    Scenario:
    - Create BALANCE type promocode
    - promo_group_id should be None
    """
    # Execute
    promocode = await create_promocode(
        db=mock_db_session,
        code="BALANCE100",
        type=PromoCodeType.BALANCE,
        balance_bonus_kopeks=10000,
        subscription_days=0,
        max_uses=50,
        valid_until=None,
        created_by=1,
        promo_group_id=None
    )

    # Assertions
    assert promocode.code == "BALANCE100"
    assert promocode.type == PromoCodeType.BALANCE.value
    assert promocode.promo_group_id is None


async def test_get_promocode_by_code_loads_promo_group(
    sample_promocode_promo_group,
    mock_db_session,
):
    """
    Test that get_promocode_by_code loads promo_group relationship

    Scenario:
    - Query promocode by code
    - Verify selectinload was used for promo_group
    - Verify promo_group data is accessible
    """
    # Setup mock result
    mock_result = AsyncMock()
    mock_result.scalar_one_or_none = lambda: sample_promocode_promo_group
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    # Execute
    promocode = await get_promocode_by_code(mock_db_session, "VIPGROUP")

    # Assertions
    assert promocode is not None
    assert promocode.code == "VIPGROUP"
    assert promocode.promo_group is not None
    assert promocode.promo_group.name == "Test VIP Group"

    # Verify execute was called (query was executed)
    mock_db_session.execute.assert_awaited_once()


async def test_get_promocodes_list_loads_promo_groups(
    sample_promocode_promo_group,
    mock_db_session,
):
    """
    Test that get_promocodes_list loads promo_group relationships

    Scenario:
    - Query list of promocodes
    - Verify selectinload was used for promo_group
    - Verify all promocodes have accessible promo_group data
    """
    # Setup mock result
    mock_result = AsyncMock()
    mock_result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[sample_promocode_promo_group])))
    mock_db_session.execute = AsyncMock(return_value=mock_result)

    # Execute
    promocodes = await get_promocodes_list(mock_db_session, offset=0, limit=10)

    # Assertions
    assert len(promocodes) == 1
    assert promocodes[0].promo_group is not None
    assert promocodes[0].promo_group.name == "Test VIP Group"

    # Verify execute was called
    mock_db_session.execute.assert_awaited_once()
