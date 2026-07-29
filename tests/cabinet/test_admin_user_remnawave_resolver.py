"""Contract tests for exact subscription-level Remnawave resolution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.cabinet.routes import admin_users


UUID = '11111111-1111-4111-8111-111111111111'


def _resolver():
    resolver = getattr(admin_users, 'get_user_by_remnawave_uuid', None)
    assert resolver is not None, 'The exact subscription resolver route must exist'
    return resolver


def _db_with_matches(*matches):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(matches)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _subscription(*, subscription_id: int, user_id: int, remnawave_uuid: str | None = UUID):
    return SimpleNamespace(id=subscription_id, user_id=user_id, remnawave_uuid=remnawave_uuid)


def test_resolver_route_is_registered_before_user_id_route(registered_paths) -> None:
    assert 'GET' in registered_paths.get('/cabinet/admin/users/by-remnawave/{remnawave_uuid}', set())


def test_resolver_requires_users_read_permission() -> None:
    resolver = _resolver()
    route = next(route for route in admin_users.router.routes if route.endpoint is resolver)
    permission_dependency = route.dependant.dependencies[0].call
    closure_values = [cell.cell_contents for cell in permission_dependency.__closure__ or ()]
    assert ('users:read',) in closure_values


@pytest.mark.parametrize(
    ('subscription_id', 'user_id'),
    [(101, 7), (202, 7)],
    ids=['primary-subscription', 'non-primary-subscription'],
)
async def test_resolver_returns_the_exact_matching_subscription(subscription_id: int, user_id: int) -> None:
    """Would fail if the resolver returned a user-level or primary subscription ID."""
    subscription = _subscription(subscription_id=subscription_id, user_id=user_id)
    db = _db_with_matches(subscription)

    response = await _resolver()(UUID, admin=SimpleNamespace(id=1), db=db)

    assert response.model_dump() == {
        'user_id': user_id,
        'subscription_id': subscription_id,
        'matched_remnawave_uuid': UUID,
    }


@pytest.mark.parametrize(
    'path_value',
    [
        '22222222-2222-4222-8222-222222222222',
        None,
        'not-a-uuid',
    ],
    ids=['unknown-uuid', 'null-uuid', 'malformed-uuid'],
)
async def test_resolver_rejects_unlinked_or_invalid_uuid_without_a_heuristic_lookup(path_value) -> None:
    """Would fail if unknown, null, or malformed input were guessed from user data."""
    db = _db_with_matches()

    with pytest.raises(HTTPException) as exc:
        await _resolver()(path_value, admin=SimpleNamespace(id=1), db=db)

    assert exc.value.status_code in {400, 404}
    if path_value in (None, 'not-a-uuid'):
        db.execute.assert_not_awaited()


async def test_resolver_rejects_a_uuid_present_only_on_the_legacy_user_field() -> None:
    """Would fail if the route reused legacy get_user_by_remnawave_uuid behavior."""
    db = _db_with_matches()

    with pytest.raises(HTTPException) as exc:
        await _resolver()(UUID, admin=SimpleNamespace(id=1), db=db)

    assert exc.value.status_code == 404
    query = str(db.execute.await_args.args[0])
    assert 'subscriptions.remnawave_uuid' in query
    assert 'users.remnawave_uuid' not in query


async def test_resolver_treats_a_physically_absent_deleted_subscription_as_not_found() -> None:
    """Would fail if absent/deleted records were accidentally resolved."""
    db = _db_with_matches()

    with pytest.raises(HTTPException) as exc:
        await _resolver()(UUID, admin=SimpleNamespace(id=1), db=db)

    assert exc.value.status_code == 404


async def test_resolver_rejects_duplicate_subscription_mappings_as_a_conflict() -> None:
    """Would fail if corrupted mappings silently selected one subscription."""
    db = _db_with_matches(
        _subscription(subscription_id=101, user_id=7),
        _subscription(subscription_id=202, user_id=8),
    )

    with pytest.raises(HTTPException) as exc:
        await _resolver()(UUID, admin=SimpleNamespace(id=1), db=db)

    assert exc.value.status_code == 409
