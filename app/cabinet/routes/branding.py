"""Branding routes for cabinet - logo, project name, and theme colors management."""

import logging
import os
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.models import User, SystemSetting
from app.config import settings

from ..dependencies import get_cabinet_db, get_current_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/branding", tags=["Branding"])

# Directory for storing branding assets
BRANDING_DIR = Path("data/branding")
LOGO_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".svg"]

# Settings keys
BRANDING_NAME_KEY = "CABINET_BRANDING_NAME"
BRANDING_LOGO_KEY = "CABINET_BRANDING_LOGO"  # Stores "custom" or "default"
THEME_COLORS_KEY = "CABINET_THEME_COLORS"  # Stores JSON with theme colors
ENABLED_THEMES_KEY = "CABINET_ENABLED_THEMES"  # Stores JSON with enabled themes {"dark": true, "light": false}
ANIMATION_ENABLED_KEY = "CABINET_ANIMATION_ENABLED"  # Stores "true" or "false"

# Allowed image types
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/svg+xml"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB for larger logos


# ============ Schemas ============

class BrandingResponse(BaseModel):
    """Current branding settings."""
    name: str
    logo_url: Optional[str] = None
    logo_letter: str
    has_custom_logo: bool


class BrandingNameUpdate(BaseModel):
    """Request to update branding name."""
    name: str


class ThemeColorsResponse(BaseModel):
    """Theme colors settings."""
    accent: str = "#3b82f6"
    darkBackground: str = "#0a0f1a"
    darkSurface: str = "#0f172a"
    darkText: str = "#f1f5f9"
    darkTextSecondary: str = "#94a3b8"
    lightBackground: str = "#F7E7CE"
    lightSurface: str = "#FEF9F0"
    lightText: str = "#1F1A12"
    lightTextSecondary: str = "#7D6B48"
    success: str = "#22c55e"
    warning: str = "#f59e0b"
    error: str = "#ef4444"


class ThemeColorsUpdate(BaseModel):
    """Request to update theme colors (partial update allowed)."""
    accent: Optional[str] = None
    darkBackground: Optional[str] = None
    darkSurface: Optional[str] = None
    darkText: Optional[str] = None
    darkTextSecondary: Optional[str] = None
    lightBackground: Optional[str] = None
    lightSurface: Optional[str] = None
    lightText: Optional[str] = None
    lightTextSecondary: Optional[str] = None
    success: Optional[str] = None
    warning: Optional[str] = None
    error: Optional[str] = None


class EnabledThemesResponse(BaseModel):
    """Enabled themes settings."""
    dark: bool = True
    light: bool = True


class EnabledThemesUpdate(BaseModel):
    """Request to update enabled themes."""
    dark: Optional[bool] = None
    light: Optional[bool] = None


class AnimationEnabledResponse(BaseModel):
    """Animation enabled setting."""
    enabled: bool = True


class AnimationEnabledUpdate(BaseModel):
    """Request to update animation setting."""
    enabled: bool


# Default theme colors
DEFAULT_THEME_COLORS = {
    "accent": "#3b82f6",
    "darkBackground": "#0a0f1a",
    "darkSurface": "#0f172a",
    "darkText": "#f1f5f9",
    "darkTextSecondary": "#94a3b8",
    "lightBackground": "#F7E7CE",
    "lightSurface": "#FEF9F0",
    "lightText": "#1F1A12",
    "lightTextSecondary": "#7D6B48",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "error": "#ef4444",
}


# ============ Helper Functions ============

def ensure_branding_dir():
    """Ensure branding directory exists."""
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)


async def get_setting_value(db: AsyncSession, key: str) -> Optional[str]:
    """Get a setting value from database."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_setting_value(db: AsyncSession, key: str, value: str):
    """Set a setting value in database."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalar_one_or_none()

    if setting:
        setting.value = value
    else:
        setting = SystemSetting(key=key, value=value)
        db.add(setting)

    await db.commit()


def get_logo_path() -> Optional[Path]:
    """Get the path to the custom logo file (any supported format)."""
    if not BRANDING_DIR.exists():
        return None

    # Search for logo file with any supported extension
    for ext in LOGO_EXTENSIONS:
        logo_path = BRANDING_DIR / f"logo{ext}"
        if logo_path.exists():
            return logo_path

    return None


def has_custom_logo() -> bool:
    """Check if a custom logo exists."""
    return get_logo_path() is not None


# ============ Routes ============

@router.get("", response_model=BrandingResponse)
async def get_branding(
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Get current branding settings.
    This is a public endpoint - no authentication required.
    """
    # Get name from database or use default from env/settings
    name = await get_setting_value(db, BRANDING_NAME_KEY)
    if name is None:  # Only use fallback if not set at all (empty string is valid)
        name = getattr(settings, 'CABINET_BRANDING_NAME', None) or \
               os.getenv('VITE_APP_NAME', 'Cabinet')

    # Check for custom logo
    custom_logo = has_custom_logo()

    # Get first letter for logo fallback (use "V" if name is empty)
    logo_letter = name[0].upper() if name else "V"

    return BrandingResponse(
        name=name,
        logo_url="/cabinet/branding/logo" if custom_logo else None,
        logo_letter=logo_letter,
        has_custom_logo=custom_logo,
    )


@router.get("/logo")
async def get_logo():
    """
    Get the custom logo image.
    Returns 404 if no custom logo is set.
    """
    logo_path = get_logo_path()

    if logo_path is None or not logo_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No custom logo set"
        )

    # Determine media type from file extension
    suffix = logo_path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    media_type = media_types.get(suffix, "image/png")

    return FileResponse(
        logo_path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"}
    )


@router.put("/name", response_model=BrandingResponse)
async def update_branding_name(
    payload: BrandingNameUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update the project name. Admin only. Empty name allowed (logo only mode)."""
    name = payload.name.strip() if payload.name else ""

    if len(name) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Name too long (max 50 characters)"
        )

    await set_setting_value(db, BRANDING_NAME_KEY, name)

    logger.info(f"Admin {admin.telegram_id} updated branding name to: {name}")

    # Return updated branding
    custom_logo = has_custom_logo()
    logo_letter = name[0].upper() if name else "C"

    return BrandingResponse(
        name=name,
        logo_url="/cabinet/branding/logo" if custom_logo else None,
        logo_letter=logo_letter,
        has_custom_logo=custom_logo,
    )


@router.post("/logo", response_model=BrandingResponse)
async def upload_logo(
    file: UploadFile = File(...),
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Upload a custom logo. Admin only."""
    # Validate content type
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid file type. Allowed: PNG, JPEG, WebP, SVG"
        )

    # Read file content
    content = await file.read()

    # Validate file size
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size: {MAX_FILE_SIZE // 1024 // 1024}MB"
        )

    # Ensure directory exists
    ensure_branding_dir()

    # Determine file extension from content type
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    extension = ext_map.get(file.content_type, ".png")

    # Remove old logo files with any extension
    for old_file in BRANDING_DIR.glob("logo.*"):
        old_file.unlink()

    # Save new logo
    logo_path = BRANDING_DIR / f"logo{extension}"
    logo_path.write_bytes(content)

    # Mark that we have a custom logo
    await set_setting_value(db, BRANDING_LOGO_KEY, "custom")

    logger.info(f"Admin {admin.telegram_id} uploaded new logo: {logo_path}")

    # Get current name for response
    name = await get_setting_value(db, BRANDING_NAME_KEY)
    if name is None:  # Only use fallback if not set at all (empty string is valid)
        name = getattr(settings, 'CABINET_BRANDING_NAME', None) or \
               os.getenv('VITE_APP_NAME', 'Cabinet')

    logo_letter = name[0].upper() if name else "C"

    return BrandingResponse(
        name=name,
        logo_url="/cabinet/branding/logo",
        logo_letter=logo_letter,
        has_custom_logo=True,
    )


@router.delete("/logo", response_model=BrandingResponse)
async def delete_logo(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Delete custom logo and revert to letter. Admin only."""
    # Remove logo files
    for old_file in BRANDING_DIR.glob("logo.*"):
        old_file.unlink()

    # Update setting
    await set_setting_value(db, BRANDING_LOGO_KEY, "default")

    logger.info(f"Admin {admin.telegram_id} deleted custom logo")

    # Get current name for response
    name = await get_setting_value(db, BRANDING_NAME_KEY)
    if name is None:  # Only use fallback if not set at all (empty string is valid)
        name = getattr(settings, 'CABINET_BRANDING_NAME', None) or \
               os.getenv('VITE_APP_NAME', 'Cabinet')

    logo_letter = name[0].upper() if name else "C"

    return BrandingResponse(
        name=name,
        logo_url=None,
        logo_letter=logo_letter,
        has_custom_logo=False,
    )


# ============ Theme Colors Routes ============

def validate_hex_color(color: str) -> bool:
    """Validate hex color format."""
    if not color or not isinstance(color, str):
        return False
    if not color.startswith("#"):
        return False
    hex_part = color[1:]
    if len(hex_part) not in (3, 6):
        return False
    try:
        int(hex_part, 16)
        return True
    except ValueError:
        return False


@router.get("/colors", response_model=ThemeColorsResponse)
async def get_theme_colors(
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Get current theme colors.
    This is a public endpoint - no authentication required.
    """
    colors_json = await get_setting_value(db, THEME_COLORS_KEY)

    if colors_json:
        try:
            colors = json.loads(colors_json)
            # Merge with defaults to ensure all fields exist
            merged = {**DEFAULT_THEME_COLORS, **colors}
            return ThemeColorsResponse(**merged)
        except (json.JSONDecodeError, TypeError):
            pass

    return ThemeColorsResponse(**DEFAULT_THEME_COLORS)


@router.patch("/colors", response_model=ThemeColorsResponse)
async def update_theme_colors(
    payload: ThemeColorsUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update theme colors. Admin only. Partial update supported."""
    # Get current colors
    colors_json = await get_setting_value(db, THEME_COLORS_KEY)
    current_colors = DEFAULT_THEME_COLORS.copy()

    if colors_json:
        try:
            current_colors.update(json.loads(colors_json))
        except (json.JSONDecodeError, TypeError):
            pass

    # Update with new values (only non-None fields)
    update_data = payload.model_dump(exclude_none=True)

    # Validate hex colors
    for key, value in update_data.items():
        if not validate_hex_color(value):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid hex color for {key}: {value}"
            )

    current_colors.update(update_data)

    # Save to database
    await set_setting_value(db, THEME_COLORS_KEY, json.dumps(current_colors))

    logger.info(f"Admin {admin.telegram_id} updated theme colors: {list(update_data.keys())}")

    return ThemeColorsResponse(**current_colors)


@router.post("/colors/reset", response_model=ThemeColorsResponse)
async def reset_theme_colors(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Reset theme colors to defaults. Admin only."""
    # Save default colors
    await set_setting_value(db, THEME_COLORS_KEY, json.dumps(DEFAULT_THEME_COLORS))

    logger.info(f"Admin {admin.telegram_id} reset theme colors to defaults")

    return ThemeColorsResponse(**DEFAULT_THEME_COLORS)


# ============ Enabled Themes Routes ============

DEFAULT_ENABLED_THEMES = {"dark": True, "light": True}


@router.get("/themes", response_model=EnabledThemesResponse)
async def get_enabled_themes(
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Get which themes are enabled.
    This is a public endpoint - no authentication required.
    """
    themes_json = await get_setting_value(db, ENABLED_THEMES_KEY)

    if themes_json:
        try:
            themes = json.loads(themes_json)
            return EnabledThemesResponse(**themes)
        except (json.JSONDecodeError, TypeError):
            pass

    return EnabledThemesResponse(**DEFAULT_ENABLED_THEMES)


@router.patch("/themes", response_model=EnabledThemesResponse)
async def update_enabled_themes(
    payload: EnabledThemesUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update which themes are enabled. Admin only. At least one theme must be enabled."""
    # Get current settings
    themes_json = await get_setting_value(db, ENABLED_THEMES_KEY)
    current_themes = DEFAULT_ENABLED_THEMES.copy()

    if themes_json:
        try:
            current_themes.update(json.loads(themes_json))
        except (json.JSONDecodeError, TypeError):
            pass

    # Update with new values
    update_data = payload.model_dump(exclude_none=True)
    current_themes.update(update_data)

    # Ensure at least one theme is enabled
    if not current_themes.get("dark") and not current_themes.get("light"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one theme must be enabled"
        )

    # Save to database
    await set_setting_value(db, ENABLED_THEMES_KEY, json.dumps(current_themes))

    logger.info(f"Admin {admin.telegram_id} updated enabled themes: {current_themes}")

    return EnabledThemesResponse(**current_themes)


# ============ Animation Routes ============

@router.get("/animation", response_model=AnimationEnabledResponse)
async def get_animation_enabled(
    db: AsyncSession = Depends(get_cabinet_db),
):
    """
    Get animation enabled setting.
    This is a public endpoint - no authentication required.
    """
    animation_value = await get_setting_value(db, ANIMATION_ENABLED_KEY)

    if animation_value is not None:
        enabled = animation_value.lower() == "true"
        return AnimationEnabledResponse(enabled=enabled)

    # Default: enabled
    return AnimationEnabledResponse(enabled=True)


@router.patch("/animation", response_model=AnimationEnabledResponse)
async def update_animation_enabled(
    payload: AnimationEnabledUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Update animation enabled setting. Admin only."""
    await set_setting_value(db, ANIMATION_ENABLED_KEY, str(payload.enabled).lower())

    logger.info(f"Admin {admin.telegram_id} set animation enabled: {payload.enabled}")

    return AnimationEnabledResponse(enabled=payload.enabled)
