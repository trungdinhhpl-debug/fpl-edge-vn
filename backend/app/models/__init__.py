"""Import all ORM models so they register on Base.metadata."""
from app.models.core import (  # noqa: F401
    Fixture,
    Gameweek,
    Player,
    PlayerGameweekStat,
    PlayerPrice,
    Season,
    SeasonRules,
    Team,
)
from app.models.meta import (  # noqa: F401
    ChampionshipStats,
    ExpertSignal,
    ExpertSource,
    ExpertTrackRecord,
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
    ProjectionSnapshot,
)

__all__ = [
    "Season",
    "SeasonRules",
    "Gameweek",
    "Team",
    "Player",
    "PlayerPrice",
    "Fixture",
    "PlayerGameweekStat",
    "ModelVersion",
    "ExpectedMinutes",
    "PlayerProjection",
    "ProjectionSnapshot",
    "ExpertSource",
    "ExpertSignal",
    "ExpertTrackRecord",
    "ChampionshipStats",
    "InjuryReport",
    "MarketOdds",
    "SetPieceRole",
    "SourceFetchLog",
    "UserProfile",
    "OptimizationRun",
]
