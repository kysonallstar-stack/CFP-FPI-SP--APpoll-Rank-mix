"""Game predictions and Monte Carlo playoff simulation.

Win probability
    margin = rating_home - rating_away + HFA (0 at a neutral site)
    P(home wins) = Phi(margin / sigma)
Ratings are the *predictive* blend (points scale). FCS opponents get one shared
rating estimated from this season's completed FBS-vs-FCS games.

One simulated season
    1. every remaining regular-season game (ratings held fixed)
    2. conference title games from simulated standings (rules below)
    3. the 12-team field and seeds from the verified 2026-27 CFP format

Conference title game participants (approximation of each league's rules)
    Top two by conference win percentage (Sun Belt: East winner vs. West
    winner). Ties are broken by (a) win pct in games among the tied teams,
    then (b) predictive rating. Real tiebreakers go further (common opponents,
    opponents' records, the ACC's new "body of work" step, the American's
    computer composite); rating stands in for all of those.

Committee approximation (clearly NOT the real committee)
    score = selection rating - loss_penalty * losses + champ_bonus (if a
    conference champion) + small per-season noise. The field: four Power 4
    champions, the best-scoring Group of 6 team (or champion; see config),
    Notre Dame if it scores in the top 12, then at-large teams by score.
    Top four scores get byes. Auto-bids scoring outside the top 12 are
    seeded last.

Usage: python -m src.sim [--season 2026] [--week N] [--sims 10000] [--seed 42]
"""
import argparse
import json
import logging
import math
from collections import defaultdict

import numpy as np

from src.config import load_config, repo_path
from src.rating import blend_week

log = logging.getLogger(__name__)

FCS = -1  # team index used for every non-FBS opponent


def win_prob(margin: float, sigma: float) -> float:
    return 0.5 * (1 + math.erf(margin / (sigma * math.sqrt(2))))


def estimate_fcs_rating(games: list, rating: dict, hfa: float, default: float) -> float:
    """Average implied rating of FCS opponents: fbs_rating + (home edge) - FBS team's margin."""
    implied = []
    for g in games:
        if g["home_fbs"] == g["away_fbs"]:
            continue
        fbs = "home" if g["home_fbs"] else "away"
        other = "away" if fbs == "home" else "home"
        fid = str(g[f"{fbs}_id"])
        if fid not in rating:
            continue
        edge = 0.0 if g["neutral"] else (hfa if fbs == "home" else -hfa)
        implied.append(rating[fid] + edge - (g[f"{fbs}_points"] - g[f"{other}_points"]))
    return float(np.mean(implied)) if len(implied) >= 10 else default


class Season:
    """Static inputs for simulating one season from a given week."""

    def __init__(self, week_data: dict, ratings: dict, cfg: dict):
        self.cfg = cfg
        self.week = week_data["week"]
        self.sel_poll_weight = ratings.get("selection_poll_weight", 0.0)
        scfg = cfg["sim"]
        self.hfa, self.sigma = scfg["hfa"], scfg["sigma"]
        self.k = scfg.get("margin_scale", 1.0)   # shrinks rating differences, not HFA
        self.teams = week_data["teams"]
        self.ids = [str(t["id"]) for t in self.teams]
        self.idx = {t: i for i, t in enumerate(self.ids)}
        self.n = len(self.ids)
        self.conf = [t["conference"] for t in self.teams]
        self.division = [t.get("division") for t in self.teams]
        self.name = [t["name"] for t in self.teams]

        rows = {str(r["id"]): r for r in ratings["teams"]}
        self.rating = np.array([rows[t]["rating"] for t in self.ids])
        self.sel_rating = np.array([rows[t]["selection_rating"] for t in self.ids])
        pred = {t: rows[t]["rating"] for t in self.ids}
        self.fcs_rating = estimate_fcs_rating(week_data["games_completed"], pred, self.hfa, scfg["fcs_rating_default"])

        # Completed results -> baseline records.
        self.base = {k: np.zeros(self.n) for k in ("w", "l", "cw", "cl")}
        self.h2h_fixed = {}  # (a, b) sorted index pair -> winner index, conference games only
        for g in week_data["games_completed"]:
            h, a = self._i(g["home_id"]), self._i(g["away_id"])
            home_won = g["home_points"] > g["away_points"]
            self._tally(self.base, h, a, home_won, g["conference_game"])
            if g["conference_game"] and h != FCS and a != FCS:
                self.h2h_fixed[tuple(sorted((h, a)))] = h if home_won else a

        # Remaining games -> arrays for vectorized simulation.
        rem = [g for g in week_data["games_remaining"] if g["home_fbs"] or g["away_fbs"]]
        self.games = rem
        self.g_home = np.array([self._i(g["home_id"]) for g in rem], dtype=int)
        self.g_away = np.array([self._i(g["away_id"]) for g in rem], dtype=int)
        self.g_conf = np.array([g["conference_game"] for g in rem], dtype=bool)
        self.g_edge = np.array([0.0 if g["neutral"] else self.hfa for g in rem])
        self.g_week = np.array([g["week"] for g in rem], dtype=int)
        self.pair_col = {tuple(sorted((h, a))): c for c, (h, a, conf)
                         in enumerate(zip(self.g_home, self.g_away, self.g_conf)) if conf and h != FCS and a != FCS}

    def _i(self, team_id) -> int:
        return self.idx.get(str(team_id), FCS)

    @staticmethod
    def _tally(rec, h, a, home_won, conf):
        for team, won in ((h, home_won), (a, not home_won)):
            if team == FCS:
                continue
            rec["w" if won else "l"][team] += 1
            if conf:
                rec["cw" if won else "cl"][team] += 1

    def r(self, i) -> float:
        return self.fcs_rating if i == FCS else self.rating[i]

    def game_probs(self) -> np.ndarray:
        rh = np.where(self.g_home == FCS, self.fcs_rating, self.rating[self.g_home])
        ra = np.where(self.g_away == FCS, self.fcs_rating, self.rating[self.g_away])
        m = self.k * (rh - ra) + self.g_edge
        return np.array([win_prob(x, self.sigma) for x in m])


def _order(members: list[int], pct: np.ndarray, rating: np.ndarray, h2h) -> list[int]:
    """Order teams by conference win pct; ties by record among the tied teams, then rating."""
    by_pct = defaultdict(list)
    for t in members:
        by_pct[round(pct[t], 6)].append(t)
    out = []
    for p in sorted(by_pct, reverse=True):
        group = by_pct[p]
        if len(group) > 1:
            def mini(t):
                w = l = 0
                for o in group:
                    if o != t:
                        res = h2h(t, o)
                        if res is not None:
                            w, l = w + res, l + (1 - res)
                return w / (w + l) if w + l else 0.5
            group = sorted(group, key=lambda t: (-mini(t), -rating[t]))
        out += group
    return out


def pick_field(score: np.ndarray, champ: np.ndarray, conf: list, nd: int | None, pcfg: dict) -> list[int]:
    """The 12-team field in seed order, given each team's committee score for one sim.

    g6_bid: highest_ranked (2026+), champion, or top5_champions (the 2024-25 rule:
    the five highest-ranked conference champions from any conference).
    """
    order = [int(t) for t in np.argsort(-score, kind="stable")]
    pos = {t: k for k, t in enumerate(order)}   # 0-based committee rank
    p4, g6 = set(pcfg["power4"]), set(pcfg["group6"])

    if pcfg["g6_bid"] == "top5_champions":
        auto = [t for t in order if champ[t]][:5]
    else:
        auto = [t for t in order if champ[t] and conf[t] in p4]
        g6_pool = [t for t in order if conf[t] in g6 and (pcfg["g6_bid"] == "highest_ranked" or champ[t])]
        if g6_pool:
            auto.append(g6_pool[0])
    if nd is not None and pcfg.get("notre_dame_rule", True) and pos[nd] < 12:
        auto.append(nd)
    field = list(dict.fromkeys(auto))
    for t in order:
        if len(field) >= pcfg["field_size"]:
            break
        if t not in field:
            field.append(t)
    # Seeding: by committee rank, except auto-bids ranked outside the top 12 go last.
    inside = sorted((t for t in field if pos[t] < 12 or t not in auto), key=pos.get)
    outside = sorted((t for t in field if pos[t] >= 12 and t in auto), key=pos.get)
    return inside + outside


def rating_uncertainty(week: int, ucfg: dict | None) -> float:
    """SD (points) of how far a team's rating will still move by season's end."""
    if not ucfg:
        return 0.0
    table = {int(k): v for k, v in ucfg["by_week"].items()}
    return float(table.get(week, table[max(table)] if week > max(table) else table[min(table)]))


def simulate(season: Season, n_sims: int, seed: int | None) -> dict:
    """Rating uncertainty: each sim draws a hidden "true rating" offset per team,
    N(0, tau) where tau is how much ratings still move from this week to the end
    of the season (measured in the backtest). The offset applies to every game
    that team plays in that sim, so a team that is secretly better wins more of
    ALL its games -- which is what spreads out season-long odds. To keep each
    single game's odds unchanged, the per-game noise shrinks to
    sqrt(sigma^2 - 2 * (margin_scale * tau)^2).
    """
    cfg, ccfg, pcfg = season.cfg, season.cfg["committee"], season.cfg["playoff"]
    rng = np.random.default_rng(seed)
    S, T, G = n_sims, season.n, len(season.games)

    tau = rating_uncertainty(season.week, cfg["sim"].get("rating_uncertainty"))
    sigma_game = float(np.sqrt(max(season.sigma ** 2 - 2 * (season.k * tau) ** 2, (0.5 * season.sigma) ** 2)))
    offset = rng.normal(0, tau, (S, T)) if tau else np.zeros((S, T))
    off_fcs = np.zeros((S, 1))
    off_all = np.hstack([offset, off_fcs])          # index -1 (FCS) -> no offset

    diff = np.where(season.g_home == FCS, season.fcs_rating, season.rating[season.g_home]) \
        - np.where(season.g_away == FCS, season.fcs_rating, season.rating[season.g_away])
    margin = (season.k * (diff + off_all[:, season.g_home] - off_all[:, season.g_away])
              + season.g_edge + rng.normal(0, sigma_game, (S, G)))
    home_win = margin > 0                                 # [sims, games]
    probs = season.game_probs()                            # marginal odds, for display

    rec = {k: np.tile(v, (S, 1)) for k, v in season.base.items()}   # [sims, teams]
    for c in range(G):
        h, a, hw = season.g_home[c], season.g_away[c], home_win[:, c]
        conf = season.g_conf[c]
        if h != FCS:
            rec["w"][:, h] += hw; rec["l"][:, h] += ~hw
            if conf: rec["cw"][:, h] += hw; rec["cl"][:, h] += ~hw
        if a != FCS:
            rec["w"][:, a] += ~hw; rec["l"][:, a] += hw
            if conf: rec["cw"][:, a] += ~hw; rec["cl"][:, a] += hw
    reg_wins = rec["w"].copy()
    future_losses = rec["l"] - season.base["l"]
    games_played = rec["cw"] + rec["cl"]
    pct = np.divide(rec["cw"], games_played, out=np.full((S, T), 0.0), where=games_played > 0)

    confs = defaultdict(list)
    for i, c in enumerate(season.conf):
        if c not in pcfg["no_title_game"]:
            confs[c].append(i)

    champ = np.zeros((S, T), dtype=bool)
    in_title = np.zeros((S, T), dtype=bool)
    nd = season.idx.get(str(pcfg["notre_dame_id"]))
    sel_noise = rng.normal(0, ccfg["noise_sd"], (S, T)) if ccfg["noise_sd"] else np.zeros((S, T))
    # Losses already played are partly priced into the selection rating through its
    # poll share (weight w_sel), so they get (1 - w_sel) of the penalty; future
    # losses get the full penalty.
    current_loss_factor = 1.0 - season.sel_poll_weight

    made = np.zeros((S, T), dtype=bool)
    bye = np.zeros((S, T), dtype=bool)
    seeds = np.zeros((S, T), dtype=int)

    for s in range(S):
        true_rating = season.rating + offset[s]

        def h2h(a, b, s=s):
            key = tuple(sorted((a, b)))
            if key in season.h2h_fixed:
                return 1 if season.h2h_fixed[key] == a else 0
            col = season.pair_col.get(key)
            if col is None:
                return None
            winner = season.g_home[col] if home_win[s, col] else season.g_away[col]
            return 1 if winner == a else 0

        for c, members in confs.items():
            if c in pcfg["divisions"]:
                winners = [_order([t for t in members if season.division[t] == d], pct[s], season.rating, h2h)
                           for d in pcfg["divisions"][c]]
                # Higher seed = division winner with the better record (it hosts, where applicable).
                pair = _order([w[0] for w in winners if w], pct[s], season.rating, h2h)
            else:
                pair = _order(members, pct[s], season.rating, h2h)[:2]
            if len(pair) < 2:
                continue
            top, other = pair                       # top = higher seed
            edge = season.hfa if c in pcfg["title_game_home_of_higher_seed"] else 0.0
            m = season.k * (true_rating[top] - true_rating[other]) + edge
            won = m + rng.normal(0, sigma_game) > 0
            w_, l_ = (top, other) if won else (other, top)
            champ[s, w_] = True
            in_title[s, [top, other]] = True
            rec["w"][s, w_] += 1; rec["l"][s, l_] += 1
            future_losses[s, l_] += 1

        score = (season.sel_rating + offset[s] + sel_noise[s]
                 - ccfg["loss_penalty"] * (future_losses[s] + current_loss_factor * season.base["l"])
                 + ccfg["champ_bonus"] * champ[s])
        for k, t in enumerate(pick_field(score, champ[s], season.conf, nd, pcfg)):
            made[s, t] = True
            seeds[s, t] = k + 1
            bye[s, t] = k < pcfg["byes"]

    return {"home_win": home_win, "probs": probs, "made": made, "bye": bye, "champ": champ,
            "in_title": in_title, "wins": rec["w"], "reg_wins": reg_wins, "seeds": seeds}


def leverage(season: Season, res: dict, week: int, min_side: int, z_min: float, top_n: int | None = 10) -> list[dict]:
    """For each game in `week`: P(playoff | home wins) - P(playoff | home loses), per team.

    The sims split into "home won" and "home lost" groups; each team's swing is the
    difference in its playoff rate between them. With a lopsided game the smaller
    group is only a few hundred sims, so every team's swing carries sampling noise
    that adds up across 138 teams. A swing only counts toward a game's total if it
    exceeds z_min standard errors.
    """
    made = res["made"].astype(float)
    out = []
    for c in np.where(season.g_week == week)[0]:
        hw = res["home_win"][:, c]
        n1, n0 = hw.sum(), (~hw).sum()
        if n1 < min_side or n0 < min_side:
            continue
        p1, p0 = made[hw].mean(0), made[~hw].mean(0)
        delta = p1 - p0
        se = np.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        real = np.abs(delta) > z_min * np.maximum(se, 1e-9)
        delta = np.where(real, delta, 0.0)
        movers = np.argsort(-np.abs(delta))[:5]
        g = season.games[c]
        out.append({
            "game_id": g["id"], "week": int(week), "home": g["home_name"], "away": g["away_name"],
            "neutral": g["neutral"], "home_win_prob": round(float(res["probs"][c]), 3),
            "total_swing": round(float(np.abs(delta).sum()), 3),
            "movers": [{"team": season.name[t], "if_home_wins": round(float(made[hw, t].mean()), 3),
                        "if_home_loses": round(float(made[~hw, t].mean()), 3),
                        "swing": round(float(delta[t]), 3)} for t in movers if abs(delta[t]) >= 0.005],
        })
    out.sort(key=lambda x: -x["total_swing"])
    return out if top_n is None else out[:top_n]


def run(week_data: dict, cfg: dict, n_sims: int, seed: int | None) -> dict:
    ratings = blend_week(week_data, cfg)
    season = Season(week_data, ratings, cfg)
    res = simulate(season, n_sims, seed)
    next_week = int(season.g_week.min()) if len(season.g_week) else None

    teams = []
    by_id = {str(r["id"]): r for r in ratings["teams"]}
    for i, t in enumerate(season.ids):
        r = by_id[t]
        teams.append({
            "id": int(t), "team": season.name[i], "conference": season.conf[i], "rank": r["rank"],
            "rating": r["rating"], "wins": r["wins"], "losses": r["losses"],
            "p_title_game": round(float(res["in_title"][:, i].mean()), 4),
            "p_win_conference": round(float(res["champ"][:, i].mean()), 4),
            "p_playoff": round(float(res["made"][:, i].mean()), 4),
            "p_bye": round(float(res["bye"][:, i].mean()), 4),
            "expected_wins": round(float(res["wins"][:, i].mean()), 2),
            "expected_regular_season_wins": round(float(res["reg_wins"][:, i].mean()), 2),
        })
    teams.sort(key=lambda x: (-x["p_playoff"], x["rank"]))

    upcoming = []
    for c in np.where(season.g_week == next_week)[0] if next_week else []:
        g = season.games[c]
        m = season.k * (season.r(season.g_home[c]) - season.r(season.g_away[c])) + season.g_edge[c]
        upcoming.append({"game_id": g["id"], "home": g["home_name"], "away": g["away_name"],
                         "home_id": g["home_id"], "away_id": g["away_id"], "fbs": g["home_fbs"] and g["away_fbs"],
                         "neutral": g["neutral"], "start": g["start"],
                         "home_win_prob": round(float(res["probs"][c]), 3), "predicted_margin": round(float(m), 1)})

    lev = leverage(season, res, next_week, cfg["sim"]["leverage_min_side_sims"],
                   cfg["sim"]["leverage_z_min"], top_n=None) if next_week else []
    return {
        "season": week_data["season"], "as_of_week": week_data["week"], "next_week": next_week,
        "sims": n_sims, "seed": seed,
        "settings": {"hfa": season.hfa, "sigma": season.sigma, "fcs_rating": round(season.fcs_rating, 1),
                     "g6_bid": cfg["playoff"]["g6_bid"], "committee": cfg["committee"]},
        "teams": teams, "upcoming_games": upcoming,
        "leverage": lev[:cfg["sim"]["top_n_games"]],
        "top_matchups": top_matchups(upcoming, by_id, teams, lev, cfg["sim"]["top_n_games"]),
    }


def top_matchups(upcoming: list, by_id: dict, teams: list, lev: list, n: int) -> list[dict]:
    """Next week's best games: FBS vs FBS, ranked by the WEAKER team's rating, so
    both teams have to be good (unlike leverage, which often surfaces Group of 6
    games that decide the one G6 playoff spot)."""
    odds = {t["id"]: t["p_playoff"] for t in teams}
    swing = {g["game_id"]: g["total_swing"] for g in lev}
    out = []
    for g in upcoming:
        if not g["fbs"]:
            continue
        h, a = by_id[str(g["home_id"])], by_id[str(g["away_id"])]
        out.append({**{k: g[k] for k in ("game_id", "home", "away", "neutral", "start", "home_win_prob", "predicted_margin")},
                    "home_rank": h["rank"], "away_rank": a["rank"],
                    "home_p_playoff": odds[h["id"]], "away_p_playoff": odds[a["id"]],
                    "playoff_swing": swing.get(g["game_id"], 0.0),
                    "quality": round(min(h["rating"], a["rating"]), 1)})
    return sorted(out, key=lambda x: -x["quality"])[:n]


def print_summary(out: dict, top: int = 20) -> None:
    s = out["settings"]
    print(f"\n{out['season']} after week {out['as_of_week']} | {out['sims']} sims, seed={out['seed']} | "
          f"HFA={s['hfa']} sigma={s['sigma']} FCS rating={s['fcs_rating']}")
    print(f"{'Team':<20} {'Rtg':>5} {'Rec':>5} {'xW':>5} {'Conf%':>6} {'CFP%':>6} {'Bye%':>6}")
    for t in out["teams"][:top]:
        print(f"{t['team']:<20} {t['rating']:>5} {t['wins']}-{t['losses']:<3} {t['expected_wins']:>5} "
              f"{100*t['p_win_conference']:>5.1f} {100*t['p_playoff']:>6.1f} {100*t['p_bye']:>6.1f}")
    print(f"\nTop matchups, week {out['next_week']} (both teams good: ranked by the weaker team's rating):")
    for g in out["top_matchups"]:
        print(f"  #{g['away_rank']} {g['away']} at #{g['home_rank']} {g['home']}: home {100*g['home_win_prob']:.0f}%, "
              f"margin {g['predicted_margin']:+.1f}, playoff swing {g['playoff_swing']:.2f}")
    print(f"\nTop leverage games, week {out['next_week']} (sum of playoff-odds swings across all teams):")
    for g in out["leverage"]:
        def fmt(m):
            win, loss = (m["if_home_wins"], m["if_home_loses"]) if m["team"] == g["home"] else (m["if_home_loses"], m["if_home_wins"])
            if m["team"] in (g["home"], g["away"]):
                return f"{m['team']} {100*win:.0f}% if win / {100*loss:.0f}% if loss"
            return f"{m['team']} {100*m['if_home_wins']:.0f}% if {g['home']} wins / {100*m['if_home_loses']:.0f}% if not"
        movers = "; ".join(fmt(m) for m in g["movers"][:3])
        print(f"  {g['away']} at {g['home']} (home {100*g['home_win_prob']:.0f}%): swing {g['total_swing']:.2f} | {movers}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=cfg["season"])
    ap.add_argument("--week", type=int, help="default: latest processed week")
    ap.add_argument("--sims", type=int, default=cfg["sim"]["n_sims"])
    ap.add_argument("--seed", type=int, default=cfg["sim"]["seed"])
    args = ap.parse_args()

    proc = repo_path(cfg["paths"]["processed"]) / str(args.season)
    week = args.week
    if week is None:
        week = max(int(p.stem.split("_")[1]) for p in proc.glob("week_[0-9]*.json"))
    data = json.loads((proc / f"week_{week}.json").read_text())
    out = run(data, cfg, args.sims, args.seed)
    path = proc / f"sim_week_{week}.json"
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    log.info("wrote %s", path.relative_to(repo_path(".")))
    print_summary(out)


if __name__ == "__main__":
    main()
