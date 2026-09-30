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
