from src.teams import TeamRegistry, normalize_name

FBS = [
    {"id": 23, "school": "San José State", "alternateNames": ["San Jose State", "SJSU"], "abbreviation": "SJSU", "conference": "Mountain West"},
    {"id": 62, "school": "Hawai'i", "alternateNames": ["Hawaii"], "abbreviation": "HAW", "conference": "Mountain West"},
    {"id": 2390, "school": "Miami", "alternateNames": ["Miami"], "abbreviation": "MIA", "conference": "ACC"},
    {"id": 193, "school": "Miami (OH)", "alternateNames": ["Miami"], "abbreviation": "M-OH", "conference": "Mid-American"},
    {"id": 245, "school": "Texas A&M", "alternateNames": ["Texas A&M"], "abbreviation": "TA&M", "conference": "SEC"},
    {"id": 194, "school": "Ohio State", "alternateNames": [], "abbreviation": "OSU", "conference": "Big Ten"},
    {"id": 2433, "school": "UL Monroe", "alternateNames": [], "abbreviation": "ULM", "conference": "Sun Belt"},
]


def reg(aliases=None):
    return TeamRegistry(FBS, aliases)


def test_normalize_handles_accents_punctuation_and_ampersand():
    assert normalize_name("San José State") == "san jose state"
    assert normalize_name("Hawai'i") == "hawaii"
    assert normalize_name("Texas A&M") == normalize_name("Texas A and M")
    assert normalize_name("Ohio St.") == "ohio state"


def test_exact_name_matches():
    assert reg().match("Ohio State") == 194


def test_spelling_variants_match_same_id():
    r = reg()
    assert r.match("San Jose State") == 23
    assert r.match("SAN JOSÉ STATE") == 23
    assert r.match("Hawaii") == 62
    assert r.match("Texas A and M") == 245
    assert r.match("Ohio St") == 194


def test_ambiguous_alias_is_not_guessed():
    # Both Miamis list "Miami" as an alternate name; only the exact school name wins.
    r = reg()
    assert r.match("Miami") == 2390          # exact school name
    assert r.match("Miami (OH)") == 193
    assert r.match("Miami OH") == 193        # normalized form of the school name


def test_config_alias_is_used():
    r = reg({"Louisiana Monroe": "UL Monroe"})
    assert r.match("Louisiana-Monroe") == 2433


def test_unknown_team_is_logged_not_guessed():
    r = reg()
    assert r.match("Montana State", source="fpi") is None
    assert r.unmatched == [("fpi", "Montana State")]


def test_alias_to_unknown_team_is_ignored(caplog):
    r = reg({"Nowhere U": "Not A Team"})
    assert r.match("Nowhere U") is None
    assert "unknown team" in caplog.text
