import copy

from src.notify import build_message


def data(week=5, odds=(0.9, 0.5), stale=None):
    teams = [{"id": i, "name": n, "rank": i, "p_playoff": p, "cfp": None, "ap": i}
             for i, (n, p) in enumerate(zip(["Alpha", "Beta"], odds), start=1)]
    return {"season": 2026, "week": week, "stale": stale or {}, "teams": teams,
            "week_games": [{"away": "Alpha", "home": "Beta"}]}


def test_no_message_when_nothing_changed():
    d = data()
    assert build_message(d, copy.deepcopy(d)) is None


def test_new_week_message_has_odds_and_movers():
    title, body = build_message(data(week=5, odds=(0.9, 0.5)), data(week=4, odds=(0.7, 0.6)))
    assert title == "CFB Blend: week 5 update"
    assert "Alpha 90%" in body and "Alpha +20 pts" in body and "Beta -10 pts" in body
    assert "Game of the week: Alpha at Beta" in body


def test_cfp_release_midweek_gets_its_own_title_and_stale_note():
    prev = data(week=10, stale={"cfp": 9})
    new = copy.deepcopy(prev)
    new["stale"] = {}
    new["teams"][1]["cfp"] = 1
    title, body = build_message(new, prev)
    assert title == "CFB Blend: new CFP rankings"
    assert "still from week" not in body
    title, body = build_message(prev, None)
    assert "CFP rankings still from week 9" in body
