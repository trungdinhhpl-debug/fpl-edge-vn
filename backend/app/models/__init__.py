"""Import all ORM models so they register on Base.metadata."""
from app.models.core import (  # noqa: F401
    Fixture,
    Gameweek,
    Player,
    PlayerGameweekStat,
    PlayerPrice,
    Season,
    Team,
)
from app.models.meta import (  # noqa: F401
    ChampionshipStats,
    ExpertSignal,
    ExpertSource,
    InjuryReport,
    MarketOdds,
    OptimizationRun,
    SetPieceRole,
    SourceFetchLog,
    UserProfile,
)
from app.models.projections import (  # noqa: F401
    ExpectedMinutes,
    ModelVersion,
    PlayerProjection,
)

__all__ = [
    "Season",
    "Gameweek",
    "Team",
    "Player",
    "PlayerPrice",
    "Fixture",
    "PlayerGameweekStat",
    "ModelVersion",
    "ExpectedMinutes",
    "PlayerProjection",
    "ExpertSource",
    "ExpertSignal",
    "ChampionshipStats",
    "InjuryReport",
    "MarketOdds",
    "SetPieceRole",
    "SourceFetchLog",
    "UserProfile",
    "OptimizationRun",
]
