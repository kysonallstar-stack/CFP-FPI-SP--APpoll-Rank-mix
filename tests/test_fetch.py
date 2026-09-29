"""Caching and missing-data behavior of fetch + normalize, with a fake API."""
import json
from datetime import datetime, timezone

from src.fetch import fetch_season
from src.normalize import build_week
from src.teams import TeamRegistry

CFG = {
    "fetch": {"games_grace_days": 2, "poll_grace_days": 9},
    "polls": {"ap": "AP Top 25", "cfp": "Playoff Committee Rankings"},
}
TEAMS = [
    {"id": 1, "school": "Alpha", "alternateNames": [], "conference": "X"},
    {"id": 2, "school": "Beta", "alternateNames": [], "conference": "X"},
]
CALENDAR = [
    {"week": 1, "seasonType": "regular", "startDate": "2026-08-29T07:00:00Z", "endDate": "2026-09-08T06:59:00Z"},
    {"week": 2, "seasonType": "regular", "startDate": "2026-09-08T07:00:00Z", "endDate": "2026-09-15T06:59:00Z"},
    {"week": 3, "seasonType": "regular", "startDate": "2026-09-15T07:00:00Z", "endDate": "2026-09-22T06:59:00Z"},
]


def game(gid, week, completed, hp=None, ap=None):
    return {"id": gid, "week": week, "completed": completed, "neutralSite": False, "conferenceGame": True,
            "homeId": 1, "homeTeam": "Alpha", "homeClassification": "fbs", "homeConference": "X", "homePoints": hp,
            "awayId": 2, "awayTeam": "Beta", "awayClassification": "fbs", "awayConference": "X", "awayPoints": ap}


def ap_poll(week):
    return [{"week": week, "polls": [{"poll": "AP Top 25", "ranks": [
        {"rank": 1, "teamId": 1, "school": "Alpha", "points": 100, "firstPlaceVotes": 4}]}]}]


class FakeClient:
    """Records every call; `polls_for` maps CFBD poll week -> response."""

    def __init__(self, games, polls_for, sp=None):
        self.games, self.polls_for, self.sp = games, polls_for, sp
        self.calls = []

    def get(self, endpoint, **p):
        self.calls.append((endpoint, p))
        return {
            "teams/fbs": TEAMS,
            "calendar": CALENDAR,
            "games": self.games,
            "rankings": self.polls_for.get(p.get("week"), []),
            "ratings/elo": [{"team": "Alpha", "elo": 1600}, {"team": "Beta", "elo": 1400}],
            "ratings/sp": self.sp if self.sp is not None else [{"team": "Alpha", "rating": 10.0}, {"team": "Beta", "rating": -3.0}],
            "ratings/fpi": [{"team": "Alpha", "fpi": 8.0}, {"team": "Beta", "fpi": -2.0}],
        }[endpoint]


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def meta(tmp_path, n):
    return json.loads((tmp_path / "2026" / f"week_{n}" / "_meta.json").read_text())


def test_completed_week_is_never_redownloaded(tmp_path):
    games = [game(10, 1, True, 21, 14), game(11, 2, True, 7, 3)]
    client = FakeClient(games, {1: ap_poll(1), 2: ap_poll(2), 3: ap_poll(3)})
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-16T12:00:00"))
    assert meta(tmp_path, 1)["complete"] and meta(tmp_path, 2)["complete"]

    client.calls.clear()
    summary = fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-17T12:00:00"))
    assert client.calls == []                       # nothing re-downloaded
    assert summary["skipped_complete"] == [0, 1, 2]


def test_week_missing_poll_stays_open_then_freezes_after_grace(tmp_path):
    games = [game(10, 1, True, 21, 14)]
    client = FakeClient(games, {1: ap_poll(1)})     # no poll released after week 1
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-09T12:00:00"))
    m = meta(tmp_path, 1)
    assert not m["complete"] and "AP poll" in m["missing"]

    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-14T12:00:00"))
    assert not meta(tmp_path, 1)["complete"]        # still within grace: keep retrying

    # The ratings/week-2 side doesn't matter here; only week 1's freeze decision.
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-18T12:00:00"))
    m = meta(tmp_path, 1)
    assert m["complete"] and "AP poll" in m["missing"]   # frozen, gap recorded


def test_unfinished_game_keeps_week_open(tmp_path):
    games = [game(10, 1, False)]
    client = FakeClient(games, {1: ap_poll(1), 2: ap_poll(2)})
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-08T12:00:00"))
    m = meta(tmp_path, 1)
    assert not m["complete"] and any("final score" in x for x in m["missing"])


def test_sp_fpi_snapshot_only_for_latest_week(tmp_path):
    games = [game(10, 1, True, 21, 14), game(11, 2, True, 7, 3)]
    client = FakeClient(games, {1: ap_poll(1), 2: ap_poll(2), 3: ap_poll(3)})
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-16T12:00:00"))
    assert (tmp_path / "2026" / "week_2" / "sp.json").exists()
    assert not (tmp_path / "2026" / "week_1" / "sp.json").exists()
    assert any("SP+/FPI" in x for x in meta(tmp_path, 1)["missing"])


def test_normalize_week_with_missing_data_does_not_crash_or_look_ahead(tmp_path):
    games = [game(10, 1, True, 21, 14), game(11, 2, True, 7, 3), game(12, 3, False)]
    client = FakeClient(games, {1: ap_poll(1), 2: ap_poll(2)})   # no poll after week 2
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-16T12:00:00"))
    reg = TeamRegistry(TEAMS)
    season_dir = tmp_path / "2026"

    wk1 = build_week(season_dir, 1, reg, CFG)
    # SP+ exists only in week 2's snapshot, so week 1 must NOT use it.
    assert wk1["ratings"]["sp"] == {} and "sp" in wk1["missing"]
    assert [g["id"] for g in wk1["games_completed"]] == [10]
    # Week 2's result must be hidden from the week-1 view.
    later = next(g for g in wk1["games_remaining"] if g["id"] == 11)
    assert later["home_points"] is None and not later["completed"]

    wk2 = build_week(season_dir, 2, reg, CFG)
    assert wk2["ratings"]["sp"] == {"1": 10.0, "2": -3.0}
    assert wk2["sources"]["ap"] == 1                  # AP carried forward from week 1
    assert wk2["polls"]["cfp"] is None                # no committee rankings yet: not an error
    assert "ap" not in wk2["missing"]


def test_unmatched_rating_name_is_recorded(tmp_path):
    games = [game(10, 1, True, 21, 14)]
    sp = [{"team": "Alpha", "rating": 5.0}, {"team": "Gamma Tech", "rating": 1.0},
          {"team": "nationalAverages", "rating": 0.0}]
    client = FakeClient(games, {1: ap_poll(1), 2: ap_poll(2)}, sp=sp)
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-09T12:00:00"))
    reg = TeamRegistry(TEAMS)
    out = build_week(tmp_path / "2026", 1, reg, CFG)
    assert out["ratings"]["sp"] == {"1": 5.0}
    assert ("sp", "Gamma Tech") in reg.unmatched
    assert all(name != "nationalAverages" for _, name in reg.unmatched)
