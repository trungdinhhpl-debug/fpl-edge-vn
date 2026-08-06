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
    # Số nhà cái để trọng số trên đạt mức đầy đủ. Trận ít nhà cái ra giá hơn thì
    # trọng số hạ theo tỷ lệ (dốc tuyến tính), vì đồng thuận mỏng là bằng chứng
    # yếu hơn. Đặt = 1 để trở lại hành vi cũ (luôn dùng trọng số đầy đủ).
    odds_full_support_books: int = 8
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

    # ---- Pre-season down-weighting ----
    # The FPL API publishes neither managers nor transfer history, so a club that
    # changed manager and a player who changed club cannot be detected from it.
    # These lists are maintained by hand; an empty list means NOBODY HAS TOLD US,
    # which season_state reports explicitly rather than implying nothing changed.
    # Defaults below are the 2026/27 lists, verified 2026-08-05. They are
    # season-specific data living in code because no API provides them; replace
    # them each season, or override per-environment with the env vars.
    #
    # New managers (Premier League official manager line-up + Flashscore, plus
    # Sky/NBC for Newcastle):
    #   BOU Marco Rose · CHE Xabi Alonso · COV Frank Lampard · CRY Pierre Sage
    #   FUL Alvaro Arbeloa · HUL Sergej Jakirovic · IPS Gary O'Neil
    #   LIV Andoni Iraola · MCI Enzo Maresca · NEW Matthias Jaissle
    #   NFO Oliver Glasner · TOT Roberto De Zerbi (bổ nhiệm 31/03/2026, tức phần
    #   lớn mẫu mùa trước vẫn dưới HLV khác)
    new_manager_clubs: str = "BOU,CHE,COV,CRY,FUL,HUL,IPS,LIV,MCI,NEW,NFO,TOT"
    # Players who moved BETWEEN Premier League clubs in summer 2026. Arrivals
    # from abroad are deliberately absent: they have zero Premier League minutes
    # and the model already estimates them by role. Every id below was
    # cross-checked against live FPL data — each player's current club matches
    # the reported destination, so the list is not taken on the report's word.
    #   43 Tielemans AVL→MUN · 162 Andrey Santos CHE→MUN · 325 Darlow LEE→MUN
    #   455 Tonali NEW→TOT · 112 Van Hecke BHA→TOT · 498 Senesi BOU→TOT
    #   40 Rogers AVL→CHE · 136 Welbeck BHA→CHE · 101 Henderson BRE→CHE
    #   200 Lacroix CRY→CHE · 481 Anderson NFO→MCI · 104 Onyeka BRE→COV
    #   259 Diop FUL→IPS · 260 Wilson FUL→LEE
    new_signing_players: str = ("43,162,325,455,112,498,40,136,101,200,481,"
                                "104,259,260")
    # How far last season's sample still describes the player. Lower = the
    # minutes model shrinks harder toward the positional prior.
    prior_weight_new_manager: float = 0.6
    prior_weight_new_signing: float = 0.4

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
