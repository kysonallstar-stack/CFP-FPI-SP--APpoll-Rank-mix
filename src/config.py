"""Load config.yaml and resolve paths relative to the repo root."""
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def current_season(now: datetime, offseason_months: list[int]) -> tuple[int, bool]:
    """(season, active). A season runs from August through January of the next
    year, so January 2027 still belongs to the 2026 season. Months listed in
    offseason_months (default Feb-Jul) are inactive: the pipeline takes a break."""
    season = now.year if now.month >= 8 else now.year - 1
    return season, now.month not in offseason_months


def load_config(path: Path | str = ROOT / "config.yaml", now: datetime | None = None) -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    # season: auto -> work it out from today's date, so next year needs no edits.
    season, active = current_season(now or datetime.now(timezone.utc), cfg.get("offseason_months", [2, 3, 4, 5, 6, 7]))
    if cfg.get("season") in (None, "auto"):
        cfg["season"] = season
    cfg["season_active"] = active
    return cfg


def repo_path(rel: str) -> Path:
    return ROOT / rel
