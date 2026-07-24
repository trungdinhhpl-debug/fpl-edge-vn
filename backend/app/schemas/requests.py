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


class WildcardRequest(BaseModel):
    budget: int = Field(1000, ge=800, le=1200)
    horizon: int = Field(6, ge=3, le=10)
    mode: str = Field("balanced", pattern="^(max_ep|balanced|aggressive)$")
