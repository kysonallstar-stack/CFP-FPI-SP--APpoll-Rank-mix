// In-browser copy of src/sim.py's season simulation, for the what-if tool.
// It reads the `whatif` inputs that the pipeline exports (Season.export) and can
// force the result of any game. Kept deliberately parallel to the Python code;
// tests/test_whatif_parity.py checks the two agree.
(function (root) {
  "use strict";

  // Small seeded random number generator (mulberry32) + normal draws (polar method).
  function rng(seed) {
    let a = seed >>> 0;
    const rand = () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    let spare = null;
    const randn = () => {
      if (spare !== null) { const s = spare; spare = null; return s; }
      let u, v, s;
      do { u = rand() * 2 - 1; v = rand() * 2 - 1; s = u * u + v * v; } while (s >= 1 || s === 0);
      const m = Math.sqrt(-2 * Math.log(s) / s);
      spare = v * m;
      return u * m;
    };
    return { rand, randn };
  }

  const FCS = -1, FLEX = -2;
  const pairKey = (a, b) => (a < b ? a * 4096 + b : b * 4096 + a);

  // Order teams by conference win pct; ties: record among tied teams, then record
  // vs common conference opponents, then rating. Same as sim._order.
  function order(members, pct, rating, h2h, pool) {
    pool = pool || members;
    const recordVs = (t, opps) => {
      let w = 0, l = 0;
      for (const o of opps) {
        if (o === t) continue;
        const r = h2h(t, o);
        if (r !== null) { w += r; l += 1 - r; }
      }
      return w + l ? w / (w + l) : 0.5;
    };
    const groups = new Map();
    for (const t of members) {
      const key = Math.round(pct[t] * 1e6);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(t);
    }
    const out = [];
    for (const key of [...groups.keys()].sort((a, b) => b - a)) {
      let g = groups.get(key);
      if (g.length > 1) {
        const common = pool.filter((o) => !g.includes(o) && g.every((t) => h2h(t, o) !== null));
        const k = new Map(g.map((t) => [t, [recordVs(t, g), recordVs(t, common), rating[t]]]));
        g = [...g].sort((a, b) => {
          const x = k.get(a), y = k.get(b);
          return (y[0] - x[0]) || (y[1] - x[1]) || (y[2] - x[2]);
        });
      }
      out.push(...g);
    }
    return out;
  }

  // The 12-team field in seed order. Same as sim.pick_field.
  function pickField(score, champ, conf, nd, p) {
    const T = score.length;
    const ord = Array.from({ length: T }, (_, i) => i).sort((a, b) => (score[b] - score[a]) || (a - b));
    const pos = new Int32Array(T);
    ord.forEach((t, k) => { pos[t] = k; });
    let auto;
    if (p.g6_bid === "top5_champions") {
      auto = ord.filter((t) => champ[t]).slice(0, 5);
    } else {
      auto = ord.filter((t) => champ[t] && p.power4.includes(conf[t]));
      const g6 = ord.find((t) => p.group6.includes(conf[t]) && (p.g6_bid === "highest_ranked" || champ[t]));
      if (g6 !== undefined) auto.push(g6);
    }
    if (nd !== null && nd !== undefined && p.notre_dame_rule && pos[nd] < 12) auto.push(nd);
    const field = [...new Set(auto)];
    for (const t of ord) {
      if (field.length >= p.field_size) break;
      if (!field.includes(t)) field.push(t);
    }
    const isAuto = new Set(auto);
    const inside = field.filter((t) => pos[t] < 12 || !isAuto.has(t)).sort((a, b) => pos[a] - pos[b]);
    const outside = field.filter((t) => pos[t] >= 12 && isAuto.has(t)).sort((a, b) => pos[a] - pos[b]);
    return inside.concat(outside);
  }

  /**
   * Run the season simulation.
   * @param inp   the `whatif` object from data.json
   * @param opts  {sims, seed, picks: {gameId: "home" | "away"}}
   * @returns per-team arrays (same order as inp.ids): playoff, bye, conf, title, wins, regWins
   */
  function simulate(inp, opts) {
    const S = opts.sims || 5000;
    const { rand, randn } = rng(opts.seed || 42);
    const T = inp.ids.length, G = inp.games.length;
    const p = inp.playoff, c = inp.committee;
    const k = inp.k, tau = inp.tau;
    const sigmaGame = Math.sqrt(Math.max(inp.sigma ** 2 - 2 * (k * tau) ** 2, (0.5 * inp.sigma) ** 2));
    const rOf = (i) => (i >= 0 ? inp.rating[i] : i === FCS ? inp.fcs_rating : inp.flex_rating);
    const picks = opts.picks || {};

    // Game arrays + forced results (1 = home wins, 0 = away wins, -1 = simulate)
    const gh = new Int32Array(G), ga = new Int32Array(G), gc = new Uint8Array(G);
    const base = new Float64Array(G), edge = new Float64Array(G), forced = new Int8Array(G).fill(-1);
    const pairCol = new Map();
    inp.games.forEach(([id, h, a, conf, e], i) => {
      gh[i] = h; ga[i] = a; gc[i] = conf; edge[i] = e;
      base[i] = rOf(h) - rOf(a);
      if (picks[id] === "home") forced[i] = 1;
      else if (picks[id] === "away") forced[i] = 0;
      if (conf && h >= 0 && a >= 0) pairCol.set(pairKey(h, a), i);
    });
    const fixed = new Map(inp.h2h.map(([a, b, w]) => [pairKey(a, b), w]));

    const confs = new Map();
    inp.conf.forEach((cf, i) => {
      if (p.no_title_game.includes(cf)) return;
      if (!confs.has(cf)) confs.set(cf, []);
      confs.get(cf).push(i);
    });
    const hostEdge = (cf) => (p.title_game_home_of_higher_seed.includes(cf) ? inp.hfa : 0);
    const curLossFactor = 1 - inp.sel_poll_weight;

    const out = { playoff: new Float64Array(T), bye: new Float64Array(T), conf: new Float64Array(T),
                  title: new Float64Array(T), wins: new Float64Array(T), regWins: new Float64Array(T),
                  score: new Float64Array(T) };
    const off = new Float64Array(T), w = new Float64Array(T), l = new Float64Array(T);
    const cw = new Float64Array(T), cl = new Float64Array(T), futureL = new Float64Array(T);
    const pct = new Float64Array(T), score = new Float64Array(T);
    const hw = new Uint8Array(G), champ = new Uint8Array(T);

    for (let s = 0; s < S; s++) {
      for (let t = 0; t < T; t++) {
        off[t] = tau ? randn() * tau : 0;
        w[t] = inp.w[t]; l[t] = inp.l[t]; cw[t] = inp.cw[t]; cl[t] = inp.cl[t];
        futureL[t] = 0; champ[t] = 0;
      }
      const o = (i) => (i >= 0 ? off[i] : 0);
      for (let g = 0; g < G; g++) {
        const h = gh[g], a = ga[g];
        let won;
        if (forced[g] >= 0) won = forced[g] === 1;
        else won = k * (base[g] + o(h) - o(a)) + edge[g] + randn() * sigmaGame > 0;
        hw[g] = won ? 1 : 0;
        if (h >= 0) { if (won) w[h]++; else { l[h]++; futureL[h]++; } if (gc[g]) { if (won) cw[h]++; else cl[h]++; } }
        if (a >= 0) { if (won) { l[a]++; futureL[a]++; } else w[a]++; if (gc[g]) { if (won) cl[a]++; else cw[a]++; } }
      }
      for (let t = 0; t < T; t++) {
        out.regWins[t] += w[t];
        const n = cw[t] + cl[t];
        pct[t] = n > 0 ? cw[t] / n : 0;
      }
      const h2h = (a, b) => {
        const key = pairKey(a, b);
        if (fixed.has(key)) return fixed.get(key) === a ? 1 : 0;
        if (!pairCol.has(key)) return null;
        const g = pairCol.get(key);
        const winner = hw[g] ? gh[g] : ga[g];
        return winner === a ? 1 : 0;
      };

      for (const [cf, members] of confs) {
        let pair;
        if (p.divisions[cf]) {
          const winners = p.divisions[cf]
            .map((d) => order(members.filter((t) => inp.div[t] === d), pct, inp.rating, h2h, members)[0])
            .filter((t) => t !== undefined);
          pair = order(winners, pct, inp.rating, h2h, members);
        } else {
          pair = order(members, pct, inp.rating, h2h).slice(0, 2);
        }
        if (pair.length < 2) continue;
        const [top, other] = pair;
        const m = k * (inp.rating[top] + off[top] - inp.rating[other] - off[other]) + hostEdge(cf);
        const won = m + randn() * sigmaGame > 0;
        const win = won ? top : other, lose = won ? other : top;
        champ[win] = 1;
        out.title[top]++; out.title[other]++;
        w[win]++; l[lose]++; futureL[lose]++;
      }

      for (let t = 0; t < T; t++) {
        const noise = c.noise_sd ? randn() * c.noise_sd : 0;
        score[t] = inp.sel[t] + off[t] + noise
          - c.loss_penalty * (futureL[t] + curLossFactor * inp.l[t]) + c.champ_bonus * champ[t];
        out.wins[t] += w[t];
        out.conf[t] += champ[t];
        out.score[t] += score[t];
      }
      pickField(score, champ, inp.conf, p.nd, p).forEach((t, i) => {
        out.playoff[t]++;
        if (i < p.byes) out.bye[t]++;
      });
    }
    for (const key of Object.keys(out)) for (let t = 0; t < T; t++) out[key][t] /= S;
    return out;
  }

  // ---- projected bracket ---------------------------------------------------

  // Normal CDF via erf (Abramowitz & Stegun 7.1.26, error < 1.5e-7).
  function erf(x) {
    const sgn = x < 0 ? -1 : 1;
    x = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * x);
    const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
    return sgn * y;
  }
  const winProb = (margin, sigma) => 0.5 * (1 + erf(margin / (sigma * Math.SQRT2)));

  /**
   * The projected field (team indices in seed order): each conference's most
   * likely champion, then the normal selection rules applied to each team's
   * average committee score across the simulated seasons.
   */
  function projectField(inp, score, pConf) {
    const T = inp.ids.length, champ = new Uint8Array(T), best = new Map();
    for (let t = 0; t < T; t++) {
      const cf = inp.conf[t];
      if (inp.playoff.no_title_game.includes(cf)) continue;
      const b = best.get(cf);
      if (b === undefined || pConf[t] > pConf[b] || (pConf[t] === pConf[b] && score[t] > score[b])) best.set(cf, t);
    }
    for (const t of best.values()) champ[t] = 1;
    return pickField(score, champ, inp.conf, inp.playoff.nd, inp.playoff);
  }

  /**
   * Bracket for a 12-team field. seeds: team ids in seed order. rating(id) gives
   * the predictive rating; params {k, sigma, hfa}. First round is at the higher
   * seed; later rounds are neutral. No re-seeding: 1 vs 8/9, 2 vs 7/10, 3 vs
   * 6/11, 4 vs 5/12; semifinals pair the 1/4 side and the 2/3 side.
   * Returns rounds with each game's favorite, plus exact title odds per team
   * (probabilities pushed through the bracket, no simulation needed).
   */
  function bracket(seeds, rating, params) {
    const seedOf = new Map(seeds.map((id, i) => [id, i + 1]));
    const p = (a, b, homeA) => winProb(params.k * (rating(a) - rating(b)) + (homeA ? params.hfa : 0), params.sigma);
    // Combine two "who could be here" distributions into who wins this game.
    const play = (A, B, homeA) => {
      const out = new Map();
      for (const [a, pa] of A) for (const [b, pb] of B) {
        const q = p(a, b, homeA);
        out.set(a, (out.get(a) || 0) + pa * pb * q);
        out.set(b, (out.get(b) || 0) + pa * pb * (1 - q));
      }
      return out;
    };
    const one = (seed) => new Map([[seeds[seed - 1], 1]]);
    const fav = (dist) => [...dist].sort((x, y) => y[1] - x[1])[0][0];
    const game = (A, B, homeA, label) => {
      const a = fav(A), b = fav(B);
      const pa = p(a, b, homeA);
      return { label, a, b, seedA: seedOf.get(a), seedB: seedOf.get(b), pA: pa, winner: pa >= 0.5 ? a : b,
               dist: play(A, B, homeA) };
    };
    const r1 = [[5, 12], [6, 11], [7, 10], [8, 9]].map(([h, l]) => game(one(h), one(l), true, `${h} vs ${l}`));
    const w = (g) => g.dist;
    const qf = [[1, r1[3]], [2, r1[2]], [3, r1[1]], [4, r1[0]]].map(([s, g]) => game(one(s), w(g), false, `${s} vs ${g.label} winner`));
    const sf = [game(w(qf[0]), w(qf[3]), false, "1/4 side"), game(w(qf[1]), w(qf[2]), false, "2/3 side")];
    const fin = game(w(sf[0]), w(sf[1]), false, "Championship");
    return {
      byes: seeds.slice(0, 4),
      rounds: [{ name: "First round", games: r1 }, { name: "Quarterfinals", games: qf },
               { name: "Semifinals", games: sf }, { name: "Championship", games: [fin] }],
      titleOdds: [...fin.dist].sort((x, y) => y[1] - x[1]),
    };
  }

  const api = { simulate, order, pickField, projectField, bracket, winProb };
  root.CFBSim = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof self !== "undefined" ? self : globalThis);
