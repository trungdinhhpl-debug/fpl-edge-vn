"""Command-line tools.

  python -m app.cli sync            # full FPL sync + build projections
  python -m app.cli sync --detail   # also pull per-player histories (slow)
  python -m app.cli project         # rebuild projections only
  python -m app.cli seed-demo       # load offline demo fixtures (no network)
"""
from __future__ import annotations

import argparse
import sys

from app.db import init_db, session_scope


def cmd_sync(detail: bool) -> None:
    from app.ingestion.fpl_sync import run_full_sync
    init_db()
    with session_scope() as db:
        result = run_full_sync(db, build_proj=True, detail=detail)
    print("Sync complete.")
    print(f"  players:     {result['bootstrap']['players']}")
    print(f"  fixtures:    {result['fixtures']['fixtures']}")
    print(f"  projections: {result['projections']['projections_written']} "
          f"across GW {result['projections']['gameweeks']}")


def cmd_project() -> None:
    from app.engine.projections import build_projections
    init_db()
    with session_scope() as db:
        result = build_projections(db)
    print(f"Projections rebuilt: {result}")


def cmd_seed_demo() -> None:
    from app.seed_demo import seed_demo
    init_db()
    with session_scope() as db:
        result = seed_demo(db)
    print(f"Demo data seeded: {result}")


def cmd_report(out: str) -> None:
    import os
    from app.report import render_report_page
    init_db()
    with session_scope() as db:
        page = render_report_page(db)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Report written: {out} ({len(page)} bytes)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync", help="Full FPL sync + projections")
    p_sync.add_argument("--detail", action="store_true", help="pull per-player histories")

    sub.add_parser("project", help="Rebuild projections only")
    sub.add_parser("seed-demo", help="Load offline demo data (no network)")

    p_report = sub.add_parser("report", help="Generate a shareable static HTML report")
    p_report.add_argument("--out", default="../reports/report.html", help="output path")

    args = parser.parse_args(argv)
    if args.command == "sync":
        cmd_sync(args.detail)
    elif args.command == "project":
        cmd_project()
    elif args.command == "seed-demo":
        cmd_seed_demo()
    elif args.command == "report":
        cmd_report(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
