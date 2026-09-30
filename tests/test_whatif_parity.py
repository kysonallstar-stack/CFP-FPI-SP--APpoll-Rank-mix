"""The browser simulator (site/sim.js) must agree with the Python one (src/sim.py).

Both are Monte Carlo with different random number generators, so they're
compared within sampling noise: with 10,000 vs 20,000 sims, a probability near
50% has a standard error of about 0.6 points between the two.
"""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.config import load_config
from src.sim import run

ROOT = Path(__file__).resolve().parent.parent
WEEK = ROOT / "data/processed/2026/week_4.json"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(not NODE or not WEEK.exists(), reason="needs node and cached 2026 data")


def run_js(inputs: dict, tmp_path: Path, sims: int, picks: dict | None = None) -> dict:
    (tmp_path / "in.json").write_text(json.dumps({"inp": inputs, "picks": picks or {}}))
    script = f"""
      const sim = require({json.dumps(str(ROOT / 'site/sim.js'))});
      const {{inp, picks}} = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'in.json'))}));
      const r = sim.simulate(inp, {{sims: {sims}, seed: 7, picks}});
      console.log(JSON.stringify(Object.fromEntries(Object.entries(r).map(([k, v]) => [k, Array.from(v)]))));
    """
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def python_run():
    return run(json.loads(WEEK.read_text()), load_config(), 10000, 42)


def test_js_matches_python(python_run, tmp_path):
    inp = python_run["inputs"]
    js = run_js(inp, tmp_path, 20000)
    py = {t["id"]: t for t in python_run["teams"]}
    for js_key, py_key in [("playoff", "p_playoff"), ("bye", "p_bye"), ("conf", "p_win_conference"),
                           ("title", "p_title_game")]:
        diff = np.array([js[js_key][i] - py[tid][py_key] for i, tid in enumerate(inp["ids"])])
        assert np.abs(diff).max() < 0.035, (js_key, np.abs(diff).max())
        assert np.abs(diff).mean() < 0.006, (js_key, np.abs(diff).mean())
    xw = np.array([js["wins"][i] - py[tid]["expected_wins"] for i, tid in enumerate(inp["ids"])])
    assert np.abs(xw).max() < 0.1


def test_forced_picks_always_happen(python_run, tmp_path):
    inp = python_run["inputs"]
    i = inp["ids"].index(61)     # Georgia: lose every remaining regular-season game
    picks = {}
    for gid, h, a, *_ in inp["games"]:
        if h == i:
            picks[gid] = "away"
        elif a == i:
            picks[gid] = "home"
    js = run_js(inp, tmp_path, 2000, picks)
    assert js["regWins"][i] == inp["w"][i]          # no more regular-season wins, in every sim
    assert js["playoff"][i] < 0.05                   # an 8-loss team doesn't make the playoff


def test_projected_bracket_follows_the_rules(python_run, tmp_path):
    """Projected field from the real sim output: 12 distinct teams, every Power 4
    conference's most likely champion included, title odds summing to 1."""
    inp = python_run["inputs"]
    (tmp_path / "b.json").write_text(json.dumps(inp))
    script = f"""
      const sim = require({json.dumps(str(ROOT / 'site/sim.js'))});
      const inp = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'b.json'))}));
      const field = sim.projectField(inp, inp.projection.score, inp.projection.p_conf);
      const ids = field.map((i) => inp.ids[i]);
      const b = sim.bracket(ids, (id) => inp.rating[inp.ids.indexOf(id)], {{k: inp.k, sigma: inp.sigma, hfa: inp.hfa}});
      console.log(JSON.stringify({{field, byes: b.byes, odds: b.titleOdds.map((x) => x[1]),
        r1: b.rounds[0].games.map((g) => [g.seedA, g.seedB]), qf: b.rounds[1].games.map((g) => g.seedA),
        sfSides: b.rounds[2].games.length}}));
    """
    out = json.loads(subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout)
    field = out["field"]
    assert len(field) == 12 and len(set(field)) == 12
    p_conf = inp["projection"]["p_conf"]
    for conf in inp["playoff"]["power4"]:
        members = [i for i, c in enumerate(inp["conf"]) if c == conf]
        assert max(members, key=lambda i: p_conf[i]) in field
    assert out["r1"] == [[5, 12], [6, 11], [7, 10], [8, 9]]
    assert out["qf"] == [1, 2, 3, 4] and out["sfSides"] == 2
    assert abs(sum(out["odds"]) - 1) < 1e-9 and len(out["odds"]) == 12
