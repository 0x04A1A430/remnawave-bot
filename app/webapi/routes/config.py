from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.system_settings_service import bot_configuration_service

from ..dependencies import get_db_session, require_api_token

router = APIRouter()


def _coerce_value(key: str, value: Any) -> Any:
    definition = bot_configuration_service.get_definition(key)

    if value is None:
        if definition.is_optional:
            return None
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Value is required")

    python_type = definition.python_type

    try:
        if python_type is bool:
            if isinstance(value, bool):
                normalized = value
            elif isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "yes", "on", "да"}:
                    normalized = True
                elif lowered in {"false", "0", "no", "off", "нет"}:
                    normalized = False
                else:
                    raise ValueError("invalid bool")
            else:
                raise ValueError("invalid bool")

        elif python_type is int:
            normalized = int(value)
        elif python_type is float:
            normalized = float(value)
        else:
            normalized = str(value)
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid value type") from None

    choices = bot_configuration_service.get_choice_options(key)
    if choices:
        allowed_values = {option.value for option in choices}
        if normalized not in allowed_values:
            readable = ", ".join(bot_configuration_service.format_value(opt.value) for opt in choices)
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Value must be one of: {readable}",
            )

    return normalized


def _serialize_definition(definition, include_choices: bool = True) -> dict[str, Any]:
    current = bot_configuration_service.get_current_value(definition.key)
    original = bot_configuration_service.get_original_value(definition.key)
    has_override = bot_configuration_service.has_override(definition.key)

    payload: dict[str, Any] = {
        "key": definition.key,
        "name": definition.display_name,
        "category": {
            "key": definition.category_key,
            "label": definition.category_label,
        },
        "type": definition.type_label,
        "is_optional": definition.is_optional,
        "current": current,
        "original": original,
        "has_override": has_override,
    }

    if include_choices:
        choices = [
            {
                "value": option.value,
                "label": option.label,
                "description": option.description,
            }
            for option in bot_configuration_service.get_choice_options(definition.key)
        ]
        if choices:
            payload["choices"] = choices

    return payload


@router.get("/categories")
async def list_categories(_: object = Depends(require_api_token)) -> list[dict[str, Any]]:
    categories = bot_configuration_service.get_categories()
    return [
        {"key": key, "label": label, "items": count}
        for key, label, count in categories
    ]


@router.get("")
async def list_settings(
    _: object = Depends(require_api_token),
    category: Optional[str] = Query(default=None, alias="category_key"),
) -> list[dict[str, Any]]:
    items = []
    if category:
        definitions = bot_configuration_service.get_settings_for_category(category)
        items.extend(_serialize_definition(defn) for defn in definitions)
        return items

    for category_key, _, _ in bot_configuration_service.get_categories():
        definitions = bot_configuration_service.get_settings_for_category(category_key)
        items.extend(_serialize_definition(defn) for defn in definitions)

    return items


@router.get("/{key}")
async def get_setting(
    key: str,
    _: object = Depends(require_api_token),
) -> dict[str, Any]:
    try:
        definition = bot_configuration_service.get_definition(key)
    except KeyError as error:  # pragma: no cover - защита от некорректного ключа
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Setting not found") from error

    return _serialize_definition(definition)


@router.put("/{key}")
async def update_setting(
    key: str,
    payload: dict[str, Any],
    _: object = Depends(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        definition = bot_configuration_service.get_definition(key)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Setting not found") from error

    if "value" not in payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing value")

    value = _coerce_value(key, payload["value"])
    await bot_configuration_service.set_value(db, key, value)
    await db.commit()

    return _serialize_definition(definition)


@router.delete("/{key}")
async def reset_setting(
    key: str,
    _: object = Depends(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    try:
        definition = bot_configuration_service.get_definition(key)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Setting not found") from error

    await bot_configuration_service.reset_value(db, key)
    await db.commit()
    return _serialize_definition(definition)
