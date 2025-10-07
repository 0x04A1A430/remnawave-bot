from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UserApiProfileResponse(BaseModel):
    user_id: int = Field(..., description="Internal database identifier of the user")
    telegram_id: int = Field(..., description="Telegram identifier of the user")
    username: Optional[str] = Field(None, description="Telegram @username if available")
    first_name: Optional[str] = Field(None, description="Telegram first name")
    last_name: Optional[str] = Field(None, description="Telegram last name")
    status: str = Field(..., description="Account status")
    language: str = Field(..., description="Preferred interface language")
    balance_kopeks: int = Field(..., description="Account balance in kopeks")
    balance_rubles: float = Field(..., description="Account balance in rubles")
    created_at: datetime = Field(..., description="Account creation timestamp")
    updated_at: datetime = Field(..., description="Last profile update timestamp")
    last_activity: Optional[datetime] = Field(None, description="Last bot interaction time")
    api_token_prefix: str = Field(..., description="First characters of the API token")
    api_token_last_digits: str = Field(..., description="Last characters of the API token")

    model_config = {
        "from_attributes": True,
    }


__all__ = ["UserApiProfileResponse"]
