import numpy as np
import pytest

from src.sim import _order, estimate_fcs_rating, pick_field, win_prob

PCFG = {
    "field_size": 12, "byes": 4,
    "power4": ["SEC", "Big Ten", "Big 12", "ACC"],
    "group6": ["Sun Belt", "Mountain West"],
    "g6_bid": "highest_ranked",
}


def test_win_prob_is_a_normal_cdf():
    assert win_prob(0, 16) == pytest.approx(0.5)
    assert win_prob(16, 16) == pytest.approx(0.8413, abs=1e-4)      # one SD
    assert win_prob(7, 16) + win_prob(-7, 16) == pytest.approx(1)


def test_order_uses_head_to_head_then_rating():
    pct = np.array([0.75, 0.75, 0.5, 0.75])
    rating = np.array([10.0, 20.0, 30.0, 5.0])
    # Two-way tie between 0 and 1 (team 3 also tied): 0 beat 1, 3 played no one.
    results = {(0, 1): 0}
    def h2h(a, b):
        key = tuple(sorted((a, b)))
        if key not in results:
            return None
        return 1 if results[key] == a else 0
    order = _order([0, 1, 2, 3], pct, rating, h2h)
    # 0 is 1-0 among tied teams, 1 is 0-1, 3 has no games (0.5): 0, 3, 1; then 2 on pct.
    assert order == [0, 3, 1, 2]


def _league(n=20):
    """Teams 0..n-1, better teams first. Conferences cycle through P4 + G6."""
    confs = ["SEC", "Big Ten", "Big 12", "ACC"] * 3 + ["Sun Belt", "Mountain West"] * 4
    return np.arange(n, 0, -1).astype(float), confs[:n]


def test_field_power4_champs_are_in_even_when_ranked_low():
    score, conf = _league()
    champ = np.zeros(20, bool)
    champ[[0, 1, 2, 11]] = True          # team 11 (ACC) wins its league but ranks 12th
    field = pick_field(score, champ, conf, None, PCFG)
    assert len(field) == 12 and 11 in field
    assert 12 in field                    # best G6 team (Sun Belt, rank 13) gets the G6 bid
    assert field[-1] == 12                # auto-bid ranked outside the top 12 is seeded last


def test_g6_bid_champion_rule_option():
    score, conf = _league()
    champ = np.zeros(20, bool)
    champ[[0, 1, 2, 3, 15]] = True        # 15 = Mountain West champion, below Sun Belt's 12
    f_ranked = pick_field(score, champ, conf, None, PCFG)
    f_champ = pick_field(score, champ, conf, None, {**PCFG, "g6_bid": "champion"})
    assert 12 in f_ranked and 15 not in f_ranked
    assert 15 in f_champ


def test_notre_dame_in_if_top_12_only():
    score, conf = _league()
    conf = list(conf)
    champ = np.zeros(20, bool)
    champ[[0, 1, 2, 3]] = True
    conf[9] = "FBS Independents"          # "Notre Dame" ranked 10th
    assert 9 in pick_field(score, champ, conf, 9, PCFG)
    conf[14] = "FBS Independents"         # ranked 15th: no guarantee
    assert 14 not in pick_field(score, champ, conf, 14, PCFG)


def test_byes_go_to_top_four_even_if_not_champions():
    score, conf = _league()
    champ = np.zeros(20, bool)
    champ[[4, 5, 6, 7]] = True            # champions are ranked 5-8
    field = pick_field(score, champ, conf, None, PCFG)
    assert field[:4] == [0, 1, 2, 3]


def test_fcs_rating_estimate():
    games = [{"home_fbs": True, "away_fbs": False, "home_id": 1, "away_id": 999, "neutral": False,
              "home_points": 40, "away_points": 10}] * 12
    # implied FCS rating = 5 + 2.7 - 30 = -22.3
    assert estimate_fcs_rating(games, {"1": 5.0}, 2.7, -20.0) == pytest.approx(-22.3)
    assert estimate_fcs_rating(games[:3], {"1": 5.0}, 2.7, -20.0) == -20.0   # too few games


def test_rating_uncertainty_lookup_and_clamping():
    from src.sim import rating_uncertainty
    u = {"by_week": {1: 9.8, 4: 6.7, 15: 0.3}}
    assert rating_uncertainty(4, u) == 6.7
    assert rating_uncertainty(20, u) == 0.3     # past the table: last value
    assert rating_uncertainty(0, u) == 9.8      # before the table: first value
    assert rating_uncertainty(4, None) == 0.0


def test_2024_25_rule_takes_five_highest_ranked_champions():
    score, conf = _league()
    champ = np.zeros(20, bool)
    champ[[0, 1, 12, 13, 14, 19]] = True       # six champions; 19 is the lowest-ranked
    rules = {**PCFG, "g6_bid": "top5_champions", "notre_dame_rule": False}
    field = pick_field(score, champ, conf, None, rules)
    assert {0, 1, 12, 13, 14} <= set(field) and 19 not in field


def test_common_opponents_break_a_tie_head_to_head_cant():
    # 0 and 1 tied and didn't play each other; both played 2 and 3.
    # 0 went 2-0 vs them, 1 went 1-1, so 0 wins the tie despite a lower rating.
    pct = np.array([0.75, 0.75, 0.25, 0.25])
    rating = np.array([5.0, 20.0, 0.0, 0.0])
    results = {(0, 2): 0, (0, 3): 0, (1, 2): 1, (1, 3): 2}   # pair -> winner
    def h2h(a, b):
        key = tuple(sorted((a, b)))
        if key not in results:
            return None
        return 1 if results[key] == a else 0
    assert _order([0, 1, 2, 3], pct, rating, h2h)[:2] == [0, 1]


def test_selection_day_reproduces_the_real_2025_bracket():
    """With the 2025 rules, the final committee ranking + actual champions give the actual field."""
    import json
    from pathlib import Path
    from src.config import load_config
    from src.sim import final_field
    path = Path(__file__).parent.parent / "data/processed/2025/week_16.json"
    if not path.exists():
        pytest.skip("2025 data not cached")
    cfg = load_config()
    cfg["playoff"] = {**cfg["playoff"], **cfg["backtest"]["playoff_rules"][2025]}
    f = final_field(json.loads(path.read_text()), cfg)
    assert [x["team"] for x in f["seeds"] if x["bye"]] == ["Indiana", "Ohio State", "Georgia", "Texas Tech"]
    # The actual 2025 first round (higher seed hosted)
    assert [(a, b) for a, b, *_ in f["first_round"]] == [
        ("Oregon", "James Madison"), ("Ole Miss", "Tulane"), ("Texas A&M", "Miami"), ("Oklahoma", "Alabama")]
    before = json.loads((path.parent / "week_14.json").read_text())
    assert final_field(before, cfg) is None                      # title games not played yet


def test_flex_week_placeholders_only_for_open_teams():
    from src.config import load_config
    from src.sim import FLEX, Season
    cfg = load_config()
    cfg["sim"] = {**cfg["sim"], "flex_weeks": {2026: {"X": 3}}}
    teams = [{"id": i, "name": f"T{i}", "conference": "X"} for i in (1, 2, 3)]
    ratings = {"teams": [{"id": t["id"], "rating": 0.0, "selection_rating": 0.0} for t in teams]}
    g = lambda gid, h, a: {"id": gid, "week": 3, "home_id": h, "away_id": a, "home_fbs": True, "away_fbs": True,
                           "neutral": False, "conference_game": False, "start": None, "home_name": "", "away_name": ""}
    week_data = {"season": 2026, "week": 1, "teams": teams, "games_completed": [], "games_remaining": [g(9, 1, 2)]}
    season = Season(week_data, ratings, cfg)
    placeholders = [x for x in season.games if x.get("placeholder")]
    assert [x["home_id"] for x in placeholders] == [3]           # teams 1 and 2 already play in week 3
    assert season.g_away[-1] == FLEX
