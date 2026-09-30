from datetime import datetime

from src.config import current_season

OFF = [2, 3, 4, 5, 6, 7]


def test_season_runs_august_through_january():
    assert current_season(datetime(2026, 9, 30), OFF) == (2026, True)
    assert current_season(datetime(2027, 1, 20), OFF) == (2026, True)   # CFP title game
    assert current_season(datetime(2027, 8, 5), OFF) == (2027, True)    # next season starts on its own


def test_offseason_is_a_break():
    assert current_season(datetime(2027, 3, 1), OFF) == (2026, False)
    assert current_season(datetime(2027, 7, 31), OFF) == (2026, False)
