"""Phone push notification (via ntfy.sh) when the site's numbers change.

Compares the new site/data.json with the previous version and sends a short
summary: the week, top playoff odds, the biggest movers, and anything still
waiting on a poll. Sends nothing if the numbers didn't change (e.g. a Wednesday
run in September, before committee rankings exist).

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


def fingerprint(d: dict) -> tuple:
    """What counts as 'something changed' for a notification."""
    return (d["season"], d["week"], tuple(sorted(d.get("stale", {}).items())),
            tuple((t["id"], t["rank"], t["p_playoff"], t["cfp"], t["ap"]) for t in d["teams"]))


def pct(p: float) -> str:
    return "<1%" if 0 < p < 0.005 else f"{round(p * 100)}%"


def build_message(new: dict, prev: dict | None) -> tuple[str, str] | None:
    if prev and fingerprint(prev) == fingerprint(new):
        return None
    new_week = not prev or prev["week"] != new["week"] or prev["season"] != new["season"]
    got_cfp = bool(prev) and not new_week and any(t["cfp"] for t in new["teams"]) and \
        [t["cfp"] for t in new["teams"]] != [t["cfp"] for t in prev["teams"]]
    if new_week:
        title = f"CFB Blend: week {new['week']} update"
    elif got_cfp:
        title = "CFB Blend: new CFP rankings"
    else:
        title = "CFB Blend: rankings updated"

    top = sorted(new["teams"], key=lambda t: -t["p_playoff"])[:5]
    lines = ["Playoff odds: " + ", ".join(f"{t['name']} {pct(t['p_playoff'])}" for t in top)]

    if prev and prev["season"] == new["season"]:
        before = {t["id"]: t["p_playoff"] for t in prev["teams"]}
        moves = sorted(((t["p_playoff"] - before.get(t["id"], 0.0), t) for t in new["teams"]),
                       key=lambda x: -abs(x[0]))[:3]
        moves = [f"{t['name']} {'+' if d > 0 else '-'}{abs(round(d * 100))} pts" for d, t in moves if abs(d) >= 0.05]
        if moves:
            lines.append("Biggest playoff-odds moves: " + ", ".join(moves))

    if new.get("top_matchups"):
        g = new["top_matchups"][0]
        lines.append(f"Game of the week: {g['away']} at {g['home']}")
    for k, wk in (new.get("stale") or {}).items():
        lines.append(f"{LABEL.get(k, k)} still from week {wk}; will re-check Wednesday.")
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
            print("No changes since the previous data; not sending.")
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
