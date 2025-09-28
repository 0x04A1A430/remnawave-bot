from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Security, status

from app.external.remnawave_api import RemnaWaveAPIError
from app.services.remnawave_service import (
    RemnaWaveConfigurationError,
    RemnaWaveService,
)

from ..dependencies import require_api_token
from ..schemas.remnawave import (
    ComponentActionRequest,
    ComponentActionResponse,
    ComponentInfo,
    ComponentListResponse,
    ComponentUpdateRequest,
)

router = APIRouter()


def _map_api_error(error: RemnaWaveAPIError) -> HTTPException:
    status_code = error.status_code or status.HTTP_502_BAD_GATEWAY
    if status_code < 400 or status_code >= 600:
        status_code = status.HTTP_502_BAD_GATEWAY
    return HTTPException(status_code=status_code, detail=error.message)


@router.get("/components", response_model=ComponentListResponse)
async def list_components(
    _: object = Security(require_api_token),
) -> ComponentListResponse:
    service = RemnaWaveService()

    try:
        components = await service.list_components()
    except RemnaWaveConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except RemnaWaveAPIError as error:
        raise _map_api_error(error)

    items = [ComponentInfo(**component) for component in components]
    return ComponentListResponse(items=items, total=len(items))


@router.get("/components/{component_id}", response_model=ComponentInfo)
async def get_component(
    component_id: str,
    _: object = Security(require_api_token),
) -> ComponentInfo:
    service = RemnaWaveService()

    try:
        component = await service.get_component_details(component_id)
    except RemnaWaveConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except RemnaWaveAPIError as error:
        raise _map_api_error(error)

    if not component:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Компонент не найден")

    return ComponentInfo(**component)


@router.post(
    "/components/{component_id}/actions/{action}",
    response_model=ComponentActionResponse,
)
async def perform_component_action(
    component_id: str,
    action: str,
    request: Optional[ComponentActionRequest] = None,
    _: object = Security(require_api_token),
) -> ComponentActionResponse:
    service = RemnaWaveService()
    payload = request.payload if request else None

    try:
        result = await service.perform_component_action(component_id, action, payload)
    except RemnaWaveConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except RemnaWaveAPIError as error:
        raise _map_api_error(error)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    component_data = result.get("component")
    component = ComponentInfo(**component_data) if isinstance(component_data, dict) else None

    details = result.get("details")
    if details is not None and not isinstance(details, dict):
        details = {"response": details}

    return ComponentActionResponse(
        success=bool(result.get("success", True)),
        message=result.get("message"),
        component=component,
        details=details,
    )


@router.patch(
    "/components/{component_id}",
    response_model=ComponentActionResponse,
)
async def update_component(
    component_id: str,
    request: ComponentUpdateRequest,
    _: object = Security(require_api_token),
) -> ComponentActionResponse:
    service = RemnaWaveService()

    if not request.payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Поле payload не может быть пустым",
        )

    try:
        result = await service.update_component_settings(component_id, request.payload)
    except RemnaWaveConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    except RemnaWaveAPIError as error:
        raise _map_api_error(error)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    component_data = result.get("component")
    component = ComponentInfo(**component_data) if isinstance(component_data, dict) else None

    details = result.get("details")
    if details is not None and not isinstance(details, dict):
        details = {"response": details}

    return ComponentActionResponse(
        success=bool(result.get("success", True)),
        message=result.get("message"),
        component=component,
        details=details,
    )
