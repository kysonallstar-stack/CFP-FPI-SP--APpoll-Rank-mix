import copy

from src.notify import build_message, reasons


def data(week=5, odds=(0.9, 0.5), ranks=(1, 2), ap=(1, 2), cfp=(None, None), stale=None, final=None):
    teams = [{"id": i + 1, "name": n, "rank": ranks[i], "p_playoff": odds[i], "cfp": cfp[i], "ap": ap[i], "ap_pts": 100 - i}
             for i, n in enumerate(["Alpha", "Beta"])]
    return {"season": 2026, "week": week, "stale": stale or {}, "teams": teams, "final_field": final,
            "week_games": [{"away": "Alpha", "home": "Beta"}]}


def test_quiet_when_nothing_changed():
    d = data()
    assert build_message(d, copy.deepcopy(d)) is None


def test_quiet_on_small_drift():
    # Odds wobble by 2 points and nobody moves two places: site updates, phone stays quiet.
    assert reasons(data(odds=(0.88, 0.52)), data()) == []


def test_new_week_message_has_odds_and_movers():
    title, body = build_message(data(week=5, odds=(0.9, 0.5)), data(week=4, odds=(0.7, 0.6)))
    assert title == "CFB Blend: week 5 update"
    assert "Alpha 90%" in body and "Alpha +20 pts" in body and "Beta -10 pts" in body
    assert "Game of the week: Alpha at Beta" in body


def test_new_cfp_rankings_midweek():
    title, _ = build_message(data(week=10, cfp=(2, 1)), data(week=10))
    assert title == "CFB Blend: new CFP rankings"


def test_new_ap_poll_and_late_input_catching_up():
    prev = data(stale={"sp": 4, "ap": 4})
    assert reasons(data(ap=(2, 1)), prev) == ["New AP poll", "SP+ and AP poll updated"]
    title, body = build_message(data(stale={"ap": 4}), prev)
    assert title == "CFB Blend: SP+ updated"
    assert "AP poll still from week 4" in body


def test_real_ranking_moves_notify():
    assert reasons(data(odds=(0.9, 0.56)), data()) == ["Rankings changed"]          # 6-point odds move
    assert reasons(data(ranks=(3, 2)), data(ranks=(1, 2))) == ["Rankings changed"]  # top-25 team moved 2 places
    assert reasons(data(ranks=(60, 2)), data(ranks=(61, 2))) == []                  # one place, outside top 25


def test_playoff_field_set():
    title, _ = build_message(data(final={"seeds": []}), data())
    assert title == "CFB Blend: the playoff field is set"
