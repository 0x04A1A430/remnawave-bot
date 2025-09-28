from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ComponentInfo(BaseModel):
    """Normalized representation of a Remnawave component."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    uuid: Optional[str] = None
    name: Optional[str] = None
    slug: Optional[str] = None
    status: Optional[str] = None
    installed_version: Optional[str] = Field(default=None, alias="installedVersion")
    latest_version: Optional[str] = Field(default=None, alias="latestVersion")
    category: Optional[str] = None
    description: Optional[str] = None
    is_installed: Optional[bool] = Field(default=None, alias="isInstalled")
    is_enabled: Optional[bool] = Field(default=None, alias="isEnabled")
    installed_at: Optional[str] = Field(default=None, alias="installedAt")
    updated_at: Optional[str] = Field(default=None, alias="updatedAt")
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class ComponentListResponse(BaseModel):
    items: List[ComponentInfo]
    total: int


class ComponentActionRequest(BaseModel):
    payload: Optional[Dict[str, Any]] = None


class ComponentUpdateRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


class ComponentActionResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    component: Optional[ComponentInfo] = None
    details: Optional[Dict[str, Any]] = None
