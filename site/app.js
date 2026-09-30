// CFB Blend front end. Loads data.json once, then renders views from the URL hash:
// #rankings, #games, #disagree, #about, #team/<id>
"use strict";

const state = { data: null, byId: new Map(), sort: { key: "rank", asc: true } };
const $ = (sel) => document.querySelector(sel);

// ---------- formatting ----------------------------------------------------
function pct(p) {
  if (p == null) return "–";
  if (p === 0) return "–";
  if (p < 0.005) return "<1%";
  if (p > 0.995 && p < 1) return ">99%";
  return Math.round(p * 100) + "%";
}
const dash = (v) => (v == null ? "–" : v);
const confName = (c) => (c === "FBS Independents" ? "Independent" : c);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rec = (t) => `${t.w}-${t.l}` + (t.cw + t.cl ? ` (${t.cw}-${t.cl})` : "");
const rankTag = (id) => { const t = state.byId.get(id); return t ? `#${t.rank}` : "FCS"; };
function teamLink(id, name) {
  return state.byId.has(id) ? `<a href="#team/${id}">${esc(name)}</a>` : esc(name);
}

// ---------- rankings ------------------------------------------------------
function renderRankings() {
  const q = $("#search").value.trim().toLowerCase();
  const conf = $("#conf").value;
  const showAll = $("#all").checked || q || conf;
  const { key, asc } = state.sort;

  let rows = state.data.teams.filter((t) =>
    (!q || t.name.toLowerCase().includes(q) || (t.abbr || "").toLowerCase() === q) &&
    (!conf || t.conf === conf));
  if (!showAll) rows = rows.filter((t) => t.rank <= 25);

  const val = (t) => (key === "name" ? t.name : t[key]);
  rows.sort((a, b) => {
    const x = val(a), y = val(b);
    if (x == null && y == null) return a.rank - b.rank;
    if (x == null) return 1;                  // blanks (e.g. unranked in AP) always last
    if (y == null) return -1;
    const c = typeof x === "string" ? x.localeCompare(y) : x - y;
    return (asc ? c : -c) || a.rank - b.rank;
  });

  const hasCfp = state.data.teams.some((t) => t.cfp != null);
  document.querySelectorAll(".cfp-col").forEach((el) => (el.hidden = !hasCfp));
  document.querySelectorAll("#rank-table th").forEach((th) => {
    th.classList.toggle("sorted", th.dataset.sort === key);
    th.classList.toggle("asc", th.dataset.sort === key && asc);
  });

  $("#rank-table tbody").innerHTML = rows.map((t) => `
    <tr data-id="${t.id}" tabindex="0">
      <td class="num">${t.rank}</td>
      <td class="team-col"><div class="teamcell">
        <span class="swatch" style="background:${esc(t.color || "")}"></span>
        <span><span class="tname">${esc(t.name)}</span><span class="trec">${rec(t)} · ${esc(confName(t.conf))}</span></span>
      </div></td>
      <td class="num ${t.p_playoff >= 0.5 ? "pct-hi" : ""}">${pct(t.p_playoff)}</td>
      <td class="num">${pct(t.p_bye)}</td>
      <td class="num">${pct(t.p_conf)}</td>
      <td class="num">${t.rating.toFixed(1)}</td>
      <td class="num ${t.ap ? "" : "dim"}">${dash(t.ap)}</td>
      <td class="num cfp-col" ${hasCfp ? "" : "hidden"}>${dash(t.cfp)}</td>
      <td class="num">${dash(t.sp)}</td>
      <td class="num">${dash(t.fpi)}</td>
      <td class="num">${t.xw.toFixed(1)}</td>
    </tr>`).join("") || `<tr><td colspan="11" class="dim">No teams match.</td></tr>`;
}

// ---------- this week -----------------------------------------------------
function gameCard(g, extra) {
  const hp = g.home_win_prob;
  const fav = g.predicted_margin >= 0 ? g.home : g.away;
  const where = g.neutral ? "neutral site" : `at ${esc(g.home)}`;
  const aid = state.byId.has(g.away_id) ? g.away_id : null, hid = state.byId.has(g.home_id) ? g.home_id : null;
  return `<div class="card">
    <div class="matchup">
      <span class="who">${teamLink(aid, g.away)} <span class="rk">${aid ? rankTag(aid) : ""}</span></span>
      <span class="pr">${pct(1 - hp)}</span>
      <span class="who">${g.neutral ? "vs" : "@"} ${teamLink(hid, g.home)} <span class="rk">${hid ? rankTag(hid) : ""}</span></span>
      <span class="pr">${pct(hp)}</span>
    </div>
    <div class="probbar" aria-hidden="true"><span style="width:${Math.round((1 - hp) * 100)}%"></span></div>
    <div class="meta">${esc(fav)} by ${Math.abs(g.predicted_margin).toFixed(1)} · ${where}${g.start ? " · " + fmtDate(g.start) : ""}</div>
    ${extra || ""}
  </div>`;
}
function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}
function moversList(g) {
  if (!g.movers || !g.movers.length) return "";
  return `<ul class="movers">${g.movers.slice(0, 4).map((m) => {
    const playing = m.team === g.home || m.team === g.away;
    let win = m.if_home_wins, loss = m.if_home_loses, label;
    if (playing) {
      if (m.team === g.away) [win, loss] = [loss, win];
      label = `${pct(win)} with a win, ${pct(loss)} with a loss`;
    } else {
      label = `${pct(m.if_home_wins)} if ${esc(g.home)} wins, ${pct(m.if_home_loses)} if not`;
    }
    return `<li><span>${esc(m.team)}</span><span class="delta">${label}</span></li>`;
  }).join("")}</ul>`;
}
// Sum over all teams of |P(playoff | home wins) - P(playoff | home loses)|, in percentage points.
const swingText = (s) => s >= 0.005
  ? `Moves playoff odds ${Math.round(s * 100)} points in total (all teams combined)`
  : "Little effect on playoff odds";

const GAME_SORTS = {
  quality:   { note: "Best games on paper: ranked by the weaker team's rating, so both teams are good.",
               key: (g) => -g.quality },
  swing:     { note: "Games whose result moves playoff odds the most, added up over every team affected.",
               key: (g) => -g.swing },
  closeness: { note: "Closest to a coin flip first.",
               key: (g) => g.closeness },
};

function renderGames() {
  const d = state.data;
  const sort = state.gameSort || "quality";
  $("#games-title").textContent = d.next_week ? `Week ${d.next_week}` : "No games left";
  $("#games-note").textContent = GAME_SORTS[sort].note;
  document.querySelectorAll(".seg button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.sort === sort)));

  const games = [...(d.week_games || [])].sort((a, b) =>
    GAME_SORTS[sort].key(a) - GAME_SORTS[sort].key(b) || b.quality - a.quality);
  const shown = state.showAllGames ? games : games.slice(0, 10);
  $("#week-games").innerHTML = shown.map((g) => {
    const tag = sort === "swing" ? `${Math.round(g.swing * 100)} pt swing`
      : sort === "closeness" ? (g.closeness <= 0.03 ? "Toss-up"
        : `${pct(Math.max(g.home_win_prob, 1 - g.home_win_prob))} favorite`) : "";
    const extra = `<div class="meta">${swingText(g.swing)}${tag ? `<span class="tag">${esc(tag)}</span>` : ""}</div>` +
      (sort === "swing" ? moversList(g) : "");
    return gameCard(g, extra);
  }).join("") || `<p class="note">No upcoming games.</p>`;

  const more = $("#more-games");
  more.hidden = games.length <= 10;
  more.textContent = state.showAllGames ? "Show top 10" : `Show all ${games.length} games`;
}

// ---------- disagreements ---------------------------------------------------
function renderDisagree() {
  const d = state.data;
  const human = d.selection_poll === "cfp" ? "CFP" : "AP";
  $("#disagree-note").textContent =
    `Computer rank (SP+ and FPI combined) vs. the ${human === "CFP" ? "committee's ranking" : "AP poll"}. ` +
    `Unranked counts as 26th, so a team the computers rank highly but the poll leaves out shows up here.`;
  const item = (id) => {
    const t = state.byId.get(id);
    const h = human === "CFP" ? t.cfp : t.ap;
    return `<li data-id="${id}"><span><b>${esc(t.name)}</b> <span class="dim">${rec(t)}</span></span>
      <span class="ranks">Computers #${t.metrics} · ${human} ${h ? "#" + h : "unranked"}</span></li>`;
  };
  $("#poll-higher").innerHTML = d.disagree.poll_higher.map(item).join("") || "<li>None</li>";
  $("#metrics-higher").innerHTML = d.disagree.metrics_higher.map(item).join("") || "<li>None</li>";
}

// ---------- team detail ---------------------------------------------------
function renderTeam(id) {
  const t = state.byId.get(id);
  const el = $("#view-team");
  if (!t) { el.innerHTML = `<p>Team not found. <a href="#rankings">Back to rankings</a></p>`; return; }
  const games = state.data.games.filter((g) => g.h === id || g.a === id);
  const hist = t.hist.map(([wk, rk]) => `<span>Wk ${wk}: #${rk}</span>`).join("");
  const sched = games.map((g) => {
    const home = g.h === id;
    const opp = home ? g.an : g.hn, oppId = home ? g.a : g.h;
    const site = g.n ? "vs" : home ? "vs" : "@";
    let res;
    if (g.hp != null) {
      const us = home ? g.hp : g.apts, them = home ? g.apts : g.hp;
      res = `<span class="${us > them ? "win" : "loss"}">${us > them ? "W" : "L"} ${us}-${them}</span>`;
    } else {
      const p = home ? g.p : 1 - g.p;
      res = `<span title="Win probability">${pct(p)}</span>`;
    }
    return `<li><span class="wk">Wk ${g.wk}</span>
      <span>${site} ${teamLink(oppId, opp)} ${state.byId.has(oppId) ? `<span class="dim">${rankTag(oppId)}</span>` : ""}${g.n ? ' <span class="dim">(N)</span>' : ""}</span>
      <span class="res">${res}</span></li>`;
  }).join("");
  const cfp = t.cfp ? `<div class="stat"><b>#${t.cfp}</b><span>CFP</span></div>` : "";
  el.innerHTML = `
    <a class="back" href="#rankings">← Rankings</a>
    <div class="team-head" style="border-left-color:${esc(t.color || "")}">
      <h2>${esc(t.name)}</h2>
      <div class="dim">#${t.rank} · ${rec(t)} · ${esc(confName(t.conf))} · rating ${t.rating.toFixed(1)}</div>
    </div>
    <div class="stats">
      <div class="stat"><b>${pct(t.p_playoff)}</b><span>Playoff</span></div>
      <div class="stat"><b>${pct(t.p_bye)}</b><span>Bye</span></div>
      <div class="stat"><b>${pct(t.p_conf)}</b><span>Win conf.</span></div>
      <div class="stat"><b>${pct(t.p_title)}</b><span>Title game</span></div>
      <div class="stat"><b>${t.xw.toFixed(1)}</b><span>Exp. wins</span></div>
      <div class="stat"><b>#${t.sel_rank}</b><span>Committee view*</span></div>
    </div>
    <h3>Where each source ranks them</h3>
    <div class="stats">
      <div class="stat"><b>#${t.rank}</b><span>Blend</span></div>
      <div class="stat"><b>${t.ap ? "#" + t.ap : "–"}</b><span>AP</span></div>
      ${cfp}
      <div class="stat"><b>${t.sp ? "#" + t.sp : "–"}</b><span>SP+</span></div>
      <div class="stat"><b>${t.fpi ? "#" + t.fpi : "–"}</b><span>FPI</span></div>
      <div class="stat"><b>#${t.metrics}</b><span>Computers</span></div>
    </div>
    <h3>Blend rank by week</h3>
    <div class="history">${hist}</div>
    <p class="note">Weeks without SP+/FPI snapshots use Elo for the computer side.</p>
    <h3>Schedule</h3>
    <ul class="sched">${sched}</ul>
    ${state.data.whatif && games.some((g) => g.hp == null)
      ? `<button type="button" class="more" id="win-out">What if ${esc(t.name)} wins out?</button>` : ""}
    <p class="note">*Committee view: the rating used to model playoff selection (uses the committee's own rankings once released).</p>`;
  const btn = $("#win-out");
  if (btn) btn.addEventListener("click", () => winOut(id));
}

// ---------- what if ------------------------------------------------------
// Picks live in state.picks ({gameId: "home" | "away"}) and in localStorage so
// they survive a reload. The simulation runs in a Web Worker (whatif-worker.js)
// using site/sim.js, the browser copy of the Python simulator.
const WI_SIMS = 5000, WI_SEED = 2026;
const wi = { worker: null, pending: new Map(), nextId: 1, baseline: null };

function loadPicks() {
  try { return JSON.parse(localStorage.getItem("cfb-picks-" + state.data.season + "-" + state.data.week)) || {}; }
  catch { return {}; }
}
function savePicks() {
  try { localStorage.setItem("cfb-picks-" + state.data.season + "-" + state.data.week, JSON.stringify(state.picks)); }
  catch { /* private browsing etc.: picks just won't persist */ }
}

function runSim(picks) {
  const opts = { sims: WI_SIMS, seed: WI_SEED, picks };
  if (!wi.worker && typeof Worker !== "undefined") {
    try {
      wi.worker = new Worker("whatif-worker.js");
      wi.worker.onmessage = (e) => { wi.pending.get(e.data.id)(e.data.result); wi.pending.delete(e.data.id); };
    } catch { wi.worker = false; }
  }
  if (!wi.worker) {   // no worker support: run on the main thread
    return new Promise((res) => setTimeout(() => res(CFBSim.simulate(state.data.whatif, opts)), 30));
  }
  const id = wi.nextId++;
  return new Promise((res) => { wi.pending.set(id, res); wi.worker.postMessage({ id, inp: state.data.whatif, opts }); });
}

function remainingGames() {
  const ids = new Set(state.data.whatif.games.map((g) => g[0]));
  return state.data.games.filter((g) => g.hp == null && ids.has(g.id));
}

function renderWhatifGames() {
  const wk = $("#wi-week").value;
  const q = $("#wi-search").value.trim().toLowerCase();
  const games = remainingGames().filter((g) =>
    (wk === "all" || String(g.wk) === wk) &&
    (!q || g.hn.toLowerCase().includes(q) || g.an.toLowerCase().includes(q)));
  const side = (g, which) => {
    const id = which === "home" ? g.h : g.a;
    const name = which === "home" ? g.hn : g.an;
    const p = which === "home" ? g.p : 1 - g.p;
    const on = state.picks[g.id] === which;
    return `<button type="button" class="pick" data-g="${g.id}" data-side="${which}" aria-pressed="${on}">
      <span class="pick-name">${esc(name)}${state.byId.has(id) ? ` <span class="rk">${rankTag(id)}</span>` : ""}</span>
      <span class="pick-p">${pct(p)}</span></button>`;
  };
  $("#wi-games").innerHTML = games.map((g) => `
    <div class="wi-game">
      <div class="wi-meta">Wk ${g.wk}${g.n ? " · neutral" : ""}${g.ph ? " · placeholder" : ""}</div>
      <div class="wi-sides">${side(g, "away")}<span class="at">${g.n ? "vs" : "@"}</span>${side(g, "home")}</div>
    </div>`).join("") || `<p class="note">No games match.</p>`;
  const n = Object.keys(state.picks).length;
  $("#wi-count").textContent = n ? `${n} pick${n > 1 ? "s" : ""}` : "No picks yet";
  $("#wi-run").disabled = !n;
}

async function simulateWhatif() {
  const box = $("#wi-results");
  const picks = { ...state.picks };
  if (!Object.keys(picks).length) return;
  box.innerHTML = `<p class="note">Simulating ${WI_SIMS.toLocaleString()} seasons…</p>`;
  $("#wi-run").disabled = true;
  // The baseline is run by the same browser simulator, so the comparison isn't
  // mixing two different sets of random draws.
  if (!wi.baseline) wi.baseline = await runSim({});
  const r = await runSim(picks);
  $("#wi-run").disabled = false;
  const ids = state.data.whatif.ids;
  const rows = ids.map((id, i) => ({ t: state.byId.get(id), i,
    before: wi.baseline.playoff[i], after: r.playoff[i], bye: r.bye[i], conf: r.conf[i], xw: r.wins[i] }))
    .filter((x) => x.t && Math.abs(x.after - x.before) >= 0.005)
    .sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before))
    .slice(0, 25);
  const sign = (d) => (d > 0 ? "+" : "") + Math.round(d * 100);
  box.innerHTML = rows.length ? `
    <h3>How your picks change playoff odds</h3>
    <div class="table-scroll wi-table"><table>
      <thead><tr><th class="team-col">Team</th><th class="num">Before</th><th class="num">After</th>
        <th class="num">Change</th><th class="num">Bye</th><th class="num">Conf</th><th class="num">xW</th></tr></thead>
      <tbody>${rows.map((x) => `<tr data-id="${x.t.id}">
        <td class="team-col"><div class="teamcell"><span class="swatch" style="background:${esc(x.t.color || "")}"></span>
          <span class="tname">${esc(x.t.name)}</span></div></td>
        <td class="num">${pct(x.before)}</td><td class="num pct-hi">${pct(x.after)}</td>
        <td class="num ${x.after > x.before ? "win" : "loss"}">${sign(x.after - x.before)}</td>
        <td class="num">${pct(x.bye)}</td><td class="num">${pct(x.conf)}</td><td class="num">${x.xw.toFixed(1)}</td>
      </tr>`).join("")}</tbody></table></div>
    <p class="note">Change is in percentage points of playoff odds. Both columns come from ${WI_SIMS.toLocaleString()}
      in-browser simulations, so differences under ~2 points are noise.</p>`
    : `<p class="note">Your picks barely move anyone's playoff odds (all changes under 1 point).</p>`;
}

function initWhatif() {
  const wf = state.data.whatif;
  if (!wf) {
    $("#wi-body").innerHTML = `<p>The playoff field is set, so there's nothing left to simulate.
      See <a href="#games">the bracket</a>.</p>`;
    return;
  }
  $("#wi-sims").textContent = WI_SIMS.toLocaleString();
  state.picks = loadPicks();
  const weeks = [...new Set(remainingGames().map((g) => g.wk))].sort((a, b) => a - b);
  $("#wi-week").innerHTML = weeks.map((w) => `<option value="${w}">Week ${w}</option>`).join("") +
    `<option value="all">All weeks</option>`;
  $("#wi-week").addEventListener("change", renderWhatifGames);
  $("#wi-search").addEventListener("input", renderWhatifGames);
  $("#wi-games").addEventListener("click", (e) => {
    const b = e.target.closest(".pick");
    if (!b) return;
    const g = Number(b.dataset.g);
    if (state.picks[g] === b.dataset.side) delete state.picks[g];
    else state.picks[g] = b.dataset.side;
    savePicks();
    renderWhatifGames();
  });
  $("#wi-reset").addEventListener("click", () => {
    state.picks = {};
    savePicks();
    $("#wi-results").innerHTML = "";
    renderWhatifGames();
  });
  $("#wi-run").addEventListener("click", simulateWhatif);
  $("#wi-results").addEventListener("click", (e) => {
    const row = e.target.closest("[data-id]");
    if (row) location.hash = "team/" + row.dataset.id;
  });
  renderWhatifGames();
}

// "What if they win out?" from a team page: pick every remaining game for them.
function winOut(id) {
  for (const g of remainingGames()) {
    if (g.h === id) state.picks[g.id] = "home";
    else if (g.a === id) state.picks[g.id] = "away";
  }
  savePicks();
  $("#wi-week").value = "all";
  $("#wi-search").value = state.byId.get(id).name;
  location.hash = "whatif";
  renderWhatifGames();
  simulateWhatif();
}

// ---------- Selection Day ---------------------------------------------------
function renderFinalField() {
  const f = state.data.final_field;
  const el = $("#final-field");
  if (!f) { el.hidden = true; return; }
  el.hidden = false;
  const seed = (x) => `<li data-id="${x.id}"><span><b>${x.seed}.</b> ${esc(x.team)}${x.champion ? ' <span class="tag">Champion</span>' : ""}</span>
    <span class="ranks">${x.cfp_rank ? "CFP #" + x.cfp_rank : "unranked"}${x.bye ? " · bye" : ""}</span></li>`;
  el.innerHTML = `
    <h2>The playoff field</h2>
    <p class="note">Set by the committee's final rankings. Top four seeds get first-round byes; no re-seeding.</p>
    <ol class="dlist">${f.seeds.map(seed).join("")}</ol>
    <h3>First round (at the higher seed)</h3>
    <div class="cards">${f.first_round.map(([a, b, sa, sb]) =>
      `<div class="card"><b>#${sb} ${esc(b)}</b> at <b>#${sa} ${esc(a)}</b></div>`).join("")}</div>
    <h3>Quarterfinals</h3>
    <div class="cards">${f.quarterfinals.map(([a, opp, s]) =>
      `<div class="card"><b>#${s} ${esc(a)}</b> vs winner of ${esc(opp)}</div>`).join("")}</div>`;
}

// ---------- routing -------------------------------------------------------
function route() {
  const hash = location.hash.slice(1) || "rankings";
  const [view, arg] = hash.split("/");
  const name = ["rankings", "games", "whatif", "disagree", "about", "team"].includes(view) ? view : "rankings";
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== "view-" + name));
  document.querySelectorAll(".tabs a").forEach((a) =>
    a.classList.toggle("active", a.dataset.tab === name || (name === "team" && a.dataset.tab === "rankings")));
  if (name === "team") renderTeam(Number(arg));
  window.scrollTo(0, 0);
}

function init(data) {
  state.data = data;
  data.teams.forEach((t) => state.byId.set(t.id, t));

  const updated = new Date(data.generated_at);
  $("#subtitle").textContent = data.final_field
    ? `${data.season} · the playoff field is set`
    : `${data.season} · through week ${data.week} · ${data.sims.toLocaleString()} simulated seasons`;
  const label = { ap: "AP poll", cfp: "CFP rankings", sp: "SP+", fpi: "FPI", elo: "Elo" };
  const stale = Object.entries(data.stale || {})
    .map(([k, wk]) => `${label[k] || k} is from week ${wk} (this week's isn't out yet)`);
  $("#updated").textContent = `Last updated ${updated.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}` +
    (stale.length ? ` · ${stale.join(" · ")}` : "") +
    (data.missing.length ? ` · missing: ${data.missing.join(", ")}` : "");
  $("#about-sims").textContent = data.sims.toLocaleString();
  $("#about-hfa").textContent = data.settings.hfa;

  const confs = [...new Set(data.teams.map((t) => t.conf))].sort();
  $("#conf").insertAdjacentHTML("beforeend", confs.map((c) => `<option value="${esc(c)}">${esc(confName(c))}</option>`).join(""));

  renderRankings();
  renderGames();
  renderFinalField();
  renderDisagree();
  initWhatif();

  document.querySelectorAll(".seg button").forEach((b) => b.addEventListener("click", () => {
    state.gameSort = b.dataset.sort;
    renderGames();
  }));
  $("#more-games").addEventListener("click", () => { state.showAllGames = !state.showAllGames; renderGames(); });

  $("#search").addEventListener("input", renderRankings);
  $("#conf").addEventListener("change", renderRankings);
  $("#all").addEventListener("change", renderRankings);
  document.querySelectorAll("#rank-table th").forEach((th) => th.addEventListener("click", () => {
    const key = th.dataset.sort;
    // Rank and name sort ascending first; odds and ratings sort biggest first.
    const ascFirst = ["rank", "name", "ap", "cfp", "sp", "fpi"].includes(key);
    state.sort = state.sort.key === key ? { key, asc: !state.sort.asc } : { key, asc: ascFirst };
    renderRankings();
  }));
  const openRow = (e) => {
    const row = e.target.closest("[data-id]");
    if (row && !e.target.closest("a")) location.hash = "team/" + row.dataset.id;
  };
  $("#rank-table tbody").addEventListener("click", openRow);
  $("#rank-table tbody").addEventListener("keydown", (e) => { if (e.key === "Enter") openRow(e); });
  $("#view-disagree").addEventListener("click", openRow);
  $("#final-field").addEventListener("click", openRow);

  window.addEventListener("hashchange", route);
  route();
}

fetch("data.json", { cache: "no-cache" })
  .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
  .then(init)
  .catch((err) => {
    $("#subtitle").textContent = "Couldn't load data.";
    document.querySelector("main").innerHTML = `<p>Couldn't load the rankings (${esc(err.message)}). Try refreshing.</p>`;
  });
