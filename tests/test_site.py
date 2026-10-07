from src.site import _change

SRC = {3: ["elo"], 4: ["sp", "fpi"], 5: ["sp", "fpi"]}
HIST = [[3, 20, 10.0], [4, 14, 17.7], [5, 13, 17.9]]


def test_change_from_last_week():
    assert _change(HIST, 5, SRC) == {"d_rank": 1, "d_rating": 0.2}     # #14 -> #13 is up one


def test_no_change_shown_across_a_method_switch_or_without_history():
    assert _change(HIST, 4, SRC) == {"d_rank": None, "d_rating": None}  # Elo week -> SP+/FPI week
    assert _change([[5, 13, 17.9]], 5, SRC) == {"d_rank": None, "d_rating": None}
