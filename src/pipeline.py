"""Run the whole weekly update: fetch -> normalize -> rate -> simulate -> site data.

Usage: python -m src.pipeline [--season 2026] [--skip-fetch]
This is what the GitHub Actions workflow runs every Monday.
"""
import argparse
import json
import logging

from src import espn, site
from src.cfbd import CFBDClient
from src.config import load_config, repo_path
from src.fetch import fetch_season
from src.normalize import normalize_season
from src.rating import write_outputs
from src.teams import TeamRegistry

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=cfg["season"])
    ap.add_argument("--skip-fetch", action="store_true", help="use cached data only (no API key needed)")
    args = ap.parse_args()

    if not cfg["season_active"] and args.season == cfg["season"]:
        log.info("Offseason (%s season is over): nothing to do until August.", args.season)
        return

    if not args.skip_fetch:
        client = CFBDClient.from_config(cfg)
        try:
            summary = fetch_season(client, args.season, repo_path(cfg["paths"]["raw"]), cfg)
        except RuntimeError as e:  # e.g. next season's calendar isn't published yet
            log.info("Nothing to fetch yet: %s", e)
            return
        log.info("fetch: %d week(s) updated, %d API call(s)", len(summary["fetched"]), client.calls)
        if cfg.get("espn", {}).get("enabled") and summary["latest_week"] is not None:
            raw_root = repo_path(cfg["paths"]["raw"])
            reg = TeamRegistry.from_files(json.loads((raw_root / str(args.season) / "teams_fbs.json").read_text()),
                                          repo_path(cfg["paths"]["aliases"]))
            saved = espn.snapshot(args.season, raw_root, summary["latest_week"], reg, cfg)
            log.info("ESPN: saved %s", ", ".join(saved) or "nothing")

    raw = repo_path(cfg["paths"]["raw"]) / str(args.season)
    if not any(raw.glob("week_*")):
        log.info("The %s season hasn't started yet (no weeks finished); nothing to publish.", args.season)
        return
    normalize_season(args.season, cfg)
    proc = repo_path(cfg["paths"]["processed"]) / str(args.season)
    try:
        data, ratings, sim = site.build(args.season, proc, cfg, cfg["sim"]["n_sims"], cfg["sim"]["seed"])
    except ValueError as e:   # e.g. preseason ratings not published yet
        log.warning("Not enough data to rate teams yet (%s); leaving the site unchanged.", e)
        return

    write_outputs(ratings, proc)
    (proc / f"sim_week_{data['week']}.json").write_text(json.dumps(sim, indent=1, ensure_ascii=False))
    path = site.write(data, repo_path("site"))
    log.info("site data: %s (week %d, %d teams, %d games)",
             path.relative_to(repo_path(".")), data["week"], len(data["teams"]), len(data["games"]))


if __name__ == "__main__":
    main()
