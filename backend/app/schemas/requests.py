"""Pydantic request/response schemas (validation for POST endpoints)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TeamImportRequest(BaseModel):
    team_id: int = Field(..., ge=1, description="FPL Team/Entry ID")


class TeamAnalyzeRequest(BaseModel):
    squad_ids: list[int] = Field(..., min_length=1, max_length=15)
    bank: int = Field(0, ge=0, description="Bank in tenths of a million")


class NextGwRequest(BaseModel):
    squad_ids: list[int] = Field(..., min_length=15, max_length=15)
    bank: int = 0
    free_transfers: int = Field(1, ge=1, le=5)
    max_transfers: int = Field(2, ge=0, le=5)


class LongTermRequest(BaseModel):
    squad_ids: list[int] = Field(..., min_length=15, max_length=15)
    bank: int = 0
    free_transfers: int = Field(1, ge=1, le=5)
    horizon: int = Field(5, ge=3, le=8)
    discount: float = Field(0.9, ge=0.5, le=1.0)


class FreeHitRequest(BaseModel):
    gameweek: int | None = None
    budget: int = Field(1000, ge=800, le=1200, description="Budget in tenths")
    mode: str = Field("max_ep", pattern="^(max_ep|balanced|aggressive)$")
    locked: list[int] = Field(default_factory=list)
    excluded: list[int] = Field(default_factory=list)


class ChipCalendarRequest(BaseModel):
    """Đội hình là tuỳ chọn: không có đội thì bảng vẫn trả về cửa sổ chip, lịch
    blank/double và giới hạn, chỉ thiếu phần điểm (đánh dấu `needs_squad`)."""

    squad_ids: list[int] = Field(default_factory=list, max_length=15)
    bank: int = Field(0, ge=0, description="Bank in tenths of a million")
    free_transfers: int = Field(1, ge=1, le=5)
    # tên chip FPL đã dùng ("wildcard", "freehit", "bboost", "3xc"); dùng để
    # loại chip đã tiêu và tính rủi ro hết hạn cho các chip còn lại
    chips_used: list[str] = Field(default_factory=list)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)


class WildcardRequest(BaseModel):
    budget: int = Field(1000, ge=800, le=1200)
    horizon: int = Field(6, ge=3, le=10)
    mode: str = Field("balanced", pattern="^(max_ep|balanced|aggressive)$")
