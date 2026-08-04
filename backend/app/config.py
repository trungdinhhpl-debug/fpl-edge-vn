"""Application configuration.

All secrets come from the environment / .env — never hard-coded.
Sensible defaults let the whole stack run locally with zero external services
(SQLite + in-memory cache), while production can point at Postgres + Redis.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App ----
    app_name: str = "FPL Edge VN"
    environment: str = "development"       # development | production
    debug: bool = True
    timezone: str = "Asia/Ho_Chi_Minh"

    # ---- API ----
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ---- Database ----
    # dev default = local SQLite file (no server needed);
    # prod = postgresql+psycopg://user:pass@host:5432/dbname
    database_url: str = "sqlite:///./fpl_edge.db"

    # ---- Cache ----
    # leave empty to use the built-in in-memory TTL cache
    redis_url: str = ""
    cache_ttl_seconds: int = 900

    # ---- External data sources ----
    fpl_base_url: str = "https://fantasy.premierleague.com/api"
    fpl_request_delay_ms: int = 120       # politeness delay between element-summary calls
    http_timeout_seconds: float = 20.0

    # The Odds API (optional) — probability provider. Empty => internal model fallback.
    odds_api_key: str = ""
    # how far to trust bookmaker consensus over the internal model, 0..1
    odds_market_weight: float = 0.7
    # Also pull the Asian handicap (`spreads`). It is the sharpest of the three
    # markets, but a request costs (markets x regions) credits, so this takes a
    # sync from 2 to 3 against the monthly quota.
    odds_include_handicap: bool = True
    # Relative weights of the three markets in the joint fit of (lam_home,
    # lam_away). Each market contributes the MEAN squared error over its own
    # lines, so these are true relative weights regardless of how many lines a
    # book hangs. See app/providers/probability.py.
    odds_weight_1x2: float = 1.0
    odds_weight_totals: float = 1.0
    odds_weight_handicap: float = 1.0
    # Dixon–Coles low-score correlation. Negative lifts 0-0 / 1-1 and trims
    # 1-0 / 0-1; -0.13 is the estimate from the original paper's English league
    # sample. It is a constant, not a fitted parameter — one match's prices
    # cannot identify it. Set 0 to fall back to an independent double-Poisson.
    odds_dixon_coles_rho: float = -0.13

    # Understat (optional, Phase 2). Empty => rely on FPL's own xG/xA.
    understat_enabled: bool = False

    # Championship data for newly-promoted clubs (free CSV, no key).
    # Only used to rank the promoted sides against each other — see
    # app/providers/championship.py. Set false to drop the feature entirely.
    championship_enabled: bool = True
    # how strongly Championship dominance carries over (0 = ignore, 1 = full)
    championship_damping: float = 0.35

    # ---- Ingestion / jobs ----
    auto_sync_on_startup: bool = True      # run a sync if the DB looks empty
    enable_scheduler: bool = False         # APScheduler background refresh
    sync_players_detail: bool = False      # also pull per-player element-summary (slow: ~700 calls)

    # ---- Modelling ----
    projection_horizon: int = 8            # gameweeks the engine projects ahead
    montecarlo_iterations: int = 10_000
    model_version: str = "xp-0.3.0"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_url(self) -> str:
        """Normalised SQLAlchemy URL.

        Managed Postgres providers (Railway, Render, Supabase, Neon, Heroku) hand
        out `postgres://` or `postgresql://` URLs; SQLAlchemy 2 + psycopg 3 needs
        the `postgresql+psycopg://` driver prefix. Normalise so the raw provider
        URL works as-is in DATABASE_URL.
        """
        url = self.database_url
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://"):]
        if url.startswith("postgresql://") and "+psycopg" not in url:
            return "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.db_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
