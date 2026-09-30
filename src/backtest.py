"""Backtest the blend on past seasons using only what was known each week.

SP+ and FPI have no weekly history in the API, so the metrics side here is
CFBD's weekly Elo plus our own margin rating (ridge-regularized SRS) computed
from games played so far. This tests the *blend method*, not SP+ itself.

For each week n, ratings as of the end of week n predict week n+1's FBS-vs-FBS
games. Methods compared:
  blend    - metrics (Elo + SRS) blended with the AP poll, as configured
  metrics  - the same metrics with poll weight 0 (stands in for "SP+ alone")
  ap       - the AP poll alone (points z-scored, unranked = floor)
  vegas    - median closing spread across sportsbooks (reference, not a model)

Also measured: the margin SD (sigma), how far ratings move from week n to the
end of the season (rating uncertainty for the sim), committee loss/champion
weights (fitted to final CFP rankings), and whether season simulations are
calibrated with and without rating uncertainty.

Usage: python -m src.backtest [--season 2025] [--holdout 2024]
"""
import argparse
import copy
import itertools
import json
import logging
import statistics as st
from collections import defaultdict

import numpy as np

from src.config import load_config, repo_path
from src.rating import blend_week, poll_side, poll_points
from src import sim as simmod

log = logging.getLogger(__name__)


# ---------- inputs ----------------------------------------------------------

def load_weeks(season: int, cfg: dict) -> dict[int, dict]:
    proc = repo_path(cfg["paths"]["processed"]) / str(season)
    return {int(p.stem.split("_")[1]): json.loads(p.read_text()) for p in proc.glob("week_[0-9]*.json")}


def load_lines(season: int, cfg: dict) -> dict[int, float]:
    """game id -> predicted home margin from the median sportsbook spread."""
    path = repo_path(cfg["paths"]["raw"]) / str(season) / "lines.json"
    if not path.exists():
        return {}
    out = {}
    for g in json.loads(path.read_text()):
        spreads = [l["spread"] for l in g.get("lines", []) if l.get("spread") is not None]
        if spreads:
            out[g["id"]] = -float(np.median(spreads))   # spread is from the home team's side
    return out


def srs(games: list, team_ids: list, hfa: float, lam: float, cap: float) -> dict[str, float]:
    """Ridge-regularized margin rating: margin ~ r_home - r_away + HFA.

    lam shrinks every team toward average; lam = sigma^2 / sd(ratings)^2 is the
    Bayesian prior "a team is average until its games say otherwise". Margins
    are capped so one blowout doesn't dominate. All FCS teams share one rating.
    """
    idx = {t: i for i, t in enumerate(team_ids)}
    fcs = len(team_ids)
    rows, y = [], []
    for g in games:
        h = idx.get(str(g["home_id"]), fcs)
        a = idx.get(str(g["away_id"]), fcs)
        if h == a:
            continue
        x = np.zeros(fcs + 1)
        x[h], x[a] = 1.0, -1.0
        rows.append(x)
        margin = np.clip(g["home_points"] - g["away_points"], -cap, cap)
        y.append(margin - (0.0 if g["neutral"] else hfa))
    if not rows:
        return {}
    X, y = np.array(rows), np.array(y)
    r = np.linalg.solve(X.T @ X + lam * np.eye(fcs + 1), X.T @ y)
    return {t: float(r[i]) for t, i in idx.items()}


def bt_config(cfg: dict, w: tuple | None = None) -> dict:
    c = copy.deepcopy(cfg)
    c["rating"]["metric_sources"] = cfg["backtest"]["metric_sources"]
    c["rating"]["metric_fallback"] = []
    if w is not None:
        c["weight"]["start"], c["weight"]["end"] = w
    return c


def with_srs(wd: dict, cfg: dict) -> dict:
    wd = copy.copy(wd)
    wd["ratings"] = dict(wd["ratings"])
    b = cfg["backtest"]
    ids = [str(t["id"]) for t in wd["teams"]]
    wd["ratings"]["srs"] = srs(wd["games_completed"], ids, cfg["sim"]["hfa"], b["srs_lambda"], b["srs_margin_cap"])
    return wd


# ---------- game-prediction accuracy ----------------------------------------

def method_ratings(wd: dict, cfg: dict, w: tuple | None = None) -> dict[str, dict[str, float]]:
    """Points-scale ratings per method for one week."""
    blend = blend_week(wd, bt_config(cfg, w))
    out = {"blend": {str(t["id"]): t["rating"] for t in blend["teams"]}}
    sd = blend["points_scale"]["sd"]
    out["metrics"] = {str(t["id"]): t["metrics_z"] * sd for t in blend["teams"]}
    pts, _ = poll_points(wd, use_cfp=False)
    if pts:
        z = poll_side(pts, {t: 0.0 for t in out["blend"]}, "zscore")
        out["ap"] = {t: v * sd for t, v in z.items()}
    return out


def predictions(weeks: dict, cfg: dict, lines: dict, w: tuple | None = None, prepared=None) -> list[dict]:
    hfa = cfg["sim"]["hfa"]
    rows = []
    for n in sorted(weeks):
        if n < 1 or n + 1 not in weeks:
            continue
        wd = prepared[n] if prepared else with_srs(weeks[n], cfg)
        rat = method_ratings(wd, cfg, w)
        for g in weeks[n + 1]["games_completed"]:
            if g["week"] != n + 1 or not (g["home_fbs"] and g["away_fbs"]):
                continue
            h, a = str(g["home_id"]), str(g["away_id"])
            edge = 0.0 if g["neutral"] else hfa
            actual = g["home_points"] - g["away_points"]
            if actual == 0:
                continue
            base = {"week": n + 1, "game_id": g["id"], "actual": actual,
                    "interconf": g["home_conference"] != g["away_conference"]}
            for m, r in rat.items():
                if h in r and a in r:
                    rows.append({**base, "method": m, "pred": r[h] - r[a] + edge})
            if g["id"] in lines:
                rows.append({**base, "method": "vegas", "pred": lines[g["id"]]})
    return rows


def score(rows: list[dict]) -> dict:
    if not rows:
        return {}
    p = np.array([r["pred"] for r in rows]); a = np.array([r["actual"] for r in rows])
    slope = float(np.polyfit(p, a, 1)[0]) if p.std() > 0 else None
    return {"n": len(rows),
            "pick_acc": round(float(np.mean((p >= 0) == (a > 0))), 3),   # a 0.0 prediction picks home
            "mae": round(float(np.mean(np.abs(a - p))), 2),
            "resid_sd": round(float(np.std(a - p)), 2),
            "slope": round(slope, 2) if slope is not None else None}


def accuracy_tables(rows: list[dict]) -> dict:
    by = defaultdict(list)
    for r in rows:
        by[("all", r["method"])].append(r)
        by[(f"week {r['week']}", r["method"])].append(r)
        if r["interconf"]:
            by[("interconf", r["method"])].append(r)
    # Compare methods on the same games: only games every model (not vegas) predicted.
    return {f"{k[0]}|{k[1]}": score(v) for k, v in sorted(by.items())}


def common_games(rows: list[dict], methods: list[str]) -> list[dict]:
    have = defaultdict(set)
    for r in rows:
        have[r["game_id"]].add(r["method"])
    keep = {g for g, ms in have.items() if all(m in ms for m in methods)}
    return [r for r in rows if r["game_id"] in keep and r["method"] in methods]


# ---------- tuning and uncertainty ------------------------------------------

def tune_w(weeks, cfg, lines, grid) -> list[dict]:
    prepared = {n: with_srs(wd, cfg) for n, wd in weeks.items() if n >= 1}
    out = []
    for w in grid:
        rows = [r for r in predictions(weeks, cfg, {}, w, prepared) if r["method"] == "blend"]
        s = score(rows)
        out.append({"start": w[0], "end": w[1], "mae": s["mae"], "pick_acc": s["pick_acc"]})
    return sorted(out, key=lambda x: x["mae"])


def rating_drift(weeks, cfg) -> dict[int, float]:
    """SD across FBS teams of (end-of-regular-season rating - rating at week n)."""
    last = max(n for n in weeks if weeks[n]["games_completed"] and n >= 1)
    final = {str(t["id"]): t["rating"] for t in blend_week(with_srs(weeks[last], cfg), bt_config(cfg))["teams"]}
    out = {}
    for n in sorted(weeks):
        if 1 <= n < last:
            cur = {str(t["id"]): t["rating"] for t in blend_week(with_srs(weeks[n], cfg), bt_config(cfg))["teams"]}
            out[n] = round(float(np.std([final[t] - cur[t] for t in cur if t in final])), 2)
    return out


# ---------- committee fit ----------------------------------------------------

def committee_data(weeks, cfg) -> dict | None:
    """End-of-season metrics rating, losses, champions, and final CFP ranks."""
    for n in sorted(weeks, reverse=True):
        wd = weeks[n]
        cfp = wd["polls"].get("cfp")
        titles = [g for g in wd["games_completed"] if "Championship" in (g.get("notes") or "")
                  and g["home_fbs"] and g["away_fbs"]]
        if cfp and titles and wd["sources"].get("cfp") == n:
            break
    else:
        return None
    blend = blend_week(with_srs(wd, cfg), bt_config(cfg, (0.0, 0.0)))   # metrics only
    champs = {str(g["home_id"] if g["home_points"] > g["away_points"] else g["away_id"]) for g in titles}
    return {"week": n, "rating": {str(t["id"]): t["rating"] for t in blend["teams"]},
            "losses": {str(t["id"]): t["losses"] for t in blend["teams"]},
            "champs": champs, "cfp": {str(p["id"]): p["rank"] for p in cfp}}


def committee_cost(data: dict, L: float, C: float) -> tuple[float, int]:
    """Sum of |our rank - CFP rank| over the CFP top 25, and top-12 overlap."""
    s = {t: data["rating"][t] - L * data["losses"][t] + (C if t in data["champs"] else 0.0) for t in data["rating"]}
    order = sorted(s, key=s.get, reverse=True)
    ours = {t: i + 1 for i, t in enumerate(order)}
    footrule = sum(abs(ours[t] - r) for t, r in data["cfp"].items() if t in ours)
    top12 = len(set(order[:12]) & {t for t, r in data["cfp"].items() if r <= 12})
    return footrule, top12


def fit_committee(datasets: list[dict]) -> list[dict]:
    out = []
    for L, C in itertools.product(np.arange(0, 10.5, 0.5), np.arange(0, 8.5, 0.5)):
        costs = [committee_cost(d, L, C) for d in datasets]
        out.append({"loss_penalty": float(L), "champ_bonus": float(C),
                    "footrule": sum(c[0] for c in costs), "top12_overlap": [c[1] for c in costs]})
    return sorted(out, key=lambda x: (x["footrule"], -sum(x["top12_overlap"])))


# ---------- season-sim calibration --------------------------------------------

def actual_outcomes(season: int, cfg: dict, weeks: dict) -> dict:
    """The real CFP field (from postseason playoff games) and conference champions."""
    raw = repo_path(cfg["paths"]["raw"]) / str(season)
    last = max(weeks)
    titles = [g for g in weeks[last]["games_completed"] if "Championship" in (g.get("notes") or "")]
    champs = {str(g["home_id"] if g["home_points"] > g["away_points"] else g["away_id"]) for g in titles}
    field = set()
    for g in json.loads((raw / "postseason.json").read_text()):
        if "College Football Playoff" in (g.get("notes") or ""):
            field |= {str(g["homeId"]), str(g["awayId"])}
    return {"champs": champs, "field": field}


def sim_calibration(season, weeks, cfg, at_weeks, n_sims) -> list[dict]:
    truth = actual_outcomes(season, cfg, weeks)
    out = []
    for n in at_weeks:
        if n not in weeks:
            continue
        wd = with_srs(weeks[n], cfg)
        for label, unc in (("no uncertainty", False), ("with uncertainty", True)):
            c = bt_config(cfg)
            c["playoff"] = {**cfg["playoff"], **cfg["backtest"]["playoff_rules"].get(season, {})}
            if not unc:
                c["sim"] = {**cfg["sim"], "rating_uncertainty": None}
            res = simmod.run(wd, c, n_sims, seed=1)
            p_po = np.array([t["p_playoff"] for t in res["teams"]])
            y_po = np.array([str(t["id"]) in truth["field"] for t in res["teams"]], dtype=float)
            p_cf = np.array([t["p_win_conference"] for t in res["teams"]])
            y_cf = np.array([str(t["id"]) in truth["champs"] for t in res["teams"]], dtype=float)
            eps = 1e-3
            ll = lambda p, y: float(-np.mean(y * np.log(np.clip(p, eps, 1)) + (1 - y) * np.log(np.clip(1 - p, eps, 1))))
            out.append({"week": n, "model": label,
                        "brier_playoff": round(float(np.mean((p_po - y_po) ** 2)), 4),
                        "logloss_playoff": round(ll(p_po, y_po), 4),
                        "brier_conf": round(float(np.mean((p_cf - y_cf) ** 2)), 4),
                        "logloss_conf": round(ll(p_cf, y_cf), 4),
                        "p_assigned_to_actual_field": round(float(p_po[y_po == 1].sum()), 2)})
    return out


# ---------- report ------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--holdout", type=int, default=2024)
    ap.add_argument("--sims", type=int, default=2000)
    args = ap.parse_args()

    report = {}
    for season in (args.season, args.holdout):
        weeks = load_weeks(season, cfg)
        lines = load_lines(season, cfg)
        rows = predictions(weeks, cfg, lines)
        models = ["blend", "metrics", "ap"]
        fair = common_games(rows, models)
        vegas_fair = common_games(rows, models + ["vegas"])
        report[season] = {
            "accuracy": accuracy_tables(fair),
            "accuracy_vs_vegas": accuracy_tables(vegas_fair),
            "rating_drift": rating_drift(weeks, cfg),
        }
        grid = [(s, e) for s in np.round(np.arange(0, 0.65, 0.05), 2) for e in np.round(np.arange(0, 0.65, 0.05), 2)]
        report[season]["w_tuning"] = tune_w(weeks, cfg, lines, grid)
        cd = committee_data(weeks, cfg)
        report[season]["committee_data_week"] = cd and cd["week"]
        report[season]["_committee"] = cd

    datasets = [report[s].pop("_committee") for s in report if report[s].get("committee_data_week")]
    report["committee_fit"] = fit_committee(datasets)[:10]
    report["committee_current"] = {
        "loss_penalty": cfg["committee"]["loss_penalty"], "champ_bonus": cfg["committee"]["champ_bonus"],
        "footrule": sum(committee_cost(d, cfg["committee"]["loss_penalty"], cfg["committee"]["champ_bonus"])[0] for d in datasets)}
    for season in (args.season, args.holdout):
        report[season]["sim_calibration"] = sim_calibration(
            season, load_weeks(season, cfg), cfg, cfg["backtest"]["calibration_weeks"], args.sims)

    out = repo_path("reports") / "backtest.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    print(f"wrote {out.relative_to(repo_path('.'))}")


if __name__ == "__main__":
    main()
