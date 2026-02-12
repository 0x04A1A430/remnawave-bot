"""Admin routes for per-section cabinet button style configuration."""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.utils.button_styles_cache import (
    BUTTON_STYLES_KEY,
    DEFAULT_BUTTON_STYLES,
    SECTIONS,
    VALID_STYLES,
    load_button_styles_cache,
)

from ..dependencies import get_cabinet_db, get_current_admin_user


logger = logging.getLogger(__name__)

router = APIRouter(prefix='/admin/button-styles', tags=['Admin Button Styles'])


# ---- Schemas ---------------------------------------------------------------


class ButtonSectionConfig(BaseModel):
    """Configuration for a single button section."""

    style: str = 'primary'
    icon_custom_emoji_id: str = ''


class ButtonStylesResponse(BaseModel):
    """Full button styles configuration (all 7 sections)."""

    home: ButtonSectionConfig = ButtonSectionConfig()
    subscription: ButtonSectionConfig = ButtonSectionConfig()
    balance: ButtonSectionConfig = ButtonSectionConfig()
    referral: ButtonSectionConfig = ButtonSectionConfig()
    support: ButtonSectionConfig = ButtonSectionConfig()
    info: ButtonSectionConfig = ButtonSectionConfig()
    admin: ButtonSectionConfig = ButtonSectionConfig()


class ButtonSectionUpdate(BaseModel):
    """Partial update for a single section (None = keep current)."""

    style: str | None = None
    icon_custom_emoji_id: str | None = None


class ButtonStylesUpdate(BaseModel):
    """Partial update — only include sections you want to change."""

    home: ButtonSectionUpdate | None = None
    subscription: ButtonSectionUpdate | None = None
    balance: ButtonSectionUpdate | None = None
    referral: ButtonSectionUpdate | None = None
    support: ButtonSectionUpdate | None = None
    info: ButtonSectionUpdate | None = None
    admin: ButtonSectionUpdate | None = None


# ---- Helpers ---------------------------------------------------------------


async def _get_setting_value(db: AsyncSession, key: str) -> str | None:
    from sqlalchemy import select

    from app.database.models import SystemSetting

    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def _set_setting_value(db: AsyncSession, key: str, value: str) -> None:
    from sqlalchemy import select

    from app.database.models import SystemSetting

    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = SystemSetting(key=key, value=value)
        db.add(setting)
    await db.commit()


def _build_response(styles: dict[str, dict]) -> ButtonStylesResponse:
    return ButtonStylesResponse(
        **{section: ButtonSectionConfig(**cfg) for section, cfg in styles.items() if section in SECTIONS},
    )


# ---- Routes ----------------------------------------------------------------


@router.get('', response_model=ButtonStylesResponse)
async def get_button_styles(
    _admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Return current per-section button styles. Admin only."""
    raw = await _get_setting_value(db, BUTTON_STYLES_KEY)
    merged = {section: {**cfg} for section, cfg in DEFAULT_BUTTON_STYLES.items()}

    if raw:
        try:
            db_data = json.loads(raw)
            for section, overrides in db_data.items():
                if section in merged and isinstance(overrides, dict):
                    if overrides.get('style') in VALID_STYLES:
                        merged[section]['style'] = overrides['style']
                    if isinstance(overrides.get('icon_custom_emoji_id'), str):
                        merged[section]['icon_custom_emoji_id'] = overrides['icon_custom_emoji_id']
        except (json.JSONDecodeError, TypeError):
            pass

    return _build_response(merged)


@router.patch('', response_model=ButtonStylesResponse)
async def update_button_styles(
    payload: ButtonStylesUpdate,
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Partially update per-section button styles. Admin only."""
    # Load current state
    raw = await _get_setting_value(db, BUTTON_STYLES_KEY)
    current: dict[str, dict] = {section: {**cfg} for section, cfg in DEFAULT_BUTTON_STYLES.items()}

    if raw:
        try:
            db_data = json.loads(raw)
            for section, overrides in db_data.items():
                if section in current and isinstance(overrides, dict):
                    current[section].update(overrides)
        except (json.JSONDecodeError, TypeError):
            pass

    # Apply updates
    update_data = payload.model_dump(exclude_none=True)
    changed_sections: list[str] = []

    for section, updates in update_data.items():
        if section not in current or not isinstance(updates, dict):
            continue

        if 'style' in updates:
            style_val = updates['style']
            if style_val not in VALID_STYLES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f'Invalid style "{style_val}" for section "{section}". '
                    f'Allowed: {", ".join(sorted(VALID_STYLES))}',
                )
            current[section]['style'] = style_val

        if 'icon_custom_emoji_id' in updates:
            emoji_val = (updates['icon_custom_emoji_id'] or '').strip()
            current[section]['icon_custom_emoji_id'] = emoji_val

        changed_sections.append(section)

    # Persist
    await _set_setting_value(db, BUTTON_STYLES_KEY, json.dumps(current))

    # Refresh in-process cache
    await load_button_styles_cache()

    logger.info('Admin %s updated button styles for sections: %s', admin.telegram_id, changed_sections)

    return _build_response(current)


@router.post('/reset', response_model=ButtonStylesResponse)
async def reset_button_styles(
    admin: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_cabinet_db),
):
    """Reset all button styles to defaults. Admin only."""
    await _set_setting_value(db, BUTTON_STYLES_KEY, json.dumps(DEFAULT_BUTTON_STYLES))
    await load_button_styles_cache()

    logger.info('Admin %s reset button styles to defaults', admin.telegram_id)

    return _build_response(DEFAULT_BUTTON_STYLES)
