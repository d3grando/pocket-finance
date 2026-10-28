"""Category API schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import Aliases, Color, Icon, Id, Schema
from app.schemas.transactions import CleanInput


class CategoryOut(Schema):
    id: int
    name: str
    kind: Literal["expense", "income"]
    is_business: bool
    icon: str | None = None
    color: str | None = None
    aliases: list[str]
    is_system: bool
    is_new: bool
    is_archived: bool
    sort_order: int
    version: int
    tx_count: int | None = None


class CategoryCreate(CleanInput):
    name: str = Field(min_length=1, max_length=64)
    kind: Literal["expense", "income"]
    is_business: bool = False
    icon: Icon | None = None
    color: Color | None = None
    aliases: Aliases = Field(default_factory=list)


class CategoryPatch(CleanInput):
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=64)
    is_business: bool | None = None
    icon: Icon | None = None
    color: Color | None = None
    aliases: Aliases | None = None
    sort_order: int | None = Field(default=None, ge=-(2**31), lt=2**31)
    is_archived: bool | None = None
    is_new: bool | None = Field(default=None, description="false = подтвердить (убрать 🆕)")


class CategoryMergeIn(CleanInput):
    source_id: Id
    target_id: Id


class CategorySuggestion(Schema):
    category: CategoryOut
    score: float


class CategorySuggestions(Schema):
    """«В другую»: similar categories first (backend-ranked), then the rest."""

    similar: list[CategorySuggestion]
    all: list[CategoryOut]
