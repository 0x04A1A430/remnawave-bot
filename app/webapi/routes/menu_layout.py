"""API эндпоинты для конструктора меню."""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException, Response, Security, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.menu_layout_service import (
    MenuContext,
    MenuLayoutService,
)

from ..dependencies import get_db_session, require_api_token
from ..schemas.menu_layout import (
    AddCustomButtonRequest,
    AddRowRequest,
    BuiltinButtonInfo,
    BuiltinButtonsListResponse,
    ButtonConditions,
    ButtonUpdateRequest,
    MenuButtonConfig,
    MenuLayoutConfig,
    MenuLayoutResponse,
    MenuLayoutUpdateRequest,
    MenuPreviewButton,
    MenuPreviewRequest,
    MenuPreviewResponse,
    MenuPreviewRow,
    MenuRowConfig,
    MoveButtonResponse,
    MoveButtonToRowRequest,
    ReorderButtonsInRowRequest,
    ReorderButtonsResponse,
    RowsReorderRequest,
    SwapButtonsRequest,
    SwapButtonsResponse,
)


router = APIRouter()


def _serialize_config(config: dict, is_enabled: bool, updated_at) -> MenuLayoutResponse:
    """Сериализовать конфигурацию в response."""
    rows = []
    for row_data in config.get("rows", []):
        rows.append(
            MenuRowConfig(
                id=row_data["id"],
                buttons=row_data.get("buttons", []),
                conditions=ButtonConditions(**row_data["conditions"])
                if row_data.get("conditions")
                else None,
                max_per_row=row_data.get("max_per_row", 2),
            )
        )

    buttons = {}
    for btn_id, btn_data in config.get("buttons", {}).items():
        buttons[btn_id] = MenuButtonConfig(
            type=btn_data["type"],
            builtin_id=btn_data.get("builtin_id"),
            text=btn_data.get("text", {}),
            action=btn_data.get("action", ""),
            enabled=btn_data.get("enabled", True),
            visibility=btn_data.get("visibility", "all"),
            conditions=ButtonConditions(**btn_data["conditions"])
            if btn_data.get("conditions")
            else None,
            dynamic_text=btn_data.get("dynamic_text", False),
        )

    return MenuLayoutResponse(
        version=config.get("version", 1),
        rows=rows,
        buttons=buttons,
        is_enabled=is_enabled,
        updated_at=updated_at,
    )


@router.get("", response_model=MenuLayoutResponse)
async def get_menu_layout(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MenuLayoutResponse:
    """Получить текущую конфигурацию меню."""
    config = await MenuLayoutService.get_config(db)
    updated_at = await MenuLayoutService.get_config_updated_at(db)
    return _serialize_config(config, settings.MENU_LAYOUT_ENABLED, updated_at)


@router.put("", response_model=MenuLayoutResponse)
async def update_menu_layout(
    payload: MenuLayoutUpdateRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MenuLayoutResponse:
    """Обновить конфигурацию меню полностью."""
    config = await MenuLayoutService.get_config(db)
    config = config.copy()

    if payload.rows is not None:
        config["rows"] = [row.model_dump() for row in payload.rows]

    if payload.buttons is not None:
        config["buttons"] = {
            btn_id: btn.model_dump() for btn_id, btn in payload.buttons.items()
        }

    await MenuLayoutService.save_config(db, config)
    updated_at = await MenuLayoutService.get_config_updated_at(db)
    return _serialize_config(config, settings.MENU_LAYOUT_ENABLED, updated_at)


@router.post("/reset", response_model=MenuLayoutResponse)
async def reset_menu_layout(
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MenuLayoutResponse:
    """Сбросить конфигурацию к дефолтной."""
    config = await MenuLayoutService.reset_to_default(db)
    updated_at = await MenuLayoutService.get_config_updated_at(db)
    return _serialize_config(config, settings.MENU_LAYOUT_ENABLED, updated_at)


@router.get("/builtin-buttons", response_model=BuiltinButtonsListResponse)
async def list_builtin_buttons(
    _: Any = Security(require_api_token),
) -> BuiltinButtonsListResponse:
    """Получить список встроенных кнопок."""
    items = []
    for btn_info in MenuLayoutService.get_builtin_buttons_info():
        items.append(
            BuiltinButtonInfo(
                id=btn_info["id"],
                default_text=btn_info["default_text"],
                callback_data=btn_info["callback_data"],
                default_conditions=ButtonConditions(**btn_info["default_conditions"])
                if btn_info.get("default_conditions")
                else None,
                supports_dynamic_text=btn_info.get("supports_dynamic_text", False),
            )
        )

    return BuiltinButtonsListResponse(items=items, total=len(items))


@router.patch("/buttons/{button_id}")
async def update_button(
    button_id: str,
    payload: ButtonUpdateRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MenuButtonConfig:
    """Обновить конфигурацию отдельной кнопки."""
    try:
        updates = payload.model_dump(exclude_unset=True)
        # Конвертируем visibility в строку если есть
        if "visibility" in updates and updates["visibility"] is not None:
            if hasattr(updates["visibility"], "value"):
                updates["visibility"] = updates["visibility"].value
        # Конвертируем conditions - убираем None значения если это dict
        if "conditions" in updates and updates["conditions"] is not None:
            if isinstance(updates["conditions"], dict):
                updates["conditions"] = {k: v for k, v in updates["conditions"].items() if v is not None}
            elif hasattr(updates["conditions"], "model_dump"):
                updates["conditions"] = updates["conditions"].model_dump(exclude_none=True)

        button = await MenuLayoutService.update_button(db, button_id, updates)

        return MenuButtonConfig(
            type=button["type"],
            builtin_id=button.get("builtin_id"),
            text=button.get("text", {}),
            action=button.get("action", ""),
            enabled=button.get("enabled", True),
            visibility=button.get("visibility", "all"),
            conditions=ButtonConditions(**button["conditions"])
            if button.get("conditions")
            else None,
            dynamic_text=button.get("dynamic_text", False),
        )
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.post("/rows/reorder", response_model=List[MenuRowConfig])
async def reorder_rows(
    payload: RowsReorderRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> List[MenuRowConfig]:
    """Изменить порядок строк."""
    try:
        rows = await MenuLayoutService.reorder_rows(db, payload.ordered_ids)
        return [
            MenuRowConfig(
                id=row["id"],
                buttons=row.get("buttons", []),
                conditions=ButtonConditions(**row["conditions"])
                if row.get("conditions")
                else None,
                max_per_row=row.get("max_per_row", 2),
            )
            for row in rows
        ]
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.post("/rows", response_model=MenuRowConfig, status_code=status.HTTP_201_CREATED)
async def add_row(
    payload: AddRowRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MenuRowConfig:
    """Добавить новую строку."""
    try:
        row_config = {
            "id": payload.id,
            "buttons": payload.buttons,
            "conditions": payload.conditions.model_dump(exclude_none=True)
            if payload.conditions
            else None,
            "max_per_row": payload.max_per_row,
        }
        row = await MenuLayoutService.add_row(db, row_config, payload.position)

        return MenuRowConfig(
            id=row["id"],
            buttons=row.get("buttons", []),
            conditions=ButtonConditions(**row["conditions"])
            if row.get("conditions")
            else None,
            max_per_row=row.get("max_per_row", 2),
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.delete("/rows/{row_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_row(
    row_id: str,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Удалить строку."""
    try:
        await MenuLayoutService.delete_row(db, row_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.post(
    "/buttons", response_model=MenuButtonConfig, status_code=status.HTTP_201_CREATED
)
async def add_custom_button(
    payload: AddCustomButtonRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MenuButtonConfig:
    """Добавить кастомную кнопку (URL или MiniApp)."""
    try:
        button_config = {
            "type": payload.type.value,
            "text": payload.text,
            "action": payload.action,
            "visibility": payload.visibility.value,
            "conditions": payload.conditions.model_dump(exclude_none=True)
            if payload.conditions
            else None,
        }
        button = await MenuLayoutService.add_custom_button(
            db, payload.id, button_config, payload.row_id
        )

        return MenuButtonConfig(
            type=button["type"],
            builtin_id=button.get("builtin_id"),
            text=button.get("text", {}),
            action=button.get("action", ""),
            enabled=button.get("enabled", True),
            visibility=button.get("visibility", "all"),
            conditions=ButtonConditions(**button["conditions"])
            if button.get("conditions")
            else None,
            dynamic_text=button.get("dynamic_text", False),
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.delete("/buttons/{button_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_custom_button(
    button_id: str,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Удалить кастомную кнопку."""
    try:
        await MenuLayoutService.delete_custom_button(db, button_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/preview", response_model=MenuPreviewResponse)
async def preview_menu(
    payload: MenuPreviewRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MenuPreviewResponse:
    """Предпросмотр меню для указанного контекста пользователя."""
    context = MenuContext(
        language=payload.language,
        is_admin=payload.is_admin,
        is_moderator=payload.is_moderator,
        has_active_subscription=payload.has_active_subscription,
        subscription_is_active=payload.subscription_is_active,
        balance_kopeks=payload.balance_kopeks,
    )

    preview_rows = await MenuLayoutService.preview_keyboard(db, context)

    rows = []
    total_buttons = 0
    for row_data in preview_rows:
        buttons = [
            MenuPreviewButton(
                text=btn["text"],
                action=btn["action"],
                type=btn["type"],
            )
            for btn in row_data["buttons"]
        ]
        total_buttons += len(buttons)
        rows.append(MenuPreviewRow(buttons=buttons))

    return MenuPreviewResponse(rows=rows, total_buttons=total_buttons)


# --- Эндпоинты для перемещения кнопок ---


@router.post("/buttons/{button_id}/move-up", response_model=MoveButtonResponse)
async def move_button_up(
    button_id: str,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MoveButtonResponse:
    """Переместить кнопку вверх (в предыдущую строку или на позицию выше в текущей строке)."""
    try:
        result = await MenuLayoutService.move_button_up(db, button_id)
        return MoveButtonResponse(
            button_id=button_id,
            new_row_index=result.get("new_row_index"),
            position=result.get("new_position"),
        )
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/buttons/{button_id}/move-down", response_model=MoveButtonResponse)
async def move_button_down(
    button_id: str,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MoveButtonResponse:
    """Переместить кнопку вниз (в следующую строку или на позицию ниже в текущей строке)."""
    try:
        result = await MenuLayoutService.move_button_down(db, button_id)
        return MoveButtonResponse(
            button_id=button_id,
            new_row_index=result.get("new_row_index"),
            position=result.get("new_position"),
        )
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/buttons/{button_id}/move-to-row", response_model=MoveButtonResponse)
async def move_button_to_row(
    button_id: str,
    payload: MoveButtonToRowRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> MoveButtonResponse:
    """Переместить кнопку в указанную строку."""
    try:
        result = await MenuLayoutService.move_button_to_row(
            db, button_id, payload.target_row_id, payload.position
        )
        return MoveButtonResponse(
            button_id=button_id,
            target_row_id=payload.target_row_id,
            position=result.get("new_position"),
        )
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/rows/{row_id}/reorder-buttons", response_model=ReorderButtonsResponse)
async def reorder_buttons_in_row(
    row_id: str,
    payload: ReorderButtonsInRowRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> ReorderButtonsResponse:
    """Изменить порядок кнопок в строке."""
    try:
        result = await MenuLayoutService.reorder_buttons_in_row(
            db, row_id, payload.ordered_button_ids
        )
        return ReorderButtonsResponse(
            row_id=row_id,
            buttons=result["buttons"],
        )
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


@router.post("/buttons/swap", response_model=SwapButtonsResponse)
async def swap_buttons(
    payload: SwapButtonsRequest,
    _: Any = Security(require_api_token),
    db: AsyncSession = Depends(get_db_session),
) -> SwapButtonsResponse:
    """Обменять местами две кнопки (даже из разных строк)."""
    try:
        result = await MenuLayoutService.swap_buttons(
            db, payload.button_id_1, payload.button_id_2
        )
        return SwapButtonsResponse(
            button_1=result["button_1"],
            button_2=result["button_2"],
        )
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
