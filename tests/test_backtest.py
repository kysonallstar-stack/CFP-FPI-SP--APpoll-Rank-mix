import pytest

from src.backtest import committee_cost, srs


def g(h, a, hp, ap, neutral=True):
    return {"home_id": h, "away_id": a, "home_points": hp, "away_points": ap, "neutral": neutral}


def test_srs_recovers_ordering_and_caps_blowouts():
    games = [g(1, 2, 21, 14), g(2, 3, 28, 21), g(1, 3, 35, 14)]
    r = srs(games, ["1", "2", "3"], hfa=0.0, lam=0.01, cap=28)
    assert r["1"] > r["2"] > r["3"]
    # a 70-point win counts the same as a 28-point win
    big = srs([g(1, 2, 70, 0)], ["1", "2"], 0.0, 0.01, 28)
    capped = srs([g(1, 2, 28, 0)], ["1", "2"], 0.0, 0.01, 28)
    assert big["1"] == pytest.approx(capped["1"])


def test_srs_home_field_is_removed():
    # Home team wins by exactly HFA at home: teams should rate equal.
    r = srs([g(1, 2, 3, 0, neutral=False), g(2, 1, 3, 0, neutral=False)], ["1", "2"], 3.0, 0.01, 28)
    assert r["1"] == pytest.approx(r["2"], abs=1e-6)


def test_committee_cost_rewards_matching_order():
    data = {"rating": {"a": 10, "b": 9, "c": 8}, "losses": {"a": 2, "b": 0, "c": 0},
            "champs": set(), "cfp": {"b": 1, "c": 2, "a": 3}}
    no_penalty, _ = committee_cost(data, 0.0, 0.0)
    penalty, _ = committee_cost(data, 5.0, 0.0)
    assert penalty < no_penalty and penalty == 0
