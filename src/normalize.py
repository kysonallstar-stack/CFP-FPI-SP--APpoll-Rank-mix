"""Turn raw cached JSON into one clean file per week, keyed by team ID.

data/processed/<season>/week_<n>.json describes the world as of the end of
week n and never includes anything learned later:
  - completed games: weeks 1..n, from the frozen week folders
  - remaining games: weeks > n, with scores removed
  - ratings/polls: from week n; if missing, carried forward from the most
    recent earlier week (recorded under "sources"), never borrowed from later
Unmatched team names go to data/processed/<season>/unmatched.log.

Usage: python -m src.normalize [--season 2026]
"""
import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_config, repo_path
from src.teams import TeamRegistry

log = logging.getLogger(__name__)


def _read(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _game(g: dict, reg: TeamRegistry, hide_score: bool) -> dict:
    out = {"id": g["id"], "week": g["week"], "start": g.get("startDate"),
           "neutral": bool(g.get("neutralSite")), "conference_game": bool(g.get("conferenceGame")),
           "completed": bool(g.get("completed")) and not hide_score, "notes": g.get("notes")}
    for side in ("home", "away"):
        tid = g.get(f"{side}Id")
        fbs = reg.is_fbs(tid)
        if g.get(f"{side}Classification") == "fbs" and not fbs:
            reg.unmatched.append(("games", f"{g.get(f'{side}Team')} (id {tid})"))
        out[f"{side}_id"] = tid
        out[f"{side}_name"] = reg.name(tid) if fbs else g.get(f"{side}Team")
        out[f"{side}_fbs"] = fbs
        out[f"{side}_conference"] = g.get(f"{side}Conference")
        out[f"{side}_points"] = None if hide_score else g.get(f"{side}Points")
    return out


def _poll(poll_list: list[dict], name: str, reg: TeamRegistry) -> list[dict] | None:
    poll = next((p for p in poll_list if p["poll"] == name), None)
    if poll is None:
        return None
    out = []
    for r in poll["ranks"]:
        tid = r.get("teamId")
        if not reg.is_fbs(tid):  # fall back to the name if the ID is unknown
            tid = reg.match(r["school"], source=name)
        if tid is not None:
            out.append({"id": tid, "rank": r["rank"], "points": r.get("points"),
                        "first_place_votes": r.get("firstPlaceVotes")})
    return out


def _ratings(rows: list[dict], field, reg: TeamRegistry, source: str) -> dict[int, float]:
    out = {}
    for r in rows:
        if r["team"] == "nationalAverages":  # SP+ includes a league-average row
            continue
        tid = reg.match(r["team"], source=source)
        val = field(r)
        if tid is not None and val is not None:
            out[tid] = val
    return out


RATING_SOURCES = {
    "sp": ("sp.json", lambda r: r.get("rating")),
    "fpi": ("fpi.json", lambda r: r.get("fpi")),
    "elo": ("elo.json", lambda r: r.get("elo")),
}


def build_week(season_dir: Path, n: int, reg: TeamRegistry, cfg: dict) -> dict:
    wdirs = {k: season_dir / f"week_{k}" for k in range(n + 1)}
    missing, sources = [], {}

    def latest_file(name: str):
        """Newest copy of `name` from week n back to week 0 (carry-forward, never look-ahead)."""
        for k in range(n, -1, -1):
            data = _read(wdirs[k] / name)
            if data:
                return k, data
        return None, None

    completed = []
    for k in range(1, n + 1):
        wk_games = _read(wdirs[k] / "games.json")
        if wk_games is None:
            missing.append(f"games for week {k}")
            continue
        completed += [_game(g, reg, hide_score=False) for g in wk_games]

    schedule = _read(season_dir / "schedule.json") or []
    remaining = [_game(g, reg, hide_score=True) for g in schedule if g["week"] > n]

    ratings = {}
    for key, (fname, field) in RATING_SOURCES.items():
        k, rows = latest_file(fname)
        sources[key] = k
        if rows is None:
            missing.append(key)
            ratings[key] = {}
        else:
            ratings[key] = _ratings(rows, field, reg, key)

    polls = {}
    for key in ("ap", "cfp"):
        name = cfg["polls"][key]
        polls[key], sources[key] = None, None
        for k in range(n, -1, -1):
            p = _poll(_read(wdirs[k] / "polls.json") or [], name, reg)
            if p is not None:
                polls[key], sources[key] = p, k
                break
        if polls[key] is None and key == "ap":
            missing.append("ap")

    return {
        "season": int(season_dir.name), "week": n,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "teams": list(reg.by_id.values()),
        # JSON keys must be strings; team IDs are stored as strings here.
        "ratings": {k: {str(t): v for t, v in r.items()} for k, r in ratings.items()},
        "polls": polls,
        "games_completed": [g for g in completed if g["completed"]],
        "games_remaining": remaining + [g for g in completed if not g["completed"]],
        "sources": sources,   # week each input came from (None = unavailable)
        "missing": missing,
    }


def normalize_season(season: int, cfg: dict) -> list[Path]:
    raw = repo_path(cfg["paths"]["raw"]) / str(season)
    out_dir = repo_path(cfg["paths"]["processed"]) / str(season)
    out_dir.mkdir(parents=True, exist_ok=True)
    reg = TeamRegistry.from_files(_read(raw / "teams_fbs.json"), repo_path(cfg["paths"]["aliases"]))

    written = []
    weeks = sorted(int(p.name.split("_")[1]) for p in raw.glob("week_*"))
    for n in weeks:
        data = build_week(raw, n, reg, cfg)
        path = out_dir / f"week_{n}.json"
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        written.append(path)

    unmatched = sorted(set(reg.unmatched))
    (out_dir / "unmatched.log").write_text("".join(f"{src}\t{name}\n" for src, name in unmatched))
    for src, name in unmatched:
        log.warning("unmatched team from %s: %r", src, name)
    return written


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=cfg["season"])
    args = ap.parse_args()
    for p in normalize_season(args.season, cfg):
        log.info("wrote %s", p.relative_to(repo_path(".")))


if __name__ == "__main__":
    main()
