"""Build site/data.json: everything the static page shows, in one file.

The page is plain HTML/CSS/JS that fetches this file, so the site needs no
server and no build step. Keys are short to keep the file small on phones.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from src.rating import blend_week
from src.sim import run as run_sim


def _latest_week(proc: Path) -> int:
    return max(int(p.stem.split("_")[1]) for p in proc.glob("week_[0-9]*.json"))


def _load(proc: Path, n: int) -> dict:
    return json.loads((proc / f"week_{n}.json").read_text())


def build(season: int, proc: Path, cfg: dict, n_sims: int, seed) -> tuple[dict, dict, dict]:
    """Returns (site data, ratings output, sim output) for the latest processed week."""
    week = _latest_week(proc)
    wd = _load(proc, week)
    ratings = blend_week(wd, cfg)
    sim = run_sim(wd, cfg, n_sims, seed)

    # Rank history: re-rate every earlier week with the same method.
    history, history_sources = {}, {}
    for k in range(1, week + 1):
        if not (proc / f"week_{k}.json").exists():
            continue
        r = blend_week(_load(proc, k), cfg)
        history_sources[k] = r["metric_sources"]
        for t in r["teams"]:
            history.setdefault(t["id"], []).append([k, t["rank"], t["rating"]])

    odds = {t["id"]: t for t in sim["teams"]}
    info = {t["id"]: t for t in wd["teams"]}
    teams = []
    for t in ratings["teams"]:
        o, i = odds[t["id"]], info[t["id"]]
        teams.append({
            "id": t["id"], "name": t["team"], "abbr": i.get("abbreviation"), "conf": t["conference"],
            "color": i.get("color"),
            "rank": t["rank"], "rating": t["rating"], "sel_rank": t["selection_rank"],
            "w": t["wins"], "l": t["losses"], "cw": t["conf_wins"], "cl": t["conf_losses"],
            "ap": t["ap_rank"], "ap_pts": t["ap_points"], "cfp": t["cfp_rank"],
            "sp": t["sp_rank"], "fpi": t["fpi_rank"], "elo": t["elo_rank"], "metrics": t["metrics_rank"],
            "gap": t["poll_vs_metrics"],
            "p_title": o["p_title_game"], "p_conf": o["p_win_conference"],
            "p_playoff": o["p_playoff"], "p_bye": o["p_bye"], "xw": o["expected_wins"],
            "hist": history.get(t["id"], []),
        })

    games = []
    for g in wd["games_completed"]:
        if g["home_fbs"] or g["away_fbs"]:
            games.append({"id": g["id"], "wk": g["week"], "start": g["start"], "n": g["neutral"],
                          "h": g["home_id"], "a": g["away_id"], "hn": g["home_name"], "an": g["away_name"],
                          "hp": g["home_points"], "apts": g["away_points"]})
    for g in sim["remaining_games"]:
        games.append({"id": g["game_id"], "wk": g["week"], "start": g["start"], "n": g["neutral"],
                      "h": g["home_id"], "a": g["away_id"], "hn": g["home"], "an": g["away"],
                      "p": g["home_win_prob"], "m": g["predicted_margin"]})

    gaps = [t for t in teams if t["gap"] is not None]
    site = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "season": season, "week": week, "next_week": sim["next_week"],
        "poll": ratings["poll_source"], "poll_weight": ratings["poll_weight"],
        "selection_poll": ratings["selection_poll_source"],
        "metric_sources": ratings["metric_sources"], "history_sources": history_sources,
        "missing": wd["missing"], "stale": wd.get("stale", {}),
        "sims": sim["sims"], "settings": sim["settings"],
        "teams": teams,
        "disagree": {
            "poll_higher": sorted((t for t in gaps if t["gap"] > 0), key=lambda t: -t["gap"])[:10],
            "metrics_higher": sorted((t for t in gaps if t["gap"] < 0), key=lambda t: t["gap"])[:10],
        },
        "top_matchups": sim["top_matchups"],
        "leverage": sim["leverage"],
        "games": sorted(games, key=lambda g: (g["wk"], g["start"] or "")),
    }
    # The disagreement lists only need ids; the page looks teams up.
    site["disagree"] = {k: [t["id"] for t in v] for k, v in site["disagree"].items()}
    return site, ratings, sim


def write(site: dict, site_dir: Path) -> Path:
    path = site_dir / "data.json"
    path.write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")))
    return path
