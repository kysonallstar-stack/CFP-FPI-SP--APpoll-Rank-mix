"""Fresher ratings and polls straight from ESPN.

CFBD is the backbone (games, schedule, teams, Elo), but it lags ESPN on SP+
and FPI by an unpredictable number of days and only carries the AP Top 25.
ESPN publishes:
  - SP+   weekly, Sunday morning, as a table in Bill Connelly's rankings article
          (read from ESPN's article feed, where the table is structured data;
          the web page itself isn't served to cloud machines like GitHub's)
  - FPI   daily, as JSON behind espn.com/college-football/fpi
  - polls the AP Top 25 *plus others receiving votes* (and CFP rankings when
          they exist), as JSON behind the rankings page
ESPN's team ids are the same ids CFBD uses, so only SP+ (names) needs matching.

These are unofficial sources that can change without notice, so every fetch is
optional: on any error or surprise (a team missing, a table that doesn't
parse) we log a warning, write nothing, and the pipeline uses CFBD's numbers.
Three requests per run.

Snapshots go in the latest week's folder next to CFBD's:
  espn_sp.json, espn_fpi.json, espn_polls.json
"""
import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from src.teams import TeamRegistry

log = logging.getLogger(__name__)

SP_TEAM = re.compile(r"^(\d+)\.\s*(.+?)\s*\((\d+)-(\d+)\)$")   # "2. Ohio St. (4-1)"
POLL_TYPES = {"ap": "ap", "cfp": "cfp"}   # ESPN poll type -> our key


def _get(url: str, ecfg: dict) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": ecfg["user_agent"]}, timeout=ecfg.get("timeout_seconds", 30))
    resp.raise_for_status()
    return resp


def parse_sp(article: dict, reg: TeamRegistry, overrides: dict) -> dict:
    """SP+ ratings from the article's first table whose columns start Team, Rating.
    Raises ValueError unless every FBS team appears exactly once -- a partial or
    mis-matched table is worse than none."""
    head = article["headlines"][0]
    table = next((m["json"] for m in head.get("inlines", [])
                  if m.get("moduleType") == "table" and m.get("json", {}).get("header", [])[:2] == ["Team", "Rating"]), None)
    if table is None:
        raise ValueError("SP+ article has no Team/Rating table")
    teams, seen = [], set()
    for row in table["body"]:
        m = SP_TEAM.match(html.unescape(row[0]).strip())
        if not m:
            raise ValueError(f"SP+ table: can't read row {row[0]!r}")
        rank, name, wins, losses = m.groups()
        tid = reg.match(overrides.get(name, name), source="espn_sp")
        if tid is None or tid in seen:
            raise ValueError(f"SP+ table: can't place {name!r} ({'duplicate' if tid in seen else 'unknown team'})")
        seen.add(tid)
        teams.append({"id": tid, "rank": int(rank), "rating": float(row[1]), "wins": int(wins), "losses": int(losses)})
    missing = set(reg.by_id) - seen
    if missing or not teams:
        raise ValueError(f"SP+ table: {len(teams)} teams parsed, missing {sorted(reg.name(t) for t in missing)[:5]}")
    return {"modified": head.get("lastModified"), "teams": teams}


def parse_fpi(data: dict, reg: TeamRegistry) -> dict:
    names = next(c["names"] for c in data["categories"] if c["name"] == "fpi")
    col = names.index("fpi")
    teams = []
    for t in data["teams"]:
        tid = int(t["team"]["id"])
        values = next(c["values"] for c in t["categories"] if c["name"] == "fpi")
        if reg.is_fbs(tid) and values[col] is not None:
            teams.append({"id": tid, "fpi": float(values[col])})
    missing = set(reg.by_id) - {t["id"] for t in teams}
    if missing:
        raise ValueError(f"FPI: missing {sorted(reg.name(t) for t in missing)[:5]}")
    return {"updated": data.get("lastUpdated"), "teams": teams}


def parse_polls(data: dict, reg: TeamRegistry) -> dict:
    """AP (ranked teams + others receiving votes, rank None) and CFP rankings if present."""
    out = {}
    for poll in data.get("rankings", []):
        key = POLL_TYPES.get(poll.get("type"))
        if not key:
            continue
        entries = []
        for e in poll.get("ranks", []) + poll.get("others", []):
            tid = int(e["team"]["id"])
            if not reg.is_fbs(tid):
                continue
            rank = e.get("current") or None                # others receiving votes come as 0
            entries.append({"id": tid, "rank": rank, "points": e.get("points"),
                            "first_place_votes": e.get("firstPlaceVotes")})
        if entries:
            out[key] = entries
            out[f"{key}_week"] = poll.get("occurrence", {}).get("number")
            out[f"{key}_updated"] = poll.get("lastUpdated")
    return out


def snapshot(season: int, raw_root: Path, latest_week: int, reg: TeamRegistry, cfg: dict, get=_get) -> list[str]:
    """Fetch what ESPN has now into the latest week's folder. Returns the names saved."""
    ecfg = cfg["espn"]
    wdir = raw_root / str(season) / f"week_{latest_week}"
    saved = []

    def save(name: str, build):
        try:
            data = build()
            data["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            wdir.mkdir(parents=True, exist_ok=True)
            (wdir / name).write_text(json.dumps(data, indent=1, ensure_ascii=False))
            saved.append(name)
        except Exception as e:  # noqa: BLE001 - any ESPN problem must not stop the weekly run
            log.warning("ESPN %s skipped (%s); using CFBD for this.", name, e)

    def polls():
        data = parse_polls(get(ecfg["rankings_url"], ecfg).json(), reg)
        # CFBD-style week mapping: the poll labeled week n+1 is the one released after week n.
        for key in ("ap", "cfp"):
            if key in data and data[f"{key}_week"] != latest_week + 1:
                log.info("ESPN %s poll is for week %s, not week %s yet; ignoring it.", key, data[f"{key}_week"], latest_week + 1)
                data.pop(key)
        if "ap" not in data and "cfp" not in data:
            raise ValueError("no poll for this week yet")
        return data

    article_id = (ecfg.get("sp_article_id") or {}).get(season)
    if article_id:
        url = ecfg["article_url"].format(id=article_id)
        save("espn_sp.json", lambda: parse_sp(get(url, ecfg).json(), reg, ecfg.get("sp_name_overrides") or {}))
    else:
        log.warning("No ESPN SP+ article configured for %s (config espn.sp_article_id); using CFBD's SP+.", season)
    save("espn_fpi.json", lambda: parse_fpi(get(ecfg["fpi_url"], ecfg).json(), reg))
    save("espn_polls.json", polls)
    return saved
