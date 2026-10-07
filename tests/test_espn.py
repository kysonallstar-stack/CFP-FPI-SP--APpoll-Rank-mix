import json

import pytest

from src import espn
from src.normalize import build_week
from src.teams import TeamRegistry

TEAMS = [
    {"id": 1, "school": "Kansas State", "alternateNames": ["KSU"], "conference": "Big 12"},
    {"id": 2, "school": "Kennesaw State", "alternateNames": [], "conference": "Conference USA"},
    {"id": 3, "school": "Ohio State", "alternateNames": [], "conference": "Big Ten"},
]
SP_PAGE = """<script>{"dateModified":"2026-10-04T14:45:00Z"}</script>
<table><thead><tr><th>Team</th><th>Rating</th></tr></thead><tbody>
<tr class="last"><td>1. Ohio St. (4-1)</td><td>30.3</td><td>42.0 (3)</td></tr>
<tr class="last"><td>2. Kansas St. (3-1)</td><td>13.8</td><td>30.0 (40)</td></tr>
<tr class="last"><td>3. KSU (1-3)</td><td>-11.5</td><td>20.0 (100)</td></tr>
</tbody></table>
<table><tbody><tr><td>1. Ohio St. (4-1)</td><td>99.9</td></tr></tbody></table>"""
OVERRIDES = {"KSU": "Kennesaw State"}


def reg():
    return TeamRegistry(TEAMS)


def test_sp_table_parses_first_table_only_with_overrides():
    out = espn.parse_sp(SP_PAGE, reg(), OVERRIDES)
    assert out["modified"] == "2026-10-04T14:45:00Z"
    assert {t["id"]: t["rating"] for t in out["teams"]} == {3: 30.3, 1: 13.8, 2: -11.5}   # not the 99.9 table


def test_sp_table_rejected_when_a_name_lands_on_the_wrong_team():
    # Without the override, "KSU" matches Kansas State a second time: refuse the whole table.
    with pytest.raises(ValueError, match="duplicate"):
        espn.parse_sp(SP_PAGE, reg(), {})


def test_sp_table_rejected_when_a_team_is_missing():
    page = SP_PAGE.replace('<tr class="last"><td>3. KSU (1-3)</td><td>-11.5</td><td>20.0 (100)</td></tr>', "")
    with pytest.raises(ValueError, match="missing"):
        espn.parse_sp(page, reg(), OVERRIDES)


def team(tid):
    return {"team": {"id": str(tid)}}


def test_polls_include_others_receiving_votes_as_unranked():
    data = {"rankings": [
        {"type": "ap", "occurrence": {"number": 6}, "lastUpdated": "x",
         "ranks": [{**team(3), "current": 1, "points": 1500.0, "firstPlaceVotes": 60}],
         "others": [{**team(1), "current": 0, "points": 12.0}, {**team(999), "current": 0, "points": 5.0}]},
        {"type": "usa", "occurrence": {"number": 6}, "ranks": [{**team(3), "current": 1, "points": 1.0}]},
    ]}
    out = espn.parse_polls(data, reg())
    assert out["ap_week"] == 6 and "cfp" not in out
    assert out["ap"] == [{"id": 3, "rank": 1, "points": 1500.0, "first_place_votes": 60},
                         {"id": 1, "rank": None, "points": 12.0, "first_place_votes": None}]   # 999 isn't FBS


def test_fpi_requires_every_team():
    data = {"lastUpdated": "t", "categories": [{"name": "fpi", "names": ["fpi", "fpirank"]}],
            "teams": [{**team(i), "categories": [{"name": "fpi", "values": [10.0 - i, i]}]} for i in (1, 2, 3)]}
    assert {t["id"]: t["fpi"] for t in espn.parse_fpi(data, reg())["teams"]} == {1: 9.0, 2: 8.0, 3: 7.0}
    data["teams"].pop()
    with pytest.raises(ValueError):
        espn.parse_fpi(data, reg())


class Resp:
    def __init__(self, payload):
        self.payload = payload
        self.text = payload if isinstance(payload, str) else ""

    def json(self):
        return self.payload


CFG = {"espn": {"user_agent": "t", "rankings_url": "R", "fpi_url": "F", "sp_article": {2026: "S"}, "sp_name_overrides": OVERRIDES},
       "polls": {"ap": "AP Top 25", "cfp": "Playoff Committee Rankings"}}


def test_snapshot_survives_failures_and_normalize_prefers_espn(tmp_path):
    fpi = {"lastUpdated": "t", "categories": [{"name": "fpi", "names": ["fpi"]}],
           "teams": [{**team(i), "categories": [{"name": "fpi", "values": [10.0 - i]}]} for i in (1, 2, 3)]}

    def get(url, ecfg):
        if url == "S":
            return Resp(SP_PAGE)
        if url == "F":
            return Resp(fpi)
        raise RuntimeError("ESPN rankings are down")          # polls fail: must not stop anything

    wdir = tmp_path / "2026" / "week_1"
    wdir.mkdir(parents=True)
    (wdir / "sp.json").write_text(json.dumps([{"team": "Ohio State", "rating": 1.0}]))     # stale CFBD copy
    (wdir / "polls.json").write_text(json.dumps([{"poll": "AP Top 25", "ranks": [
        {"rank": 1, "teamId": 3, "school": "Ohio State", "points": 100}]}]))
    (wdir / "games.json").write_text("[]")
    saved = espn.snapshot(2026, tmp_path, 1, reg(), CFG, get=get)
    assert saved == ["espn_sp.json", "espn_fpi.json"]

    out = build_week(tmp_path / "2026", 1, reg(), CFG)
    assert out["origins"] == {"sp": "espn", "fpi": "espn", "ap": "cfbd"}     # ESPN where it worked, CFBD where not
    assert out["ratings"]["sp"]["3"] == 30.3 and out["polls"]["ap"][0]["points"] == 100


def test_poll_for_the_wrong_week_is_ignored(tmp_path):
    polls = {"rankings": [{"type": "ap", "occurrence": {"number": 6}, "ranks": [{**team(3), "current": 1, "points": 9.0}]}]}
    get = lambda url, ecfg: Resp(polls)
    cfg = {"espn": {**CFG["espn"], "sp_article": {}}}
    assert "espn_polls.json" in espn.snapshot(2026, tmp_path, 5, reg(), cfg, get=get)       # week 6 poll follows week 5
    assert "espn_polls.json" not in espn.snapshot(2026, tmp_path, 6, reg(), cfg, get=get)   # not out yet for week 6
