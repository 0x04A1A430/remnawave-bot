from fastapi import APIRouter, Depends, HTTPException, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.tariff import (
    create_tariff,
    delete_tariff,
    get_tariff_by_id,
    list_tariffs,
    update_tariff,
)
from app.database.models import SubscriptionTariff

from ..dependencies import get_db_session, require_api_token
from ..schemas.tariffs import (
    TariffCreateRequest,
    TariffResponse,
    TariffUpdateRequest,
    TariffPricePayload,
)

router = APIRouter()


def _serialize_tariff(tariff: SubscriptionTariff) -> TariffResponse:
    return TariffResponse(
        id=tariff.id,
        name=tariff.name,
        description=tariff.description,
        traffic_limit_gb=tariff.traffic_limit_gb,
        device_limit=tariff.device_limit,
        is_active=tariff.is_active,
        sort_order=tariff.sort_order,
        server_squads=[server.squad_uuid for server in tariff.server_squads if getattr(server, "squad_uuid", None)],
        promo_group_ids=[group.id for group in tariff.promo_groups if group is not None],
        prices=[
            TariffPricePayload(period_days=price.period_days, price_kopeks=price.price_kopeks)
            for price in sorted(tariff.prices, key=lambda item: item.period_days)
        ],
        created_at=tariff.created_at,
        updated_at=tariff.updated_at,
    )


@router.get("", response_model=list[TariffResponse], tags=["tariffs"])
async def list_subscription_tariffs(
    include_inactive: bool = False,
    _: str = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> list[TariffResponse]:
    tariffs = await list_tariffs(db, include_inactive=include_inactive)
    return [_serialize_tariff(tariff) for tariff in tariffs]


@router.get("/{tariff_id}", response_model=TariffResponse, tags=["tariffs"])
async def get_subscription_tariff(
    tariff_id: int,
    _: str = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TariffResponse:
    tariff = await get_tariff_by_id(db, tariff_id, include_inactive=True)
    if not tariff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tariff not found")
    return _serialize_tariff(tariff)


@router.post("", response_model=TariffResponse, status_code=status.HTTP_201_CREATED, tags=["tariffs"])
async def create_subscription_tariff(
    payload: TariffCreateRequest,
    _: str = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TariffResponse:
    tariff = await create_tariff(
        db,
        name=payload.name,
        description=payload.description,
        traffic_limit_gb=payload.traffic_limit_gb,
        device_limit=payload.device_limit,
        server_uuids=payload.server_squads,
        promo_group_ids=payload.promo_group_ids,
        prices=[price.model_dump() for price in payload.prices],
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    return _serialize_tariff(tariff)


@router.put("/{tariff_id}", response_model=TariffResponse, tags=["tariffs"])
async def update_subscription_tariff(
    tariff_id: int,
    payload: TariffUpdateRequest,
    _: str = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> TariffResponse:
    tariff = await update_tariff(
        db,
        tariff_id,
        name=payload.name,
        description=payload.description,
        traffic_limit_gb=payload.traffic_limit_gb,
        device_limit=payload.device_limit,
        server_uuids=payload.server_squads,
        promo_group_ids=payload.promo_group_ids,
        prices=[price.model_dump() for price in payload.prices] if payload.prices is not None else None,
        is_active=payload.is_active,
        sort_order=payload.sort_order,
    )
    if not tariff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tariff not found")
    return _serialize_tariff(tariff)


@router.delete("/{tariff_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["tariffs"])
async def delete_subscription_tariff(
    tariff_id: int,
    _: str = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    success = await delete_tariff(db, tariff_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to delete tariff")

    return None
