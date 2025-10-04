from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.database.models import PromoOfferTarget, PromoOfferType


class PromoOfferResponse(BaseModel):
    id: int
    title: str
    message_text: str
    button_text: str
    offer_type: PromoOfferType
    target_segments: List[PromoOfferTarget]
    starts_at: datetime
    expires_at: datetime
    discount_percent: int
    bonus_amount_kopeks: int
    discount_valid_hours: int
    test_access_hours: int
    test_squad_uuids: List[str]
    status: str
    total_count: int
    sent_count: int
    failed_count: int
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class PromoOfferListResponse(BaseModel):
    items: List[PromoOfferResponse]
    total: int
    limit: int
    offset: int


class PromoOfferCreateRequest(BaseModel):
    title: str = Field(..., max_length=255)
    message_text: str
    button_text: Optional[str] = Field(default="🎁 Активировать", max_length=100)
    offer_type: PromoOfferType
    target_segments: List[PromoOfferTarget]
    starts_at: datetime
    expires_at: datetime
    discount_percent: Optional[int] = Field(default=0, ge=0, le=100)
    bonus_amount_kopeks: Optional[int] = Field(default=0, ge=0)
    discount_valid_hours: Optional[int] = Field(default=0, ge=0)
    test_access_hours: Optional[int] = Field(default=0, ge=0)
    test_squad_uuids: Optional[List[str]] = None

    @field_validator("target_segments")
    @classmethod
    def ensure_targets_not_empty(cls, value: List[PromoOfferTarget]) -> List[PromoOfferTarget]:
        if not value:
            raise ValueError("target_segments must contain at least one segment")
        return value

    @model_validator(mode="after")
    def validate_offer(self) -> "PromoOfferCreateRequest":
        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be greater than starts_at")

        segments = {segment.value for segment in self.target_segments}

        if self.offer_type == PromoOfferType.TEST_SQUADS:
            allowed = {
                PromoOfferTarget.PAID_ACTIVE.value,
                PromoOfferTarget.TRIAL_ACTIVE.value,
            }
            if not segments.issubset(allowed):
                raise ValueError(
                    "test_squads offers can target only paid_active or trial_active segments"
                )
            if not self.test_squad_uuids:
                raise ValueError("test_squad_uuids must be provided for test_squads offers")
            if not self.test_access_hours or self.test_access_hours <= 0:
                raise ValueError("test_access_hours must be positive for test_squads offers")

        elif self.offer_type == PromoOfferType.RENEWAL_DISCOUNT:
            allowed = {PromoOfferTarget.PAID_ACTIVE.value}
            if not segments.issubset(allowed):
                raise ValueError("renewal_discount offers can target only paid_active segment")
            if not self.discount_percent or self.discount_percent <= 0:
                raise ValueError("discount_percent must be positive for renewal_discount offers")

        elif self.offer_type == PromoOfferType.PURCHASE_DISCOUNT:
            allowed = {
                PromoOfferTarget.PAID_EXPIRED.value,
                PromoOfferTarget.TRIAL_ACTIVE.value,
                PromoOfferTarget.TRIAL_EXPIRED.value,
                PromoOfferTarget.NO_SUBSCRIPTION.value,
            }
            if not segments.issubset(allowed):
                raise ValueError(
                    "purchase_discount offers can target expired paid, trial or no subscription segments"
                )
            if not self.discount_percent or self.discount_percent <= 0:
                raise ValueError("discount_percent must be positive for purchase_discount offers")

        if self.offer_type in {
            PromoOfferType.RENEWAL_DISCOUNT,
            PromoOfferType.PURCHASE_DISCOUNT,
        }:
            if self.button_text is None:
                self.button_text = "🎁 Получить скидку"

        return self
