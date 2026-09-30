# CFB Blend

A weekly college football ranking that blends computer ratings (SP+ and ESPN's
FPI) with the AP poll, plus 12-team playoff odds from 10,000 simulated seasons.

**Site:** https://kysonallstar-stack.github.io/CFP-FPI-SP--APpoll-Rank-mix/
(updates every Monday at 10:00 Mountain time, after the Sunday AP poll)

## The method, in plain language

**1. The computers.** SP+ and FPI each rate every FBS team in points. We
convert each to a z-score (how many standard deviations above or below
average) and average the two.

**2. The humans.** The AP poll's vote points are put on the same scale, so the
gaps between teams match the gaps in votes, not just the rank order. The AP only
publishes its Top 25, so every other team is treated as "no better than one
step below #25". Being unranked can pull a good team down on the poll side,
but it never pulls a bad team *up*.

**3. The blend.** `blend = (1 - w) × computers + w × poll`. The poll's weight
`w` grows from 25% (week 5 or earlier) to 40% (week 12 or later), because a
poll means more once voters have seen more games. The result is converted back
to points, so the difference between two teams' ratings is a predicted margin.

**4. The committee.** Once the College Football Playoff committee releases its
rankings (early November), they replace the AP poll at a 50% weight in a
separate *selection rating*. That rating is only used to predict whom the
committee picks. Game predictions keep using the AP, because the committee's
opinion doesn't help predict scores. The "Disagree" page compares the
committee to the computers, which shows where its choices depart from the
numbers.

**5. Game odds.** Predicted margin = rating difference × 0.9 + 2.7 points for
the home team (0 at a neutral site). A normal distribution with a standard
deviation of 16 points turns that into a win probability. Home field and the
standard deviation were measured from 2025 games, and the 0.9 comes from the
backtest.

**6. The season simulation.** 10,000 times over, we:
- play every remaining game;
- set up each conference title game from the simulated standings (top two by
  conference win percentage; the Sun Belt uses its East and West division
  winners);
- simulate the title games;
- pick the 12-team field using the 2026-27 rules. The four Power 4 champions
  get automatic bids. So does the highest-ranked Group of 6 team, champion or
  not. Notre Dame gets one if it's in the committee's top 12. The rest are
  at-large picks. The top four teams get byes.

Each simulated season also draws how much better or worse each team *really*
is than its current rating, based on how much ratings actually moved in past
seasons. A team that's secretly better wins more of *all* its games, which
keeps the odds from being overconfident.

**7. Leverage and top matchups.**
- *Top matchups* are next week's games where both teams are good.
- *Leverage* is how much a game's result changes playoff odds, added up over
  every team it affects. Small changes that could just be random noise from
  the simulation are left out.

## Known weaknesses

- **The preseason prior.** Early in the season, SP+ and FPI lean heavily on
  preseason expectations (returning players, recruiting, last year's results).
  A team that's much better or worse than expected takes weeks to show up in
  the ratings. The AP poll has the same problem. The backtest shows ratings
  still move about 6–7 points after week 4 on average.
- **Few games between conferences.** Conferences mostly play themselves, so
  cross-conference strength rests on a small number of non-conference games
  (about 160–175 FBS-vs-FBS games a season). One lopsided September result can
  shift a whole conference.
- **The committee is an approximation.** The simulated committee ranks teams by
  rating, subtracts 8.5 points per loss and adds 0.5 for a conference title.
  Those numbers were fitted to the 2024 and 2025 final rankings, and got 9–10
  of each season's top 12 right. The real committee weighs head-to-head
  results, strength of schedule, "eye test" and much more. Treat playoff odds
  as "what usually happens", not a prediction of the committee's thinking.
- **Injuries aren't modeled**, especially quarterback injuries. A team that
  loses its starting QB keeps its rating until its results drag it down.
- **Simplified tiebreakers.** Conference ties are broken by record among the
  tied teams, then by rating. Real tiebreakers go further (common opponents,
  opponents' records, the ACC's new "body of work" step, the American's
  computer average).
- **Data gaps.**
  - The data source (CollegeFootballData) only serves current SP+ and FPI
    values, so this project saves a weekly snapshot starting with 2026 week 4.
  - The AP data only includes the Top 25 (no "others receiving votes").
  - Committee rankings have no points, so rank *r* is treated as 26 − *r*.
  - The Pac-12's late-season "flex week" games aren't simulated until
    they're scheduled.

## How well does it work?

See [reports/BACKTEST.md](reports/BACKTEST.md). In short, replaying 2024 and
2025 week by week (with Elo and our own margin rating standing in for SP+/FPI,
which have no weekly history):
- the blend picked winners about 69–73% of the time, about as often as Vegas;
- its average miss on the margin was about 1 point worse than Vegas;
- it beat computer ratings alone on games between conferences;
- adding rating uncertainty to the simulation made its playoff odds better
  calibrated at every point checked.

## Run it yourself

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export CFBD_API_KEY=your-key          # free key: https://collegefootballdata.com/key
.venv/bin/python -m src.pipeline      # fetch -> normalize -> rate -> simulate -> site/data.json
python3 -m http.server 8765 --directory site   # then open http://localhost:8765
```

Individual steps:

| Command | What it does |
|---|---|
| `python -m src.fetch` | Download and cache raw data. Finished weeks are never re-downloaded. |
| `python -m src.normalize` | One clean file per week, keyed by team ID |
| `python -m src.rating` | Print the blended ranking |
| `python -m src.sim --seed 42` | Playoff odds, leverage, top matchups |
| `python -m src.backtest` | Re-run the 2024/2025 backtest |
| `python -m pytest -q` | Run the tests |

Every tunable number lives in [config.yaml](config.yaml), with a note on where
it came from.

## Project layout

```
config.yaml            all constants (weights, home field, sigma, committee, playoff rules)
config/team_aliases.yaml   extra team-name spellings for matching
src/fetch.py           API download + cache       data/raw/<season>/week_<n>/
src/normalize.py       clean per-week files       data/processed/<season>/week_<n>.json
src/teams.py           one team ID across all sources
src/rating.py          the blend
src/sim.py             game odds + season simulation
src/backtest.py        historical accuracy and tuning
src/site.py            builds site/data.json
src/pipeline.py        runs all of the above (what the weekly job runs)
site/                  the static page (HTML/CSS/JS, no framework)
.github/workflows/update.yml   Monday job: pipeline -> commit -> deploy to Pages
```

## Automation setup (one time)

1. Add the API key as a repository secret named `CFBD_API_KEY` (Settings → Secrets
   and variables → Actions). It's never stored in the code.
2. Set GitHub Pages to deploy from GitHub Actions (Settings → Pages → Source).
3. The workflow then runs every Monday, and also on demand from the Actions tab
   ("Run workflow").

Data from [CollegeFootballData.com](https://collegefootballdata.com). Not
affiliated with the College Football Playoff, the AP or ESPN.
