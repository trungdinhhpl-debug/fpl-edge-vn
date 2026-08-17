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


class LeagueAnalyzeRequest(BaseModel):
    """Mã giải classic; đội của bạn là tuỳ chọn (không có thì chỉ xem template)."""

    league_id: int = Field(..., ge=1, description="Mã mini-league (classic)")
    entry_id: int | None = Field(None, ge=1, description="Team ID của bạn")
    squad_ids: list[int] = Field(default_factory=list, max_length=15)
    # Mỗi đối thủ tốn một lệnh gọi API — xem MAX_RIVALS trong services/league.py.
    top_n: int = Field(30, ge=1, le=50)


class WildcardRequest(BaseModel):
    budget: int = Field(1000, ge=800, le=1200)
    horizon: int = Field(6, ge=3, le=10)
    mode: str = Field("balanced", pattern="^(max_ep|balanced|aggressive)$")
    # Khoá/loại là cách người dùng đưa vào những gì mô hình không biết: một tin
    # chuyển nhượng chưa vào dữ liệu, hay một cầu thủ họ nhất định không mua.
    # Tối đa 14 khoá — khoá đủ 15 thì không còn bài toán tối ưu nào để giải.
    locked: list[int] = Field(default_factory=list, max_length=14)
    excluded: list[int] = Field(default_factory=list, max_length=200)
