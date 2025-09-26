from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.crud import api_token as api_token_crud
from app.database.models import ApiToken
from app.webapi.dependencies import get_current_token, get_db, require_permission
from app.webapi.schemas import (
    APIMessage,
    TokenCreateRequest,
    TokenResponse,
    TokenUpdateRequest,
    TokenWithSecretResponse,
)

router = APIRouter(prefix="/auth")


def _token_to_response(token: ApiToken) -> TokenResponse:
    return TokenResponse(
        id=token.id,
        name=token.name,
        description=token.description,
        permissions=token.permissions or [],
        allowed_ips=token.allowed_ips or [],
        is_active=token.is_active,
        token_prefix=token.token_prefix,
        created_at=token.created_at,
        updated_at=token.updated_at,
        last_used_at=token.last_used_at,
        expires_at=token.expires_at,
    )


@router.post("/token", response_model=TokenWithSecretResponse, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: TokenCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    if not settings.WEBAPI_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WEBAPI_MASTER_KEY не задан в конфигурации",
        )

    if payload.secret != settings.WEBAPI_MASTER_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Неверный секрет",
        )

    plain_token = api_token_crud.generate_token()

    expires_at = None
    if payload.expires_in_hours:
        expires_at = datetime.utcnow() + timedelta(hours=payload.expires_in_hours)
    else:
        ttl = settings.get_webapi_token_ttl()
        if ttl:
            expires_at = datetime.utcnow() + ttl

    api_token = await api_token_crud.create_api_token(
        db,
        name=payload.name,
        token=plain_token,
        description=payload.description,
        permissions=payload.permissions,
        allowed_ips=payload.allowed_ips,
        expires_at=expires_at,
    )

    response = TokenWithSecretResponse(
        **_token_to_response(api_token).model_dump(),
        token=plain_token,
    )
    return response


@router.get("/tokens", response_model=List[TokenResponse])
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    _: ApiToken = Depends(require_permission("webapi.tokens:read")),
) -> List[TokenResponse]:
    tokens = await api_token_crud.list_api_tokens(db)
    return [_token_to_response(token) for token in tokens]


@router.patch("/tokens/{token_id}", response_model=TokenResponse)
async def update_token(
    payload: TokenUpdateRequest,
    token_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: ApiToken = Depends(require_permission("webapi.tokens:write")),
) -> TokenResponse:
    token = await api_token_crud.get_api_token_by_id(db, token_id)
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Токен не найден")

    updated = await api_token_crud.update_api_token(
        db,
        token,
        name=payload.name,
        description=payload.description,
        permissions=payload.permissions,
        allowed_ips=payload.allowed_ips,
        expires_at=payload.expires_at,
        is_active=payload.is_active,
    )
    return _token_to_response(updated)


@router.post("/tokens/{token_id}/revoke", response_model=TokenResponse)
async def revoke_token(
    token_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: ApiToken = Depends(require_permission("webapi.tokens:write")),
) -> TokenResponse:
    token = await api_token_crud.get_api_token_by_id(db, token_id)
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Токен не найден")

    revoked = await api_token_crud.deactivate_api_token(db, token)
    return _token_to_response(revoked)


@router.delete("/tokens/{token_id}", response_model=APIMessage)
async def delete_token(
    token_id: int = Path(..., ge=1),
    db: AsyncSession = Depends(get_db),
    _: ApiToken = Depends(require_permission("webapi.tokens:write")),
) -> APIMessage:
    token = await api_token_crud.get_api_token_by_id(db, token_id)
    if not token:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Токен не найден")

    await api_token_crud.delete_api_token(db, token_id)
    return APIMessage(message="Токен удален")


@router.get("/me", response_model=TokenResponse)
async def whoami(current_token: ApiToken = Depends(get_current_token)) -> TokenResponse:
    return _token_to_response(current_token)
