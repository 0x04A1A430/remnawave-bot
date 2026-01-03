"""Branding routes for cabinet - logo and project name management."""

import logging
import os
import base64
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
LOGO_FILENAME = "logo.png"

# Settings keys
BRANDING_NAME_KEY = "CABINET_BRANDING_NAME"
BRANDING_LOGO_KEY = "CABINET_BRANDING_LOGO"  # Stores "custom" or "default"

# Allowed image types
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/svg+xml"}
MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB


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


def get_logo_path() -> Path:
    """Get the path to the custom logo file."""
    return BRANDING_DIR / LOGO_FILENAME


def has_custom_logo() -> bool:
    """Check if a custom logo exists."""
    return get_logo_path().exists()


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

    if not logo_path.exists():
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
