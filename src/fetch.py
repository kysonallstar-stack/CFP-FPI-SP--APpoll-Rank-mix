"""Download and cache raw CFBD data.

Layout:
  data/raw/<season>/teams_fbs.json      FBS teams + conferences (fetched once per season)
  data/raw/<season>/calendar.json       week date ranges (fetched once per season)
  data/raw/<season>/schedule.json       every FBS game, refreshed each run (remaining schedule)
  data/raw/<season>/week_<n>/
      games.json     games played in week n            -- frozen once the week is complete
      polls.json     polls released AFTER week n's games (CFBD labels these week n+1)
                     -- refreshed EVERY run, because polls don't arrive on a fixed day:
                        the AP is usually Sunday, CFP rankings are Tuesday night, and
                        either can be late. One API call covers the whole season.
      elo.json       Elo after week n
      sp.json/fpi.json  snapshot of the "current" values while week n is the latest
                     finished week (current season only; the API has no weekly history).
                     Re-taken on every run until the next week finishes, so a late
                     SP+/FPI update is still picked up.
      _meta.json     fetched_at, complete flag, anything missing

A week is complete once every FBS game in it is final (or a grace period has
passed for cancelled games). Complete weeks' games are never downloaded again.
week_0 holds the preseason poll.

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
    """Fetch what's new for `season`. Returns a summary."""
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
    summary = {"season": season, "latest_week": latest, "fetched": [], "polls_updated": [],
               "skipped_complete": [n for n in finished if n not in todo]}
    if not finished:
        return summary

    # Polls: one call for the whole season, written into every finished week.
    # Only rewrite a file when its contents changed, so git history stays clean.
    all_polls = {w["week"]: w["polls"] for w in client.get("rankings", year=season, seasonType="regular")}
    for n in finished:
        path = season_dir / f"week_{n}" / "polls.json"
        new = all_polls.get(n + 1, [])
        if new and new != _read(path):
            _write(path, new)
            summary["polls_updated"].append(n)
        elif not path.exists():
            _write(path, new)

    # The latest week's ratings snapshot is retaken every run while the season is on.
    if in_progress and latest and latest > 0:
        wdir = season_dir / f"week_{latest}"
        for name, endpoint in [("sp.json", "ratings/sp"), ("fpi.json", "ratings/fpi")]:
            _write(wdir / name, client.get(endpoint, year=season))
        _write(wdir / "elo.json", client.get("ratings/elo", year=season, week=latest))

    if not todo:
        return summary

    schedule = client.get("games", year=season, seasonType="regular", classification="fbs")
    _write(season_dir / "schedule.json", schedule)

    for n in todo:
        wdir = season_dir / f"week_{n}"
        missing = []

        games = [g for g in schedule if g["week"] == n] if n > 0 else []
        unfinished = [g["id"] for g in games if not g.get("completed")]
        complete = not unfinished or now > ends[n] + timedelta(days=fcfg["games_grace_days"])
        if unfinished:
            missing.append(f"{len(unfinished)} game(s) without a final score")
        if n > 0:
            _write(wdir / "games.json", games)
            if not (wdir / "elo.json").exists():
                elo = client.get("ratings/elo", year=season, week=n)
                _write(wdir / "elo.json", elo)
                if not elo:
                    missing.append("Elo")
            if not (wdir / "sp.json").exists():
                missing.append("SP+/FPI (no weekly history available)")

        if not any(p["poll"] == polls_cfg["ap"] for p in _read(wdir / "polls.json") or []):
            missing.append("AP poll (not released yet; re-checked every run)")

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
    if summary["polls_updated"]:
        log.info("new/changed polls for week(s): %s", summary["polls_updated"])
    log.info("skipped %d frozen week(s); API calls this run: %d",
             len(summary["skipped_complete"]), client.calls)


if __name__ == "__main__":
    main()
