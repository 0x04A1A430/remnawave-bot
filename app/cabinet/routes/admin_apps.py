"""Admin routes for managing VPN applications in app-config.json."""

import json
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.config import settings
from app.services.remnawave_service import RemnaWaveService
from app.services.system_settings_service import bot_configuration_service

from ..dependencies import get_cabinet_db, get_current_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/apps", tags=["Cabinet Admin Apps"])


# ============ Schemas ============

class LocalizedText(BaseModel):
    """Localized text for multiple languages."""
    en: str = ""
    ru: str = ""
    zh: Optional[str] = ""
    fa: Optional[str] = ""


class AppButton(BaseModel):
    """Button with link and localized text."""
    buttonLink: str
    buttonText: LocalizedText


class AppStep(BaseModel):
    """Step with description and optional buttons/title."""
    description: LocalizedText
    buttons: Optional[List[AppButton]] = None
    title: Optional[LocalizedText] = None


class AppDefinition(BaseModel):
    """VPN application definition."""
    id: str
    name: str
    isFeatured: bool = False
    urlScheme: str
    isNeedBase64Encoding: Optional[bool] = None
    installationStep: AppStep
    addSubscriptionStep: AppStep
    connectAndUseStep: AppStep
    additionalBeforeAddSubscriptionStep: Optional[AppStep] = None
    additionalAfterAddSubscriptionStep: Optional[AppStep] = None


class PlatformApps(BaseModel):
    """Apps for a specific platform."""
    platform: str
    apps: List[AppDefinition]


class AppConfigBranding(BaseModel):
    """Branding configuration."""
    name: str
    logoUrl: str
    supportUrl: str


class AppConfigConfig(BaseModel):
    """Top-level config section."""
    additionalLocales: List[str]
    branding: AppConfigBranding


class AppConfigResponse(BaseModel):
    """Full app config response."""
    config: AppConfigConfig
    platforms: Dict[str, List[AppDefinition]]


class CreateAppRequest(BaseModel):
    """Request to create a new app."""
    platform: str
    app: AppDefinition


class UpdateAppRequest(BaseModel):
    """Request to update an app."""
    app: AppDefinition


class ReorderAppsRequest(BaseModel):
    """Request to reorder apps in a platform."""
    app_ids: List[str]


class UpdateBrandingRequest(BaseModel):
    """Request to update branding."""
    branding: AppConfigBranding


# ============ Helpers ============

def _get_config_path() -> Path:
    """Get path to app-config.json."""
    return Path(settings.get_app_config_path())


def _load_config() -> dict:
    """Load app config from file."""
    config_path = _get_config_path()
    if not config_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App config file not found: {config_path}",
        )

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to parse app config: {e}",
        )


def _save_config(config: dict) -> None:
    """Save app config to file."""
    config_path = _get_config_path()

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save app config: {e}",
        )


VALID_PLATFORMS = ["ios", "android", "macos", "windows", "linux", "androidTV", "appleTV"]


# ============ Routes ============

@router.get("", response_model=AppConfigResponse)
async def get_app_config(
    admin: User = Depends(get_current_admin_user),
):
    """Get full app configuration."""
    config = _load_config()
    return config


@router.get("/platforms", response_model=List[str])
async def get_platforms(
    admin: User = Depends(get_current_admin_user),
):
    """Get list of available platforms."""
    return VALID_PLATFORMS


@router.get("/platforms/{platform}", response_model=List[AppDefinition])
async def get_platform_apps(
    platform: str,
    admin: User = Depends(get_current_admin_user),
):
    """Get apps for a specific platform."""
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform: {platform}. Valid platforms: {VALID_PLATFORMS}",
        )

    config = _load_config()
    platforms = config.get("platforms", {})
    return platforms.get(platform, [])


@router.post("/platforms/{platform}", response_model=AppDefinition)
async def create_app(
    platform: str,
    request: CreateAppRequest,
    admin: User = Depends(get_current_admin_user),
):
    """Create a new app for a platform."""
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform: {platform}",
        )

    config = _load_config()
    platforms = config.get("platforms", {})

    if platform not in platforms:
        platforms[platform] = []

    # Check if app with same ID already exists
    existing_ids = [app.get("id") for app in platforms[platform]]
    if request.app.id in existing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"App with ID '{request.app.id}' already exists in {platform}",
        )

    # Add new app
    app_dict = request.app.model_dump(exclude_none=True)
    platforms[platform].append(app_dict)
    config["platforms"] = platforms

    _save_config(config)
    logger.info(f"Admin {admin.id} created app '{request.app.id}' for platform '{platform}'")

    return request.app


@router.put("/platforms/{platform}/{app_id}", response_model=AppDefinition)
async def update_app(
    platform: str,
    app_id: str,
    request: UpdateAppRequest,
    admin: User = Depends(get_current_admin_user),
):
    """Update an existing app."""
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform: {platform}",
        )

    config = _load_config()
    platforms = config.get("platforms", {})
    apps = platforms.get(platform, [])

    # Find and update app
    app_index = None
    for i, app in enumerate(apps):
        if app.get("id") == app_id:
            app_index = i
            break

    if app_index is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_id}' not found in platform '{platform}'",
        )

    # Update app
    app_dict = request.app.model_dump(exclude_none=True)
    apps[app_index] = app_dict
    platforms[platform] = apps
    config["platforms"] = platforms

    _save_config(config)
    logger.info(f"Admin {admin.id} updated app '{app_id}' in platform '{platform}'")

    return request.app


@router.delete("/platforms/{platform}/{app_id}")
async def delete_app(
    platform: str,
    app_id: str,
    admin: User = Depends(get_current_admin_user),
):
    """Delete an app from a platform."""
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform: {platform}",
        )

    config = _load_config()
    platforms = config.get("platforms", {})
    apps = platforms.get(platform, [])

    # Find and remove app
    original_length = len(apps)
    apps = [app for app in apps if app.get("id") != app_id]

    if len(apps) == original_length:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_id}' not found in platform '{platform}'",
        )

    platforms[platform] = apps
    config["platforms"] = platforms

    _save_config(config)
    logger.info(f"Admin {admin.id} deleted app '{app_id}' from platform '{platform}'")

    return {"status": "deleted", "app_id": app_id}


@router.post("/platforms/{platform}/reorder")
async def reorder_apps(
    platform: str,
    request: ReorderAppsRequest,
    admin: User = Depends(get_current_admin_user),
):
    """Reorder apps in a platform."""
    if platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform: {platform}",
        )

    config = _load_config()
    platforms = config.get("platforms", {})
    apps = platforms.get(platform, [])

    # Create a map of apps by ID
    apps_map = {app.get("id"): app for app in apps}

    # Verify all IDs exist
    for app_id in request.app_ids:
        if app_id not in apps_map:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"App '{app_id}' not found in platform '{platform}'",
            )

    # Reorder apps
    reordered_apps = [apps_map[app_id] for app_id in request.app_ids]

    # Add any apps that weren't in the reorder list (shouldn't happen but just in case)
    for app in apps:
        if app.get("id") not in request.app_ids:
            reordered_apps.append(app)

    platforms[platform] = reordered_apps
    config["platforms"] = platforms

    _save_config(config)
    logger.info(f"Admin {admin.id} reordered apps in platform '{platform}'")

    return {"status": "reordered", "order": request.app_ids}


@router.put("/branding", response_model=AppConfigBranding)
async def update_branding(
    request: UpdateBrandingRequest,
    admin: User = Depends(get_current_admin_user),
):
    """Update branding configuration."""
    config = _load_config()

    if "config" not in config:
        config["config"] = {}

    config["config"]["branding"] = request.branding.model_dump()

    _save_config(config)
    logger.info(f"Admin {admin.id} updated branding")

    return request.branding


@router.get("/branding", response_model=AppConfigBranding)
async def get_branding(
    admin: User = Depends(get_current_admin_user),
):
    """Get branding configuration."""
    config = _load_config()
    branding = config.get("config", {}).get("branding", {})
    return branding


@router.post("/platforms/{platform}/copy/{app_id}")
async def copy_app_to_platform(
    platform: str,
    app_id: str,
    target_platform: str,
    admin: User = Depends(get_current_admin_user),
):
    """Copy an app from one platform to another."""
    if platform not in VALID_PLATFORMS or target_platform not in VALID_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid platform(s)",
        )

    config = _load_config()
    platforms = config.get("platforms", {})
    source_apps = platforms.get(platform, [])

    # Find source app
    source_app = None
    for app in source_apps:
        if app.get("id") == app_id:
            source_app = app.copy()
            break

    if not source_app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"App '{app_id}' not found in platform '{platform}'",
        )

    # Generate new ID for copied app
    import time
    new_id = f"{app_id}-copy-{int(time.time())}"
    source_app["id"] = new_id

    # Add to target platform
    if target_platform not in platforms:
        platforms[target_platform] = []

    platforms[target_platform].append(source_app)
    config["platforms"] = platforms

    _save_config(config)
    logger.info(f"Admin {admin.id} copied app '{app_id}' from '{platform}' to '{target_platform}' as '{new_id}'")

    return {"status": "copied", "new_id": new_id, "target_platform": target_platform}


# ============ RemnaWave Config Routes ============

class RemnaWaveConfigStatus(BaseModel):
    """Status of RemnaWave config integration."""
    enabled: bool
    config_uuid: Optional[str] = None


class UpdateRemnaWaveUuidRequest(BaseModel):
    """Request to update RemnaWave config UUID."""
    uuid: Optional[str] = None


def _get_remnawave_config_uuid() -> Optional[str]:
    """Get RemnaWave config UUID from system settings or env."""
    try:
        return bot_configuration_service.get_current_value("CABINET_REMNA_SUB_CONFIG")
    except Exception:
        return settings.CABINET_REMNA_SUB_CONFIG


@router.get("/remnawave/status", response_model=RemnaWaveConfigStatus)
async def get_remnawave_config_status(
    admin: User = Depends(get_current_admin_user),
):
    """Get RemnaWave config integration status."""
    config_uuid = _get_remnawave_config_uuid()
    return RemnaWaveConfigStatus(
        enabled=bool(config_uuid),
        config_uuid=config_uuid,
    )


@router.put("/remnawave/uuid", response_model=RemnaWaveConfigStatus)
async def set_remnawave_config_uuid(
    request: UpdateRemnaWaveUuidRequest,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Set RemnaWave subscription config UUID."""
    uuid_value = request.uuid.strip() if request.uuid else None

    # Validate UUID format if provided
    if uuid_value:
        import re
        uuid_pattern = re.compile(
            r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
        )
        if not uuid_pattern.match(uuid_value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid UUID format",
            )

    try:
        await bot_configuration_service.set_value(db, "CABINET_REMNA_SUB_CONFIG", uuid_value)
        await db.commit()
        logger.info(f"Admin {admin.id} updated CABINET_REMNA_SUB_CONFIG to '{uuid_value}'")
    except Exception as e:
        logger.error(f"Error saving RemnaWave config UUID: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save configuration",
        )

    return RemnaWaveConfigStatus(
        enabled=bool(uuid_value),
        config_uuid=uuid_value,
    )


@router.get("/remnawave/config")
async def get_remnawave_subscription_config(
    admin: User = Depends(get_current_admin_user),
):
    """
    Fetch subscription page config from RemnaWave panel.
    Uses CABINET_REMNA_SUB_CONFIG setting for the config UUID.
    """
    config_uuid = _get_remnawave_config_uuid()
    if not config_uuid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="CABINET_REMNA_SUB_CONFIG is not configured",
        )

    try:
        service = RemnaWaveService()
        async with service.get_api_client() as api:
            config = await api.get_subscription_page_config(config_uuid)
            if not config:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Subscription config '{config_uuid}' not found in RemnaWave",
                )

            # Return the raw config data from RemnaWave
            return {
                "uuid": config.uuid,
                "name": config.name,
                "view_position": config.view_position,
                "config": config.config,
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching RemnaWave config: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch config from RemnaWave: {str(e)}",
        )


@router.get("/remnawave/configs")
async def list_remnawave_subscription_configs(
    admin: User = Depends(get_current_admin_user),
):
    """List available subscription page configs from RemnaWave panel."""
    try:
        service = RemnaWaveService()
        async with service.get_api_client() as api:
            configs = await api.get_subscription_page_configs()
            return [
                {
                    "uuid": c.uuid,
                    "name": c.name,
                    "view_position": c.view_position,
                }
                for c in configs
            ]
    except Exception as e:
        logger.error(f"Error listing RemnaWave configs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch configs from RemnaWave: {str(e)}",
        )
