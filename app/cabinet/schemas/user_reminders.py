"""Схемы напоминаний: карточка пользователя и админ-API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ReminderCardButton(BaseModel):
    kind: Literal['cabinet', 'url']
    target: str
    text: str


class ReminderCard(BaseModel):
    id: int
    title: str
    body: str
    button: ReminderCardButton | None = None
