from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.tariff import (
    get_active_tariffs_for_promo_group,
    get_tariff_by_id,
)
from app.database.models import SubscriptionTariff, User


class TariffService:
    @staticmethod
    async def get_available_tariffs(
        db: AsyncSession,
        user: Optional[User],
    ) -> List[SubscriptionTariff]:
        promo_group_id = getattr(user, "promo_group_id", None) if user else None
        tariffs = await get_active_tariffs_for_promo_group(db, promo_group_id)
        return sorted(tariffs, key=lambda tariff: (tariff.sort_order, tariff.id))

    @staticmethod
    async def get_tariff_for_user(
        db: AsyncSession,
        tariff_id: int,
        user: Optional[User],
    ) -> Optional[SubscriptionTariff]:
        promo_group_id = getattr(user, "promo_group_id", None) if user else None
        tariff = await get_tariff_by_id(db, tariff_id, include_inactive=False)
        if not tariff:
            return None

        available_servers = [
            server
            for server in tariff.server_squads
            if server.is_available and not server.is_full
        ]
        if not available_servers:
            return None

        if not tariff.is_available_for_promo_group(promo_group_id):
            return None

        tariff.server_squads = available_servers
        return tariff
