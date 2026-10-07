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


def _change(hist: list, week: int, sources: dict) -> dict:
    """Change in blend rank and rating since last week. d_rank > 0 = moved up.
    None when there's no prior week, or the two weeks used different computer
    inputs (e.g. Elo early, SP+/FPI later), which would look like a fake jump."""
    by_week = {w: (rank, rating) for w, rank, rating in hist}
    if week not in by_week or week - 1 not in by_week or sources.get(week) != sources.get(week - 1):
        return {"d_rank": None, "d_rating": None}
    (r1, x1), (r0, x0) = by_week[week], by_week[week - 1]
    return {"d_rank": r0 - r1, "d_rating": round(x1 - x0, 1)}


def _expected_margins(proc: Path, week: int) -> dict:
    """game id -> predicted home margin, from the simulation saved the week before each game."""
    out = {}
    for k in range(1, week + 1):
        path = proc / f"sim_week_{k - 1}.json"
        if path.exists():
            for g in json.loads(path.read_text()).get("remaining_games", []):
                if g["week"] == k:
                    out[g["game_id"]] = g["predicted_margin"]
    return out


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
            "color": i.get("color"), "logo": i.get("logo"),
            "rank": t["rank"], "rating": t["rating"], "sel_rank": t["selection_rank"],
            "w": t["wins"], "l": t["losses"], "cw": t["conf_wins"], "cl": t["conf_losses"],
            "ap": t["ap_rank"], "ap_pts": t["ap_points"], "cfp": t["cfp_rank"],
            "sp": t["sp_rank"], "fpi": t["fpi_rank"], "elo": t["elo_rank"], "srs": t["srs_rank"], "metrics": t["metrics_rank"],
            "gap": t["poll_vs_metrics"],
            "p_title": o["p_title_game"], "p_conf": o["p_win_conference"],
            "p_playoff": o["p_playoff"], "p_bye": o["p_bye"], "xw": o["expected_wins"],
            "hist": history.get(t["id"], []),
            **_change(history.get(t["id"], []), week, history_sources),
        })

    expected = _expected_margins(proc, week)
    games = []
    for g in wd["games_completed"]:
        if g["home_fbs"] or g["away_fbs"]:
            games.append({"id": g["id"], "wk": g["week"], "start": g["start"], "n": g["neutral"],
                          "h": g["home_id"], "a": g["away_id"], "hn": g["home_name"], "an": g["away_name"],
                          "hp": g["home_points"], "apts": g["away_points"],
                          # what the model predicted before the game (home margin), if we saved it
                          "exp": expected.get(g["id"])})
    for g in sim["remaining_games"]:
        games.append({"id": g["game_id"], "wk": g["week"], "start": g["start"], "n": g["neutral"],
                      "h": g["home_id"], "a": g["away_id"], "hn": g["home"], "an": g["away"],
                      "p": g["home_win_prob"], "m": g["predicted_margin"], "ph": g["placeholder"]})

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
        "week_games": sim["week_games"],
        "final_field": sim["final_field"],   # Selection Day bracket, else None
        "whatif": sim["inputs"],             # inputs for the in-browser what-if simulator
        "games": sorted(games, key=lambda g: (g["wk"], g["start"] or "")),
    }
    # The disagreement lists only need ids; the page looks teams up.
    site["disagree"] = {k: [t["id"] for t in v] for k, v in site["disagree"].items()}
    return site, ratings, sim


def write(site: dict, site_dir: Path) -> Path:
    path = site_dir / "data.json"
    path.write_text(json.dumps(site, ensure_ascii=False, separators=(",", ":")))
    return path
