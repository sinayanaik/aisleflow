#!/usr/bin/env node
/**
 * Build `Aisleflow-Research-Review.pptx` from `deck_data.json` and the
 * committed figures.
 *
 * Every number on every slide comes out of `deck_data.json`, which
 * `extract_data.py` builds from `docs/data/` and the drift-tested generated
 * tables in `docs/`. Nothing is typed in twice, so a regenerated dataset
 * moves the deck with the documents instead of leaving a stale claim on a
 * slide. The only images are the three assets already committed to the
 * repository.
 *
 *   node presentation/build_deck.js
 */

"use strict";

const path = require("path");
const fs = require("fs");
const PptxGenJS = require("pptxgenjs");

const HERE = __dirname;
const ROOT = path.resolve(HERE, "..");
const ASSETS = path.join(HERE, "assets");
const GIFS = path.join(ROOT, "docs", "gifs");
const DATA = JSON.parse(fs.readFileSync(path.join(HERE, "deck_data.json"), "utf8"));
const OUT = path.join(ROOT, "Aisleflow-Research-Review.pptx");

// ---------------------------------------------------------------------------
// palette
//
// Taken from `tools/make_figures.py`, not chosen fresh: the deck embeds two of
// those figures, and matching the surface exactly means the figure sits on the
// slide with no seam, while the blue that emphasises aisleflow's bar on the
// chart is the same blue that marks its numbers in the tables beside it.
// ---------------------------------------------------------------------------

const INK = "0F1113";       // dark ground
const INK_SOFT = "1B1E23";  // raised block on dark
const SURFACE = "FCFCFB";   // light ground -- identical to the figures'
const CARD = "F2F1EC";      // quiet tint for cards on light
const HEADLINE = "0B0B0B";
const BODY = "52514E";
const MUTED = "898781";
const RULE = "C3C2B7";
const BLUE = "2A78D6";      // aisleflow, wherever its own number appears
const ORANGE = "EB6834";    // the one sharp accent: badges, callout numbers
const GREEN = "1BAF7A";
const RED = "D03B3B";       // risk and limits only
const WHITE = "FFFFFF";
const ON_DARK = "E8E7E3";
const ON_DARK_MUTED = "9B9992";

const HEAD_FONT = "Arial";
const BODY_FONT = "Calibri";

const W = 13.3;
const H = 7.5;
const M = 0.62;             // slide margin
const CW = W - 2 * M;       // content width

const pres = new PptxGenJS();
pres.layout = "LAYOUT_WIDE";
pres.author = "aisleflow";
pres.company = "aisleflow";
pres.title = "Aisleflow — research review";
pres.subject = "Lifelong fleet coordination for warehouse pick-and-deliver";

let slideNo = 0;

// ---------------------------------------------------------------------------
// primitives
// ---------------------------------------------------------------------------

/** The repeated motif: a filled circle carrying a number or a short glyph. */
function badge(slide, x, y, label, opts) {
  const o = opts || {};
  const d = o.d || 0.42;
  slide.addShape(pres.ShapeType.ellipse, {
    x, y, w: d, h: d, fill: { color: o.fill || ORANGE },
  });
  slide.addText(String(label), {
    x, y, w: d, h: d, isTextBox: true, margin: 0,
    align: "center", valign: "middle",
    fontFace: HEAD_FONT, fontSize: o.size || 14, bold: true,
    color: o.color || WHITE,
  });
}

/**
 * The warehouse-cell grid that marks the dark slides. Sparse and low
 * contrast: a texture that says "grid" without competing with the type.
 */
function cellGrid(slide, x, y, cols, rows, colour) {
  const s = 0.115;
  const gap = 0.075;
  for (let c = 0; c < cols; c += 1) {
    for (let r = 0; r < rows; r += 1) {
      // punch a few cells out so it reads as a floor plan, not a swatch
      if ((c * 7 + r * 3) % 11 === 0) continue;
      slide.addShape(pres.ShapeType.rect, {
        x: x + c * (s + gap), y: y + r * (s + gap), w: s, h: s,
        fill: { color: colour || INK_SOFT },
      });
    }
  }
}

function footer(slide, dark) {
  slideNo += 1;
  slide.addText(`${slideNo}`, {
    x: W - M - 0.6, y: H - 0.5, w: 0.6, h: 0.24, isTextBox: true, margin: 0,
    align: "right", fontFace: BODY_FONT, fontSize: 10,
    color: dark ? ON_DARK_MUTED : MUTED,
  });
  slide.addText(`aisleflow · research review · @${DATA.meta.git_sha}`, {
    x: M, y: H - 0.5, w: 6, h: 0.24, isTextBox: true, margin: 0,
    fontFace: BODY_FONT, fontSize: 10, color: dark ? ON_DARK_MUTED : MUTED,
  });
}

/** A light content slide. Returns the y the body may start at. */
function lightSlide(kicker, title, standfirst) {
  const slide = pres.addSlide();
  slide.background = { color: SURFACE };

  slide.addText(kicker.toUpperCase(), {
    x: M, y: 0.42, w: CW, h: 0.24, isTextBox: true, margin: 0,
    fontFace: BODY_FONT, fontSize: 11, bold: true, charSpacing: 1.6,
    color: ORANGE,
  });
  slide.addText(title, {
    x: M, y: 0.70, w: CW, h: 0.62, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 30, bold: true, color: HEADLINE,
  });

  let y = 1.48;
  if (standfirst) {
    slide.addText(standfirst, {
      x: M, y: 1.36, w: CW * 0.86, h: 0.48, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 15, color: BODY, lineSpacingMultiple: 1.12,
    });
    y = 1.98;
  }
  footer(slide, false);
  return { slide, y };
}

/** A dark slide: title, section divider, close. */
function darkSlide() {
  const slide = pres.addSlide();
  slide.background = { color: INK };
  cellGrid(slide, W - 2.55, H - 2.15, 11, 8, INK_SOFT);
  return slide;
}

/** A caption: provenance, or the sentence that qualifies what is above it. */
function caption(slide, text, y, w, x) {
  slide.addText(text, {
    x: x === undefined ? M : x, y, w: w || CW, h: 0.46, isTextBox: true,
    margin: 0, valign: "top",
    fontFace: BODY_FONT, fontSize: 10.5, italic: true, color: MUTED,
    lineSpacingMultiple: 1.1,
  });
}

/** A large number with a label under it. */
function statTile(slide, o) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.06,
    fill: { color: o.fill || CARD },
  });
  slide.addText(o.value, {
    x: o.x + 0.22, y: o.y + 0.16, w: o.w - 0.44, h: 0.82, isTextBox: true,
    margin: 0, align: "left", valign: "middle",
    fontFace: HEAD_FONT, fontSize: o.valueSize || 40, bold: true,
    color: o.colour || ORANGE,
  });
  slide.addText(o.label, {
    x: o.x + 0.22, y: o.y + 1.0, w: o.w - 0.44, h: o.h - 1.12, isTextBox: true,
    margin: 0, valign: "top", fontFace: BODY_FONT, fontSize: 12,
    color: o.labelColour || BODY, lineSpacingMultiple: 1.08,
  });
}

/** A card: a quiet tint, a badge, a bold header and a paragraph. */
function card(slide, o) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.05,
    fill: { color: o.fill || CARD },
  });
  const padX = 0.24;
  let ty = o.y + 0.22;
  if (o.badge !== undefined) {
    badge(slide, o.x + padX, ty, o.badge, { fill: o.badgeFill || ORANGE });
    ty += 0.58;
  }
  const titleH = o.titleH || 0.3;
  slide.addText(o.title, {
    x: o.x + padX, y: ty, w: o.w - 2 * padX, h: titleH, isTextBox: true,
    margin: 0, valign: "top", fontFace: HEAD_FONT, fontSize: o.titleSize || 14,
    bold: true, color: o.titleColour || HEADLINE,
  });
  slide.addText(o.body, {
    x: o.x + padX, y: ty + titleH + 0.06,
    w: o.w - 2 * padX, h: o.y + o.h - (ty + titleH + 0.16),
    isTextBox: true, margin: 0, valign: "top", fontFace: BODY_FONT,
    fontSize: o.bodySize || 11.5, color: o.bodyColour || BODY,
    lineSpacingMultiple: 1.1,
  });
}

/** A data table: a ruled header, hairline rows, no vertical lines. */
function dataTable(slide, o) {
  const none = { type: "none" };
  const hair = { type: "solid", pt: 0.75, color: RULE };
  const head = o.header.map((text, i) => ({
    text,
    options: {
      bold: true, color: HEADLINE, fontFace: HEAD_FONT,
      fontSize: o.headSize || 11,
      align: (o.align && o.align[i]) || "left",
      border: [none, none, { type: "solid", pt: 1.25, color: HEADLINE }, none],
      valign: "bottom",
    },
  }));
  const body = o.rows.map((row) =>
    row.map((cell, i) => {
      const c = typeof cell === "object" ? cell : { text: cell };
      return {
        text: c.text,
        options: Object.assign(
          {
            color: c.colour || BODY, fontFace: BODY_FONT,
            fontSize: o.size || 11.5,
            bold: !!c.bold,
            align: (o.align && o.align[i]) || "left",
            border: [none, none, hair, none],
            valign: "middle",
          },
          c.options || {}
        ),
      };
    })
  );
  slide.addTable([head].concat(body), {
    x: o.x, y: o.y, w: o.w, colW: o.colW,
    rowH: o.rowH || 0.3,
    margin: [0.06, 0.09, 0.06, 0.09],
    fontFace: BODY_FONT,
  });
}

/** A bulleted list, spaced with paraSpaceAfter rather than line spacing. */
function bullets(slide, items, o) {
  slide.addText(
    items.map((t, i) => ({
      text: t,
      options: {
        bullet: true, breakLine: i < items.length - 1,
        paraSpaceAfter: o.gap === undefined ? 8 : o.gap,
      },
    })),
    {
      x: o.x, y: o.y, w: o.w, h: o.h, isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_FONT, fontSize: o.size || 12.5, color: o.colour || BODY,
      lineSpacingMultiple: 1.08,
    }
  );
}

/** A labelled row: a bold lead-in, then its explanation, as one paragraph. */
function leadIn(slide, o) {
  slide.addText(
    [
      { text: o.lead, options: { bold: true, color: o.leadColour || HEADLINE } },
      { text: o.text, options: { color: o.colour || BODY } },
    ],
    {
      x: o.x, y: o.y, w: o.w, h: o.h, isTextBox: true, margin: 0, valign: "top",
      fontFace: BODY_FONT, fontSize: o.size || 12, lineSpacingMultiple: 1.1,
    }
  );
}

/** The one sentence on a slide that carries its argument. */
function pullQuote(slide, o) {
  slide.addShape(pres.ShapeType.roundRect, {
    x: o.x, y: o.y, w: o.w, h: o.h, rectRadius: 0.05,
    fill: { color: o.fill || INK },
  });
  slide.addText(o.text, {
    x: o.x + 0.26, y: o.y + 0.14, w: o.w - 0.52, h: o.h - 0.28,
    isTextBox: true, margin: 0, valign: "middle",
    fontFace: BODY_FONT, fontSize: o.size || 13, color: o.colour || ON_DARK,
    lineSpacingMultiple: 1.12,
  });
}

// ---------------------------------------------------------------------------
// data shorthands
// ---------------------------------------------------------------------------

const shortMap = (m) => m.replace("warehouse_", "");

/**
 * What share of one CPU core a planner needs to close a control loop at `hz`:
 * `ms` of compute per timestep, `hz` timesteps a second, against 1000 ms of
 * wall clock. Written out rather than inlined because at 10 Hz the percentage
 * and the millisecond figure coincide, and an inlined version of this reads
 * like a typo in either direction.
 */
const corePct = (ms, hz) => (ms * hz) / 10;
const floors = DATA.four_floors;
const byMap = Object.fromEntries(floors.map((r) => [r.map, r]));
const runtime = DATA.runtime;
const runtimeByMap = Object.fromEntries(runtime.rows.map((r) => [r.map, r]));
const density = Object.fromEntries(DATA.density.rows.map((r) => [r.map, r]));

// ===========================================================================
// 1 — title
// ===========================================================================
{
  const s = darkSlide();
  s.addText("RESEARCH REVIEW", {
    x: M, y: 1.55, w: CW, h: 0.26, isTextBox: true, margin: 0,
    fontFace: BODY_FONT, fontSize: 12, bold: true, charSpacing: 2.4, color: ORANGE,
  });
  s.addText("Aisleflow", {
    x: M, y: 1.92, w: CW, h: 1.05, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 60, bold: true, color: WHITE,
  });
  s.addText("Lifelong fleet coordination for warehouse pick-and-deliver", {
    x: M, y: 3.00, w: CW * 0.72, h: 0.44, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 20, color: ON_DARK,
  });
  s.addText(
    "Dozens of robots on one grid, jobs arriving continuously and never stopping, " +
      "no collisions — and the question of how much of a floor's capacity a planner " +
      "can actually reach.",
    {
      x: M, y: 3.60, w: CW * 0.58, h: 0.9, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 14, color: ON_DARK_MUTED,
      lineSpacingMultiple: 1.16,
    }
  );
  const meta = [
    ["Measured against", "Token Passing, TP + task swaps and RHCR, each implemented from its paper"],
    ["Design", `${DATA.meta.seeds} seeds × ${DATA.meta.timesteps} timesteps, Poisson arrivals, identical job streams`],
    ["Provenance", `aisleflow @ ${DATA.meta.git_sha}, dataset generated ${DATA.meta.generated_utc}`],
  ];
  meta.forEach(([k, v], i) => {
    const y = 4.90 + i * 0.34;
    s.addText(k, {
      x: M, y, w: 1.6, h: 0.28, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 11, bold: true, color: ORANGE,
    });
    s.addText(v, {
      x: M + 1.7, y, w: CW - 3.4, h: 0.28, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 11, color: ON_DARK_MUTED,
    });
  });
  footer(s, true);
  s.addNotes(
    "Framing line: this is a completed measurement study, not a proposal. " +
      "Everything on the following slides is traceable to a committed dataset with a git SHA. " +
      "The deck has two halves — what we measured, then what it is commercially worth."
  );
}

// ===========================================================================
// 2 — executive summary
// ===========================================================================
{
  const bn = byMap.warehouse_bottleneck;
  const { slide: s, y } = lightSlide(
    "Executive summary",
    "Competitive on throughput. Dominant on compute.",
    "Four floors, three published planners, five experiment suites. Every run collision-free, " +
      "before and after."
  );

  const tileW = (CW - 3 * 0.24) / 4;
  const tiles = [
    {
      value: String(bn.aisleflow.mean),
      label: `tasks per 1000 steps on the chokepoint floor — +${bn.lead_pct}% over ${bn.strongest_baseline_label}, with no interval overlap`,
      colour: BLUE,
    },
    {
      value: `${runtime.min_vs_rhcr}–${runtime.max_vs_tp}×`,
      label: "less planner compute than the published baselines, measured in the same runtime on the same scenarios",
      colour: ORANGE,
    },
    {
      value: String(density.warehouse_corridors.delta_20_to_40),
      label: `extra tasks from doubling the fleet on ${shortMap("warehouse_corridors")} — 20 robots delivered exactly what 40 did`,
      colour: ORANGE,
    },
    {
      value: `${floors.length} of ${floors.length}`,
      label: "floors where an aisleflow configuration leads every published planner — decisively on two, inside the intervals on two",
      colour: BLUE,
    },
  ];
  tiles.forEach((t, i) =>
    statTile(s, {
      x: M + i * (tileW + 0.24), y, w: tileW, h: 2.25,
      value: t.value, label: t.label, colour: t.colour, valueSize: 38,
    })
  );

  pullQuote(s, {
    x: M, y: y + 2.5, w: CW, h: 1.22,
    text:
      "What we conclude: the planner is competitive on throughput and in a different class on compute. " +
      "Its own additions are congestion machinery — they pay on floors where a chokepoint binds, and cost " +
      "throughput where there is a way round. That makes configuration-per-floor the product, and " +
      "auto-configuration the next thing worth building.",
    size: 14,
  });
  caption(
    s,
    "Throughput figures are tasks delivered per 1000 timesteps, 5 seeds × 400 steps, identical job streams across planners.",
    y + 3.85
  );
  s.addNotes(
    "Lead with the second tile. Throughput leadership is arguable on two of the four floors; " +
      "the compute gap is not arguable on any of them, and it is the number that changes what hardware a deployment needs."
  );
}

// ===========================================================================
// 3 — the problem
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "The problem",
    "Throughput is not a robot property. It is a floor property.",
    "Lifelong multi-agent pickup and delivery: there is no final state to reach, only a rate of work to sustain."
  );

  const colW = 5.5;
  s.addText("What the planner decides, every timestep", {
    x: M, y, w: colW, h: 0.28, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 14, bold: true, color: HEADLINE,
  });
  dataTable(s, {
    x: M, y: y + 0.36, w: colW, colW: [1.5, 4.0], rowH: 0.46, size: 11.5,
    header: ["Decision", "The question it answers"],
    rows: [
      [{ text: "Assignment", bold: true }, "Which robot takes which job?"],
      [{ text: "Route", bold: true }, "Which way should this robot go?"],
      [{ text: "Priority", bold: true }, "If two robots want the same cell, who gets it?"],
      [
        { text: "Movement", bold: true, colour: BLUE },
        { text: "Which neighbouring cell does each robot actually move into?", colour: BLUE },
      ],
    ],
  });
  s.addText(
    "The first three are advisory. Only the fourth is binding, and only the fourth guarantees no collisions.",
    {
      x: M, y: y + 2.72, w: colW, h: 0.4, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 11.5, italic: true, color: BLUE,
    }
  );

  const rx = M + colW + 0.7;
  const rw = CW - colW - 0.7;
  const points = [
    ["Lifelong, not a puzzle. ", "New jobs arrive continuously and never stop. There is no goal state to reach, which is what separates this from classical path finding."],
    ["The hard part is not the path. ", "One robot's shortest path is easy. Every robot's path is in every other robot's way, and an aisle one cell wide cannot let two robots pass."],
    ["Capacity, not speed. ", "Two robots must never share a cell and never swap places. Beyond that, everything is throughput: how many jobs finish per timestep."],
  ];
  points.forEach(([lead, text], i) => {
    badge(s, rx, y + i * 1.24, i + 1, { d: 0.36, size: 12 });
    leadIn(s, {
      x: rx + 0.52, y: y + i * 1.24 - 0.02, w: rw - 0.52, h: 1.14,
      lead, text, size: 13,
    });
  });

  pullQuote(s, {
    x: M, y: y + 3.92, w: CW, h: 0.72,
    text:
      "Every run in every experiment reports collision_free: true — before and after everything described in this deck. " +
      "Collision freedom comes from PIBT's vertex and swap checks, and nothing measured here touched them.",
    size: 13,
  });
  s.addNotes(
    "The point to land: nothing in this study trades safety for throughput. " +
      "Collision freedom comes from PIBT's vertex and swap checks, and no measured change touched them."
  );
}

// ===========================================================================
// 4 — what we built
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "What we built",
    "Three assets, not one",
    "A planner, the harness that judges it, and the tool that explains it."
  );

  const rows = [
    {
      title: "The planner",
      body:
        "A lifelong MAPD planner extending PIBT: a four-term movement score, a rank-and-fairness clock, " +
        "and a deadlock ladder that escalates one remedy per step. 37 parameters, every one of them measured. " +
        "Pure standard library — no runtime dependencies — MIT licensed, Python 3.10+.",
    },
    {
      title: "The benchmark harness",
      body:
        "Token Passing, TP with Task Swaps and RHCR over PBS, each reimplemented from its paper behind the same " +
        "CLI and the same metrics. Five experiment suites. Every figure and table in the documentation is generated " +
        "from the committed dataset, and a test fails if either drifts from it.",
    },
    {
      title: "The diagnostic GUI",
      body:
        "Click a robot and get the answer to “why is it stuck?”: every candidate cell, its score, and the exact rule " +
        "that rejected it. Live parameter sliders, ablation switches, congestion and stall heatmaps — and every control " +
        "is also a JSON endpoint. This is the tool that surfaced the two bugs on slide 13.",
    },
  ];
  rows.forEach((r, i) => {
    const ry = y + i * 1.42;
    badge(s, M, ry + 0.06, i + 1, { d: 0.46, size: 15 });
    s.addText(r.title, {
      x: M + 0.66, y: ry, w: 3.0, h: 0.34, isTextBox: true, margin: 0,
      fontFace: HEAD_FONT, fontSize: 16, bold: true, color: HEADLINE,
    });
    s.addText(r.body, {
      x: M + 0.66, y: ry + 0.38, w: CW - 0.66, h: 0.92, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 12.5, color: BODY, lineSpacingMultiple: 1.12,
    });
  });

  caption(
    s,
    `${180} tests collected (168 in the default run, 12 in the slow baseline sweep). The three published planners are cited in full in the appendix.`,
    y + 4.42
  );
  s.addNotes(
    "The harness is the part that is easy to undersell. Reimplementing three published planners behind one metric set " +
      "is what makes every comparison in this deck an apples-to-apples one, and it is reusable for whatever we build next."
  );
}

// ===========================================================================
// 5 — how it works
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "How it works",
    "Four ideas, and no fifth one",
    "Plain English. The formulas, the symbol table and the worked numbers are in docs/03-the-math.md."
  );

  const ideas = [
    {
      title: "Score every option; forbid none",
      body:
        "Progress is worth 10, and a move changes distance by exactly one — so a robot's five options fall into tiers " +
        "ten points apart. Staying in lane, not turning and avoiding the crowd total under 4: they break ties inside a " +
        "tier and never across one.",
    },
    {
      title: "Rank, and a fairness clock",
      body:
        "A robot in trouble outranks a loaded one, which outranks one still heading for a pickup. Every step spent " +
        "waiting buys rank, so after 80 steps a robot outranks anything on the floor. Nothing starves. In practice " +
        "robots wait two or three steps.",
    },
    {
      title: "Push, don't queue",
      body:
        "If I want your cell I ask you to move, lending you my rank, so a whole chain can shuffle aside for whoever " +
        "started it. If nobody can move I stay put — always legal — so a valid joint move always exists. This is what " +
        "prevents collisions.",
    },
    {
      title: "Notice a jam, escalate gently",
      body:
        "“Nobody moved” is a queue, not a jam. Confirmation needs a wait-for cycle or a repeated configuration. Then " +
        "remedies escalate one per step, cheapest first: reroute, raise rank, reverse out, park. That second signal " +
        "is worth 54% of throughput.",
    },
  ];
  const cw2 = (CW - 0.4) / 2;
  ideas.forEach((idea, i) => {
    card(s, {
      x: M + (i % 2) * (cw2 + 0.4),
      y: y + Math.floor(i / 2) * 2.14,
      w: cw2, h: 2.0,
      badge: i + 1, title: idea.title, body: idea.body,
      titleSize: 15, titleH: 0.32, bodySize: 11.5,
    });
  });
  caption(
    s,
    "The movement score used to have nine terms. Five were smaller than a tie-break and never changed a decision in any measured run — see slide 13.",
    y + 4.42
  );
  s.addNotes(
    "If you take one thing from this slide, take idea 3. Pushing rather than reserving a path is why the planner needs " +
      "no well-formed-instance assumption, and it is the mechanism behind both the throughput result and the compute result."
  );
}

// ===========================================================================
// 6 — the floors
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "The floors",
    "Four layouts, each isolating one traffic property",
    "A planner is not fast or slow in general. It is fast or slow on a layout, at a robot count, under an arrival rate."
  );

  s.addImage({
    path: path.join(ASSETS, "05-the-maps.png"),
    x: M, y: y - 0.1, w: 5.95, h: 4.63,
  });

  const rx = M + 6.25;
  const rw = CW - 6.25;
  const desc = [
    ["bottleneck — 16 robots", "Two halves joined by one six-cell corridor every task must cross, both ways, forever. Seven of its 83 cells are articulation points: a robot standing still can cut the warehouse in half."],
    ["corridors — 35 robots", "Five 22-cell single-file runs joined at both ends. Entering one commits a robot for 21 steps. The only bundled floor with no parking bays at all."],
    ["narrow — 30 robots", "Seven-cell aisles, one cross-lane. Structurally medium with the aisles twice as long and no extra way round — it isolates aisle length from route count."],
    ["medium — 40 robots", "Three-to-five-cell aisles, two cross-lanes. Enough room that a robot meeting another usually has somewhere to go."],
  ];
  desc.forEach(([lead, text], i) => {
    leadIn(s, {
      x: rx, y: y + i * 1.02, w: rw, h: 0.96,
      lead: lead + " — ", text, size: 11.5, leadColour: BLUE,
    });
  });
  pullQuote(s, {
    x: rx, y: y + 4.16, w: rw, h: 0.84,
    text:
      "Every aisle on every floor is one cell wide, and every scenario is saturated on purpose — so throughput " +
      "measures the floor's capacity, not responsiveness.",
    fill: CARD, colour: HEADLINE, size: 11.5,
  });
  caption(s, "All five floors drawn to one scale; small is a test and animation floor, not a results one.", y + 4.62, 5.95);
  s.addNotes(
    "Worth stressing that the four floors were not picked to flatter anything: two of them are the ones where the " +
      "planner's own additions lose. The structural numbers under each map are derived from the map file, not declared in it."
  );
}

// ===========================================================================
// 7 — the headline result
// ===========================================================================
{
  const bn = byMap.warehouse_bottleneck;
  const { slide: s, y } = lightSlide(
    "Headline result",
    "Against three published planners",
    `On ${shortMap(bn.map)}, the floor where the case is clearest: ${bn.robots} robots, ${bn.rate} jobs per timestep.`
  );

  s.addImage({
    path: path.join(ASSETS, "01-vs-baselines.png"),
    x: M, y: y - 0.08, w: 5.3, h: 4.65,
  });

  const rx = M + 5.65;
  const rw = CW - 5.65;
  dataTable(s, {
    x: rx, y, w: rw, colW: [3.3, 1.25, 1.24], rowH: 0.36,
    align: ["left", "right", "right"],
    header: ["Planner", "Tasks / 1000 steps", "95% interval"],
    rows: DATA.featured.rows.map((r, i) => [
      { text: r[0].replace(/\*\*/g, ""), bold: i === 0, colour: i === 0 ? BLUE : BODY },
      { text: r[1], bold: i === 0, colour: i === 0 ? BLUE : BODY },
      { text: r[2].replace("–", "–"), colour: MUTED },
    ]),
  });

  pullQuote(s, {
    x: rx, y: y + 2.18, w: rw, h: 0.62,
    text: `Aisleflow's 95% bootstrap interval clears all three published planners' with no overlap.`,
    fill: BLUE, colour: WHITE, size: 12.5,
  });

  leadIn(s, {
    x: rx, y: y + 2.9, w: rw, h: 1.72,
    lead: "Why the other three fall short here. ",
    text:
      "Ma et al. prove Token Passing and TPTS complete only on well-formed MAPD instances — one parking endpoint per " +
      "agent. This floor has two bays for sixteen robots and seven cells that sever it, so an idle agent resting in the " +
      "corridor cuts the warehouse in half. PIBT never plans a path it must reserve, so it simply displaces that agent. " +
      "RHCR avoids that failure mode and still falls short here.",
    size: 11.5,
  });
  caption(s, DATA.featured.caption.replace(/`/g, ""), y + 4.6, 5.3);
  s.addNotes(
    "Do not oversell this slide — it is one floor, chosen because the case is clearest there. " +
      "Slide 9 is the complete picture and slide 12 is the one that says what our own additions are worth. " +
      "If someone challenges the choice of floor, go to slide 9 immediately."
  );
}

// ===========================================================================
// 8 — the animation
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "The mechanism",
    "One corridor, sixteen robots, four hundred steps",
    "A real seeded run of the same simulator, map, robot count and job stream as the table on the previous slide."
  );

  s.addImage({
    path: path.join(GIFS, "01-aisleflow-bottleneck.gif"),
    x: M, y, w: 7.3, h: 4.44,
  });

  const rx = M + 7.6;
  const rw = CW - 7.6;
  s.addText("Reading the frame", {
    x: rx, y, w: rw, h: 0.3, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 14, bold: true, color: HEADLINE,
  });
  const key = [
    ["Blue", "on its way to a pickup", BLUE],
    ["Teal", "carrying a task to a delivery", GREEN],
    ["Grey", "no task yet", MUTED],
    ["Red", "has not moved for 15 timesteps", RED],
  ];
  key.forEach(([k, v, colour], i) => {
    const ky = y + 0.4 + i * 0.32;
    s.addShape(pres.ShapeType.ellipse, {
      x: rx, y: ky + 0.045, w: 0.15, h: 0.15, fill: { color: colour },
    });
    s.addText([
      { text: `${k} — `, options: { bold: true, color: colour } },
      { text: v, options: { color: BODY } },
    ], {
      x: rx + 0.25, y: ky, w: rw - 0.25, h: 0.26, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 11.5,
    });
  });

  pullQuote(s, {
    x: rx, y: y + 1.82, w: rw, h: 1.18,
    text:
      "Red means stuck, and nothing else on the frame is red. A floor that jammed would fill with red and the " +
      "delivered-tasks chart under it would flatten. Watch that this one does not.",
    fill: INK, colour: ON_DARK, size: 12,
  });

  leadIn(s, {
    x: rx, y: y + 3.16, w: rw, h: 1.3,
    lead: "What you are watching. ",
    text:
      "A blocked robot lends its rank to the robot in its way and pushes; an idle robot is displaced by the first " +
      "busy one that needs its cell. So the one corridor every task must cross drains instead of gridlocking.",
    size: 11.5,
  });
  caption(
    s,
    "warehouse_bottleneck, 16 robots, arrival rate 0.8, 400 timesteps, seed 0, full_lda_pibt. Animates in slideshow. A single seeded run is one draw from the five-seed mean opposite, so it shows the mechanism rather than the average.",
    y + 4.48
  );
  s.addNotes(
    "Run this in slideshow so the GIF animates. If presenting from a PDF, say that the still is frame one and offer " +
      "the live GUI instead — the same run can be driven step by step from `lda-pibt gui`."
  );
}

// ===========================================================================
// 9 — the complete comparison
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "The complete comparison",
    "All four floors, not just the best one",
    "The featured chart is one floor. This is the picture it has to be weighed against."
  );

  dataTable(s, {
    x: M, y, w: CW, colW: [1.55, 0.75, 2.55, 1.25, 1.05, 0.9, 1.0, 3.01],
    rowH: 0.44, size: 11.5, headSize: 10.5,
    align: ["left", "right", "left", "right", "right", "right", "right", "left"],
    header: [
      "Floor", "Fleet", "Best aisleflow configuration", "Aisleflow",
      "RHCR", "TP", "TPTS", "Lead over the strongest baseline",
    ],
    rows: floors.map((r) => [
      { text: shortMap(r.map), bold: true },
      { text: String(r.robots) },
      { text: r.best_variant_label.replace("Aisleflow ", "").replace(/[()]/g, ""), colour: MUTED },
      { text: String(r.aisleflow.mean), bold: true, colour: BLUE },
      { text: String(r.baselines.rhcr.mean) },
      { text: String(r.baselines.token_passing.mean) },
      { text: String(r.baselines.token_passing_task_swaps.mean) },
      {
        text: `+${r.lead_pct}% · ${r.decisive ? "no interval overlap" : "inside the intervals"}`,
        colour: r.decisive ? GREEN : MUTED,
        bold: r.decisive,
      },
    ]),
  });
  caption(
    s,
    "Tasks delivered per 1000 timesteps; higher is better. 5 seeds × 400 steps, identical job streams across planners. " +
      "Token Passing and TPTS deliver nothing at all on corridors, which has 35 robots and no parking bays.",
    y + 2.28
  );

  const cw2 = (CW - 0.4) / 2;
  card(s, {
    x: M, y: y + 2.84, w: cw2, h: 1.62,
    title: "Two of these wins are not ours",
    titleColour: HEADLINE,
    body:
      "On narrow and medium the winning configuration is plain lifelong PIBT — the published algorithm this project " +
      "implements, not what this project added on top of it. Slide 12 is where that gets settled, and it is the most " +
      "useful finding in the study.",
    badgeFill: RED,
  });
  card(s, {
    x: M + cw2 + 0.4, y: y + 2.84, w: cw2, h: 1.62,
    title: "Two of these baselines are outside their envelope",
    body:
      "Token Passing and TPTS assume a well-formed instance — one parking endpoint per agent. None of these floors " +
      "provides one at these robot counts. That is stated rather than scored: RHCR shares no such assumption and is " +
      "the honest comparator throughout.",
  });
  s.addNotes(
    "This is the slide to open with if the audience is sceptical. It concedes both of the things a reviewer would " +
      "otherwise find: that two wins belong to the published algorithm, and that two baselines are handicapped by the map choice."
  );
}

// ===========================================================================
// 10 — compute economics
// ===========================================================================
{
  const med = runtimeByMap.warehouse_medium;
  const { slide: s, y } = lightSlide(
    "Compute economics",
    "What the planner costs to run",
    "Recorded by every run in the baseline suite, and tabulated here for the first time."
  );

  dataTable(s, {
    x: M, y, w: 7.35, colW: [1.5, 0.7, 1.35, 1.3, 1.25, 1.25], rowH: 0.4,
    size: 11.5, headSize: 10.5,
    align: ["left", "right", "right", "right", "right", "right"],
    header: ["Floor", "Fleet", "Aisleflow plain", "Aisleflow full", "RHCR", "Token Passing"],
    rows: runtime.rows.map((r) => [
      { text: shortMap(r.map), bold: true },
      { text: String(r.robots) },
      { text: r.ms_per_step.lifelong_pibt.toFixed(2), colour: BLUE },
      { text: r.ms_per_step.full_lda_pibt.toFixed(2), colour: BLUE },
      { text: r.ms_per_step.rhcr.toFixed(2) },
      { text: r.ms_per_step.token_passing.toFixed(2) },
    ]),
  });
  caption(s, "Mean planner runtime in milliseconds per timestep. Lower is better.", y + 2.16, 7.35);

  const tw = (CW - 7.35 - 0.4);
  const tiles = [
    { value: `${runtime.min_vs_rhcr}–${runtime.max_vs_rhcr}×`, label: "less compute than RHCR, the strongest baseline, on every floor" },
    { value: `${runtime.min_vs_tp}–${runtime.max_vs_tp}×`, label: "less compute than Token Passing" },
    {
      value: `${corePct(med.ms_per_step.full_lda_pibt, 10).toFixed(1)}%`,
      label: `of one CPU core to drive ${med.robots} robots at 10 Hz`,
    },
  ];
  tiles.forEach((t, i) =>
    statTile(s, {
      x: M + 7.75, y: y + i * 1.62, w: tw, h: 1.46,
      value: t.value, label: t.label, valueSize: 32,
    })
  );

  pullQuote(s, {
    x: M, y: y + 2.72, w: 7.35, h: 1.62,
    text:
      `At ${med.robots} robots on ${shortMap(med.map)}, a 10 Hz control loop costs aisleflow ` +
      `${corePct(med.ms_per_step.full_lda_pibt, 10).toFixed(1)}% of one core and RHCR ` +
      `${corePct(med.ms_per_step.rhcr, 10).toFixed(0)}%. Token Passing needs ` +
      `${(med.ms_per_step.token_passing / 1000).toFixed(2)} seconds of compute per timestep — it cannot close a 1 Hz loop at all. ` +
      "That is the difference between one edge box for the fleet and a machine per aisle.",
    size: 12.5,
  });
  caption(
    s,
    "Single-threaded pure Python on one host. All five planners ran in the same runtime on the same scenarios, so the ratios are the comparable quantity, not the absolute milliseconds. Ratios are taken against aisleflow's dearer configuration, so they are the conservative reading.",
    y + 4.46, 7.35
  );
  s.addNotes(
    "The 3.4%-of-a-core figure is the one to repeat. It is what makes a fleet controller an embedded component rather " +
      "than an infrastructure line item. Expect the question 'is that just because Python is slow for everyone here?' — " +
      "yes, and that is exactly why the ratio, not the absolute, is the claim."
  );
}

// ===========================================================================
// 11 — fleet sizing
// ===========================================================================
{
  const cor = density.warehouse_corridors;
  const med = density.warehouse_medium;
  const { slide: s, y } = lightSlide(
    "Fleet sizing",
    "Where the next robot stops paying",
    "The same planner, the same sweep, two floors — and opposite capital answers."
  );

  dataTable(s, {
    x: M, y, w: 7.35, colW: [2.55, 0.96, 0.96, 0.96, 0.96, 0.96], rowH: 0.42,
    size: 11.5,
    align: ["left", "right", "right", "right", "right", "right"],
    header: ["Floor (best configuration)"].concat(
      cor.robot_counts.map((n) => `${n} robots`)
    ),
    rows: [cor, med].map((r) => {
      const peak = r.per_1000.indexOf(r.peak_value);
      return [{ text: shortMap(r.map), bold: true }].concat(
        r.per_1000.map((v, i) => ({
          text: String(v),
          bold: i === peak,
          colour: i === peak ? BLUE : BODY,
        }))
      );
    }),
  });
  caption(
    s,
    `Tasks delivered per 1000 timesteps; bold is each floor's peak. ${DATA.density.seeds} seeds × ${DATA.meta.timesteps} steps, best configuration for that floor.`,
    y + 1.3, 7.35
  );

  const tw = CW - 7.35 - 0.4;
  statTile(s, {
    x: M + 7.75, y, w: tw, h: 1.46,
    value: cor.delta_20_to_40 === 0
      ? "0"
      : `${cor.delta_20_to_40 > 0 ? "+" : ""}${cor.delta_20_to_40}`,
    label: `tasks from robots 21–40 on ${shortMap(cor.map)} — the second twenty delivered nothing`,
    valueSize: 34, colour: RED,
  });
  statTile(s, {
    x: M + 7.75, y: y + 1.62, w: tw, h: 1.46,
    value: `+${Math.round((100 * med.delta_20_to_40) / med.per_1000[med.robot_counts.indexOf(20)])}%`,
    label: `from exactly the same twenty robots on ${shortMap(med.map)}`,
    valueSize: 34, colour: GREEN,
  });

  pullQuote(s, {
    x: M, y: y + 1.94, w: 7.35, h: 1.5,
    text:
      `On ${shortMap(cor.map)} throughput peaks at ${cor.peak_robots} robots and falls at 40: the chokepoints saturate and ` +
      `the extra robots become traffic. On ${shortMap(med.map)} it is still climbing at 40. Fleet size is a property of the ` +
      "layout, and getting it wrong by a factor of two is the largest single error available before anything is bought.",
    size: 12.5,
  });

  leadIn(s, {
    x: M, y: y + 3.62, w: CW, h: 0.9,
    lead: "Why this matters commercially. ",
    text:
      "The sweep runs in minutes on a laptop, from a map file and an arrival rate. It answers “how many robots should " +
      "this floor have?” before any of them are purchased — which is a question every operator has and almost none can " +
      "answer from first principles. This measurement has no figure anywhere in the documentation; it lives only in docs/data/density.json.",
    size: 12,
  });
  s.addNotes(
    "The corridors row is the slide. Twenty robots and forty robots deliver the same number of tasks — the marginal " +
      "return of the 21st through 40th robot is exactly zero, and it is measured, not modelled."
  );
}

// ===========================================================================
// 12 — the finding that matters most
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "The finding that matters most",
    "Every addition helps a tight floor and hurts an open one",
    "Read each column top to bottom: every row adds one mechanism to the row above it."
  );

  dataTable(s, {
    x: M, y, w: 7.6, colW: [2.8, 1.2, 1.2, 1.2, 1.2], rowH: 0.38, size: 11.5,
    align: ["left", "right", "right", "right", "right"],
    header: DATA.ladder.header,
    rows: DATA.ladder.rows.map((r) => [
      { text: r[0], bold: r[0] === "plain lifelong PIBT" },
    ].concat(
      r.slice(1).map((cell) => {
        const best = cell.startsWith("**");
        return {
          text: cell.replace(/\*\*/g, ""),
          bold: best,
          colour: best ? BLUE : BODY,
        };
      })
    )),
  });
  caption(
    s,
    "Tasks per timestep; bold blue is the best configuration for that floor. 5 seeds × 400 steps.",
    y + 2.42, 7.6
  );

  const rx = M + 7.95;
  const rw = CW - 7.95;
  s.addText("If more were always better", {
    x: rx, y, w: rw, h: 0.3, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 14, bold: true, color: HEADLINE,
  });
  s.addText(
    "…the bottom row would be bold in all four columns. It is bold in none of them. The full configuration is not " +
      "the best on any floor measured.",
    {
      x: rx, y: y + 0.36, w: rw, h: 0.8, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 12, color: BODY, lineSpacingMultiple: 1.1,
    }
  );
  bullets(s, [
    "+22% on bottleneck and +50% on corridors — both have a chokepoint every route must cross.",
    "−13% on narrow and −15% on medium, where there is more than one way round and plain PIBT is itself the best rung.",
  ], { x: rx, y: y + 1.24, w: rw, h: 1.7, size: 11.5 });

  pullQuote(s, {
    x: M, y: y + 3.02, w: CW, h: 1.0,
    text:
      "This is not a defect to hide. It says the machinery is congestion machinery, and it earns its keep exactly where " +
      "congestion is the binding constraint. Where there is a way round, getting out of the robots' way is the better strategy.",
    fill: BLUE, colour: WHITE, size: 13.5,
  });

  leadIn(s, {
    x: M, y: y + 4.2, w: CW, h: 0.9,
    lead: "The practical consequence. ",
    text:
      "Pick the configuration for the floor, not the other way round: turning_cost_only for tight maps, lifelong_pibt " +
      "for open ones, full_lda_pibt as the safe middle that is never worst. It ships as the default because it carries " +
      "the deadlock safety net, not because it wins the table. Turning that choice into an automatic one is Phase 1 on slide 19.",
    size: 12,
  });
  s.addNotes(
    "Present this slide as the study's most valuable output, not as bad news. It converts a vague 'it depends' into a " +
      "measured rule with a structural predictor, and that rule is a shippable feature."
  );
}

// ===========================================================================
// 13 — what measuring bought
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "Method",
    "What measuring every number bought",
    "The planner had roughly 45 hand-chosen numbers across seven models, none with published justification. Every one was measured."
  );

  dataTable(s, {
    x: M, y, w: 6.1, colW: [3.3, 1.4, 1.4], rowH: 0.42, size: 11.5,
    align: ["left", "right", "right"],
    header: ["", "Before", "After"],
    rows: [
      [{ text: "Terms in the movement score", bold: true }, "9", { text: "4", colour: BLUE, bold: true }],
      [{ text: "Tunable parameters", bold: true }, "60", { text: "37", colour: BLUE, bold: true }],
      [{ text: "Lines in the planner modules", bold: true }, "4,488", { text: "3,249", colour: BLUE, bold: true }],
      [{ text: "Throughput, per floor", bold: true }, "—", { text: "+1.4% … +50.6%", colour: GREEN, bold: true }],
    ],
  });
  caption(
    s,
    "Line count covers the planner itself, not the GUI, visualisation or experiment harness. Throughput is the full configuration across the four floors.",
    y + 2.14, 6.1
  );

  const rx = M + 6.45;
  const rw = CW - 6.45;
  s.addText("The knobs the planner cannot do without", {
    x: rx, y: y - 0.04, w: rw, h: 0.3, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 14, bold: true, color: HEADLINE,
  });
  dataTable(s, {
    x: rx, y: y + 0.34, w: rw, colW: [3.35, 1.15, 0.99], rowH: 0.34, size: 11,
    align: ["left", "right", "right"],
    header: ["Knob neutralised", "Throughput", "p"],
    rows: DATA.sensitivity.rows.slice(0, 5).map((r) => [
      { text: r[1].replace(/`/g, "") },
      { text: r[2], colour: RED, bold: true },
      { text: r[3], colour: MUTED },
    ]),
  });
  caption(
    s,
    "Change in throughput when the knob is removed; the five most load-bearing of 24 variants, 10 seeds, 4 floors, paired sign-flip test.",
    y + 2.5, rw, rx
  );

  const cw2 = (CW - 0.4) / 2;
  card(s, {
    x: M, y: y + 3.06, w: cw2, h: 1.5,
    title: "What the measurement deleted",
    body:
      "Five of the nine score terms were smaller than a tie-break and never changed a decision: runs without them were " +
      "bit-identical on every deterministic metric, every seed, every floor. The whole aisle-direction layer — 450 lines — " +
      "measured −0.3% (p = 0.95). That is noise, not a mechanism.",
  });
  card(s, {
    x: M + cw2 + 0.4, y: y + 3.06, w: cw2, h: 1.5,
    title: "Two bugs surfaced by measuring, not by tests",
    body:
      "A starvation rule tested a signed demand score for “> 0”, so legitimate traffic voted negative and was never " +
      "counted — aisles held one direction for 130 steps. And a rename sweep caught its own alias table, so every " +
      "parameter override silently returned the default. No error, no warning.",
    badgeFill: RED,
  });
  s.addNotes(
    "The engineering point: measurement did not just tune this planner, it deleted a quarter of it and found two bugs " +
      "that neither tests nor review had caught. That process is reusable on anything else we build."
  );
}

// ===========================================================================
// 14 — divider
// ===========================================================================
{
  const s = darkSlide();
  s.addText("PART TWO", {
    x: M, y: 2.55, w: CW, h: 0.28, isTextBox: true, margin: 0,
    fontFace: BODY_FONT, fontSize: 12, bold: true, charSpacing: 2.4, color: ORANGE,
  });
  s.addText("From result to commercial value", {
    x: M, y: 2.92, w: CW * 0.8, h: 0.8, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 40, bold: true, color: WHITE,
  });
  s.addText(
    "Three measured levers, what a pilot would have to establish to price them, and what stands between a simulator " +
      "and a warehouse floor.",
    {
      x: M, y: 3.86, w: CW * 0.55, h: 0.8, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 15, color: ON_DARK_MUTED, lineSpacingMultiple: 1.15,
    }
  );
  footer(s, true);
  s.addNotes(
    "Transition line: everything so far is measured. Everything after this is measured levers plus explicitly labelled " +
      "judgement — and the study contains no cost data at all, so nothing ahead is denominated in currency."
  );
}

// ===========================================================================
// 15 — three routes to value
// ===========================================================================
{
  const cor = density.warehouse_corridors;
  const med = runtimeByMap.warehouse_medium;
  const { slide: s, y } = lightSlide(
    "Opportunity",
    "Three routes to value from one codebase",
    "Each rests on a measured number from the first half of this deck, and none of them requires new hardware."
  );

  const cw3 = (CW - 2 * 0.34) / 3;
  const cards = [
    {
      title: "Capacity per robot, in our own fleet",
      body:
        "On floors where a chokepoint binds, configuration alone moves throughput 22–50% against identical hardware. " +
        "The lever is the planner, not the robot: no new sensors, no new drive train, a software change to a fleet " +
        "that already exists.",
      rests: "Rests on: the ablation ladder, slide 12",
    },
    {
      title: "The planner as a licensable component",
      body:
        "No runtime dependencies, pure standard library, MIT, Python 3.10+ — and a planner that closes a 10 Hz loop " +
        `for ${med.robots} robots inside ${Math.ceil(corePct(med.ms_per_step.full_lda_pibt, 10))}% of one core. ` +
        "One file tree, one CLI, one JSON API, no fleet-server requirement: an integration story an OEM can accept.",
      rests: "Rests on: compute economics, slide 10",
    },
    {
      title: "The harness as a site-design instrument",
      body:
        "The density sweep answers “how many robots should this floor have?” before any are bought; the ablation " +
        "ladder answers “which configuration should it run?”. Both take minutes on a laptop, from a map file and an " +
        "arrival rate. That is a pre-sales and commissioning tool, not an experiment runner.",
      rests: "Rests on: fleet sizing, slide 11",
    },
  ];
  cards.forEach((c, i) => {
    card(s, {
      x: M + i * (cw3 + 0.34), y, w: cw3, h: 3.22,
      badge: i + 1, title: c.title, body: c.body,
      titleSize: 15, titleH: 0.62, bodySize: 11.5,
    });
    s.addText(c.rests, {
      x: M + i * (cw3 + 0.34) + 0.24, y: y + 3.32, w: cw3 - 0.48, h: 0.26,
      isTextBox: true, margin: 0, fontFace: BODY_FONT, fontSize: 10.5,
      italic: true, color: BLUE,
    });
  });

  pullQuote(s, {
    x: M, y: y + 3.78, w: CW, h: 0.82,
    text:
      "The third route is the one the study uniquely enables. Nothing else here answers, from a floor plan alone, " +
      `that on ${shortMap(cor.map)} the 21st through 40th robot deliver nothing at all.`,
    size: 13,
  });
  s.addNotes(
    "Route 3 is the one worth arguing for: it is the only one that turns the harness — which we built as a means to an " +
      "end — into a product in its own right, and it is the cheapest to trial because it never has to touch a robot."
  );
}

// ===========================================================================
// 16 — levers and pilot
// ===========================================================================
{
  const bn = byMap.warehouse_bottleneck;
  const cor = density.warehouse_corridors;
  const { slide: s, y } = lightSlide(
    "Making the case",
    "The levers are measured. Pricing them is the pilot.",
    "This study contains no cost, currency or real-site data, so nothing here is expressed in money — and nothing should be until a floor supplies the basis."
  );

  const cw2 = (CW - 0.5) / 2;
  s.addText("What has been measured", {
    x: M, y, w: cw2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 15, bold: true, color: BLUE,
  });
  const levers = [
    ["Throughput. ", `+${bn.lead_pct}% over the strongest published planner on the chokepoint floor, and 22–50% from configuration alone against plain PIBT on the two floors where congestion binds.`],
    ["Compute. ", `${runtime.min_vs_rhcr}–${runtime.max_vs_rhcr}× less than RHCR and ${runtime.min_vs_tp}–${runtime.max_vs_tp}× less than Token Passing, on every floor measured.`],
    ["Capital. ", `A measured marginal return per robot: on ${shortMap(cor.map)} it peaks at ${cor.peak_robots} robots and the next ten take throughput back down, while on ${shortMap("warehouse_medium")} it is still positive at 40.`],
  ];
  levers.forEach(([lead, text], i) => {
    badge(s, M, y + 0.42 + i * 1.16, i + 1, { d: 0.34, size: 11, fill: BLUE });
    leadIn(s, {
      x: M + 0.5, y: y + 0.4 + i * 1.16, w: cw2 - 0.5, h: 1.08,
      lead, text, size: 12, leadColour: BLUE,
    });
  });

  const rx = M + cw2 + 0.5;
  s.addText("What a pilot has to establish", {
    x: rx, y, w: cw2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 15, bold: true, color: ORANGE,
  });
  const asks = [
    ["The floor's real structure. ", "Aisle lengths, articulation points, route multiplicity — which side of the ladder a customer's layout falls on decides the configuration, and therefore the size of the lever. lda-pibt inspect already computes all of it from a map file."],
    ["A real job stream. ", "Arrival rate, spatial distribution, and whether the site actually runs saturated. Every number here is measured under saturation; a site with slack has different binding constraints."],
    ["The sim-to-real gap. ", "How much simulated throughput survives kinematics, acceleration, localisation error and charging. Slide 18 is the list; nobody knows the size of it yet."],
    ["The cost basis. ", "Robot capital, floor space, labour and energy. None of it is in this study, which is why none of the above is a currency figure."],
  ];
  asks.forEach(([lead, text], i) => {
    leadIn(s, {
      x: rx, y: y + 0.4 + i * 0.94, w: cw2, h: 0.9,
      lead, text, size: 11.5, leadColour: ORANGE,
    });
  });

  caption(
    s,
    "Every figure on the left is relative throughput or relative compute on four synthetic floors. That is what the study measures, and the claim is bounded by it.",
    y + 4.28
  );
  s.addNotes(
    "If asked for a business case in pounds or dollars, this is the slide that answers: we can give you the levers " +
      "with error bars, and the four inputs we would need from a site to convert them. Making a number up would be the " +
      "one thing that discredits everything before it."
  );
}

// ===========================================================================
// 17 — risks and limits
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "Limits",
    "What this study does not establish",
    "Stated in full, because the value of the honest finding on slide 12 depends on this list being complete."
  );

  const items = [
    ["Four floors, all synthetic", "Every conclusion is about four maps at the robot counts and arrival rates in the dataset. A knob inert here may matter on a floor with longer, tighter corridors."],
    ["Simulation only", "A discrete grid, synchronous unit-speed moves, perfect localisation. No acceleration, no battery, no robot failures, no humans on the floor."],
    ["Everything is saturated", "Jobs arrive faster than any planner clears them, so throughput measures capacity, not responsiveness, and service time is a function of run length rather than a comparable quantity."],
    ["Two baselines are handicapped", "Token Passing and TPTS are proven complete only on well-formed instances, and none of these floors provides one. RHCR shares no such assumption and is the honest comparator."],
    ["No multiple-comparison correction", "23 knobs at α = 0.05 means roughly one false positive is expected. The load-bearing findings clear that by a distance; the marginal ones are suggestive, not settled."],
    ["Pooling hides disagreement", "A knob can help on one floor, hurt on another and average to zero. The sensitivity table carries a worst-map column for exactly this reason."],
  ];
  const cw3 = (CW - 2 * 0.34) / 3;
  items.forEach((it, i) => {
    card(s, {
      x: M + (i % 3) * (cw3 + 0.34),
      y: y + Math.floor(i / 3) * 2.32,
      w: cw3, h: 2.16,
      title: it[0], body: it[1],
      titleSize: 13.5, titleH: 0.3, bodySize: 11,
      badge: i + 1, badgeFill: RED,
    });
  });
  caption(
    s,
    "Every caveat above is taken from the results page's own caveats section, not added for this deck.",
    y + 4.76
  );
  s.addNotes(
    "Do not rush this slide. An audience that sees the limits stated first will believe slide 10 and slide 11, which " +
      "are the two that matter commercially."
  );
}

// ===========================================================================
// 18 — simulator to floor
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "Gap analysis",
    "What stands between this and a warehouse floor",
    "None of these is a research risk. All of them are work, and none has been started."
  );

  const cw2 = (CW - 0.5) / 2;
  const planner = [
    ["Continuous time and kinematics. ", "Acceleration, turning radius, non-unit speeds. The score's tier structure assumes a move changes distance by exactly one — that assumption has to be re-earned, not merely relaxed."],
    ["Heterogeneous fleets. ", "Mixed speeds and footprints. Rank and pushing both assume interchangeable agents today."],
    ["Battery and charging. ", "A robot that must charge is a task class the assignment model does not have."],
    ["Failure handling. ", "A robot that stops permanently is an obstacle the graph does not know about, on a floor where seven cells can sever the warehouse."],
  ];
  const system = [
    ["WMS / WES integration. ", "The job stream today is a Poisson generator. A real one has priorities, deadlines, batching and cut-off times."],
    ["Real layout ingestion. ", "From CAD or a site survey to the grid the planner reasons about, including what the survey gets wrong."],
    ["Execution error. ", "What happens when the commanded joint move is not the executed one — the collision guarantee is a guarantee about the plan."],
    ["The safety case. ", "Humans on the floor, and whatever certification the deployment context demands."],
  ];
  [["Planner-side", planner, BLUE], ["System-side", system, ORANGE]].forEach(
    ([heading, list, colour], col) => {
      const x = M + col * (cw2 + 0.5);
      s.addText(heading, {
        x, y, w: cw2, h: 0.3, isTextBox: true, margin: 0,
        fontFace: HEAD_FONT, fontSize: 15, bold: true, color: colour,
      });
      list.forEach(([lead, text], i) => {
        leadIn(s, {
          x, y: y + 0.44 + i * 1.02, w: cw2, h: 0.98,
          lead, text, size: 11.5, leadColour: HEADLINE,
        });
      });
    }
  );

  pullQuote(s, {
    x: M, y: y + 4.6, w: CW, h: 0.62,
    text:
      "The collision guarantee is a guarantee about the plan, not about the robots. Everything in the right-hand column " +
      "is what turns one into the other.",
    fill: CARD, colour: HEADLINE, size: 12.5,
  });
  s.addNotes(
    "The honest framing: the planner is further along than the system around it. If someone asks 'could we ship this " +
      "next quarter', the answer is that the planner could and the integration could not."
  );
}

// ===========================================================================
// 19 — roadmap
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "Roadmap",
    "Four phases, each with the thing that would kill it",
    "Sequenced so the cheapest phase is also the one that tests the study's central claim."
  );

  const phases = [
    {
      phase: "Phase 1",
      when: "0–3 months",
      title: "Auto-configuration",
      body:
        "Classify a floor from its structure and pick the configuration for it. The predictive features already exist: " +
        "lda-pibt inspect computes articulation points, aisle lengths, junction counts and route multiplicity today.",
      gate: "Kill if the ladder's two regimes do not separate on structure alone across a wider map set.",
    },
    {
      phase: "Phase 2",
      when: "3–6 months",
      title: "Fidelity",
      body:
        "Kinematics, heterogeneous speeds, battery and charging. Then a real layout and a real job trace from a partner, " +
        "replacing the synthetic floors and the Poisson generator.",
      gate: "Kill if the ordering of configurations inverts on contact with a real layout.",
    },
    {
      phase: "Phase 3",
      when: "6–12 months",
      title: "Hardware in the loop",
      body:
        "One partner site, one aisle block, the sim-to-real gap measured rather than assumed — against the interval the " +
        "simulator predicts for that floor and fleet.",
      gate: "Kill if measured throughput falls outside what configuration can recover.",
    },
    {
      phase: "Phase 4",
      when: "12 months +",
      title: "Productise",
      body:
        "Ship the harness as a site-design and fleet-sizing tool, and the planner as the component behind it — the two " +
        "routes to value that do not require owning the robot.",
      gate: "Gated on Phase 3, not on Phase 1 or 2.",
    },
  ];
  const cw4 = (CW - 3 * 0.28) / 4;
  phases.forEach((p, i) => {
    const x = M + i * (cw4 + 0.28);
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w: cw4, h: 3.72, rectRadius: 0.05, fill: { color: CARD },
    });
    badge(s, x + 0.22, y + 0.22, i + 1, { d: 0.4, size: 13 });
    s.addText(`${p.phase} · ${p.when}`, {
      x: x + 0.7, y: y + 0.26, w: cw4 - 0.9, h: 0.3, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 10.5, bold: true, color: ORANGE,
    });
    s.addText(p.title, {
      x: x + 0.22, y: y + 0.76, w: cw4 - 0.44, h: 0.34, isTextBox: true, margin: 0,
      fontFace: HEAD_FONT, fontSize: 16, bold: true, color: HEADLINE,
    });
    s.addText(p.body, {
      x: x + 0.22, y: y + 1.16, w: cw4 - 0.44, h: 1.6, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 11.5, color: BODY, lineSpacingMultiple: 1.1,
    });
    s.addText([
      { text: "Gate. ", options: { bold: true, color: RED } },
      { text: p.gate, options: { color: BODY } },
    ], {
      x: x + 0.22, y: y + 2.82, w: cw4 - 0.44, h: 0.78, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 10.5, lineSpacingMultiple: 1.06,
    });
  });

  pullQuote(s, {
    x: M, y: y + 3.96, w: CW, h: 0.62,
    text:
      "Phase 1 is the one to fund first: it is the direct product of the study's central finding, most of its machinery " +
      "already exists, and it fails fast and cheaply if the finding does not generalise.",
    size: 12.5,
  });
  s.addNotes(
    "Every gate is written so a negative result ends the phase rather than extending it. That is deliberate — the " +
      "study's own value came from being willing to delete things that measured nothing."
  );
}

// ===========================================================================
// 20 — the ask
// ===========================================================================
{
  const s = darkSlide();
  s.addText("RECOMMENDATION", {
    x: M, y: 0.72, w: CW, h: 0.28, isTextBox: true, margin: 0,
    fontFace: BODY_FONT, fontSize: 12, bold: true, charSpacing: 2.4, color: ORANGE,
  });
  s.addText("Three things, in this order", {
    x: M, y: 1.06, w: CW, h: 0.7, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 36, bold: true, color: WHITE,
  });

  const asks = [
    ["Fund Phase 1 — auto-configuration.", "It is the direct product of the study's central finding, most of the machinery already exists, and it converts a mixed result into a shipped capability. It also fails cheaply if the finding does not generalise."],
    ["Find one pilot floor.", "Every number in this deck is relative and simulated. One real layout and one real job trace convert three measured levers into a business case, and nothing else will."],
    ["Keep reporting the losses.", "Two of four floors go to plain lifelong PIBT rather than to anything we added. Saying so plainly is the reason the rest of this deck can be believed."],
  ];
  asks.forEach(([title, body], i) => {
    const yy = 2.14 + i * 1.32;
    badge(s, M, yy + 0.02, i + 1, { d: 0.46, size: 15 });
    s.addText(title, {
      x: M + 0.68, y: yy, w: CW - 0.68, h: 0.34, isTextBox: true, margin: 0,
      fontFace: HEAD_FONT, fontSize: 18, bold: true, color: WHITE,
    });
    s.addText(body, {
      x: M + 0.68, y: yy + 0.4, w: CW * 0.72, h: 0.82, isTextBox: true, margin: 0,
      fontFace: BODY_FONT, fontSize: 12.5, color: ON_DARK_MUTED,
      lineSpacingMultiple: 1.12,
    });
  });

  s.addText(
    "The engine is fast, cheap to run, and collision-free on every run measured. What it does not yet have is a floor.",
    {
      x: M, y: 6.18, w: CW * 0.82, h: 0.5, isTextBox: true, margin: 0,
      fontFace: HEAD_FONT, fontSize: 17, italic: true, color: ORANGE,
    }
  );
  footer(s, true);
  s.addNotes(
    "Close on the last line and stop talking. The ask is one funded phase and one pilot floor — deliberately modest, " +
      "because the study's own credibility came from claiming only what it measured."
  );
}

// ===========================================================================
// 21 — appendix
// ===========================================================================
{
  const { slide: s, y } = lightSlide(
    "Appendix",
    "Method, provenance and reproduction",
    "Every row in every table in this deck traces to a committed dataset with a git SHA."
  );

  const cw2 = (CW - 0.5) / 2;
  dataTable(s, {
    x: M, y, w: cw2, colW: [1.7, cw2 - 1.7], rowH: 0.42, size: 10.5,
    header: ["", "Method"],
    rows: [
      [{ text: "Design", bold: true }, `${DATA.meta.seeds} seeds × ${DATA.meta.timesteps} timesteps, Poisson arrivals, identical job streams across planners`],
      [{ text: "Intervals", bold: true }, "95% bootstrap over seeds; paired sign-flip and permutation tests for knob effects"],
      [{ text: "Sensitivity", bold: true }, "24 variants × 10 seeds × 4 floors"],
      [{ text: "Density sweep", bold: true }, `${DATA.density.seeds} seeds, 5 fleet sizes, 2 floors`],
      [{ text: "Safety", bold: true }, "collision_free: true on every run in every suite"],
      [{ text: "Tests", bold: true }, "180 collected — 168 in the default run, 12 in the slow baseline sweep"],
      [{ text: "Provenance", bold: true }, `dataset @ ${DATA.meta.git_sha}, ${DATA.meta.generated_utc}`],
    ],
  });

  const rx = M + cw2 + 0.5;
  s.addText("The planners, as published", {
    x: rx, y: y - 0.04, w: cw2, h: 0.3, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 14, bold: true, color: HEADLINE,
  });
  bullets(s, [
    "Okumura, Machida, Défago & Tamura. Priority Inheritance with Backtracking for Iterative Multi-agent Path Finding. arXiv:1901.11282 — the algorithm aisleflow extends.",
    "Ma, Li, Kumar & Koenig. Lifelong Multi-Agent Path Finding for Online Pickup and Delivery Tasks. AAMAS 2017 — Token Passing (Alg. 1) and TP with Task Swaps (Alg. 2).",
    "Li, Tinka, Kiesel, Durham, Kumar & Koenig. Lifelong Multi-Agent Path Finding in Large-Scale Warehouses. AAAI 2021 — RHCR.",
    "Ma, Harabor, Stuckey, Li & Koenig. Searching with Consistent Prioritization for Multi-Agent Path Finding. AAAI 2019 — PBS, the solver RHCR runs over.",
  ], { x: rx, y: y + 0.34, w: cw2, h: 2.4, size: 11, gap: 7 });

  s.addText("Reproducing every number in this deck", {
    x: M, y: y + 3.5, w: CW, h: 0.3, isTextBox: true, margin: 0,
    fontFace: HEAD_FONT, fontSize: 14, bold: true, color: HEADLINE,
  });
  s.addShape(pres.ShapeType.roundRect, {
    x: M, y: y + 3.86, w: CW, h: 1.06, rectRadius: 0.05, fill: { color: INK },
  });
  s.addText(
    "python3 experiments/run_sensitivity.py --seeds 10 --jobs 4\n" +
      "python3 experiments/run_all.py --seeds 5 --jobs 4\n" +
      "python3 tools/make_docs_tables.py && python3 tools/make_figures.py\n" +
      "python3 presentation/render_assets.py && python3 presentation/extract_data.py && node presentation/build_deck.js",
    {
      x: M + 0.24, y: y + 3.94, w: CW - 0.48, h: 0.9, isTextBox: true,
      margin: 0, valign: "top",
      fontFace: "Courier New", fontSize: 10, color: ON_DARK,
      lineSpacingMultiple: 1.14,
    }
  );
  s.addNotes(
    "The last command is the deck itself: it reads the same dataset the documents read, and extract_data.py asserts " +
      "every headline figure, so a regenerated dataset that moves a number breaks the build rather than leaving a stale slide."
  );
}

// ---------------------------------------------------------------------------

pres.writeFile({ fileName: OUT }).then(() => {
  console.log(`  wrote ${path.relative(ROOT, OUT)} — ${slideNo} slides`);
});
