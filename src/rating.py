"""The blended rating.

    metrics = z(mean(z(SP+), z(FPI)))          computer ratings side
    poll    = AP points (or CFP ranks once released), put on a z scale
    blend   = z((1 - w) * metrics + w * poll)   w ramps with the week
    rating  = blend * SD(SP+) + mean(SP+)       points scale, so A - B ~ predicted margin

Why re-standardize (the outer z) after averaging: the average of two
correlated z-scores has an SD below 1, so converting it with SP+'s SD would
shrink every predicted margin. Re-standardizing keeps the scale honest.

Two poll methods (config poll.method):
  zscore   - as specified: z-score points over all FBS teams, unranked teams
             get the floor. Heavily skewed: only 25 teams get votes.
  matched  - linearly map points onto the metrics scale using the ranked
             teams (same gaps between teams, comparable spread); an unranked
             team's poll value is min(its metrics value, the floor), so the
             poll can say "not top 25" without dragging bad teams upward.

Usage: python -m src.rating [--season 2026] [--week N] [--method zscore|matched]
"""
import argparse
import csv
import json
import logging
import statistics as st
from pathlib import Path

from src.config import load_config, repo_path

log = logging.getLogger(__name__)


def zscores(values: dict) -> dict:
    vals = list(values.values())
    if len(vals) < 2:
        return {k: 0.0 for k in values}
    m, s = st.mean(vals), st.pstdev(vals)
    return {k: (v - m) / s if s else 0.0 for k, v in values.items()}


def poll_weight(week: int, wcfg: dict) -> float:
    """Linear ramp from `start` at start_week (or earlier) to `end` at end_week (or later)."""
    a, b = wcfg["start_week"], wcfg["end_week"]
    t = min(max((week - a) / (b - a), 0.0), 1.0)
    return wcfg["start"] + t * (wcfg["end"] - wcfg["start"])


def metrics_side(ratings: dict, team_ids: list, rcfg: dict) -> tuple[dict, list]:
    """Average z-scores of the configured metric sources; fall back (e.g. to Elo) if none have data."""
    used = [s for s in rcfg["metric_sources"] if ratings.get(s)]
    if not used:
        used = [s for s in rcfg["metric_fallback"] if ratings.get(s)]
    if not used:
        raise ValueError("No metric ratings available for this week")
    zs = {s: zscores({t: ratings[s][t] for t in team_ids if t in ratings[s]}) for s in used}
    avg = {}
    for t in team_ids:
        parts = [zs[s][t] for s in used if t in zs[s]]
        if parts:                       # a team missing from one source uses the others
            avg[t] = sum(parts) / len(parts)
    return zscores(avg), used


def poll_points(week_data: dict, use_cfp: bool) -> tuple[dict, str | None]:
    """Points per ranked team. CFP rankings have no points, so rank r becomes 26 - r."""
    polls = week_data["polls"]
    if use_cfp and polls.get("cfp"):
        return {str(p["id"]): 26 - p["rank"] for p in polls["cfp"]}, "cfp"
    if polls.get("ap"):
        return {str(p["id"]): p["points"] for p in polls["ap"]}, "ap"
    return {}, None


def floor_points(points: dict) -> float:
    """Lowest vote-getter minus the average gap between adjacent ranked teams."""
    ranked = sorted(points.values(), reverse=True)
    if len(ranked) < 2:
        return 0.0
    gap = (ranked[0] - ranked[-1]) / (len(ranked) - 1)
    return max(ranked[-1] - gap, 0.0)


def poll_side(points: dict, metrics: dict, method: str) -> dict:
    if not points:
        return {}
    floor = floor_points(points)
    if method == "zscore":
        return zscores({t: points.get(t, floor) for t in metrics})
    if method == "matched":
        ranked = [t for t in points if t in metrics]
        pm, ps = st.mean(points[t] for t in ranked), st.pstdev(points[t] for t in ranked)
        mm, ms = st.mean(metrics[t] for t in ranked), st.pstdev(metrics[t] for t in ranked)
        to_scale = lambda p: mm + ms * (p - pm) / ps
        floor_val = to_scale(floor)
        return {t: to_scale(points[t]) if t in points else min(metrics[t], floor_val) for t in metrics}
    raise ValueError(f"unknown poll method {method!r}")


def _record(games: list, tid: str) -> dict:
    r = {"w": 0, "l": 0, "cw": 0, "cl": 0}
    for g in games:
        for side, other in (("home", "away"), ("away", "home")):
            if str(g[f"{side}_id"]) != tid:
                continue
            won = g[f"{side}_points"] > g[f"{other}_points"]
            r["w" if won else "l"] += 1
            if g["conference_game"]:
                r["cw" if won else "cl"] += 1
    return r


def _ranks(values: dict) -> dict:
    return {t: i + 1 for i, t in enumerate(sorted(values, key=values.get, reverse=True))}


def _blend(week_data: dict, metrics: dict, cfg: dict, method: str, use_cfp: bool):
    points, src = poll_points(week_data, use_cfp)
    poll = poll_side(points, metrics, method)
    if not poll:
        return dict(metrics), {}, points, None, 0.0
    w = cfg["cfp"]["weight"] if src == "cfp" else poll_weight(week_data["week"], cfg["weight"])
    blend = zscores({t: (1 - w) * metrics[t] + w * poll[t] for t in metrics})
    return blend, poll, points, src, w


def blend_week(week_data: dict, cfg: dict, method: str | None = None) -> dict:
    """Two ratings from the same formula:
    - rating (predictive): AP poll with the week ramp. Used to predict games.
    - selection_rating: committee rankings at the CFP weight once they exist
      (else identical to rating). Used to model the committee's picks.
    """
    rcfg, pcfg, ccfg = cfg["rating"], cfg["poll"], cfg["cfp"]
    method = method or pcfg["method"]
    team_ids = [str(t["id"]) for t in week_data["teams"]]
    ratings = week_data["ratings"]
    week = week_data["week"]

    metrics, used = metrics_side(ratings, team_ids, rcfg)
    blend, poll, points, poll_src, w = _blend(week_data, metrics, cfg, method, use_cfp=False)
    sel, _, _, sel_src, sel_w = _blend(week_data, metrics, cfg, method, use_cfp=ccfg["replace_ap"])

    sp = ratings.get("sp") or {}
    if sp:
        sp_vals = [sp[t] for t in team_ids if t in sp]
        scale_mean, scale_sd = st.mean(sp_vals), st.pstdev(sp_vals)
    else:
        scale_mean, scale_sd = 0.0, rcfg["fallback_points_sd"]

    names = {str(t["id"]): t for t in week_data["teams"]}
    src_ranks = {s: _ranks({t: v for t, v in (ratings.get(s) or {}).items() if t in metrics})
                 for s in ("sp", "fpi", "elo")}
    # Disagreement is measured against the committee once it ranks teams, else the AP.
    poll_rank = {str(p["id"]): p["rank"] for p in (week_data["polls"].get(sel_src) or [])} if sel_src else {}
    ap_rank = {str(p["id"]): p["rank"] for p in (week_data["polls"].get("ap") or [])}
    cfp_rank = {str(p["id"]): p["rank"] for p in (week_data["polls"].get("cfp") or [])}
    blend_rank, metrics_rank, sel_rank = _ranks(blend), _ranks(metrics), _ranks(sel)

    rows = []
    for t in sorted(blend, key=blend.get, reverse=True):
        rec = _record(week_data["games_completed"], t)
        # How far the humans and the computers disagree. Unranked counts as 26.
        human = poll_rank.get(t, 26)
        rows.append({
            "id": int(t), "team": names[t]["name"], "conference": names[t]["conference"],
            "rank": blend_rank[t], "rating": round(blend[t] * scale_sd + scale_mean, 1),
            "blend_z": round(blend[t], 3), "metrics_z": round(metrics[t], 3),
            "poll_z": round(poll[t], 3) if poll else None,
            "selection_rank": sel_rank[t], "selection_rating": round(sel[t] * scale_sd + scale_mean, 1),
            "metrics_rank": metrics_rank[t],
            "sp_rank": src_ranks["sp"].get(t), "fpi_rank": src_ranks["fpi"].get(t),
            "elo_rank": src_ranks["elo"].get(t),
            "ap_rank": ap_rank.get(t), "ap_points": points.get(t) if poll_src == "ap" else None,
            "cfp_rank": cfp_rank.get(t),
            "wins": rec["w"], "losses": rec["l"], "conf_wins": rec["cw"], "conf_losses": rec["cl"],
            "poll_vs_metrics": (metrics_rank[t] - human) if (t in poll_rank or metrics_rank[t] <= 25) else None,
        })

    return {
        "season": week_data["season"], "week": week,
        "poll_method": method, "poll_source": poll_src, "poll_weight": round(w, 3),
        "selection_poll_source": sel_src, "selection_poll_weight": round(sel_w, 3),
        "metric_sources": used, "points_scale": {"mean": round(scale_mean, 2), "sd": round(scale_sd, 2)},
        "missing": week_data["missing"], "teams": rows,
    }


def write_outputs(result: dict, out_dir: Path) -> tuple[Path, Path]:
    n = result["week"]
    jpath, cpath = out_dir / f"ratings_week_{n}.json", out_dir / f"ratings_week_{n}.csv"
    jpath.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    with open(cpath, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(result["teams"][0]))
        w.writeheader()
        w.writerows(result["teams"])
    return jpath, cpath


def print_table(result: dict, top: int = 25) -> None:
    dash = lambda v: "-" if v is None else v
    print(f"\n{result['season']} week {result['week']} | poll={result['poll_source']} "
          f"w={result['poll_weight']} method={result['poll_method']} metrics={'+'.join(result['metric_sources'])}")
    print(f"{'#':>3} {'Team':<20} {'Rtg':>6} {'Rec':>5} | {'SP+':>4} {'FPI':>4} {'AP':>4} {'CFP':>4}")
    for r in result["teams"][:top]:
        print(f"{r['rank']:>3} {r['team']:<20} {r['rating']:>6} {r['wins']}-{r['losses']:<3} | "
              f"{dash(r['sp_rank']):>4} {dash(r['fpi_rank']):>4} {dash(r['ap_rank']):>4} {dash(r['cfp_rank']):>4}")
    dis = sorted((r for r in result["teams"] if r["poll_vs_metrics"] is not None),
                 key=lambda r: -abs(r["poll_vs_metrics"]))[:8]
    print("\nBiggest poll vs. metrics disagreements (metrics rank - poll rank; unranked = 26):")
    for r in dis:
        who = "poll higher" if r["poll_vs_metrics"] > 0 else "metrics higher"
        human = r["cfp_rank"] if result["selection_poll_source"] == "cfp" else r["ap_rank"]
        print(f"  {r['team']:<20} metrics #{r['metrics_rank']:<3} poll {dash(human)!s:>4}  ({who})")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=cfg["season"])
    ap.add_argument("--week", type=int, help="default: latest processed week")
    ap.add_argument("--method", choices=["zscore", "matched"])
    args = ap.parse_args()

    proc = repo_path(cfg["paths"]["processed"]) / str(args.season)
    week = args.week
    if week is None:
        week = max(int(p.stem.split("_")[1]) for p in proc.glob("week_*.json"))
    data = json.loads((proc / f"week_{week}.json").read_text())
    result = blend_week(data, cfg, args.method)
    for p in write_outputs(result, proc):
        log.info("wrote %s", p.relative_to(repo_path(".")))
    print_table(result)


if __name__ == "__main__":
    main()
