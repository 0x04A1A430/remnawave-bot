from fastapi import APIRouter, Depends, HTTPException, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.crud.user import get_user_by_id
from app.database.models import UserApiToken

from ..dependencies import get_db_session, require_user_api_token
from ..schemas.user_api import UserApiProfileResponse


router = APIRouter()


@router.get("/profile", response_model=UserApiProfileResponse)
async def get_profile(
    token: UserApiToken = Security(require_user_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> UserApiProfileResponse:
    user = token.user

    if user is None:
        user = await get_user_by_id(db, token.user_id)
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    balance_rubles = round(user.balance_kopeks / 100, 2)

    return UserApiProfileResponse(
        user_id=user.id,
        telegram_id=user.telegram_id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        status=user.status,
        language=user.language,
        balance_kopeks=user.balance_kopeks,
        balance_rubles=balance_rubles,
        created_at=user.created_at,
        updated_at=user.updated_at,
        last_activity=user.last_activity,
        api_token_prefix=token.token_prefix,
        api_token_last_digits=token.token_last_digits,
    )
