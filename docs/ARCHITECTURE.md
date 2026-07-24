# Kiến trúc — FPL Edge VN

## 1. Tổng thể

```mermaid
flowchart TD
    subgraph Sources[Nguồn dữ liệu]
        FPL[FPL API công khai]
        ODDS[The Odds API - tùy chọn]
        EXP[Expert seeds - có nhãn mock]
    end

    subgraph Backend[FastAPI backend]
        ING[Ingestion<br/>fpl_sync / team_import]
        DB[(PostgreSQL / SQLite<br/>SQLAlchemy)]
        ENG[Projection Engine<br/>team_strength · xMins · xP · MonteCarlo · risk]
        OPT[Optimizer<br/>PuLP/CBC MILP]
        SVC[Services + API routes]
    end

    subgraph Frontend[Next.js frontend]
        UI[10 trang · vi/en · dark mode · Recharts]
    end

    FPL --> ING
    ODDS -. optional .-> ENG
    EXP --> ING
    ING --> DB
    DB --> ENG
    ENG --> DB
    DB --> OPT
    OPT --> SVC
    DB --> SVC
    SVC -->|JSON /api| UI
```

## 2. Luồng dữ liệu

```mermaid
sequenceDiagram
    participant Cron as Startup/Scheduler
    participant Ing as Ingestion
    participant DB
    participant Eng as Engine
    participant API
    participant UI

    Cron->>Ing: run_full_sync()
    Ing->>DB: upsert teams/players/fixtures (+ SourceFetchLog)
    Ing->>Eng: build_projections()
    Eng->>DB: write ExpectedMinutes + PlayerProjection
    UI->>API: GET /api/dashboard, /api/players ...
    API->>DB: query projections
    API-->>UI: JSON (xP, xMins, confidence, risk)
    UI->>API: POST /api/optimizer/free-hit
    API->>DB: load projections → build OptPlayer
    API->>API: PuLP/CBC solve
    API-->>UI: squad hợp lệ + giải thích
```

## 3. Cấu trúc thư mục

```
fpl-planner/
├── backend/
│   ├── app/
│   │   ├── config.py          # settings (env-driven, không hard-code key)
│   │   ├── db.py              # SQLAlchemy engine/session, init_db()
│   │   ├── cache.py           # Redis hoặc in-memory TTL
│   │   ├── scoring.py         # luật tính điểm mùa hiện tại (configurable)
│   │   ├── models/            # ORM: core, projections, meta
│   │   ├── schemas/           # Pydantic request models
│   │   ├── providers/         # fpl_client, probability, expert_provider
│   │   ├── ingestion/         # fpl_sync, team_import
│   │   ├── engine/            # team_strength, xmins, xpoints, montecarlo,
│   │   │                      #   fixture_difficulty, risk, projections
│   │   ├── optimizer/         # constraints, squad, transfer (next+long-term)
│   │   ├── services/          # players, fixtures, captains, news, team, gameweek
│   │   ├── api/routes/        # health, catalog, optimizer_routes
│   │   ├── seed_demo.py       # dữ liệu offline có nhãn mock
│   │   ├── cli.py             # sync / project / seed-demo
│   │   └── main.py            # FastAPI app + lifespan + CORS
│   ├── alembic/               # migration (baseline create_all)
│   └── tests/                 # pytest: constraints, optimizer, engine, api
└── frontend/
    ├── app/                   # App Router: 10 trang + layout + providers
    ├── components/            # nav, pitch, fpl (cards/badges), ui primitives
    └── lib/                   # api, i18n, format, utils
```

## 4. Database schema (chính)

```mermaid
erDiagram
    teams ||--o{ players : has
    players ||--o{ player_projections : projected
    players ||--o{ expected_minutes : projected
    players ||--o{ player_gameweek_stats : actuals
    players ||--o{ player_prices : history
    teams ||--o{ fixtures : home_away
    expert_sources ||--o{ expert_signals : emits
    gameweeks ||--o{ fixtures : schedules
```

Bảng đầy đủ (spec §20): `seasons, gameweeks, teams, players, player_prices,
fixtures, player_gameweek_stats, player_projections, expected_minutes,
model_versions, injury_reports, set_piece_roles, expert_sources, expert_signals,
source_fetch_logs, user_profiles, optimization_runs`.

Mỗi `player_projections` gắn với `model_version` + `data_cutoff` + `gameweek`
để truy vết (spec: projection phải liên kết model version & cutoff time).

## 5. Nguyên tắc thiết kế

- **Zero-config dev:** SQLite + cache in-memory, không cần Postgres/Redis để chạy thử.
- **Scale-up prod:** cùng codebase chạy Postgres + Redis qua biến môi trường.
- **Provider abstraction:** khi chưa có API tỷ lệ, dùng model nội bộ và gắn nhãn `model_estimate`.
- **Truy vết & minh bạch:** mọi số liệu có nguồn + thời điểm; trang Methodology công khai.
