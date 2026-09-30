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
function renderGames() {
  const d = state.data;
  $("#games-title").textContent = d.next_week ? `Week ${d.next_week}` : "No games left";
  $("#matchups").innerHTML = d.top_matchups.map((g) =>
    gameCard(g, `<div class="meta">${swingText(g.playoff_swing)}</div>`)).join("")
    || `<p class="note">No upcoming games.</p>`;
  $("#leverage").innerHTML = d.leverage.map((g) =>
    gameCard(g, `<div class="meta">${swingText(g.total_swing)}</div>${moversList(g)}`)).join("")
    || `<p class="note">No upcoming games.</p>`;
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
    <p class="note">*Committee view: the rating used to model playoff selection (uses the committee's own rankings once released).</p>`;
}

// ---------- routing -------------------------------------------------------
function route() {
  const hash = location.hash.slice(1) || "rankings";
  const [view, arg] = hash.split("/");
  const name = ["rankings", "games", "disagree", "about", "team"].includes(view) ? view : "rankings";
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
  $("#subtitle").textContent = `${data.season} · through week ${data.week} · ${data.sims.toLocaleString()} simulated seasons`;
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
  renderDisagree();

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
