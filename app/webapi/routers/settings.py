from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_settings_service import bot_configuration_service
from app.webapi.dependencies import get_db, require_permission
from app.webapi.schemas import SettingResponse, SettingsListResponse, SettingUpdateRequest

router = APIRouter(prefix="/settings")


def _build_setting_response(key: str) -> SettingResponse:
    summary = bot_configuration_service.get_setting_summary(key)
    return SettingResponse(
        key=summary["key"],
        name=summary["name"],
        value=summary["current"],
        original=summary["original"],
        has_override=summary["has_override"],
        category_key=summary["category_key"],
        category_label=summary["category_label"],
        type=summary["type"],
    )


@router.get("", response_model=SettingsListResponse)
async def list_settings(
    category: Optional[str] = Query(default=None),
    _: object = Depends(require_permission("webapi.settings:read")),
) -> SettingsListResponse:
    bot_configuration_service.initialize_definitions()

    if category:
        definitions = bot_configuration_service.get_settings_for_category(category)
    else:
        definitions = list(bot_configuration_service._definitions.values())  # type: ignore[attr-defined]

    definitions = sorted(definitions, key=lambda definition: definition.display_name)

    items = [_build_setting_response(definition.key) for definition in definitions]
    return SettingsListResponse(items=items)


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(
    key: str = Path(..., min_length=1),
    _: object = Depends(require_permission("webapi.settings:read")),
) -> SettingResponse:
    try:
        bot_configuration_service.get_definition(key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Настройка не найдена")

    return _build_setting_response(key)


@router.put("/{key}", response_model=SettingResponse)
async def update_setting(
    payload: SettingUpdateRequest,
    key: str = Path(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.settings:write")),
) -> SettingResponse:
    try:
        bot_configuration_service.get_definition(key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Настройка не найдена")

    try:
        if payload.value is None:
            parsed_value = None
        else:
            parsed_value = bot_configuration_service.parse_user_value(key, str(payload.value))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))

    await bot_configuration_service.set_value(db, key, parsed_value)
    return _build_setting_response(key)


@router.delete("/{key}", response_model=SettingResponse)
async def reset_setting(
    key: str = Path(..., min_length=1),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission("webapi.settings:write")),
) -> SettingResponse:
    try:
        bot_configuration_service.get_definition(key)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Настройка не найдена")

    await bot_configuration_service.reset_value(db, key)
    return _build_setting_response(key)
