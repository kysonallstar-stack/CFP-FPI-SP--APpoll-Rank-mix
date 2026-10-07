"""Caching and missing-data behavior of fetch + normalize, with a fake API."""
import json
from datetime import datetime, timezone

from src.fetch import fetch_season
from src.normalize import build_week
from src.teams import TeamRegistry

CFG = {
    "fetch": {"games_grace_days": 2},
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
            # The real endpoint returns every week's polls when no week is given.
            "rankings": [w for resp in self.polls_for.values() for w in resp],
            # Elo moves every week, like the real thing.
            "ratings/elo": [{"team": "Alpha", "elo": 1600 + 10 * (p.get("week") or 0)},
                            {"team": "Beta", "elo": 1400 - 10 * (p.get("week") or 0)}],
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
    endpoints = [e for e, _ in client.calls]
    # Only the cheap refreshes: polls, the latest week's ratings snapshot, and one
    # schedule call to see whether the week in progress has finished.
    assert "teams/fbs" not in endpoints and "calendar" not in endpoints
    assert endpoints.count("rankings") == 1 and endpoints.count("games") <= 1
    assert summary["fetched"] == []                 # no frozen week was rewritten
    assert summary["skipped_complete"] == [0, 1, 2]


def test_poll_released_late_is_picked_up_after_week_freezes(tmp_path):
    games = [game(10, 1, True, 21, 14)]
    polls = {1: ap_poll(1)}                           # nothing released after week 1 yet
    client = FakeClient(games, polls)
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-09T12:00:00"))
    m = meta(tmp_path, 1)
    assert m["complete"]                              # games are final, so the week freezes...
    assert any("AP poll" in x for x in m["missing"])  # ...but the missing poll is noted

    polls[2] = ap_poll(2)                             # the AP comes out late
    summary = fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-10T12:00:00"))
    assert summary["polls_updated"] == [1]
    saved = json.loads((tmp_path / "2026" / "week_1" / "polls.json").read_text())
    assert saved[0]["poll"] == "AP Top 25"


def test_tuesday_cfp_rankings_reach_a_week_frozen_on_monday(tmp_path):
    games = [game(10, 1, True, 21, 14)]
    polls = {1: ap_poll(1), 2: ap_poll(2)}            # Monday: AP is out, CFP isn't
    client = FakeClient(games, polls)
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-09T12:00:00"))
    assert meta(tmp_path, 1)["complete"]

    cfp = {"poll": "Playoff Committee Rankings", "ranks": [{"rank": 1, "teamId": 2, "school": "Beta", "points": 0}]}
    polls[2] = [{"week": 2, "polls": polls[2][0]["polls"] + [cfp]}]   # Tuesday night release
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-10T12:00:00"))
    saved = json.loads((tmp_path / "2026" / "week_1" / "polls.json").read_text())
    assert {p["poll"] for p in saved} == {"AP Top 25", "Playoff Committee Rankings"}
    out = build_week(tmp_path / "2026", 1, TeamRegistry(TEAMS), CFG)
    assert out["polls"]["cfp"][0]["id"] == 2


def test_unfinished_game_keeps_week_open(tmp_path):
    games = [game(10, 1, False)]
    client = FakeClient(games, {1: ap_poll(1), 2: ap_poll(2)})
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-08T12:00:00"))
    m = meta(tmp_path, 1)
    assert not m["complete"] and any("final score" in x for x in m["missing"])


def test_sp_fpi_snapshot_only_for_latest_week_and_refreshed_each_run(tmp_path):
    games = [game(10, 1, True, 21, 14), game(11, 2, True, 7, 3)]
    client = FakeClient(games, {1: ap_poll(1), 2: ap_poll(2), 3: ap_poll(3)})
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-16T12:00:00"))
    sp_path = tmp_path / "2026" / "week_2" / "sp.json"
    assert sp_path.exists()
    assert not (tmp_path / "2026" / "week_1" / "sp.json").exists()
    assert any("SP+/FPI" in x for x in meta(tmp_path, 1)["missing"])

    # SP+ updates later in the week: the next run (same latest week) picks it up.
    client.sp = [{"team": "Alpha", "rating": 12.0}, {"team": "Beta", "rating": -4.0}]
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-17T12:00:00"))
    assert json.loads(sp_path.read_text())[0]["rating"] == 12.0


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
    assert wk2["stale"] == {"ap": 1}                  # ...and flagged as stale
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


def test_empty_calendar_is_not_cached(tmp_path):
    import pytest
    client = FakeClient([], {})
    client.get = lambda endpoint, **p: [] if endpoint == "calendar" else TEAMS
    with pytest.raises(RuntimeError):
        fetch_season(client, 2027, tmp_path, CFG, now=at("2027-08-01T12:00:00"))
    assert not (tmp_path / "2027" / "calendar.json").exists()   # re-checked next run


def test_sunday_run_picks_up_a_week_whose_games_are_all_final(tmp_path):
    """Week 2 "ends" Tue 06:59 UTC on the calendar; a run before that still counts it
    once every game is final, and doesn't if one is still unplayed."""
    polls = {1: ap_poll(1), 2: ap_poll(2), 3: ap_poll(3)}
    sunday = at("2026-09-13T19:00:00")                         # before week 2's calendar end
    done = FakeClient([game(10, 1, True, 21, 14), game(11, 2, True, 7, 3)], polls)
    assert fetch_season(done, 2026, tmp_path / "a", CFG, now=sunday)["latest_week"] == 2
    assert (tmp_path / "a" / "2026" / "week_2" / "games.json").exists()

    pending = FakeClient([game(10, 1, True, 21, 14), game(11, 2, False)], polls)
    assert fetch_season(pending, 2026, tmp_path / "b", CFG, now=sunday)["latest_week"] == 1


def test_unchanged_ratings_are_reported_as_stale(tmp_path):
    """SP+ downloaded again in week 2 but identical to week 1's numbers: it's really week-1 data."""
    client = FakeClient([game(10, 1, True, 21, 14), game(11, 2, False)],
                        {1: ap_poll(1), 2: ap_poll(2), 3: ap_poll(3)})
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-09T12:00:00"))   # week 1 snapshot
    client.games = [game(10, 1, True, 21, 14), game(11, 2, True, 7, 3)]
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-16T12:00:00"))   # week 2: same numbers
    out = build_week(tmp_path / "2026", 2, TeamRegistry(TEAMS), CFG)
    assert out["sources"]["sp"] == 1 and out["stale"]["sp"] == 1
    assert out["ratings"]["sp"] == {"1": 10.0, "2": -3.0}      # still used, just flagged

    client.sp = [{"team": "Alpha", "rating": 12.0}, {"team": "Beta", "rating": -4.0}]
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-17T12:00:00"))   # source updates
    out = build_week(tmp_path / "2026", 2, TeamRegistry(TEAMS), CFG)
    assert out["sources"]["sp"] == 2 and "sp" not in out["stale"]


def test_source_updates_are_logged_only_when_numbers_change(tmp_path):
    client = FakeClient([game(10, 1, True, 21, 14), game(11, 2, False)], {1: ap_poll(1), 2: ap_poll(2)})
    log = tmp_path / "2026" / "source_updates.json"
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-09T12:00:00"))
    first = json.loads(log.read_text())
    assert {u["source"] for u in first} == {"sp", "fpi"} and all(u["first_snapshot"] for u in first)

    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-10T12:00:00"))   # same numbers
    assert json.loads(log.read_text()) == first

    client.sp = [{"team": "Alpha", "rating": 12.0}, {"team": "Beta", "rating": -4.0}]
    fetch_season(client, 2026, tmp_path, CFG, now=at("2026-09-11T12:00:00"))   # SP+ updated at the source
    last = json.loads(log.read_text())[-1]
    assert last["source"] == "sp" and last["seen_at"].startswith("2026-09-11") and not last["first_snapshot"]
