"""Download and cache raw CFBD data.

Layout:
  data/raw/<season>/teams_fbs.json      FBS teams + conferences (fetched once per season)
  data/raw/<season>/calendar.json       week date ranges (fetched once per season)
  data/raw/<season>/schedule.json       every FBS game, refreshed each run (remaining schedule)
  data/raw/<season>/week_<n>/
      games.json     games played in week n
      polls.json     polls released AFTER week n's games (CFBD labels these week n+1)
      elo.json       Elo after week n
      sp.json/fpi.json  snapshot taken while week n was the latest week (current season only;
                        the API has no weekly history for these)
      _meta.json     fetched_at, complete flag, anything missing

week_0 holds the preseason poll. Once a week's _meta.json says complete it is
never downloaded again.

Usage: python -m src.fetch [--season 2026]
"""
import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.cfbd import CFBDClient
from src.config import load_config, repo_path

log = logging.getLogger(__name__)


def _read(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def regular_weeks(calendar: list[dict]) -> list[dict]:
    return sorted((w for w in calendar if w["seasonType"] == "regular"), key=lambda w: w["week"])


def fetch_season(client, season: int, raw_root: Path, cfg: dict, now: datetime | None = None) -> dict:
    """Fetch every finished week of `season` that isn't frozen yet. Returns a summary."""
    now = now or datetime.now(timezone.utc)
    fcfg, polls_cfg = cfg["fetch"], cfg["polls"]
    season_dir = raw_root / str(season)

    for name, endpoint, params in [
        ("teams_fbs.json", "teams/fbs", {"year": season}),
        ("calendar.json", "calendar", {"year": season}),
    ]:
        if not (season_dir / name).exists():
            _write(season_dir / name, client.get(endpoint, **params))

    weeks = regular_weeks(_read(season_dir / "calendar.json"))
    if not weeks:
        raise RuntimeError(f"No regular-season calendar for {season}")
    # week_0 = preseason: "ends" when week 1 starts.
    ends = {0: _ts(weeks[0]["startDate"])} | {w["week"]: _ts(w["endDate"]) for w in weeks}
    finished = [n for n, end in sorted(ends.items()) if end <= now]
    latest = finished[-1] if finished else None
    in_progress = now < ends[weeks[-1]["week"]]

    todo = [n for n in finished if not (_read(season_dir / f"week_{n}" / "_meta.json") or {}).get("complete")]
    summary = {"season": season, "latest_week": latest, "fetched": [], "skipped_complete": [n for n in finished if n not in todo]}
    if not todo:
        return summary

    schedule = client.get("games", year=season, seasonType="regular", classification="fbs")
    _write(season_dir / "schedule.json", schedule)

    for n in todo:
        wdir = season_dir / f"week_{n}"
        missing = []

        games = [g for g in schedule if g["week"] == n] if n > 0 else []
        unfinished = [g["id"] for g in games if not g.get("completed")]
        games_done = not unfinished or now > ends[n] + timedelta(days=fcfg["games_grace_days"])
        if unfinished:
            missing.append(f"{len(unfinished)} game(s) without a final score")
        if n > 0:
            _write(wdir / "games.json", games)

        polls = client.get("rankings", year=season, week=n + 1, seasonType="regular")
        poll_list = polls[0]["polls"] if polls else []
        _write(wdir / "polls.json", poll_list)
        has_ap = any(p["poll"] == polls_cfg["ap"] for p in poll_list)
        if not has_ap:
            missing.append("AP poll")

        if n > 0:
            elo = client.get("ratings/elo", year=season, week=n)
            _write(wdir / "elo.json", elo)
            if not elo:
                missing.append("Elo")

        # SP+/FPI only exist as "current" values, so snapshot them for the latest
        # week of an in-progress season. Earlier weeks can't be backfilled.
        if n == latest and in_progress and n > 0:
            for name, endpoint in [("sp.json", "ratings/sp"), ("fpi.json", "ratings/fpi")]:
                data = client.get(endpoint, year=season)
                _write(wdir / name, data)
                if not data:
                    missing.append(name[:-5].upper())
        elif not (wdir / "sp.json").exists() and n > 0:
            missing.append("SP+/FPI (no weekly history available)")

        poll_ok = has_ap or now > ends[n] + timedelta(days=fcfg["poll_grace_days"])
        complete = games_done and poll_ok
        _write(wdir / "_meta.json", {
            "season": season, "week": n,
            "fetched_at": now.isoformat(timespec="seconds"),
            "complete": complete, "missing": missing,
        })
        for m in missing:
            log.warning("%s week %d: missing %s", season, n, m)
        summary["fetched"].append({"week": n, "complete": complete, "missing": missing})

    return summary


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=cfg["season"])
    args = ap.parse_args()

    client = CFBDClient.from_config(cfg)
    summary = fetch_season(client, args.season, repo_path(cfg["paths"]["raw"]), cfg)
    for w in summary["fetched"]:
        state = "complete" if w["complete"] else "incomplete (will re-fetch next run)"
        log.info("week %d: %s", w["week"], state)
    log.info("skipped %d frozen week(s); API calls this run: %d",
             len(summary["skipped_complete"]), client.calls)


if __name__ == "__main__":
    main()
