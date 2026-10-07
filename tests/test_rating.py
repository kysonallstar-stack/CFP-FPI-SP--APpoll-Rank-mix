import statistics as st

import pytest

from src.rating import blend_week, floor_points, poll_side, poll_weight, zscores

WEIGHT = {"start_week": 5, "start": 0.25, "end_week": 12, "end": 0.40}
CFG = {
    "rating": {"metric_sources": ["sp", "fpi"], "metric_fallback": ["elo"], "fallback_points_sd": 13.3},
    "poll": {"method": "zscore"},
    "weight": WEIGHT,
    "cfp": {"replace_ap": True, "weight": 0.5},
}


def week(n=4, ap=None, cfp=None, sp=None, fpi=None, elo=None):
    teams = [{"id": i, "name": f"T{i}", "conference": "X"} for i in range(1, 7)]
    sp = sp if sp is not None else {"1": 30, "2": 20, "3": 10, "4": 0, "5": -10, "6": -30}
    fpi = fpi if fpi is not None else {"1": 25, "2": 22, "3": 8, "4": 1, "5": -12, "6": -25}
    return {"season": 2026, "week": n, "teams": teams, "missing": [], "games_completed": [],
            "ratings": {"sp": sp, "fpi": fpi, "elo": elo or {}},
            "polls": {"ap": ap, "cfp": cfp}}


@pytest.mark.parametrize("wk,expected", [(1, 0.25), (5, 0.25), (12, 0.40), (15, 0.40), (8.5, 0.325)])
def test_poll_weight_ramp(wk, expected):
    assert poll_weight(wk, WEIGHT) == pytest.approx(expected)


def test_zscores_have_mean_zero_sd_one():
    z = zscores({"a": 1, "b": 5, "c": 9, "d": 3})
    assert st.mean(z.values()) == pytest.approx(0)
    assert st.pstdev(z.values()) == pytest.approx(1)


def test_floor_is_one_average_gap_below_last_ranked():
    assert floor_points({"a": 100, "b": 60, "c": 20}) == pytest.approx(0)      # gap 40 -> max(20-40, 0)
    assert floor_points({"a": 300, "b": 200, "c": 150}) == pytest.approx(75)   # gap 75


def test_matched_poll_keeps_gaps_and_never_lifts_bad_unranked_teams():
    metrics = {"1": 2.0, "2": 1.0, "3": 0.5, "4": -1.0, "5": -2.0}
    points = {"1": 300, "2": 100, "3": 200}      # poll likes 3 more than 2
    p = poll_side(points, metrics, "matched")
    # linear in points: equal point gaps -> equal poll gaps
    assert p["1"] - p["3"] == pytest.approx(p["3"] - p["2"])
    # unranked teams: poll value never above their own metrics value
    assert p["4"] <= metrics["4"] and p["5"] == metrics["5"]


def test_blend_rating_is_on_points_scale():
    out = blend_week(week(ap=[{"id": 1, "rank": 1, "points": 100}, {"id": 2, "rank": 2, "points": 50}]), CFG)
    ratings = [t["rating"] for t in out["teams"]]
    sp_vals = [30, 20, 10, 0, -10, -30]
    assert st.pstdev(ratings) == pytest.approx(st.pstdev(sp_vals), abs=0.1)
    assert out["poll_source"] == "ap" and out["poll_weight"] == 0.25


def test_cfp_drives_selection_rating_but_not_predictive_rating():
    ap = [{"id": 1, "rank": 1, "points": 100}, {"id": 2, "rank": 2, "points": 90}]
    cfp = [{"id": 2, "rank": 1, "points": 0}, {"id": 1, "rank": 2, "points": 0}]
    out = blend_week(week(n=10, ap=ap, cfp=cfp), CFG)
    assert out["poll_source"] == "ap" and out["poll_weight"] == pytest.approx(0.25 + 5 / 7 * 0.15, abs=1e-3)
    assert out["selection_poll_source"] == "cfp" and out["selection_poll_weight"] == 0.5
    row = {t["team"]: t for t in out["teams"]}
    assert row["T2"]["cfp_rank"] == 1 and row["T1"]["ap_rank"] == 1
    # The committee prefers T2, so T2 gains relative to T1 in the selection rating.
    gap_pred = row["T1"]["rating"] - row["T2"]["rating"]
    gap_sel = row["T1"]["selection_rating"] - row["T2"]["selection_rating"]
    assert gap_sel < gap_pred
    # Disagreements are measured against the committee once it exists.
    assert row["T2"]["poll_vs_metrics"] == row["T2"]["metrics_rank"] - 1


def test_before_cfp_selection_equals_predictive():
    out = blend_week(week(ap=[{"id": 1, "rank": 1, "points": 100}, {"id": 3, "rank": 2, "points": 50}]), CFG)
    assert all(t["rating"] == t["selection_rating"] for t in out["teams"])


def test_falls_back_to_elo_when_sp_and_fpi_missing():
    out = blend_week(week(sp={}, fpi={}, elo={str(i): 1600 - 50 * i for i in range(1, 7)}), CFG)
    assert out["metric_sources"] == ["elo"]
    assert out["points_scale"]["sd"] == 13.3
    assert out["teams"][0]["team"] == "T1"


def test_no_poll_means_metrics_only():
    out = blend_week(week(), CFG)
    assert out["poll_weight"] == 0.0 and out["poll_source"] is None


def test_metric_weights_and_always_fresh_sources():
    """Elo at half weight pulls the computer side toward Elo's order, but less than at full weight."""
    from src.rating import metrics_side
    ids = ["1", "2", "3"]
    ratings = {"sp": {"1": 10, "2": 0, "3": -10}, "elo": {"1": 1400, "2": 1500, "3": 1600}, "fpi": {}}
    base = {"metric_sources": ["sp", "fpi", "elo"], "metric_fallback": []}
    sp_only, _ = metrics_side({**ratings, "elo": {}}, ids, base)
    half, used = metrics_side(ratings, ids, {**base, "metric_weights": {"elo": 0.5}})
    assert used == ["sp", "elo"]                      # FPI has no data: skipped
    assert sp_only["1"] > half["1"] > 0               # team 1 still on top, by less
    equal, _ = metrics_side(ratings, ids, base)
    assert abs(equal["1"]) < 1e-9                     # equal weights: opposite orders cancel out


def test_srs_is_added_only_from_the_configured_week():
    from src.rating import with_srs
    g = {"home_id": 1, "away_id": 2, "home_points": 30, "away_points": 10, "neutral": True}
    cfg = {"rating": {"srs_lambda": 1.5, "srs_margin_cap": 28, "srs_min_week": 4}, "sim": {"hfa": 2.7}}
    wd = {"week": 3, "teams": [{"id": 1}, {"id": 2}], "ratings": {}, "games_completed": [g]}
    assert "srs" not in with_srs(wd, cfg)["ratings"]
    out = with_srs({**wd, "week": 4}, cfg)
    assert out["ratings"]["srs"]["1"] > out["ratings"]["srs"]["2"]
