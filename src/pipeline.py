"""Run the whole weekly update: fetch -> normalize -> rate -> simulate -> site data.

Usage: python -m src.pipeline [--season 2026] [--skip-fetch]
This is what the GitHub Actions workflow runs every Monday.
"""
import argparse
import json
import logging

from src import site
from src.cfbd import CFBDClient
from src.config import load_config, repo_path
from src.fetch import fetch_season
from src.normalize import normalize_season
from src.rating import write_outputs

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--season", type=int, default=cfg["season"])
    ap.add_argument("--skip-fetch", action="store_true", help="use cached data only (no API key needed)")
    args = ap.parse_args()

    if not args.skip_fetch:
        client = CFBDClient.from_config(cfg)
        summary = fetch_season(client, args.season, repo_path(cfg["paths"]["raw"]), cfg)
        log.info("fetch: %d week(s) updated, %d API call(s)", len(summary["fetched"]), client.calls)

    normalize_season(args.season, cfg)
    proc = repo_path(cfg["paths"]["processed"]) / str(args.season)
    data, ratings, sim = site.build(args.season, proc, cfg, cfg["sim"]["n_sims"], cfg["sim"]["seed"])

    write_outputs(ratings, proc)
    (proc / f"sim_week_{data['week']}.json").write_text(json.dumps(sim, indent=1, ensure_ascii=False))
    path = site.write(data, repo_path("site"))
    log.info("site data: %s (week %d, %d teams, %d games)",
             path.relative_to(repo_path(".")), data["week"], len(data["teams"]), len(data["games"]))


if __name__ == "__main__":
    main()
