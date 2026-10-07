# CFB Blend

A weekly college football ranking that blends computer ratings (SP+ and ESPN's
FPI) with the AP poll, plus 12-team playoff odds from 10,000 simulated seasons.

**Site:** https://kysonallstar-stack.github.io/CFP-FPI-SP--APpoll-Rank-mix/
(updates Sunday 1:00 p.m. Mountain, an hour after the AP poll, and checks every
morning for anything new; takes the offseason off)

## The method, in plain language

**1. The computers.** Four ratings, each converted to a z-score (how many
standard deviations above or below average) and combined:
- **SP+** and **FPI** from ESPN, weight 1.0 each. These are the strongest
  systems, but our data source (CollegeFootballData) can run a week or more
  behind ESPN on them.
- **Elo** (from CollegeFootballData) and **our own margin rating**, weight 0.5
  each. Both are recalculated from every game played, so they are always
  current. The margin rating is a standard "who beat whom by how much" fit,
  with blowouts capped at 28 points; it's used from week 4 on.

So two-thirds of the computer side is ESPN's ratings and one-third always
reflects last weekend. When SP+ or FPI is out of date, the site footer says
which week it's from.

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
  winners). Ties go to the team with the better record against the other tied
  teams, then against common conference opponents, then the higher rating;
- give each Pac-12 team a stand-in game in the conference's late-scheduled
  "flex week" until the real matchups are announced (neutral site, against an
  opponent rated at the Group of 6 median);
- simulate the title games;
- pick the 12-team field using the 2026-27 rules. The four Power 4 champions
  get automatic bids. So does the highest-ranked Group of 6 team, champion or
  not. Notre Dame gets one if it's in the committee's top 12. The rest are
  at-large picks. The top four teams get byes.

Each simulated season also draws how much better or worse each team *really*
is than its current rating, based on how much ratings actually moved in past
seasons. A team that's secretly better wins more of *all* its games, which
keeps the odds from being overconfident.

**7. This week's games.** Every game next week, sortable three ways:
- *Best games*: both teams are good (ranked by the weaker team's rating).
- *Playoff swing*: how much the result changes playoff odds, added up over
  every team it affects. Small changes that could just be random noise from
  the simulation are left out.
- *Closest*: nearest to a coin flip.

**Change from last week.** The rankings show how many places each team moved
(±) and how much its rating changed (Rtg ±). Each team page shows rank and
rating by week, and for finished games how the result compared with what the
model predicted beforehand. Beating expectations is what raises a rating.
If SP+ or FPI hasn't been updated at the source since an earlier week, the
footer says which week the numbers date from; in those weeks, changes come
only from the poll.

**8. What if.** Pick winners of any remaining games and re-run 5,000 seasons
with those results locked in. The simulation runs in your browser
(`site/sim.js`, a copy of the Python simulator; a test checks the two agree).
Each team's page has a "What if they win out?" shortcut.

**Projected bracket.** The Bracket tab shows the field "if the season went as
expected": each conference's most likely champion, then the selection rules
applied to each team's average committee score across the simulations. Every
matchup shows win chances (first round at the higher seed, later rounds
neutral), and each team's title odds are worked out exactly through the
bracket. The What-if tab redraws it with your picks.

**9. Selection Day.** Once every conference title game is final and the
committee's final rankings are out, the site stops simulating and shows the
real field: seeds, byes and first-round matchups, taken from the committee's
ranking and the actual champions. Replaying 2025 this way reproduces the
actual 2025 bracket exactly.

**10. The offseason.** From February through July the weekly job exits
without doing anything. In August it picks up the new season on its own.

## When polls come out on different days

Polls don't arrive on a fixed schedule. The AP is usually Sunday; CFP rankings
come out Tuesday nights in November and on Sunday of Selection Day; either can
be late. So:
- A week's *games* are frozen once they're final, but its *polls* are
  re-downloaded on every run (one API call covers the whole season). A late
  poll is picked up by the next run.
- ESPN publishes SP+ on Sunday mornings (about 10:45 a.m. Eastern) and updates
  FPI daily, but CollegeFootballData picks them up on no fixed schedule. In
  early October 2026 it was more than three days behind. Each time its numbers
  change, the date is logged in `data/raw/<season>/source_updates.json`.
- The job runs Sunday at 1:00 p.m. Mountain, an hour after the AP poll, and
  again every morning at 10:00. If nothing new was downloaded, nothing is
  published and no notification is sent.
- Until a poll is released, the site uses the most recent one and says so in
  the footer, e.g. "CFP rankings is from week 9 (this week's isn't out yet)".
- The latest SP+/FPI snapshot is re-taken on each run until the next week
  finishes, so a mid-week update to those ratings is also picked up.

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
- **Elo and the margin rating favor unbeaten teams with weak schedules** more
  than SP+ does, which lifts some Group of 6 teams. On week-5 games they made
  predictions slightly worse (average miss 12.0 vs 11.7 points) in a week when
  SP+/FPI were fresh; they earn their place in the weeks SP+/FPI are stale.
- **Simplified tiebreakers.** Conference ties are broken by record among the
  tied teams, then record against common opponents, then rating. Real
  tiebreakers go further (opponents' records, the ACC's new "body of work"
  step, the American's computer average).
- **Data gaps.**
  - The data source (CollegeFootballData) only serves current SP+ and FPI
    values, so this project saves a weekly snapshot starting with 2026 week 4.
  - The AP data only includes the Top 25 (no "others receiving votes").
  - Committee rankings have no points, so rank *r* is treated as 26 − *r*.
  - The Pac-12's "flex week" games are stand-ins against a generic opponent
    until the real matchups are scheduled.

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
src/notify.py          phone push notification when the numbers change
site/                  the static page (HTML/CSS/JS, no framework)
.github/workflows/update.yml   Sunday + daily job: pipeline -> commit -> deploy to Pages
site/sim.js            browser copy of the simulator (the What-if tab)
```

## Automation setup (one time)

1. Add the API key as a repository secret named `CFBD_API_KEY` (Settings → Secrets
   and variables → Actions). It's never stored in the code.
2. Set GitHub Pages to deploy from GitHub Actions (Settings → Pages → Source).
3. The workflow then runs every Sunday afternoon and every morning, and also on demand from
   the Actions tab ("Run workflow").

### Phone notifications

After each update that changes the numbers, the workflow sends a push
notification through [ntfy](https://ntfy.sh): the week, the top playoff odds,
the biggest movers, the game of the week, and any poll still being waited on.
Tapping it opens the site. Runs where nothing changed send nothing.

1. Install the free ntfy app (iOS or Android).
2. Subscribe to the topic stored in the `NTFY_TOPIC` repository secret.
   The topic name works like a password, so keep it private.
3. Test it with `NTFY_TOPIC=<topic> python -m src.notify --test`.

Data from [CollegeFootballData.com](https://collegefootballdata.com). Not
affiliated with the College Football Playoff, the AP or ESPN.
