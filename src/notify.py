"""Phone push notification (via ntfy.sh) when the site's numbers change.

Compares the new site/data.json with the previous version and sends a short
summary: the week, top playoff odds, the biggest movers, and anything still
waiting on a poll. Sends nothing unless something worth
knowing changed: a new week, a new AP poll or CFP ranking, an input that had
been out of date catching up, the playoff field being set, or a real move in
the rankings (see reasons()). Small drift updates the site without a ping.

The ntfy topic works like a password (anyone who knows it can read or post),
so it comes from the NTFY_TOPIC environment variable / repo secret.
Standard library only, so the deploy job doesn't need to install anything.

Usage:
  python -m src.notify --prev-ref HEAD~1     compare with the previous commit's data
  python -m src.notify --test                send a test message
  add --dry-run to print instead of sending
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

SITE_URL = "https://kysonallstar-stack.github.io/CFP-FPI-SP--APpoll-Rank-mix/"
DATA = Path(__file__).resolve().parent.parent / "site" / "data.json"
LABEL = {"ap": "AP poll", "cfp": "CFP rankings", "sp": "SP+", "fpi": "FPI"}


def load_prev(ref: str) -> dict | None:
    try:
        out = subprocess.run(["git", "show", f"{ref}:site/data.json"], capture_output=True, check=True, text=True)
        return json.loads(out.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


# What's worth a notification. Small day-to-day drift (FPI changes a little
# every day) updates the site quietly; these are the thresholds for "it moved".
MIN_RANK_MOVE = 2       # a top-25 team moving at least this many places in the blend
MIN_ODDS_MOVE = 0.05    # any team's playoff odds moving at least 5 points
TOP_N = 25


def reasons(new: dict, prev: dict | None) -> list[str]:
    """Why this update deserves a notification (empty list = stay quiet)."""
    if not prev or prev["season"] != new["season"]:
        return [f"Week {new['week']} update"]
    out = []
    if prev["week"] != new["week"]:
        out.append(f"Week {new['week']} update")
    if new.get("final_field") and not prev.get("final_field"):
        out.append("The playoff field is set")
    old = {t["id"]: t for t in prev["teams"]}
    changed = lambda key: any(t.get(key) != old.get(t["id"], {}).get(key) for t in new["teams"])
    if changed("cfp"):
        out.append("New CFP rankings")
    if changed("ap") or changed("ap_pts"):
        out.append("New AP poll")
    # An input that was out of date last time and is current now (e.g. SP+ arrived).
    caught_up = [LABEL.get(k, k) for k in (prev.get("stale") or {}) if k not in (new.get("stale") or {})]
    if caught_up:
        out.append(" and ".join(caught_up) + " updated")
    if not out:
        moved = [t for t in new["teams"] if t["id"] in old and
                 (min(t["rank"], old[t["id"]]["rank"]) <= TOP_N and abs(t["rank"] - old[t["id"]]["rank"]) >= MIN_RANK_MOVE
                  or abs(t["p_playoff"] - old[t["id"]]["p_playoff"]) >= MIN_ODDS_MOVE)]
        if moved:
            out.append("Rankings changed")
    return out


def pct(p: float) -> str:
    return "<1%" if 0 < p < 0.005 else f"{round(p * 100)}%"


def build_message(new: dict, prev: dict | None) -> tuple[str, str] | None:
    why = reasons(new, prev)
    if not why:
        return None
    head = why[0]
    if head[1:2].islower():                 # "Week 6 update" -> "week 6 update"; leave "SP+ updated" alone
        head = head[0].lower() + head[1:]
    title = "CFB Blend: " + head
    lines = ["Also: " + ", ".join(why[1:])] if len(why) > 1 else []

    top = sorted(new["teams"], key=lambda t: -t["p_playoff"])[:5]
    lines.append("Playoff odds: " + ", ".join(f"{t['name']} {pct(t['p_playoff'])}" for t in top))

    if prev and prev["season"] == new["season"]:
        before = {t["id"]: t["p_playoff"] for t in prev["teams"]}
        moves = sorted(((t["p_playoff"] - before.get(t["id"], 0.0), t) for t in new["teams"]),
                       key=lambda x: -abs(x[0]))[:3]
        moves = [f"{t['name']} {'+' if d > 0 else '-'}{abs(round(d * 100))} pts" for d, t in moves if abs(d) >= 0.05]
        if moves:
            lines.append("Biggest playoff-odds moves: " + ", ".join(moves))

    if new.get("week_games"):
        g = new["week_games"][0]          # sorted best matchup first
        lines.append(f"Game of the week: {g['away']} at {g['home']}")
    for k, wk in (new.get("stale") or {}).items():
        lines.append(f"{LABEL.get(k, k)} still from week {wk}; it will be picked up when it's released.")
    return title, "\n".join(lines)


def send(topic: str, title: str, body: str) -> None:
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), method="POST",
        headers={"Title": title, "Tags": "football", "Click": SITE_URL},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise RuntimeError(f"ntfy returned {resp.status}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prev-ref", default="HEAD~1", help="git ref holding the previous site/data.json")
    ap.add_argument("--test", action="store_true", help="send a test notification")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    args = ap.parse_args()

    if args.test:
        msg = ("CFB Blend: test notification", "Notifications are working. Tap to open the site.")
    else:
        msg = build_message(json.loads(DATA.read_text()), load_prev(args.prev_ref))
        if msg is None:
            print("Nothing worth a notification since the previous data; not sending.")
            return

    title, body = msg
    topic = os.environ.get("NTFY_TOPIC")
    if args.dry_run or not topic:
        print(("(dry run)" if args.dry_run else "(NTFY_TOPIC not set, not sending)") + f"\n{title}\n{body}")
        return
    send(topic, title, body)
    print(f"Sent: {title}")


if __name__ == "__main__":
    sys.exit(main())
