"use strict";
/* BCLC Lottery Planner front end. The server does all validation and calculation. */

const $ = (id) => document.getElementById(id);
const state = { rules: null, draws: null, lastPlan: null, favourites: new Set(), precheckTimer: null };

// ---------- small helpers ----------
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function svgEl(tag, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  return node;
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const store = {
  get(key) { try { return localStorage.getItem("bclc." + key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem("bclc." + key, value); } catch { /* private mode */ } },
};
const game = () => document.querySelector('input[name="game"]:checked').value;
const gameRules = () => (state.rules ? state.rules.games[game()] : null);
const budgetText = () => $("budget").value.trim().replace(/^\$/, "");
const fmtInt = (n) => Number(n).toLocaleString("en-CA", { maximumFractionDigits: 0 });

function oneIn(record) {
  if (!record) return "unknown";
  if (record.numerator === "0") return "impossible (0)";
  const v = record.one_in;
  return "1 in " + (v >= 100 ? fmtInt(v) : v.toLocaleString("en-CA", { maximumFractionDigits: 2 }));
}
function pct(record) {
  if (!record) return "";
  const p = record.percent;
  if (p === 0) return "0%";
  return (p < 0.001 ? p.toExponential(2) : Number(p.toPrecision(3)).toString()) + "%";
}
function exactTitle(record) {
  if (!record) return null;
  const text = record.numerator + "/" + record.denominator;
  return text.length < 60 ? "Exact: " + text : "Exact fraction available from the server";
}
function showError(id, message) { $(id).textContent = message || ""; }
function clear(id) { const node = $(id); while (node.firstChild) node.firstChild.remove(); return node; }

async function api(path, options) {
  const opts = Object.assign({ credentials: "same-origin", headers: {} }, options || {});
  if (opts.json !== undefined) {
    opts.body = JSON.stringify(opts.json);
    opts.headers["Content-Type"] = "application/json";
    delete opts.json;
  }
  let response;
  try {
    response = await fetch(path, opts);
  } catch {
    throw new Error("Could not reach the server. Check your connection and try again.");
  }
  const type = response.headers.get("content-type") || "";
  const data = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : `Request failed (HTTP ${response.status}). Please try again.`);
  }
  return data;
}

// ---------- tabs, game and budget ----------
function showTab(name) {
  for (const tab of document.querySelectorAll(".tab")) tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  for (const panel of document.querySelectorAll(".panel")) panel.hidden = panel.dataset.panel !== name;
  store.set("tab", name);
  if (name === "ticket") loadResults();
}

function budgetCents() {
  const text = budgetText();
  const match = /^(\d{1,7})(?:\.(\d{1,2}))?$/.exec(text);
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
}

function updateBudgetNote() {
  const rules = gameRules();
  const note = $("budget-note");
  if (!rules) return;
  const cents = budgetCents();
  if (cents === null) {
    note.textContent = "Enter an amount like 3 or 30.00 (at most two decimals).";
    return;
  }
  const plays = Math.floor(cents / rules.price_cents);
  const spent = plays * rules.price_cents;
  let text = `${rules.price} per play → ${plays} play${plays === 1 ? "" : "s"} ($${(spent / 100).toFixed(2)}), ` +
    `$${((cents - spent) / 100).toFixed(2)} left unspent.`;
  if (rules.key === "max") text += ` You choose ${plays} line${plays === 1 ? "" : "s"}; ${plays * 3} more are Quick Picks issued at purchase.`;
  text += " The server re-checks this exactly.";
  note.textContent = text;
}

function onGameChange() {
  store.set("game", game());
  renderRules();
  renderNumberGrid();
  populateDraws();
  renderUpcoming();
  updateBudgetNote();
  refreshSession();
  $("history-template").href = "/api/templates/" + (game() === "649" ? "history_649" : "history_max");
  prepareTicketForm();
}

// ---------- ticket checker ----------
const ticketState = { game: null, draws: [] };

function prepareTicketForm() {
  const is649 = game() === "649";
  $("ticket-gold-row").hidden = !is649;
  $("ticket-lines").placeholder = is649 ? "1 14 30 32 43 45" : "3 12 19 28 35 42 49\n8 23 32 39 43 44 50";
  $("ticket-hint").textContent = is649
    ? "Six numbers from 1 to 49 per line. Spaces or commas are fine."
    : "Seven numbers from 1 to 52 per line. Enter all four lines of each $6 play, including the three Quick Picks.";
  clear("ticket-results");
  showError("ticket-error", "");
  if (!$("tab-ticket").hidden) loadResults();
}

function balls(numbers, hits, bonus) {
  return el("span", { class: "balls" }, numbers.map((n) =>
    el("span", { class: "ball" + (hits && hits.includes(n) ? " hit" : "") + (n === bonus ? " bonus" : "") },
      String(n).padStart(2, "0"))));
}

async function loadResults() {
  const key = game();
  if (ticketState.game === key && ticketState.draws.length) return;
  const select = $("ticket-draw");
  while (select.firstChild) select.firstChild.remove();
  select.append(el("option", { value: "" }, "Loading draws…"));
  showError("ticket-error", "");
  try {
    const data = await api(`/api/results/${key}`);
    if (game() !== key) return;   // the game changed while loading
    ticketState.game = key;
    ticketState.draws = data.draws;
    while (select.firstChild) select.firstChild.remove();
    for (const draw of data.draws) {
      select.append(el("option", { value: draw.date },
        `${drawDay(draw.date, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}: ` +
        `${draw.numbers.join(" ")} + bonus ${draw.bonus}`));
    }
    $("ticket-source").textContent = "Official results: " + data.status;
  } catch (error) {
    ticketState.draws = [];
    while (select.firstChild) select.firstChild.remove();
    select.append(el("option", { value: "" }, "No results available"));
    showError("ticket-error", error.message);
  }
}

function renderTicket(report) {
  const root = clear("ticket-results");
  root.append(el("div", { class: report.any_win ? "banner good" : "banner info" },
    report.any_win ? "Prize found! Confirm it with PlayNow or a BCLC retailer." : "No prize on these lines for this draw."));
  if (report.wins.length) root.append(el("ul", null, report.wins.map((w) => el("li", null, w))));
  root.append(el("p", null, `Winning numbers for ${drawDay(report.date, { weekday: "long", month: "long", day: "numeric" })}: `,
    balls(report.winning_numbers, report.winning_numbers, null), "  bonus ", balls([report.bonus], [], report.bonus)));
  const body = el("tbody");
  report.lines.forEach((row, i) => {
    const result = row.tier ? `${row.tier} – ${row.prize}` : "No prize";
    const extras = row.extras.map((x) => `${x.draw} exact match – ${x.prize}`);
    body.append(el("tr", null, el("td", { class: "num" }, i + 1),
      el("td", null, balls(row.line, row.matched, row.bonus_on_line ? report.bonus : null)),
      el("td", { class: "num" }, row.matched_count + (row.bonus_on_line ? " + bonus" : "")),
      el("td", { class: row.tier || extras.length ? "ok" : "" }, [result, ...extras].join("; "))));
  });
  root.append(el("div", { class: "table-wrap" }, el("table", null, el("thead", null, el("tr", null,
    el("th", { class: "num" }, "Line"), el("th", null, "Your numbers (matches highlighted)"),
    el("th", { class: "num" }, "Matched"), el("th", null, "Result"))), body)));
  const gold = report.gold_ball;
  if (gold) {
    const winners = gold.winning_numbers.join(", ") || "not shown";
    root.append(el("p", { class: "small" }, gold.checked
      ? (gold.won ? `Gold Ball ${gold.number}: ${[gold.kind && gold.kind + " " + gold.gold_ball_prize, ...gold.super_draw_prizes.map((p) => "Super Draw " + p)].filter(Boolean).join("; ")}.`
        : `Gold Ball ${gold.number}: no match (winning Gold Ball number ${winners}).`)
      : `Gold Ball not checked. This draw's winning Gold Ball number: ${winners}.`));
  }
  root.append(el("ul", { class: "small muted" }, report.notes.map((n) => el("li", null, n))));
  if (report.details_url) {
    root.append(el("p", { class: "small" }, el("a", { href: report.details_url, rel: "noopener noreferrer", target: "_blank" },
      "Official prize breakdown for this draw")));
  }
}

async function checkTicket() {
  showError("ticket-error", "");
  const date = $("ticket-draw").value;
  if (!date) { showError("ticket-error", "Choose a draw first."); return; }
  const button = $("run-ticket");
  button.disabled = true;
  try {
    const gold = $("ticket-gold").value.trim();
    renderTicket(await api("/api/check-ticket", { method: "POST", json: {
      game: game(), draw_date: date, lines: $("ticket-lines").value, gold_ball: game() === "649" && gold ? gold : null } }));
  } catch (error) {
    clear("ticket-results");
    showError("ticket-error", error.message);
  } finally {
    button.disabled = false;
  }
}

// ---------- rules ----------
function renderRules() {
  const rules = gameRules();
  if (!rules) return;
  $("rules-title").textContent = `${rules.name} rules`;
  $("rules-lines").textContent = rules.lines;
  $("rules-draws").textContent = `Draws: ${rules.draws}. Any prize: ${rules.any_prize}.`;
  const body = $("rules-table").tBodies[0];
  while (body.firstChild) body.firstChild.remove();
  for (const tier of rules.tiers) {
    const odds = tier.published_one_in_per_play;
    body.append(el("tr", null, el("td", null, tier.tier), el("td", null, tier.prize),
      el("td", { class: "num" }, "1 in " + (odds >= 100 ? fmtInt(odds) : odds))));
  }
  const extras = clear("rules-extras");
  for (const item of rules.extras) extras.append(el("li", null, item));
  $("rules-note").textContent = state.rules.verification_note;
  const sources = clear("rules-sources");
  state.rules.sources.forEach((url, i) => {
    if (i) sources.append(", ");
    sources.append(el("a", { href: url, rel: "noopener noreferrer", target: "_blank" }, new URL(url).hostname + new URL(url).pathname));
  });
}

async function loadRules() {
  try {
    state.rules = await api("/api/rules");
  } catch (error) {
    $("verified").textContent = "could not load rules";
    $("honesty").textContent = error.message;
    return;
  }
  const checked = new Date(state.rules.verified_on + "T12:00:00");
  $("verified").textContent = "rules checked " + checked.toLocaleDateString("en-CA", { year: "numeric", month: "long", day: "numeric" });
  $("honesty").textContent = state.rules.honesty + " Popularity filters only aim to reduce prize sharing, which is unproven.";
  onGameChange();
  loadDraws();
}

// ---------- dated draw information (live from WCLC, or the saved snapshot) ----------
async function loadDraws() {
  try {
    state.draws = await api("/api/draws");
  } catch (error) {
    state.draws = null;
    $("draws-status").textContent = "Draw information could not be loaded: " + error.message;
    clear("upcoming-draws").append(el("li", null, "Unavailable right now. Try reloading the page."));
    return;
  }
  for (const id of ["draws-status", "odds-draws-status"]) $(id).textContent = "Draw information: " + state.draws.status;
  populateDraws();
  renderUpcoming();
}

function drawDay(iso, options) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-CA", options || { weekday: "short", month: "short", day: "numeric" });
}

function drawSummary(draw) {
  if (draw.game === "649") {
    return `Gold Ball jackpot $${draw.gold_jackpot_cad / 1e6}M or guaranteed $1M (${draw.gold_balls} balls in the drum); Classic $5M`;
  }
  const mm = draw.maxmillions ? `, ${draw.maxmillions} × $1M MAXMILLIONS` : "";
  return `Jackpot $${draw.jackpot_cad / 1e6}M${mm}, ${draw.maxplus} × $100,000 MAXPLUS`;
}

function renderUpcoming() {
  const list = $("upcoming-draws");
  if (!state.draws) return;
  clear("upcoming-draws");
  // Live data marks drawn draws; the saved snapshot falls back to the date.
  const coming = state.draws.draws.filter((d) => d.game === game() &&
    (d.drawn === undefined ? d.date >= state.draws.today : !d.drawn));
  if (!coming.length) {
    list.append(el("li", null, "The next draw's jackpot has not been announced yet."));
  }
  for (const draw of coming) {
    list.append(el("li", null, el("b", null, drawDay(draw.date, { weekday: "long", month: "long", day: "numeric" })),
      ": " + drawSummary(draw) + ".", draw.basis ? el("span", { class: "muted" }, ` (${draw.basis})`) : null));
  }
  for (const note of state.draws.notes || []) {
    if (note.toLowerCase().includes(game() === "649" ? "6/49" : "lotto max")) list.append(el("li", { class: "muted" }, note));
  }
}

// ---------- session and uploads ----------
async function refreshSession() {
  try {
    const info = await api("/api/session");
    $("storage-note").textContent = info.storage;
    const history = info.history[game()];
    $("history-status").textContent = history
      ? `Loaded ${fmtInt(history.draws)} draws (${history.first} to ${history.last}) from ${history.name}.`
      : "No history uploaded for this game.";
    $("history-status").className = "status small" + (history ? " ok" : "");
    const receipt = info.receipts[game()];
    $("receipt-status").textContent = receipt
      ? `Tickets loaded: ${receipt.plays} play(s), ${receipt.lines} line(s), ${receipt.distinct_lines} distinct, cost ${receipt.cost} (${receipt.name}).`
      : "";
  } catch (error) {
    $("history-status").textContent = error.message;
  }
}

async function uploadFile(inputId, path, errorId, statusId, describe) {
  showError(errorId, "");
  const file = $(inputId).files[0];
  if (!file) { showError(errorId, "Choose a CSV file first."); return; }
  const form = new FormData();
  form.append("file", file);
  $(statusId).textContent = "Uploading and checking…";
  try {
    const summary = await api(path, { method: "POST", body: form });
    $(statusId).textContent = describe(summary);
    $(statusId).className = "status small ok";
  } catch (error) {
    $(statusId).textContent = "";
    showError(errorId, error.message);
  }
}

// ---------- background jobs ----------
async function runJob(boxId, errorId, buttonId, startPath, body, onDone) {
  showError(errorId, "");
  const box = $(boxId), bar = box.querySelector("progress"), stage = box.querySelector(".stage"),
    cancel = box.querySelector(".cancel"), button = $(buttonId);
  button.disabled = true;
  box.hidden = false;
  bar.value = 0;
  stage.textContent = "Sending to the server… (a sleeping free server can take about a minute to wake up)";
  let job;
  try {
    job = await api(startPath, { method: "POST", json: body });
  } catch (error) {
    box.hidden = true;
    button.disabled = false;
    showError(errorId, error.message);
    return;
  }
  cancel.disabled = false;
  cancel.onclick = async () => {
    cancel.disabled = true;
    stage.textContent = "Cancelling…";
    try { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); } catch { /* status poll reports it */ }
  };
  while (job.status === "queued" || job.status === "running") {
    bar.value = job.progress;
    stage.textContent = job.stage;
    await sleep(job.status === "queued" ? 1000 : 600);
    try {
      job = await api(`/api/jobs/${job.id}`);
    } catch (error) {
      box.hidden = true;
      button.disabled = false;
      showError(errorId, error.message);
      return;
    }
  }
  box.hidden = true;
  button.disabled = false;
  if (job.status === "done") onDone(job);
  else if (job.status === "cancelled") showError(errorId, "Cancelled. No partial result is shown.");
  else showError(errorId, job.error || "The calculation failed.");
}

// ---------- charts ----------
function barChart({ values, labels, expected, title, labelEvery = 1, alt = false }) {
  const n = values.length;
  const width = Math.max(320, n * 16 + 46), height = 190;
  const pad = { l: 34, r: 8, t: 10, b: 26 };
  const expectedValues = Array.isArray(expected) ? expected : [];
  const top = Math.max(1, ...values, ...(Array.isArray(expected) ? expected : [expected || 0])) * 1.08;
  const plotW = width - pad.l - pad.r, plotH = height - pad.t - pad.b, step = plotW / n;
  const y = (v) => pad.t + plotH * (1 - v / top);
  const chart = svgEl("svg", { viewBox: `0 0 ${width} ${height}`, class: "chart", role: "img", "aria-label": title });
  chart.append(svgEl("line", { x1: pad.l, y1: pad.t + plotH, x2: width - pad.r, y2: pad.t + plotH, class: "axis" }));
  for (const tick of [0, top / 2, top / 1.08]) {
    const label = svgEl("text", { x: pad.l - 4, y: y(tick) + 4, "text-anchor": "end" });
    label.textContent = tick >= 10 ? Math.round(tick) : Number(tick.toFixed(1));
    chart.append(label);
  }
  values.forEach((value, i) => {
    const h = plotH * value / top;
    const rect = svgEl("rect", { x: pad.l + i * step + step * 0.12, y: pad.t + plotH - h, width: step * 0.76, height: h,
      class: alt ? "bar alt" : "bar" });
    const tip = svgEl("title", {});
    tip.textContent = `${labels[i]}: ${value}` + (expectedValues.length ? ` (expected ${expectedValues[i].toFixed(1)})` :
      typeof expected === "number" ? ` (expected ${expected.toFixed(1)})` : "");
    rect.append(tip);
    chart.append(rect);
    if (i % labelEvery === 0) {
      const label = svgEl("text", { x: pad.l + (i + 0.5) * step, y: height - 8, "text-anchor": "middle" });
      label.textContent = labels[i];
      chart.append(label);
    }
  });
  if (typeof expected === "number") {
    chart.append(svgEl("line", { x1: pad.l, y1: y(expected), x2: width - pad.r, y2: y(expected), class: "exp" }));
  } else if (expectedValues.length) {
    const points = expectedValues.map((v, i) => `${pad.l + (i + 0.5) * step},${y(v)}`).join(" ");
    chart.append(svgEl("polyline", { points, class: "exp" }));
    expectedValues.forEach((v, i) => chart.append(svgEl("circle", { cx: pad.l + (i + 0.5) * step, cy: y(v), r: 3, class: "exp-dot" })));
  }
  return el("figure", { class: "chart-box" }, el("figcaption", { class: "small" }, title), chart,
    el("div", { class: "legend" }, el("span", null, el("span", { class: alt ? "sw alt" : "sw bar" }), "Observed"),
      el("span", null, el("span", { class: "sw exp" }), "Expected if draws are fair")));
}

// ---------- history analysis ----------
function verdict(test) {
  return test.significant
    ? "Unusual: unlikely under fair random draws. Check the data first; this still does not point to better numbers."
    : "Consistent with fair random draws (normal variation).";
}

function renderAnalysis(report) {
  const root = clear("analysis-results");
  root.append(el("div", { class: "card" },
    el("h3", null, "How to read this"),
    el("p", { class: "small" }, "Each test asks: if the draws were completely fair, how often would a pattern at least this uneven appear by chance? " +
      "That chance is the p-value. Because several tests are run together, p-values are adjusted (Holm method) so that " +
      `chance alone rarely produces a false alarm. Only adjusted values at or below ${report.alpha} count as significant. ` +
      `P-values come from ${fmtInt(report.simulations)} simulated fair histories, which correctly handles six numbers drawn without replacement.`),
    el("p", { class: "small muted" }, report.gap_note + " A number's gap is how many of your supplied draws have passed since it last appeared; it does not make the number due.")));
  for (const regime of report.regimes) {
    const exp = regime.expected;
    const freq = regime.frequencies;
    const card = el("div", { class: "card" }, el("h2", null, `Numbers 1–${regime.pool}: ${fmtInt(regime.draws)} draws (${regime.first_date} to ${regime.last_date})`));
    const tests = el("tbody");
    for (const test of regime.tests) {
      tests.append(el("tr", null, el("td", null, test.name), el("td", { class: "num" }, test.chi_square.toFixed(2)),
        el("td", { class: "num" }, test.mc_p.toFixed(4)), el("td", { class: "num" }, test.holm_adjusted_p.toFixed(4)),
        el("td", { class: test.significant ? "" : "ok" }, verdict(test))));
    }
    card.append(el("div", { class: "table-wrap" }, el("table", null,
      el("thead", null, el("tr", null, el("th", null, "Test"), el("th", { class: "num" }, "Chi-square"),
        el("th", { class: "num" }, "p-value"), el("th", { class: "num" }, "Adjusted p"), el("th", null, "Plain-language result"))), tests)));
    card.append(barChart({ values: freq.map((f) => f.main_count), labels: freq.map((f) => f.number), expected: exp.number,
      title: "How often each number was drawn (main numbers)", labelEvery: regime.pool > 30 ? 2 : 1 }));
    const k = regime.splits.odd.length - 1;
    const counts = [...Array(k + 1).keys()];
    card.append(el("div", { class: "grid2" },
      barChart({ values: regime.splits.odd, labels: counts, expected: exp.odd, title: "Draws by how many odd numbers they had", alt: true }),
      barChart({ values: regime.splits.low, labels: counts, expected: exp.low, title: `Draws by how many low numbers (${regime.low_definition})`, alt: true })));
    card.append(barChart({ values: regime.sum_ranges.map((s) => s.draws), labels: regime.sum_ranges.map((s) => s.from),
      expected: exp.sum_bands, title: `Sum of the numbers in each draw, in bands of 25 (min ${regime.sum_min}, mean ${regime.sum_mean.toFixed(1)}, max ${regime.sum_max})`,
      labelEvery: 2 }));
    const rows = el("tbody");
    for (const f of freq) {
      rows.append(el("tr", null, el("td", { class: "num" }, f.number), el("td", { class: "num" }, f.main_count),
        el("td", { class: "num" }, f.bonus_count), el("td", null, f.last_drawn || "never"),
        el("td", { class: "num" }, (f.gap_is_lower_bound ? "≥" : "") + f.gap_draws),
        el("td", { class: "num" }, f.gap_calendar_days === null ? "" : f.gap_calendar_days)));
    }
    card.append(el("details", null, el("summary", null, "Full number table (frequencies, bonus counts, gaps)"),
      el("div", { class: "table-wrap" }, el("table", null, el("thead", null, el("tr", null,
        el("th", { class: "num" }, "Number"), el("th", { class: "num" }, "Main"), el("th", { class: "num" }, "Bonus"),
        el("th", null, "Last drawn"), el("th", { class: "num" }, "Gap (draws)"), el("th", { class: "num" }, "Gap (days)"))), rows))));
    root.append(card);
  }
}

// ---------- plans, wheels and odds ----------
function prizeFor(label, gameKey) {
  const rules = state.rules && state.rules.games[gameKey];
  const tier = rules && rules.tiers.find((t) => t.tier === label);
  return tier ? tier.prize : "";
}

function renderOdds(odds, container, gameKey) {
  const hasModel = odds.tiers.some((t) => t.including_pending_iid_quick_pick_model);
  const body = el("tbody");
  for (const tier of odds.tiers) {
    const done = tier.status === "exact";
    const cells = [el("td", null, tier.tier), el("td", null, prizeFor(tier.tier, gameKey) || tier.prize)];
    if (done) {
      cells.push(el("td", { class: "num", title: exactTitle(tier.at_least_one) }, oneIn(tier.at_least_one)),
        el("td", { class: "num" }, pct(tier.at_least_one)), el("td", { class: "ok" }, "exact"));
    } else {
      cells.push(el("td", { class: "num" }, `${pct(tier.lower_bound)} to ${pct(tier.upper_bound)}`), el("td"),
        el("td", null, "NOT FINISHED (time/work limit): proven bounds shown, not an estimate"));
    }
    if (hasModel) {
      const model = tier.including_pending_iid_quick_pick_model;
      cells.push(el("td", { class: "num" }, model ? oneIn(model) : ""));
    }
    body.append(el("tr", null, cells));
  }
  const head = [el("th", null, "Match"), el("th", null, "Prize"), el("th", { class: "num" }, "At least one line"),
    el("th", { class: "num" }, "Percent"), el("th", null, "Status")];
  if (hasModel) head.push(el("th", { class: "num" }, "With pending Quick Picks (model)"));
  container.append(el("div", { class: "table-wrap" }, el("table", null, el("thead", null, el("tr", null, head)), body)));
  container.append(el("p", { class: "small muted" }, odds.definition));
  if (hasModel) container.append(el("p", { class: "small muted" }, odds.quick_pick_model));
  if (!odds.exact_all_main_tiers_complete) {
    container.append(el("p", { class: "small" }, "Some tiers hit the website's time or work limit. The bounds are mathematically guaranteed. " +
      "For a complete exact count, run the command-line tool with --exact-work-limit 0."));
  }
}

function linesTable(report, rules) {
  const body = el("tbody");
  report.lines.forEach((line, i) => {
    body.append(el("tr", null, el("td", { class: "num" }, i + 1),
      el("td", { class: "nums" }, line.map((n) => String(n).padStart(2, "0")).join("  ")),
      el("td", null, rules.lines_per_play > 1 ? `${rules.lines_per_play - 1} Quick Picks issued at purchase` : "—")));
  });
  return el("div", { class: "table-wrap" }, el("table", null, el("thead", null, el("tr", null,
    el("th", { class: "num" }, "Play"), el("th", null, "Numbers to enter"), el("th", null, "Other lines on this play"))), body));
}

function planStats(report) {
  return el("div", { class: "stats" },
    el("div", { class: "stat" }, el("b", null, report.paid_plays), el("span", null, "paid plays")),
    el("div", { class: "stat" }, el("b", null, report.cost), el("span", null, "total cost")),
    el("div", { class: "stat" }, el("b", null, report.unspent), el("span", null, "left from budget")),
    el("div", { class: "stat" }, el("b", null, report.pending_quick_picks), el("span", null, "Quick Picks pending")));
}

function planNotes(report, rules) {
  const cov = report.coverage, bounds = report.coverage_bounds;
  const notes = el("ul", { class: "small" },
    el("li", null, `Coverage: ${cov.distinct_numbers} different numbers and ${cov.distinct_pairs} different pairs; ` +
      `lines share at most ${cov.maximum_pairwise_overlap} number(s); ${cov.duplicate_lines} duplicate lines.`),
    report.mode === "coverage" ? el("li", null, bounds.both_upper_bounds_attained
      ? "Both mathematical upper bounds are reached, so no plan with this many lines covers more numbers or pairs."
      : `Upper bounds: ${bounds.distinct_numbers_upper_bound} numbers, ${bounds.distinct_pairs_upper_bound} pairs. This plan comes from a randomized search and is not proven optimal.`) : null,
    el("li", null, report.column_layout),
    el("li", null, report.history.warning),
    el("li", null, report.sharing));
  if (report.paid_plays < report.affordable_plays && report.mode === "coverage") {
    notes.append(el("li", null, "The search could not fill every affordable play without breaking a rule, so some budget is left unspent."));
  }
  if (rules.key === "max") notes.append(el("li", null, "Enter one chosen line per $6 play. The other three lines are Quick Picks issued at purchase; their numbers cannot be controlled. Upload them later for exact odds."));
  if (rules.key === "649") notes.append(el("li", null, "Each $3 play also gets one assigned Gold Ball number. Its chance is your plays out of all Gold Ball numbers issued nationally (never shared)."));
  return notes;
}

function renderPlan(job, containerId) {
  const report = job.result, rules = state.rules.games[report.game];
  state.lastPlan = { id: job.id, game: report.game, kind: job.kind };
  const root = clear(containerId);
  const card = el("div", { class: "card" });
  if (job.kind === "wheel") {
    const wheel = report.wheel;
    card.append(el("div", { class: wheel.guaranteed ? "banner good" : "banner bad" },
      wheel.guaranteed ? `VERIFIED MATCH-3-OR-BETTER COVER: all ${wheel.total_triples} favourite triples checked against these lines.`
        : "NO GUARANTEE: " + wheel.reason));
    card.append(el("p", { class: "small" }, `Covered favourite triples: ${wheel.covered_triples} of ${wheel.total_triples}. ` +
      (wheel.guaranteed ? "Whenever at least 3 of your favourites are drawn, at least one line matches 3 or more main numbers (bonus excluded). This is a coverage guarantee, not a guaranteed win." : "") +
      (wheel.full_cover_found_plays ? ` A full cover was found with ${wheel.full_cover_found_plays} plays (${wheel.full_cover_found_cost}); that is not proven to be the minimum.` : "") +
      (wheel.overlap_relaxed ? " The overlap limit was relaxed for this wheel, as you chose." : "")));
    const missing = wheel.uncovered_triples || wheel.uncoverable_triples || [];
    if (missing.length) {
      card.append(el("details", null, el("summary", null, `${missing.length} triples not covered`),
        el("p", { class: "small nums" }, missing.slice(0, 120).map((t) => t.join("-")).join(", ") + (missing.length > 120 ? " …" : ""))));
    }
  } else {
    card.append(el("h2", null, `${rules.name} plan`));
  }
  card.append(planStats(report));
  if (report.lines.length) card.append(linesTable(report, rules));
  card.append(el("p", null, el("a", { href: `/api/jobs/${job.id}/tickets.csv`, download: "" }, "Download ticket checklist (CSV)")));
  card.append(planNotes(report, rules));
  card.append(el("h3", null, "Exact odds for your chosen lines"));
  renderOdds(report.odds, card, report.game);
  root.append(card);
}

// ---------- wheel number grid ----------
function renderNumberGrid() {
  const rules = gameRules();
  if (!rules) return;
  for (const n of [...state.favourites]) if (n > rules.pool) state.favourites.delete(n);
  const grid = clear("number-grid");
  for (let n = 1; n <= rules.pool; n += 1) {
    grid.append(el("button", { type: "button", "aria-pressed": String(state.favourites.has(n)), onclick: () => toggleFavourite(n) }, n));
  }
  $("fav-count").textContent = state.favourites.size;
  schedulePrecheck();
}

function toggleFavourite(n) {
  if (state.favourites.has(n)) state.favourites.delete(n);
  else if (state.favourites.size < 15) state.favourites.add(n);
  renderNumberGrid();
}

function schedulePrecheck() {
  clearTimeout(state.precheckTimer);
  state.precheckTimer = setTimeout(precheck, 300);
}

async function precheck() {
  const box = clear("wheel-precheck");
  const count = state.favourites.size;
  if (count < 10) { box.append(el("p", null, `Select ${10 - count} more number${10 - count === 1 ? "" : "s"} (10–15 needed).`)); return; }
  try {
    const result = await api("/api/wheel/precheck", { method: "POST", json: wheelBody() });
    if (result.needs_overlap_relaxation) {
      box.append(el("div", { class: result.overlap_relaxed ? "banner info" : "banner bad" },
        result.overlap_relaxed ? "Overlap limit relaxed for this wheel: lines may share more than two numbers."
          : "Incompatible rules: " + result.overlap_obstruction + " Tick 'Relax the overlap limit' to build a cover."));
    }
    if (result.uncoverable_triples.length) {
      box.append(el("div", { class: "banner bad" }, `${result.uncoverable_triples.length} favourite triples cannot appear in any line that passes the popularity filters ` +
        "(for example evenly spaced numbers). A full guarantee is impossible with these favourites."));
    }
    box.append(el("p", null, `${result.total_triples} triples to cover. At least ${result.lower_bound_plays} plays (${result.lower_bound_cost}) are needed; the cover actually found may need more.`));
  } catch (error) {
    box.append(el("p", { class: "error" }, error.message));
  }
}

function wheelBody() {
  return { game: game(), budget: budgetText() || "0", favourites: [...state.favourites].sort((a, b) => a - b),
    allow_overlap: $("wheel-overlap").checked, use_history: $("wheel-history").checked };
}

// ---------- odds tab ----------
function populateDraws() {
  const select = $("odds-draw");
  while (select.options.length > 1) select.remove(1);
  if (!state.draws) return;
  const snapshot = state.draws;
  // Newest first, so the next draw is at the top of the list.
  const draws = snapshot.draws.filter((d) => d.game === game()).sort((a, b) => b.date.localeCompare(a.date));
  for (const draw of draws) {
    const day = drawDay(draw.date, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
    const detail = draw.game === "649" ? `Gold Ball ${draw.gold_balls} balls` :
      `$${draw.jackpot_cad / 1e6}M, ${draw.maxmillions} MAXMILLIONS, ${draw.maxplus} MAXPLUS`;
    const closed = draw.drawn ? " (drawn)" : draw.date < snapshot.as_of ? " (closed)" : "";
    select.append(el("option", { value: draw.date }, `${day}: ${detail}${closed}`));
  }
}

function renderOddsReport(report, gameKey) {
  const root = clear("odds-results");
  root.append(el("p", { class: "small" }, report.scope));
  renderOdds(report, root, gameKey);
  const audit = report.coverage_audit;
  root.append(el("p", { class: "small muted" }, `${report.known_lines} lines (${report.distinct_known_lines} distinct); lines share at most ${audit.maximum_pairwise_overlap} numbers; ${audit.duplicate_lines} duplicates.`));
  if (report.lines_in_uploaded_history) {
    root.append(el("p", { class: "small" }, report.lines_in_uploaded_history.length
      ? `Lines matching a past jackpot in your history: ${report.lines_in_uploaded_history.join(", ")}.`
      : "No line matches a past jackpot combination in your uploaded history."));
  }
  const extra = report.additional_draws;
  if (extra) {
    root.append(el("h3", null, `Additional prize draws on ${extra.date}`),
      el("ul", { class: "small" },
        el("li", null, "Main jackpot or a MAXMILLIONS prize: " + oneIn(extra.main_or_maxmillions)),
        el("li", null, "At least one MAXMILLIONS ($1M, win or share): " + oneIn(extra.maxmillions_at_least_one)),
        el("li", null, "At least one MAXPLUS ($100,000, win or share): " + oneIn(extra.maxplus_at_least_one)),
        el("li", { class: "muted" }, extra.assumption)));
  }
  const gold = report.gold_ball;
  if (gold) {
    root.append(el("h3", null, `Gold Ball on ${gold.date}`));
    root.append(gold.any_award ? el("ul", { class: "small" },
      el("li", null, "Any Gold Ball award ($1M or more, never shared): " + oneIn(gold.any_award)),
      el("li", null, "Exactly $1,000,000 (white ball): " + oneIn(gold.exact_1m)),
      el("li", null, "Gold Ball jackpot (gold ball): " + oneIn(gold.jackpot)),
      el("li", { class: "muted" }, "Conditional on the national Gold Ball count you entered."))
      : el("p", { class: "small" }, `Unknown until sales close: ${gold.formula}. Enter a count to see a conditional figure.`));
  }
}

function renderCompare(report) {
  const root = clear("compare-results");
  const source = report.draw_info ? report.draw_info.status : `Dated draw information as of ${report.snapshot_as_of}.`;
  root.append(el("p", { class: "small" }, `${source} Week ${report.week_monday} to ${report.week_sunday}.`));
  if (!report.draws.length) root.append(el("p", null, "No draws for that week are in the available draw information."));
  const names = { gold_any_million_category: "Gold Ball, any award ($1M+)", gold_exact_1m: "Gold Ball, exactly $1M",
    gold_jackpot: "Gold Ball jackpot", classic_jackpot_category: "Classic $5M jackpot (win or share)",
    main_jackpot_category: "Max jackpot (win or share)", maxmillions_category: "MAXMILLIONS $1M (win or share)",
    maxplus_category: "MAXPLUS $100,000 (below $1M)", any_million_category: "Any $1M+ category" };
  for (const row of report.draws) {
    const list = el("ul", { class: "small" });
    for (const [key, label] of Object.entries(names)) {
      if (key in row) list.append(el("li", null, `${label}: ${row[key] ? oneIn(row[key]) : "unknown (needs national Gold Ball sales)"}`));
    }
    if (row.unknown_reason) list.append(el("li", { class: "muted" }, `${row.unknown_reason} Formula: ${row.gold_any_formula}.`));
    if (row.quick_pick_assumption) list.append(el("li", { class: "muted" }, row.quick_pick_assumption));
    for (const note of Object.values(row.sharing || {})) list.append(el("li", { class: "muted" }, note));
    root.append(el("div", { class: "card" }, el("h3", null, `${row.date} · ${row.game} · ${row.plays} plays for ${row.spent_cad} (unspent ${row.unspent_cad})`),
      el("p", { class: "small muted" }, row.timing_relative_to_snapshot), list));
  }
  root.append(el("p", { class: "small" }, report.ranking));
  if (report.thresholds.length) {
    root.append(el("ul", { class: "small" }, report.thresholds.map((t) => el("li", null,
      `Gold Ball alone beats one Max play per dollar for the ${t.max_draw_date} draw if fewer than ${fmtInt(t.gold_ball_beats_max_main_plus_mm_per_dollar_if_G_less_than)} Gold Ball numbers are issued nationally.`))));
  }
  root.append(el("details", null, el("summary", null, "Limitations"), el("ul", { class: "small" }, report.limitations.map((t) => el("li", null, t)))));
}

// ---------- wiring ----------
function wire() {
  for (const tab of document.querySelectorAll(".tab")) tab.addEventListener("click", () => showTab(tab.dataset.tab));
  for (const radio of document.querySelectorAll('input[name="game"]')) radio.addEventListener("change", onGameChange);
  $("budget").addEventListener("input", () => { store.set("budget", $("budget").value); updateBudgetNote(); });

  $("history-form").addEventListener("submit", (event) => {
    event.preventDefault();
    uploadFile("history-file", `/api/history/${game()}`, "history-error", "history-status",
      (s) => `Loaded ${fmtInt(s.draws)} draws (${s.first} to ${s.last}) from ${s.name}.`);
  });
  $("receipt-form").addEventListener("submit", (event) => {
    event.preventDefault();
    uploadFile("receipt-file", `/api/receipt/${game()}`, "odds-error", "receipt-status",
      (s) => `Tickets loaded: ${s.plays} play(s), ${s.lines} line(s), ${s.distinct_lines} distinct, cost ${s.cost}.`);
  });
  $("delete-data").addEventListener("click", async () => {
    try {
      await api("/api/session", { method: "DELETE" });
      state.lastPlan = null;
      for (const id of ["analysis-results", "generate-results", "wheel-results", "odds-results"]) clear(id);
      $("delete-status").textContent = " Deleted. Uploads, plans and calculations for this browser are gone.";
      refreshSession();
    } catch (error) {
      $("delete-status").textContent = " " + error.message;
    }
  });

  $("run-analysis").addEventListener("click", () => runJob("analysis-job", "analysis-error", "run-analysis", "/api/jobs/analyze",
    { game: game(), simulations: Number($("simulations").value) || 999 }, (job) => renderAnalysis(job.result)));
  $("run-generate").addEventListener("click", () => runJob("generate-job", "generate-error", "run-generate", "/api/jobs/generate",
    { game: game(), budget: budgetText(), use_history: $("gen-history").checked,
      slip_columns: Number($("gen-columns").value) || 7, seed: Number($("gen-seed").value) || 0 },
    (job) => renderPlan(job, "generate-results")));
  $("run-wheel").addEventListener("click", () => runJob("wheel-job", "wheel-error", "run-wheel", "/api/jobs/wheel",
    wheelBody(), (job) => renderPlan(job, "wheel-results")));
  for (const id of ["wheel-overlap", "wheel-history"]) $(id).addEventListener("change", schedulePrecheck);
  $("fav-clear").addEventListener("click", () => { state.favourites.clear(); renderNumberGrid(); });

  $("run-odds").addEventListener("click", () => {
    const source = document.querySelector('input[name="odds-source"]:checked').value;
    if (source === "plan" && (!state.lastPlan || state.lastPlan.game !== game())) {
      showError("odds-error", "Generate a plan or wheel for this game first, or choose your uploaded tickets.");
      return;
    }
    const gold = $("odds-gold").value.trim();
    const gameKey = game();
    runJob("odds-job", "odds-error", "run-odds", "/api/jobs/odds",
      { game: game(), source, plan_job_id: source === "plan" ? state.lastPlan.id : null,
        budget: budgetText() || null, draw_date: $("odds-draw").value || null, gold_issued: gold ? Number(gold) : null },
      (job) => renderOddsReport(job.result, gameKey));
  });
  $("run-ticket").addEventListener("click", checkTicket);
  $("clear-ticket").addEventListener("click", () => {
    $("ticket-lines").value = "";
    $("ticket-gold").value = "";
    clear("ticket-results");
    showError("ticket-error", "");
  });
  $("run-compare").addEventListener("click", async () => {
    showError("compare-error", "");
    const gold = $("compare-gold").value.trim();
    try {
      renderCompare(await api("/api/compare", { method: "POST",
        json: { budget: budgetText(), week_of: $("compare-week").value || null, gold_issued: gold ? Number(gold) : null } }));
    } catch (error) {
      showError("compare-error", error.message);
    }
  });
}

function restore() {
  const savedGame = store.get("game");
  if (savedGame) {
    const radio = document.querySelector(`input[name="game"][value="${savedGame === "max" ? "max" : "649"}"]`);
    if (radio) radio.checked = true;
  }
  const savedBudget = store.get("budget");
  if (savedBudget !== null) $("budget").value = savedBudget;
  const today = new Date();
  $("compare-week").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const tab = store.get("tab");
  showTab(["dashboard", "history", "generator", "wheel", "odds", "ticket"].includes(tab) ? tab : "dashboard");
}

document.addEventListener("DOMContentLoaded", () => {
  restore();
  wire();
  loadRules();
});
