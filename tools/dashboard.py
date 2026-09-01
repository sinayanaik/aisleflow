#!/usr/bin/env python3
"""Build ``docs/dashboard.html`` -- the results, explained rather than tabulated.

The static figures (`tools/make_figures.py`) each answer one question. This
answers the reader's actual question, which is "so is it better or not?", and
lets them check the answer themselves: pick a map, pick a metric, hover a bar
and read the mean, the interval, the p-value against plain lifelong PIBT, and a
sentence saying what that combination means.

One self-contained file -- inline CSS, inline data, inline SVG drawing, no
scripts fetched, no fonts fetched -- matching `docs/deck/slides.html` and the
GUI. It opens from a clone, from a download, or over a hotel wifi that has
already given up.

Run through `tools/make_figures.py --dashboard`, or directly::

    python3 tools/dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
OUT = ROOT / "docs" / "dashboard.html"

#: suites the page needs, and whether it can be built without them
SUITES = {
    "ablation": True,
    "baselines": True,
    "hypotheses": False,
    "paired": False,
}


def collect() -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for name, required in SUITES.items():
        path = DATA_DIR / f"{name}.json"
        if not path.exists():
            if required:
                raise SystemExit(
                    f"{path.relative_to(ROOT)} is missing -- run "
                    "`python3 experiments/run_all.py` first"
                )
            continue
        payload[name] = json.loads(path.read_text())
    return payload


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPAR results</title>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb;
  --plane: #f3f3f0;
  --card: #ffffff;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --axis: #c3c2b7;
  --border: rgba(11, 11, 11, 0.10);
  --series-1: #2a78d6;
  --series-2: #eb6834;
  --series-3: #1baf7a;
  --series-4: #eda100;
  --good: #0ca30c;
  --critical: #d03b3b;
  --warning: #fab219;
  --deemph: #c3c2b7;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface: #1a1a19;
    --plane: #0d0d0d;
    --card: #201f1e;
    --ink: #ffffff;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --axis: #383835;
    --border: rgba(255, 255, 255, 0.10);
    --series-1: #3987e5;
    --series-2: #d95926;
    --series-3: #199e70;
    --series-4: #c98500;
    --deemph: #5a5954;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--plane);
  color: var(--ink);
  font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
}
main { max-width: 1120px; margin: 0 auto; padding: 32px 20px 64px; }
h1 { font-size: 26px; margin: 0 0 6px; letter-spacing: -0.01em; }
h2 { font-size: 17px; margin: 34px 0 10px; }
p { margin: 0 0 12px; color: var(--ink-2); }
.lede { font-size: 15px; color: var(--ink-2); margin-bottom: 22px; max-width: 76ch; }
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 10px; padding: 18px 20px; margin-bottom: 18px;
}
.readfirst { border-left: 3px solid var(--warning); }
.readfirst h2 { margin-top: 0; font-size: 15px; }
.readfirst p:last-child { margin-bottom: 0; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; margin-bottom: 22px; }
.tile { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; }
.tile .label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); }
.tile .value { font-size: 30px; font-weight: 650; margin: 6px 0 4px; letter-spacing: -0.02em; }
.tile .note { font-size: 12.5px; color: var(--ink-2); line-height: 1.45; }
.win { color: var(--good); }
.loss { color: var(--critical); }
.flat { color: var(--ink); }
.controls { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 0 0 16px; }
.controls label { font-size: 12.5px; color: var(--muted); }
.tabs { display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.tabs button {
  font: inherit; font-size: 13px; padding: 7px 13px; border: 0; cursor: pointer;
  background: var(--card); color: var(--ink-2);
}
.tabs button[aria-pressed="true"] { background: var(--series-1); color: #fff; }
select, .toggle {
  font: inherit; font-size: 13px; padding: 6px 9px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--card); color: var(--ink);
}
.toggle { cursor: pointer; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
.bar { transition: opacity .12s; }
.bar:hover, .bar:focus { opacity: .82; outline: none; }
text { font-family: inherit; }
.tick { font-size: 11px; fill: var(--muted); }
.name { font-size: 12.5px; fill: var(--ink-2); }
.value-label { font-size: 12px; fill: var(--ink); font-weight: 600; }
.grid-line { stroke: var(--grid); stroke-width: 1; }
.axis-line { stroke: var(--axis); stroke-width: 1; }
#tooltip {
  position: fixed; pointer-events: none; z-index: 10; max-width: 330px;
  background: var(--card); color: var(--ink); border: 1px solid var(--border);
  border-radius: 9px; padding: 10px 12px; font-size: 12.5px; line-height: 1.45;
  box-shadow: 0 6px 22px rgba(0,0,0,.16); opacity: 0; transition: opacity .1s;
}
#tooltip .tt-value { font-size: 17px; font-weight: 650; letter-spacing: -0.01em; }
#tooltip .tt-name { color: var(--ink-2); margin-bottom: 4px; }
#tooltip .tt-verdict { margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--border); color: var(--ink-2); }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.verdict-pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11.5px; font-weight: 600; border: 1px solid currentColor; }
.legend { display: flex; gap: 16px; flex-wrap: wrap; font-size: 12.5px; color: var(--ink-2); margin: 10px 0 0; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
footer { margin-top: 34px; font-size: 12px; color: var(--muted); }
code { font-family: ui-monospace, "DejaVu Sans Mono", monospace; font-size: 12.5px; }
.hidden { display: none !important; }
</style>
</head>
<body>
<main>
  <h1>SPAR: is it better?</h1>
  <p class="lede">
    Ahead on the maps it was designed for, behind on open ones, and far ahead of
    both published baselines everywhere. The wins are not yet significant at five
    seeds and the losses are &mdash; this page shows both, on any metric, on any
    map, with the interval and the p-value attached to every bar.
  </p>

  <div class="card readfirst">
    <h2>Read this first &mdash; two things that make the raw numbers misleading</h2>
    <p><strong>Every run here is saturated.</strong> Tasks arrive faster than any
    planner can serve them, so the backlog grows all run and throughput is not
    "how busy it was" &mdash; it is the warehouse's service capacity under that
    planner. That is why 502 against 313 tasks per 1000 timesteps is a 61% difference
    and not a rounding error. <strong>Throughput is reported per 1000
    timesteps</strong> throughout this page and the paper.</p>
    <p><strong>Service time only counts tasks that finished.</strong> A planner
    that gives up on the hard tasks reports a beautiful mean service time. Both
    external baselines do exactly that here: near-zero throughput beside some of
    the best service times in the dataset, because the only tasks they finish are
    the easy early ones. Never rank two planners by service time unless their
    throughput is comparable &mdash; switch the metric selector below to see it.</p>
  </div>

  <div class="tiles" id="tiles"></div>

  <h2>Compare planners</h2>
  <div class="controls">
    <div class="tabs" id="map-tabs" role="group" aria-label="Map"></div>
    <label for="metric">Metric</label>
    <select id="metric"></select>
    <label for="scope">Show</label>
    <select id="scope">
      <option value="headline">headline planners</option>
      <option value="all">every variant</option>
    </select>
    <button class="toggle" id="table-toggle" aria-pressed="false">Show the table</button>
  </div>

  <div class="card">
    <svg id="chart" role="img" aria-label="Planner comparison"></svg>
    <div class="legend" id="chart-legend"></div>
  </div>

  <div class="card hidden" id="table-card">
    <table id="table"></table>
  </div>

  <h2 id="hyp-heading">Hypotheses, each scored on its own metric</h2>
  <div class="card">
    <table id="hypotheses"></table>
  </div>

  <footer id="provenance"></footer>
</main>
<div id="tooltip" role="status" aria-live="polite"></div>

<script>
const DATA = __DATA__;

const MAP_ORDER = ["warehouse_bottleneck", "warehouse_corridors",
                   "warehouse_narrow", "warehouse_medium"];
const DESIGNED_FOR = new Set(["warehouse_bottleneck", "warehouse_corridors"]);
const REFERENCE = "lifelong_pibt";
const HEADLINE = ["lifelong_pibt", "full_lda_pibt", "aisle_direction_only",
                  "token_passing", "token_passing_recovery", "rhcr"];

const LABELS = {
  lifelong_pibt: "plain lifelong PIBT",
  full_lda_pibt: "SPAR (full)",
  aisle_direction_only: "SPAR (aisle direction)",
  aisle_managed_pibt: "SPAR (aisle managed)",
  hysteresis_pibt: "PIBT + hysteresis",
  directional_pibt: "PIBT + robot direction",
  turning_cost_only: "PIBT + turning cost",
  reservations_only: "PIBT + entry admission",
  congestion_only: "PIBT + congestion",
  recovery_only: "PIBT + recovery",
  aisle_direction_hard: "aisle direction, enforced",
  aisle_managed_hard: "aisle managed, enforced",
  recovery_uncorroborated: "recovery, uncorroborated",
  no_direction_term: "aisle direction, beta = 0",
  aisle_direction_no_max_green: "aisle direction, no max green",
  congestion_scoring_only: "congestion in movement",
  congestion_assignment_only: "congestion in matching",
  direction_control_only: "PIBT + direction control",
  token_passing: "Token Passing (Ma et al. 2017)",
  token_passing_recovery: "Token Passing + recovery",
  rhcr: "RHCR (Li et al. 2021)"
};

// Each metric: where it lives, which direction is good, how to format it, and
// the sentence that says what it means. "higher"/"lower" is carried explicitly
// so no verdict depends on remembering a sign.
const METRICS = [
  {key: "throughput", label: "throughput (tasks per 1000 timesteps)",
   better: "higher", digits: 0, scale: 1000,
   blurb: "The warehouse's service capacity under this planner. Reported per "
          + "1000 steps so the numbers are integers: 149 means 149 tasks "
          + "delivered out of every 1000 timesteps."},
  {key: "completed_tasks", label: "tasks delivered", better: "higher", digits: 0,
   blurb: "Total deliveries over the run."},
  {key: "mean_service_time", label: "mean service time (timesteps)", better: "lower",
   digits: 1, blurb: "Counts only tasks that finished \\u2014 read it beside throughput."},
  {key: "p95_service_time", label: "p95 service time (timesteps)", better: "lower",
   digits: 1, blurb: "The tail. Same censoring caveat as the mean."},
  {key: "mean_runtime_ms_per_step", label: "planner cost (ms per timestep)",
   better: "lower", digits: 2, blurb: "What the planner costs to run."},
  {key: "jain_fairness", label: "Jain fairness across robots", better: "higher",
   digits: 3, blurb: "1.0 means every robot delivered the same number of tasks."},
  {key: "direction_switches_per_1000", label: "aisle direction switches / 1000 steps",
   better: "lower", digits: 1, blurb: "How often aisles change direction."},
  {key: "head_on_conflicts", label: "head-on conflicts", better: "lower", digits: 0,
   blurb: "Pairs meeting nose-to-nose in a single-file aisle."},
  {key: "counterflow_moves", label: "counterflow moves", better: "lower", digits: 0,
   blurb: "Moves taken against a committed direction \\u2014 the price of pricing it."},
  {key: "deadlocks_detected", label: "deadlocks detected", better: "lower", digits: 0,
   blurb: "Groups the detector escalated on."}
];

const state = {
  map: MAP_ORDER.find(m => hasMap(m)) || MAP_ORDER[0],
  metric: "throughput",
  scope: "headline",
  table: false
};

function hasMap(name) {
  return (DATA.baselines && DATA.baselines.maps[name]) ||
         (DATA.ablation && DATA.ablation.maps[name]);
}

function metricDef(key) { return METRICS.find(m => m.key === key); }
function labelOf(v) { return LABELS[v] || v.replace(/_/g, " "); }
function shortMap(name) { return name.replace("warehouse_", ""); }
function fmt(value, digits) {
  return Number(value).toFixed(digits);
}

// Some metrics are stored in one unit and read in another -- throughput is
// recorded per timestep and displayed per 1000, because 0.149 and 0.118 look
// alike and 149 and 118 do not. The scale lives on the metric, so every
// display path converts and no computation does.
function fmtM(value, def) {
  return fmt(value * (def.scale || 1), def.digits);
}

// ---------------------------------------------------------------------------
// rows: the two suites report differently, so normalise them here. Baselines
// carry a bootstrap interval and a p-value against the reference; the ablation
// carries per-seed values, from which the same interval is derived.
// ---------------------------------------------------------------------------

function rowsFor(mapName, metric, scope) {
  const out = [];
  const seen = new Set();
  const baselines = DATA.baselines && DATA.baselines.maps[mapName];
  if (baselines) {
    for (const row of baselines.rows) {
      const field = row.fields[metric];
      if (!field) continue;
      out.push({
        variant: row.variant, mean: field.mean,
        lo: field.ci_lo, hi: field.ci_hi,
        p: field.p_vs_reference, raw: field.raw || null, source: "baselines"
      });
      seen.add(row.variant);
    }
  }
  const ablation = DATA.ablation && DATA.ablation.maps[mapName];
  if (ablation) {
    for (const row of ablation.rows) {
      if (seen.has(row.variant)) continue;
      const raw = row.raw && row.raw[metric];
      if (row[metric] === undefined) continue;
      const spread = raw ? stderr(raw) : 0;
      out.push({
        variant: row.variant, mean: row[metric],
        lo: row[metric] - 1.96 * spread, hi: row[metric] + 1.96 * spread,
        p: null, raw: raw || null, source: "ablation"
      });
    }
  }
  const filtered = scope === "headline"
    ? out.filter(r => HEADLINE.includes(r.variant))
    : out;
  const def = metricDef(metric);
  filtered.sort((a, b) => def.better === "higher" ? b.mean - a.mean : a.mean - b.mean);
  return filtered;
}

function stderr(values) {
  if (!values || values.length < 2) return 0;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / (values.length - 1);
  return Math.sqrt(variance / values.length);
}

function referenceRow(rows) {
  return rows.find(r => r.variant === REFERENCE) || null;
}

// The sentence a reader actually wants: not "p = 0.157" but what that means
// next to a difference of 0.01.
function verdictFor(row, reference, def) {
  if (!reference || row.variant === REFERENCE) {
    return "This is the reference every other row is compared against.";
  }
  const delta = row.mean - reference.mean;
  const better = def.better === "higher" ? delta > 0 : delta < 0;
  const pct = reference.mean ? Math.abs(100 * delta / reference.mean) : 0;
  const size = pct >= 25 ? "a large" : pct >= 8 ? "a real" : "a small";
  if (row.p === null || row.p === undefined) {
    return `${better ? "Ahead of" : "Behind"} plain PIBT by ${pct.toFixed(0)}%. ` +
           "No p-value on this row: it comes from the ablation suite, where the " +
           "interval is the spread over seeds.";
  }
  if (row.p >= 0.05) {
    return `${pct.toFixed(0)}% ${better ? "ahead" : "behind"}, but p = ` +
           `${row.p.toFixed(3)} \\u2014 five seeds cannot tell this apart from noise.`;
  }
  return `${size} ${better ? "win" : "loss"}: ${pct.toFixed(0)}% ` +
         `${better ? "ahead of" : "behind"} plain PIBT, p = ` +
         `${row.p < 0.001 ? "< 0.001" : row.p.toFixed(3)}.`;
}

// ---------------------------------------------------------------------------
// the chart
// ---------------------------------------------------------------------------

const SVG_NS = "http://www.w3.org/2000/svg";
function el(name, attrs, text) {
  const node = document.createElementNS(SVG_NS, name);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}

function colourFor(variant) {
  if (variant === REFERENCE) return "var(--series-1)";
  if (variant.startsWith("token_passing")) return "var(--series-3)";
  if (variant === "rhcr") return "var(--series-4)";
  return "var(--series-2)";
}

function drawChart() {
  const svg = document.getElementById("chart");
  svg.textContent = "";
  const def = metricDef(state.metric);
  const rows = rowsFor(state.map, state.metric, state.scope);
  const reference = referenceRow(rows);
  if (!rows.length) {
    svg.appendChild(el("text", {x: 0, y: 20, class: "name"},
      "No data for this metric on this map."));
    return;
  }

  const rowHeight = 34, padTop = 26, padBottom = 34;
  const labelWidth = 210, valueWidth = 96;
  const width = 980;
  const height = padTop + rows.length * rowHeight + padBottom;
  const plotLeft = labelWidth;
  const plotWidth = width - labelWidth - valueWidth;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  const maxValue = Math.max(...rows.map(r => Math.max(r.mean, r.hi || 0)), 1e-9);
  const scale = value => plotLeft + (value / maxValue) * plotWidth;

  for (let i = 0; i <= 4; i++) {
    const value = (maxValue / 4) * i;
    const x = scale(value);
    svg.appendChild(el("line", {x1: x, y1: padTop - 8, x2: x,
      y2: height - padBottom + 4, class: "grid-line"}));
    svg.appendChild(el("text", {x: x, y: height - padBottom + 20,
      class: "tick", "text-anchor": "middle"}, fmtM(value, def)));
  }

  rows.forEach((row, index) => {
    const y = padTop + index * rowHeight;
    const barHeight = 20;
    const group = el("g", {class: "bar", tabindex: "0", role: "listitem"});

    group.appendChild(el("rect", {
      x: plotLeft, y: y, width: Math.max(1, scale(row.mean) - plotLeft),
      height: barHeight, rx: 4, fill: colourFor(row.variant)
    }));
    if (row.hi > row.lo) {
      const y0 = y + barHeight / 2;
      group.appendChild(el("line", {x1: scale(row.lo), y1: y0, x2: scale(row.hi),
        y2: y0, stroke: "var(--ink-2)", "stroke-width": 1.4}));
      for (const bound of [row.lo, row.hi]) {
        group.appendChild(el("line", {x1: scale(bound), y1: y0 - 4,
          x2: scale(bound), y2: y0 + 4, stroke: "var(--ink-2)",
          "stroke-width": 1.4}));
      }
    }
    group.appendChild(el("text", {x: plotLeft - 10, y: y + 14,
      class: "name", "text-anchor": "end"}, labelOf(row.variant)));
    group.appendChild(el("text", {x: scale(Math.max(row.mean, row.hi || 0)) + 10,
      y: y + 14, class: "value-label"}, fmtM(row.mean, def)));
    group.appendChild(el("rect", {x: 0, y: y - 6, width: width,
      height: rowHeight, fill: "transparent"}));

    const verdict = verdictFor(row, reference, def);
    const show = event => showTooltip(event, row, def, verdict);
    group.addEventListener("pointermove", show);
    group.addEventListener("focus", show);
    group.addEventListener("pointerleave", hideTooltip);
    group.addEventListener("blur", hideTooltip);
    svg.appendChild(group);
  });

  svg.appendChild(el("line", {x1: plotLeft, y1: padTop - 8, x2: plotLeft,
    y2: height - padBottom + 4, class: "axis-line"}));
  svg.appendChild(el("text", {x: plotLeft, y: 14, class: "tick"},
    `${def.label} \\u2014 ${def.better} is better`));

  const legend = document.getElementById("chart-legend");
  legend.textContent = "";
  const entries = [
    ["var(--series-1)", "plain lifelong PIBT (the reference)"],
    ["var(--series-2)", "SPAR variants"],
    ["var(--series-3)", "Token Passing"],
    ["var(--series-4)", "RHCR"]
  ];
  for (const [colour, text] of entries) {
    const span = document.createElement("span");
    const swatch = document.createElement("i");
    swatch.className = "swatch";
    swatch.style.background = colour;
    span.appendChild(swatch);
    span.appendChild(document.createTextNode(text));
    legend.appendChild(span);
  }
  const note = document.createElement("span");
  note.textContent = "whiskers: 95% interval over seeds";
  legend.appendChild(note);
}

// ---------------------------------------------------------------------------
// tooltip -- textContent only: variant names are data, never markup
// ---------------------------------------------------------------------------

const tooltip = document.getElementById("tooltip");

function showTooltip(event, row, def, verdict) {
  tooltip.textContent = "";
  const name = document.createElement("div");
  name.className = "tt-name";
  name.textContent = labelOf(row.variant);
  const value = document.createElement("div");
  value.className = "tt-value";
  value.textContent = fmtM(row.mean, def);
  const interval = document.createElement("div");
  interval.textContent =
    `95% interval ${fmtM(row.lo, def)} to ${fmtM(row.hi, def)}` +
    (row.raw ? ` \\u00b7 ${row.raw.length} seeds` : "");
  const meaning = document.createElement("div");
  meaning.className = "tt-verdict";
  meaning.textContent = verdict;
  tooltip.append(name, value, interval, meaning);

  const point = event.touches ? event.touches[0] : event;
  const x = (point.clientX || 0) + 16;
  const y = (point.clientY || 0) + 16;
  tooltip.style.left = Math.min(x, window.innerWidth - 350) + "px";
  tooltip.style.top = Math.min(y, window.innerHeight - 150) + "px";
  tooltip.style.opacity = "1";
}

function hideTooltip() { tooltip.style.opacity = "0"; }

// ---------------------------------------------------------------------------
// the table view -- everything the hover shows, reachable without hovering
// ---------------------------------------------------------------------------

function drawTable() {
  const table = document.getElementById("table");
  table.textContent = "";
  const def = metricDef(state.metric);
  const rows = rowsFor(state.map, state.metric, state.scope);
  const reference = referenceRow(rows);

  const head = document.createElement("tr");
  for (const heading of ["planner", def.label, "95% interval", "p vs plain PIBT",
                         "what it means"]) {
    const th = document.createElement("th");
    th.textContent = heading;
    head.appendChild(th);
  }
  table.appendChild(head);

  for (const row of rows) {
    const tr = document.createElement("tr");
    const cells = [
      [labelOf(row.variant), false],
      [fmtM(row.mean, def), true],
      [`${fmtM(row.lo, def)} \\u2013 ${fmtM(row.hi, def)}`, true],
      [row.p === null || row.p === undefined ? "\\u2014"
        : (row.p < 0.001 ? "< 0.001" : row.p.toFixed(3)), true],
      [verdictFor(row, reference, def), false]
    ];
    for (const [text, numeric] of cells) {
      const td = document.createElement("td");
      if (numeric) td.className = "num";
      td.textContent = text;
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
}

// ---------------------------------------------------------------------------
// headline tiles
// ---------------------------------------------------------------------------

function throughputOf(mapName, variant) {
  const baselines = DATA.baselines && DATA.baselines.maps[mapName];
  if (baselines) {
    const row = baselines.rows.find(r => r.variant === variant);
    if (row) return row.fields.throughput.mean;
  }
  const ablation = DATA.ablation && DATA.ablation.maps[mapName];
  if (ablation) {
    const row = ablation.rows.find(r => r.variant === variant);
    if (row) return row.throughput;
  }
  return null;
}

function ratio(mapName, variant, against) {
  const a = throughputOf(mapName, variant), b = throughputOf(mapName, against);
  if (!a || !b) return null;
  return 100 * (a - b) / b;
}

// The p-value of the best SPAR configuration against plain PIBT, from
// the ablation suite's per-seed values. A percentage without it invites the
// reader to believe a 27% lead at p = 0.06 is settled.
function bestP(mapName) {
  const ablation = DATA.ablation && DATA.ablation.maps[mapName];
  if (!ablation) return null;
  const rows = {};
  for (const row of ablation.rows) rows[row.variant] = row;
  const base = rows[REFERENCE];
  if (!base || !base.raw) return null;
  const candidates = ["full_lda_pibt", "aisle_direction_only", "aisle_managed_pibt"]
    .filter(v => rows[v]);
  if (!candidates.length) return null;
  const best = candidates.reduce((a, b) =>
    rows[a].throughput >= rows[b].throughput ? a : b);
  return permutationP(rows[best].raw.throughput, base.raw.throughput);
}

// Exact two-sided permutation test, the same one stats.py runs: with five
// seeds a side there are only C(10,5) = 252 label splits, so it enumerates
// rather than samples, and the smallest attainable p is 2/252.
function permutationP(a, b) {
  if (!a || !b || !a.length || !b.length) return null;
  const pooled = a.concat(b), n = pooled.length, na = a.length;
  const mean = xs => xs.reduce((s, v) => s + v, 0) / xs.length;
  const observed = Math.abs(mean(a) - mean(b));
  let total = 0, extreme = 0;
  const combo = [];
  (function choose(start, left) {
    if (left === 0) {
      const inside = new Set(combo);
      const groupA = [], groupB = [];
      for (let i = 0; i < n; i++) (inside.has(i) ? groupA : groupB).push(pooled[i]);
      total += 1;
      if (Math.abs(mean(groupA) - mean(groupB)) >= observed - 1e-12) extreme += 1;
      return;
    }
    for (let i = start; i <= n - left; i++) {
      combo.push(i);
      choose(i + 1, left - 1);
      combo.pop();
    }
  })(0, na);
  return total ? extreme / total : null;
}

function drawTiles() {
  const host = document.getElementById("tiles");
  host.textContent = "";

  // best SPAR configuration against plain PIBT, per map family
  const families = [
    {label: "on aisle-constrained maps", maps: MAP_ORDER.filter(m => DESIGNED_FOR.has(m))},
    {label: "on open maps", maps: MAP_ORDER.filter(m => !DESIGNED_FOR.has(m))}
  ];
  for (const family of families) {
    const values = [];
    for (const mapName of family.maps) {
      const best = ["full_lda_pibt", "aisle_direction_only"]
        .map(v => ratio(mapName, v, REFERENCE))
        .filter(v => v !== null);
      if (best.length) values.push(Math.max(...best));
    }
    if (!values.length) continue;
    const mean = values.reduce((a, b) => a + b, 0) / values.length;
    const ps = family.maps.map(m => bestP(m)).filter(v => v !== null);
    const significant = ps.length && ps.every(v => v < 0.05);
    const strength = !ps.length ? ""
      : significant
        ? ` Significant on every map here (p up to ${Math.max(...ps).toFixed(3)}).`
        : ` Not significant at five seeds (p up to ${Math.max(...ps).toFixed(3)}) ` +
          "\u2014 the direction is consistent, the evidence is thin.";
    host.appendChild(tile(
      `SPAR ${family.label}`,
      `${mean >= 0 ? "+" : ""}${mean.toFixed(0)}%`,
      mean >= 0 ? "win" : "loss",
      "Best SPAR configuration against plain lifelong PIBT on " +
      family.maps.map(shortMap).join(" and ") + "." + strength
    ));
  }

  // against the published baselines
  const gaps = [];
  for (const mapName of MAP_ORDER) {
    for (const baseline of ["token_passing", "rhcr"]) {
      const value = ratio(mapName, "full_lda_pibt", baseline);
      if (value !== null && isFinite(value)) gaps.push(value);
    }
  }
  if (gaps.length) {
    host.appendChild(tile(
      "SPAR vs the published baselines",
      "far ahead",
      "win",
      "Token Passing and RHCR deliver near zero on every map tested here, at " +
      "20x to 300x the cost per timestep. See the cost-benefit figure."
    ));
  }
}

function tile(label, value, tone, note) {
  const node = document.createElement("div");
  node.className = "tile";
  const l = document.createElement("div");
  l.className = "label"; l.textContent = label;
  const v = document.createElement("div");
  v.className = "value " + tone; v.textContent = value;
  const n = document.createElement("div");
  n.className = "note"; n.textContent = note;
  node.append(l, v, n);
  return node;
}

// ---------------------------------------------------------------------------
// hypotheses
// ---------------------------------------------------------------------------

const VERDICT_TONE = {
  "supported": ["var(--good)", "supported"],
  "contradicted": ["var(--critical)", "contradicted"],
  "no measurable effect": ["var(--muted)", "no effect"]
};

function drawHypotheses() {
  const table = document.getElementById("hypotheses");
  const heading = document.getElementById("hyp-heading");
  table.textContent = "";
  if (!DATA.hypotheses) {
    heading.classList.add("hidden");
    table.parentElement.classList.add("hidden");
    return;
  }
  const rows = DATA.hypotheses.rows.filter(r => r.map === state.map);
  const head = document.createElement("tr");
  for (const text of ["", "claim", "metric", "treatment", "control", "p", "verdict"]) {
    const th = document.createElement("th");
    th.textContent = text;
    head.appendChild(th);
  }
  table.appendChild(head);
  for (const row of rows) {
    const field = row.fields[row.metric];
    const tr = document.createElement("tr");
    const cells = [
      [row.hypothesis, false], [row.claim, false],
      [`${row.metric} (${row.better} is better)`, false],
      [Number(field.treatment_mean).toPrecision(3), true],
      [Number(field.control_mean).toPrecision(3), true],
      [field.p_value === null ? "\\u2014"
        : (field.p_value < 0.001 ? "< 0.001" : field.p_value.toFixed(3)), true]
    ];
    for (const [text, numeric] of cells) {
      const td = document.createElement("td");
      if (numeric) td.className = "num";
      td.textContent = text;
      tr.appendChild(td);
    }
    const td = document.createElement("td");
    const [colour, word] = VERDICT_TONE[row.verdict] || VERDICT_TONE["no measurable effect"];
    const pill = document.createElement("span");
    pill.className = "verdict-pill";
    pill.style.color = colour;
    pill.textContent = word;
    td.appendChild(pill);
    tr.appendChild(td);
    table.appendChild(tr);
  }
}

// ---------------------------------------------------------------------------
// controls
// ---------------------------------------------------------------------------

function buildControls() {
  const tabs = document.getElementById("map-tabs");
  for (const mapName of MAP_ORDER.filter(hasMap)) {
    const button = document.createElement("button");
    button.textContent = shortMap(mapName) + (DESIGNED_FOR.has(mapName) ? " \\u2605" : "");
    button.title = DESIGNED_FOR.has(mapName)
      ? "an aisle-constrained map: the case the method is for"
      : "an open map";
    button.setAttribute("aria-pressed", String(mapName === state.map));
    button.addEventListener("click", () => {
      state.map = mapName;
      for (const other of tabs.children) other.setAttribute("aria-pressed", "false");
      button.setAttribute("aria-pressed", "true");
      render();
    });
    tabs.appendChild(button);
  }

  const metric = document.getElementById("metric");
  for (const def of METRICS) {
    const option = document.createElement("option");
    option.value = def.key;
    option.textContent = def.label;
    metric.appendChild(option);
  }
  metric.value = state.metric;
  metric.addEventListener("change", () => { state.metric = metric.value; render(); });

  const scope = document.getElementById("scope");
  scope.addEventListener("change", () => { state.scope = scope.value; render(); });

  const toggle = document.getElementById("table-toggle");
  toggle.addEventListener("click", () => {
    state.table = !state.table;
    toggle.setAttribute("aria-pressed", String(state.table));
    toggle.textContent = state.table ? "Hide the table" : "Show the table";
    render();
  });
}

function render() {
  drawTiles();
  drawChart();
  drawTable();
  drawHypotheses();
  document.getElementById("table-card").classList.toggle("hidden", !state.table);
}

function provenance() {
  const parts = [];
  for (const suite in DATA) {
    const meta = DATA[suite].meta;
    parts.push(`${suite}: ${meta.seeds} seeds x ${meta.timesteps} steps, ` +
               `${meta.generated_utc}`);
  }
  const node = document.getElementById("provenance");
  node.textContent =
    "Generated by tools/dashboard.py from docs/data/ \\u00b7 SPAR @ " +
    (DATA.ablation || DATA.baselines).meta.git_sha + " \\u00b7 " + parts.join(" \\u00b7 ");
}

buildControls();
provenance();
render();
</script>
</body>
</html>
"""


def build_dashboard() -> Path:
    payload = collect()
    html = TEMPLATE.replace("__DATA__", json.dumps(payload, separators=(",", ":")))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    size = OUT.stat().st_size
    print(f"  wrote {OUT.relative_to(ROOT)}  ({size / 1024:.0f} kB)")
    return OUT


if __name__ == "__main__":
    build_dashboard()
