"""One team ID across all sources.

CFBD's FBS team list is the source of truth: its numeric `id` is our team ID
and its `school` is the clean display name. Polls and games already carry that
ID; SP+ and FPI carry only a name, so they go through `match()`.

Match order (first hit wins):
  1. exact school name
  2. normalized school name (case, accents, punctuation, "&" vs "and")
  3. normalized alias (CFBD alternate names + config/team_aliases.yaml),
     used only if the alias points to exactly one team
Anything else is recorded in `unmatched` so the pipeline can log it.
"""
import logging
import re
import unicodedata
from pathlib import Path

import yaml

log = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))  # San José -> San Jose
    s = s.lower().replace("&", " and ")
    s = re.sub(r"['’.]", "", s)                                 # Hawai'i -> hawaii, St. -> st
    s = re.sub(r"\bst\b", "state", s)                           # "Ohio St" -> "ohio state"
    s = re.sub(r"[^a-z0-9]+", " ", s)                           # "Miami (OH)" -> "miami oh"
    return " ".join(s.split())


class TeamRegistry:
    def __init__(self, fbs_teams: list[dict], extra_aliases: dict[str, str] | None = None):
        self.by_id: dict[int, dict] = {}
        self._exact: dict[str, int] = {}
        self._norm: dict[str, int] = {}
        alias_hits: dict[str, set[int]] = {}

        for t in fbs_teams:
            tid = t["id"]
            self.by_id[tid] = {
                "id": tid,
                "name": t["school"],
                "conference": t.get("conference"),
                "division": t.get("division"),
                "abbreviation": t.get("abbreviation"),
                "color": t.get("color"),
                # Small logo made for dark backgrounds (the site is always dark).
                "logo": next((u for u in t.get("logos") or [] if "/logos-dark/64/" in u),
                             (t.get("logos") or [None])[0]),
            }
            self._exact[t["school"]] = tid
            self._norm[normalize_name(t["school"])] = tid
            for alt in (t.get("alternateNames") or []) + [t.get("abbreviation")]:
                if alt:
                    alias_hits.setdefault(normalize_name(alt), set()).add(tid)

        for alias, canonical in (extra_aliases or {}).items():
            tid = self._exact.get(canonical)
            if tid is None:
                log.warning("Alias %r points to unknown team %r", alias, canonical)
                continue
            alias_hits.setdefault(normalize_name(alias), set()).add(tid)

        # Aliases shared by two teams (e.g. "Miami") are ambiguous and ignored.
        self._alias = {a: next(iter(ids)) for a, ids in alias_hits.items() if len(ids) == 1}
        self.unmatched: list[tuple[str, str]] = []

    @classmethod
    def from_files(cls, fbs_teams: list[dict], aliases_path: Path | None) -> "TeamRegistry":
        extra = {}
        if aliases_path and Path(aliases_path).exists():
            extra = yaml.safe_load(Path(aliases_path).read_text()) or {}
        return cls(fbs_teams, extra)

    def match(self, name: str, source: str = "?") -> int | None:
        if name in self._exact:
            return self._exact[name]
        n = normalize_name(name)
        tid = self._norm.get(n) or self._alias.get(n)
        if tid is None:
            self.unmatched.append((source, name))
        return tid

    def name(self, tid: int) -> str:
        return self.by_id[tid]["name"]

    def is_fbs(self, tid: int | None) -> bool:
        return tid in self.by_id
