#!/usr/bin/env python3
"""Draw the result figures in ``docs/figures/`` from ``docs/data/``.

Five figures, numbered in the order the argument makes them, each embedded in
`docs/05-results.md` under the section it belongs to:

    01-vs-baselines          against the three published planners, per map
    02-throughput-vs-robots  how that comparison moves as the floor fills up
    03-ablation-ladder       which of its own mechanisms buys which part of it
    04-knobs                 what every remaining parameter is worth
    05-the-maps              the floors all of it was measured on

Two figures this file used to draw are gone rather than tidied: a matrix of
ratios with five numbers and a capitalised verdict in every cell, and a
double-panel matrix of the same comparison at two resolutions. Both encoded
the argument rather than showing the measurement, and neither could be read
faster than the bar chart that replaced them.

Each is built to be read without the surrounding prose: the title states the
finding rather than naming the plot, the subtitle says what "better" looks like
on that axis, every bar and cell carries its own value, and the caption carries
the git SHA, seed count and horizon of the run behind it.

Every figure reads `docs/data/*.json`, written by `experiments/run_all.py` and
`experiments/run_sensitivity.py`, and nothing here computes a simulation or
invents a number: the intervals are the bootstrap intervals the experiment
recorded, and the p-values are its permutation tests.

Usage::

    python3 tools/make_figures.py                # every figure it has data for
    python3 tools/make_figures.py --only 03-where-it-wins
    python3 tools/make_figures.py --list

Figures are written as `.svg` only -- GitHub renders it inline, it stays
legible at any zoom, and `svg.fonttype: none` keeps every label as real text
rather than glyph outlines, so a reader can select and search it.
Needs `matplotlib` (``pip install -e ".[viz]"``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
OUT_DIR = ROOT / "docs" / "figures"

# --------------------------------------------------------------------------
# palette
#
# The categorical slots are used in fixed order and never cycled; the first
# four clear every adjacent-form gate (worst CVD dE 9.1, normal-vision 22.9 on
# this surface). Two of the four sit below 3:1 against the surface, so every
# figure that uses them carries visible direct labels rather than relying on
# the colour alone -- every bar, cell and rung on these figures prints its own
# value.
#
# These figures are deliberately single-mode: they are rendered inline on
# GitHub, which does not pass a viewer theme down into an embedded SVG, so the
# surface is painted explicitly rather than left transparent.
# --------------------------------------------------------------------------

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
EMPHASIS = "#2a78d6"
DEEMPHASIS = "#c3c2b7"

#: the two poles of a signed axis: ahead in blue, behind in red
DIVERGING_HIGH = "#2a78d6"
DIVERGING_LOW = "#d03b3b"

#: status, for verdicts -- always shipped with a word, never colour alone
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "critical": "#d03b3b",
    "neutral": MUTED,
}

#: the maps, ordered tightest first: how many independent ways round there
#: are between a pickup and a delivery
MAP_ORDER = [
    "warehouse_bottleneck",
    "warehouse_corridors",
    "warehouse_narrow",
    "warehouse_medium",
]
#: the floors where congestion is the binding constraint, and so the ones the
#: added mechanisms are for. Shown by emphasis and grouping, never asserted:
#: the numbers on every figure say which side of the line each map fell on.
DESIGNED_FOR = {"warehouse_bottleneck", "warehouse_corridors"}

VARIANT_LABEL = {
    "pibt_baseline": "one-shot PIBT",
    "lifelong_pibt": "plain lifelong PIBT",
    "turning_cost_only": "+ turning cost",
    "lane_bonus_only": "+ stay-in-lane bonus",
    "congestion_only": "+ crowding",
    "recovery_only": "+ deadlock recovery",
    "full_lda_pibt": "aisleflow (full)",
    "recovery_full_ladder": "recovery, full ladder",
    "recovery_uncorroborated": "recovery, uncorroborated",
    "token_passing": "Token Passing",
    "token_passing_task_swaps": "TP + task swaps",
    "rhcr": "RHCR",
}


def _stderr(values: Sequence[float]) -> float:
    """Standard error of the mean, for the suites that record raw seeds."""
    import statistics

    if not values or len(values) < 2:
        return 0.0
    return statistics.stdev(values) / (len(values) ** 0.5)


def label_of(variant: str) -> str:
    return VARIANT_LABEL.get(variant, variant.replace("_", " "))


def short_map(name: str) -> str:
    return name.replace("warehouse_", "")


# --------------------------------------------------------------------------
# units
#
# The experiment records throughput as tasks per timestep, which puts every
# interesting number in this project between 0.001 and 0.5 and makes two
# planners three decimals apart look identical at a glance. Every figure
# reports the same quantity per 1000 timesteps instead: same measurement,
# same ratios, integers a reader can hold in their head.
# --------------------------------------------------------------------------

THROUGHPUT_UNIT = "tasks delivered per 1000 timesteps"


def per_1000(value: float) -> float:
    return 1000.0 * value


#: What each map *is*, printed next to its name so a reader never has to
#: remember which of the four is which. Geometry, not verdict: an earlier
#: version of this labelled two of them "aisle-constrained" and two "open
#: floor", which are conclusions drawn from the very numbers the figures
#: beneath the labels are trying to show, and which called a map with 7-cell
#: aisles an open floor. `docs/figures/06-the-maps.svg` draws all five.
MAP_CLASS = {
    "warehouse_bottleneck": "two halves, one corridor",
    "warehouse_corridors": "five single-file runs",
    "warehouse_narrow": "long aisles, 1 cross-lane",
    "warehouse_medium": "short aisles, 2 cross-lanes",
    "warehouse_small": "short aisles, 1 cross-lane",
}


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------


class MissingData(RuntimeError):
    """Raised when a figure's suite has not been generated yet."""


def load(name: str) -> Dict[str, Any]:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        raise MissingData(
            f"{path.relative_to(ROOT)} is missing -- run "
            f"`python3 experiments/run_all.py --only {name}` first"
        )
    return json.loads(path.read_text())


def provenance(payload: Dict[str, Any]) -> str:
    """The one line every figure carries, so a number can be traced back."""
    meta = payload["meta"]
    return (
        f"{meta['seeds']} seeds x {meta['timesteps']} timesteps, Poisson "
        f"arrivals, generated {meta['generated_utc']} from aisleflow "
        f"@ {meta['git_sha']} by {meta['generator']}"
    )


# --------------------------------------------------------------------------
# matplotlib setup
# --------------------------------------------------------------------------


def style() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans"],
        "font.size": 9,
        "text.color": INK,
        "axes.labelcolor": INK_2,
        "axes.edgecolor": AXIS,
        "axes.linewidth": 0.8,
        "axes.grid": False,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5,
        "legend.frameon": False,
        "legend.fontsize": 8.5,
        "svg.fonttype": "none",
    })


def strip_frame(ax, keep: Sequence[str] = ("left", "bottom")) -> None:
    for side, spine in ax.spines.items():
        spine.set_visible(side in keep)


def value_grid(ax, axis: str = "x") -> None:
    """A recessive grid on the value axis only."""
    ax.grid(True, axis=axis, color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)


def caption(fig, text: str) -> None:
    fig.text(0.008, 0.006, text, fontsize=7, color=MUTED, ha="left", va="bottom")


def header(fig, title: str, subtitle: str) -> float:
    """Title and standfirst, placed in inches rather than figure fractions.

    A fraction-placed title collides with its own subtitle on a short figure
    and floats away from it on a tall one; these panels vary from 3 to 8
    inches high, so the offsets are absolute and the reserved top margin is
    returned for `tight_layout`.
    """
    height = fig.get_figheight()
    fig.text(0.005, 1 - 0.20 / height, title, fontsize=11.5, color=INK,
             ha="left", va="top", fontweight="bold")
    lines = subtitle.count("\n") + 1
    fig.text(0.005, 1 - 0.46 / height, subtitle, fontsize=8.5, color=INK_2,
             ha="left", va="top")
    return 1 - (0.52 + 0.16 * lines) / height


def save(fig, name: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{name}.svg"
    fig.savefig(path, bbox_inches="tight", pad_inches=0.16)
    import matplotlib.pyplot as plt

    plt.close(fig)
    print(f"  wrote {path.relative_to(ROOT)}")
    return path


# --------------------------------------------------------------------------
# figure 1: aisleflow against the three published planners
#
# A grouped bar chart, deliberately. The previous version of this figure was a
# matrix of ratios, each cell carrying a multiplier, a verdict in capitals, a
# pair of raw values and a p-value -- five numbers per cell, four cells per
# row, and a colour scale that saturated at 2x so that everything past it read
# the same. A reader cannot get "who is ahead, by how much, on which floor"
# out of that faster than out of four bars.
# --------------------------------------------------------------------------

#: the shipped configuration. The headline compares what this project actually
#: ships against what the three papers describe; the ladder figure is where
#: the per-map configurations live, because "our best configuration on each
#: map" is a claim that needs the ladder next to it to be readable.
AISLEFLOW = "full_lda_pibt"

#: The published lifelong planners, in the order the comparison figure draws
#: them. Plain lifelong PIBT is deliberately *not* here: it is aisleflow with
#: its mechanisms switched off, so putting it in a chart headed "against the
#: published planners" both misdescribes it and lets a reader read an ablation
#: rung as a rival. It belongs to the ladder, which is where it appears.
PUBLISHED_RIVALS = [
    ("token_passing", "Token Passing"),
    ("token_passing_task_swaps", "TP + task swaps"),
    ("rhcr", "RHCR"),
]


def _row(payload: Dict[str, Any], map_name: str, variant: str):
    for row in payload["maps"][map_name]["rows"]:
        if row["variant"] == variant:
            return row
    return None


def _throughput(payload: Dict[str, Any], map_name: str, variant: str):
    """(mean, low, high, per-seed values) in tasks per 1000 steps."""
    row = _row(payload, map_name, variant)
    if row is None:
        return None
    field = row["fields"]["throughput"] if "fields" in row else None
    if field is not None:
        return (per_1000(field["mean"]), per_1000(field["ci_lo"]),
                per_1000(field["ci_hi"]), [per_1000(v) for v in field["raw"]])
    raw = row["raw"]["throughput"]
    spread = _stderr(raw)
    mean = row["throughput"]
    return (per_1000(mean), per_1000(mean - 1.96 * spread),
            per_1000(mean + 1.96 * spread), [per_1000(v) for v in raw])


def figure_vs_baselines():
    """Throughput per map: aisleflow against Token Passing, TPTS and RHCR."""
    import matplotlib.pyplot as plt

    payload = load("baselines")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]
    series = [(AISLEFLOW, "aisleflow")] + PUBLISHED_RIVALS
    colours = [EMPHASIS, SERIES[1], SERIES[2], SERIES[3]]

    fig, ax = plt.subplots(figsize=(2.75 * len(maps) + 2.2, 5.0))
    group = 0.80
    width = group / len(series)

    for index, ((variant, label), colour) in enumerate(zip(series, colours)):
        centres, values, lows, highs = [], [], [], []
        for position, map_name in enumerate(maps):
            entry = _throughput(payload, map_name, variant)
            if entry is None:
                continue
            mean, lo, hi, _ = entry
            centres.append(position - group / 2 + width * (index + 0.5))
            values.append(mean)
            lows.append(max(0.0, mean - lo))
            highs.append(max(0.0, hi - mean))
        ax.bar(centres, values, width=width * 0.88, color=colour, zorder=3,
               label=label)
        ax.errorbar(centres, values, yerr=[lows, highs], fmt="none",
                    ecolor=INK_2, elinewidth=1.0, capsize=2.5, zorder=4)
        for x, value, high in zip(centres, values, highs):
            # a planner that delivered 0.4 per 1000 is not the same finding as
            # one that delivered nothing, and `:.0f` prints them identically
            text = f"{value:.1f}" if 0 < value < 1 else f"{value:.0f}"
            ax.annotate(text, (x, value + high),
                        textcoords="offset points", xytext=(0, 4), ha="center",
                        fontsize=8, color=INK,
                        fontweight="bold" if variant == AISLEFLOW else "normal")
        # a bar at zero is the one bar a reader is entitled to an explanation
        # for on the figure itself rather than three paragraphs away
        for x, value in zip(centres, values):
            if value <= 0:
                ax.annotate("nothing\ndelivered †", (x, 0),
                            textcoords="offset points", xytext=(0, 16),
                            ha="center", va="bottom", fontsize=6.8,
                            color=DIVERGING_LOW, rotation=90)

    ax.set_xticks(range(len(maps)))
    ax.set_xticklabels(
        [f"{short_map(m)}\n{MAP_CLASS[m]}\n"
         f"{payload['maps'][m]['robots']} robots, {payload['maps'][m]['rate']} jobs/step"
         for m in maps],
        fontsize=8.4, color=INK_2,
    )
    ax.set_ylabel(THROUGHPUT_UNIT, color=INK_2)
    ax.set_ylim(0, max(
        _throughput(payload, m, v)[2]
        for m in maps for v, _ in series if _throughput(payload, m, v)
    ) * 1.16)
    ax.legend(ncol=len(series), loc="upper left", bbox_to_anchor=(0, 1.02))
    strip_frame(ax)
    value_grid(ax, axis="y")

    # the shutouts, named with the map's own numbers rather than asserted
    sys.path.insert(0, str(ROOT / "src"))
    from lda_pibt.warehouse import Warehouse

    shutouts = sorted({
        short_map(m)
        for m in maps for v, _ in series
        if (_throughput(payload, m, v) or (1,))[0] <= 0
    })
    footnote = ""
    if shutouts:
        worst = f"warehouse_{shutouts[0]}"
        bays = len(Warehouse.from_file(ROOT / "maps" / f"{worst}.map").parking_vertices)
        footnote = (
            f"\n† A Token Passing agent with no task rests where it stopped, and "
            f"`{shutouts[0]}` offers {bays} parking bays for "
            f"{payload['maps'][worst]['robots']} agents. Every one of its "
            f"single-file runs has\nan agent standing in it before the first "
            f"task is handed out, so no path across the map can be planned at "
            f"all. This is the completeness assumption failing, not a tie-break."
        )

    top = header(
        fig,
        "Aisleflow against the three published lifelong planners",
        "Taller is better: tasks delivered per 1000 timesteps of simulated "
        "time, same job stream and same robot count for every planner on a "
        "map.\nWhiskers are 95% bootstrap intervals over "
        f"{payload['meta']['seeds']} seeds. Each map is a different traffic "
        "problem, named under its bars; compare planners within a map.\n"
        "Token Passing and TPTS are complete only on well-formed MAPD "
        "instances -- one parking endpoint per agent -- which none of these "
        "floors provides at these robot counts. The next figure sweeps\nthe "
        "robot count and shows where that assumption starts to bite."
        + footnote,
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 2: throughput against how crowded the floor is
#
# The canonical lifelong-MAPF plot, and the one that stops a single-scenario
# bar chart from being read as a verdict on an algorithm. Every planner here
# has a robot count past which more robots buy no more throughput; the
# interesting content is where each one turns over.
# --------------------------------------------------------------------------

def figure_density():
    """Throughput against robot count, one panel per map, one line per planner."""
    import matplotlib.pyplot as plt

    payload = load("density")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]
    series = [(AISLEFLOW, "aisleflow")] + PUBLISHED_RIVALS
    colours = [EMPHASIS, SERIES[1], SERIES[2], SERIES[3]]
    markers = ["o", "s", "^", "D"]

    fig, axes = plt.subplots(1, len(maps), figsize=(4.6 * len(maps) + 1.0, 4.8))
    if len(maps) == 1:
        axes = [axes]

    for ax, map_name in zip(axes, maps):
        peaks: List[str] = []
        counts = payload["maps"][map_name]["robot_counts"]
        rows = payload["maps"][map_name]["rows"]
        for (variant, label), colour, marker in zip(series, colours, markers):
            points = [
                (row["n_robots"], per_1000(row["throughput"]),
                 per_1000(_stderr(row["raw"]["throughput"])))
                for row in rows if row["variant"] == variant
            ]
            points.sort()
            if not points:
                continue
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            es = [p[2] for p in points]
            ax.errorbar(xs, ys, yerr=es, color=colour, marker=marker,
                        markersize=4.5, linewidth=1.8, capsize=2.5,
                        elinewidth=0.9, label=label, zorder=3)
            # where each line turns over, collected rather than annotated at
            # the point: two planners that peak at the same robot count put
            # their labels on top of each other, and the robot count is the
            # number this figure is actually about
            best = max(points, key=lambda p: p[1])
            peaks.append(f"{label}: {best[0]}")
        ax.set_xticks(counts)
        ax.set_xlabel("robots on the floor", color=INK_2, fontsize=8.6)
        ax.set_title(f"{short_map(map_name)}\n{MAP_CLASS[map_name]}",
                     fontsize=9.4, color=INK, loc="left", pad=32)
        # where each line turns over, two per line so it fits the panel it
        # describes rather than running into the next one
        rows = ["   ·   ".join(peaks[i:i + 2]) for i in range(0, len(peaks), 2)]
        ax.text(0, 1.02, "peak throughput at " + "\n".join(rows) + " robots",
                transform=ax.transAxes, fontsize=7.4, color=INK_2,
                va="bottom", linespacing=1.5)
        ax.set_ylim(bottom=0)
        strip_frame(ax)
        value_grid(ax, axis="y")

    axes[0].set_ylabel(THROUGHPUT_UNIT, color=INK_2)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=len(series),
               fontsize=8.6, bbox_to_anchor=(0.5, 0.035))

    # who actually leads on the quietest floor, computed rather than asserted
    sparse = min(payload["maps"][maps[0]]["robot_counts"])
    leader, leader_map = None, None
    for map_name in maps:
        best = max(
            (
                (row["throughput"], row["variant"])
                for row in payload["maps"][map_name]["rows"]
                if row["n_robots"] == sparse
            ),
            default=(0.0, ""),
        )
        if best[1] in {v for v, _ in PUBLISHED_RIVALS}:
            leader, leader_map = best[1], map_name
            break
    opening = (
        f"At {sparse} robots on `{short_map(leader_map)}` the leader is "
        f"{label_of(leader)}, not aisleflow"
        if leader
        else f"At {sparse} robots every planner here is within noise of the others"
    )

    top = header(
        fig,
        "Adding robots stops buying throughput, and the planners give up at different points",
        "Taller is better; the x axis is how many robots share the floor. "
        "Every line rises and then falls: past some density the robots spend "
        "their\ntime getting out of each other's way. Where a line turns over "
        "is the honest characterisation of a planner, and it is the thing a "
        "single bar\ncannot show. "
        + opening
        + ". Token Passing then falls away as the floor fills, which is what "
        "its own completeness proof predicts:\nit assumes every agent has a "
        "parking endpoint to rest at, and a crowded warehouse is exactly where "
        "that assumption runs out. Plain lifelong PIBT\nis not plotted here -- "
        "it is this planner with its mechanisms off, and it belongs to the "
        "ablation ladder, which is the next figure.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.075, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 4: the ablation ladder
# --------------------------------------------------------------------------

LADDER = [
    "lifelong_pibt",
    "turning_cost_only",
    "lane_bonus_only",
    "congestion_only",
    "full_lda_pibt",
]

#: what each rung actually switches on, printed next to its name -- the variant
#: names alone say which flag moved, not what the planner started doing
LADDER_MECHANISM = {
    "lifelong_pibt": "plain PIBT: nearest job, shortest route",
    "turning_cost_only": "charge a robot for reversing",
    "lane_bonus_only": "reward staying in one lane",
    "congestion_only": "avoid the crowded corridor",
    "full_lda_pibt": "and break jams when they form",
}


def figure_ablation_ladder():
    """The cumulative ladder, one panel per map, with intervals."""
    import matplotlib.pyplot as plt
    import statistics

    payload = load("ablation")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]
    # the rungs are identical on every map, so only the leftmost panel is
    # labelled; repeating them four times leaves no width for the bars, which
    # are the part of the picture doing the work
    fig, axes = plt.subplots(
        1, len(maps), figsize=(2.55 * len(maps) + 2.6, 4.5),
    )
    if len(maps) == 1:
        axes = [axes]

    for index, (ax, map_name) in enumerate(zip(axes, maps)):
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        variants = [v for v in LADDER if v in rows]
        values = [per_1000(rows[v]["throughput"]) for v in variants]
        errors = []
        for v in variants:
            raw = rows[v].get("raw", {}).get("throughput")
            errors.append(
                per_1000(statistics.stdev(raw) / (len(raw) ** 0.5))
                if raw and len(raw) > 1 else 0.0
            )
        best = max(values)
        colours = [
            EMPHASIS if v == "full_lda_pibt" else
            (STATUS["good"] if value == best else DEEMPHASIS)
            for v, value in zip(variants, values)
        ]
        positions = list(range(len(variants)))[::-1]
        ax.barh(positions, values, height=0.6, color=colours, zorder=3)
        ax.errorbar(values, positions, xerr=errors, fmt="none", ecolor=INK_2,
                    elinewidth=1.0, capsize=2.5, zorder=4)
        for y, value, error, variant in zip(positions, values, errors, variants):
            best_here = value == best
            ax.annotate(
                f"{value:.0f}" + ("   BEST HERE" if best_here else ""),
                # past the whisker, not past the bar, or the two collide
                (value + error, y), textcoords="offset points", xytext=(7, -3),
                fontsize=8, color=STATUS["good"] if best_here else INK_2,
                fontweight="bold" if best_here else "normal",
            )
        ax.set_yticks(positions)
        ax.set_yticklabels(
            [f"{label_of(v)}\n{LADDER_MECHANISM.get(v, '')}" for v in variants]
            if index == 0 else [""] * len(variants),
            fontsize=7.6, color=INK_2,
        )
        ax.set_xlim(0, max(values) * 1.66)
        ax.set_xlabel(THROUGHPUT_UNIT if index == 0 else "",
                      color=INK_2, fontsize=8)
        ax.set_title(f"{short_map(map_name)}\n{MAP_CLASS[map_name]}",
                     fontsize=9.5, color=INK, loc="left",
                     fontweight="bold" if map_name in DESIGNED_FOR else "normal")
        strip_frame(ax, keep=("bottom",))
        value_grid(ax, axis="x")

    #: the margin of the best rung over plain PIBT on each map, computed
    #: rather than written into the standfirst -- the last time the planner
    #: changed under this figure the bars moved and the sentence did not
    gains, losses = [], []
    best_per_map = {}
    for map_name in maps:
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        ladder = [(v, rows[v]["throughput"]) for v in LADDER if v in rows]
        variant, value = max(ladder, key=lambda pair: pair[1])
        best_per_map[map_name] = variant
        base = rows["lifelong_pibt"]["throughput"]
        margin = 100.0 * (value - base) / base if base else 0.0
        (gains if variant != "lifelong_pibt" else losses).append(
            (short_map(map_name), margin)
        )
    full_wins = sum(1 for v in best_per_map.values() if v == "full_lda_pibt")

    def _phrase(entries):
        if not entries:
            return ""
        magnitudes = sorted(round(m) for _, m in entries)
        span = (f"{magnitudes[0]:.0f}%" if magnitudes[0] == magnitudes[-1]
                else f"{magnitudes[0]:.0f}-{magnitudes[-1]:.0f}%")
        return f"{', '.join(name for name, _ in entries)} ({span})"

    top = header(
        fig,
        "The ablation ladder: each rung adds one mechanism, and more is not always better",
        "Each panel starts at the top with plain lifelong PIBT and adds one "
        "mechanism per "
        f"rung going down; longer bars are better.\nGreen marks the best rung on "
        "that map, blue is the full method, and the second line of each label "
        "says what that rung switched on.\n"
        + (f"The full configuration is the best rung on {full_wins} of these "
           f"{len(maps)} maps"
           if full_wins else
           f"The full configuration is not the best rung on any of these "
           f"{len(maps)} maps")
        + (f". The added terms buy throughput on {_phrase(gains)}"
           if gains else ". The added terms buy nothing on any map here")
        + (f", and cost it on {', '.join(n for n, _ in losses)}, "
           "where plain PIBT is already the best rung.\n"
           if losses else ".\n")
        + "That is the argument for picking a configuration per floor rather "
        "than shipping one, and for reading this ladder before the headline.\n"
        "Whiskers are the standard error over seeds. Each panel has its own "
        "scale: compare rungs within a map, not bars across maps.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 5: what every remaining knob is worth
#
# The ladder is about mechanisms; this is about the numbers inside them. Each
# row is one parameter neutralised, so a long red bar means the planner needs
# that knob and a bar to the right of zero means the knob is costing it.
# --------------------------------------------------------------------------

#: how many knobs the tornado shows. The suite measures 24 variants, most of
#: them within noise of zero; past a dozen the figure is a wall of grey bars
#: that says nothing the table below it does not say better.
KNOBS_SHOWN = 12


def _merge_identical_knobs(summary: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse knobs whose variants are the same configuration.

    Turning recovery off and capping its ladder at level 0 produce the same
    planner, so the suite measures them separately and records the identical
    number for each. Five rows of +4.2% in a row read as five findings; they
    are one, and the merged label says which knobs it covers.
    """
    merged: Dict[Tuple[str, float, float], Dict[str, Any]] = {}
    for row in summary:
        key = (row["family"], round(row["pooled_relative_delta"], 9),
               round(row["pooled_p_value"], 9))
        if key in merged:
            merged[key]["_knobs"].append(row["knob"])
        else:
            merged[key] = dict(row, _knobs=[row["knob"]])

    for row in merged.values():
        knobs = row.pop("_knobs")
        row["knob"] = knobs[0] if len(knobs) == 1 else (
            f"{knobs[0]} and {len(knobs) - 1} identical variant"
            f"{'s' if len(knobs) > 2 else ''}"
        )
    return list(merged.values())


def figure_knobs():
    """Every measured parameter, ranked by what removing it costs."""
    import matplotlib.pyplot as plt

    payload = load("sensitivity")
    measured = len(payload["summary"])
    rows = _merge_identical_knobs(payload["summary"])
    rows = sorted(rows, key=lambda r: -abs(r["pooled_relative_delta"]))[:KNOBS_SHOWN]
    rows.sort(key=lambda r: r["pooled_relative_delta"])

    fig, ax = plt.subplots(figsize=(10.2, 0.44 * len(rows) + 3.4))
    positions = list(range(len(rows)))

    for y, row in zip(positions, rows):
        percent = 100.0 * row["pooled_relative_delta"]
        significant = row["pooled_p_value"] < 0.05
        colour = (DIVERGING_LOW if percent < 0 else DIVERGING_HIGH)
        ax.barh(y, percent, height=0.58, zorder=3,
                color=colour if significant else DEEMPHASIS)
        side = -1 if percent < 0 else 1
        ax.annotate(
            f"{percent:+.1f}%", (percent, y), textcoords="offset points",
            xytext=(8 * side, -3), ha="right" if percent < 0 else "left",
            fontsize=8.6, color=INK if significant else MUTED,
            fontweight="bold" if significant else "normal",
        )
        note = "p < 0.001" if row["pooled_p_value"] < 0.001 else \
            f"p = {row['pooled_p_value']:.3f}"
        worst = row.get("worst_map")
        if worst:
            note += (f"   ·   worst on {short_map(worst)}: "
                     f"{100.0 * row['worst_relative_delta']:+.0f}%")
        ax.annotate(
            note, (percent, y), textcoords="offset points",
            xytext=(8 * side, -14), ha="right" if percent < 0 else "left",
            fontsize=6.9, color=MUTED,
        )

    ax.axvline(0, color=INK_2, linewidth=1.2, zorder=5)
    ax.set_yticks(positions)
    ax.set_yticklabels(
        [f"{r['knob']}\n{r['family']}" for r in rows], fontsize=7.6, color=INK_2,
    )
    span = max(abs(100.0 * r["pooled_relative_delta"]) for r in rows)
    ax.set_xlim(-span * 1.62, span * 0.34)
    ax.set_ylim(-0.95, len(rows) - 0.35)
    ticks = [t for t in (-100, -75, -50, -25, 0, 25) if t >= -span * 1.05]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t:+d}%" if t else "no change" for t in ticks])
    ax.set_xlabel(
        "change in throughput when the knob is neutralised   "
        "(left: the planner needs it   ·   right: the planner is better without it)",
        color=INK_2, fontsize=8.5,
    )
    strip_frame(ax, keep=("bottom",))
    value_grid(ax, axis="x")

    # the two ends of the axis, named where the bars that earn the name are:
    # the load-bearing knobs sort to the bottom, the free wins to the top
    ax.text(-span * 1.58, -0.55, "LOAD-BEARING: REMOVING THESE COSTS THROUGHPUT",
            ha="left", va="center", fontsize=8.2, color=DIVERGING_LOW,
            fontweight="bold")
    ax.text(span * 0.32, len(rows) - 0.5, "BETTER WITHOUT", ha="right",
            va="center", fontsize=8.2, color=DIVERGING_HIGH, fontweight="bold")

    top = header(
        fig,
        "What every remaining parameter is worth, measured one at a time",
        f"The {len(rows)} largest effects of the {measured} variants the "
        "suite measured, each the result of neutralising one knob and "
        "rerunning "
        "every map and seed.\nBars left of zero are knobs the planner needs; "
        "bars right of zero are knobs it would be better without. Solid colour "
        "is p < 0.05, grey is not significant.\nThe worst-map figure is there "
        "because pooling hides disagreement: a knob can help one floor, hurt "
        "another, and average to nothing.",
    )
    caption(
        fig,
        provenance(payload)
        + "  --  a separate run from the other figures, at more seeds and "
          "one knob at a time",
    )
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 6: what the maps actually are
#
# Every other figure on the page is indexed by map name, and a reader who has
# not opened `maps/*.map` has no idea whether "corridors" is wider than
# "narrow". This draws all five floors to the same scale, with the numbers
# that decide how each one behaves.
# --------------------------------------------------------------------------

MAP_SHEET = [
    "warehouse_bottleneck",
    "warehouse_corridors",
    "warehouse_narrow",
    "warehouse_small",
    "warehouse_medium",
]

CELL_COLOUR = {
    "shelf": "#3a3937",
    "floor": "#ffffff",
    "pickup": "#2a78d6",
    "delivery": "#eb6834",
    "parking": "#1baf7a",
    "passing": "#eda100",
}


def _classify(warehouse, vertex) -> str:
    info = warehouse.info[vertex]
    if info.is_pickup_area:
        return "pickup"
    if info.is_delivery_area:
        return "delivery"
    if info.is_parking_area:
        return "parking"
    if info.is_passing_bay:
        return "passing"
    return "floor"


def figure_maps():
    """The five warehouse floors, drawn to scale, with their structure."""
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    sys.path.insert(0, str(ROOT / "src"))
    from lda_pibt.warehouse import Warehouse

    houses = {
        name: Warehouse.from_file(ROOT / "maps" / f"{name}.map") for name in MAP_SHEET
    }

    # laid out by hand on one axes rather than as subplots, because subplots
    # with `aspect="equal"` and five different aspect ratios centre each panel
    # in its own box and the floors stop lining up -- which is the one thing
    # a figure whose whole job is "these are the same scale" must not do
    GAP_X, GAP_Y, CAPTION = 5.0, 17.0, 5.4
    rows = [
        ["warehouse_bottleneck", "warehouse_corridors", "warehouse_small"],
        ["warehouse_narrow", "warehouse_medium"],
    ]
    placed = {}
    y = 0.0
    for line in rows:
        x = 0.0
        for name in line:
            placed[name] = (x, y)
            x += houses[name].width + GAP_X
        y += max(houses[n].height for n in line) + GAP_Y
    total_w = max(
        sum(houses[n].width for n in line) + GAP_X * (len(line) - 1) for line in rows
    )
    total_h = y - GAP_Y + CAPTION

    fig, ax = plt.subplots(figsize=(11.6, 11.6 * (total_h + 6) / total_w))

    for name, (x0, y0) in placed.items():
        warehouse = houses[name]
        for row in range(warehouse.height):
            for col in range(warehouse.width):
                vertex = (row, col)
                kind = (
                    _classify(warehouse, vertex)
                    if warehouse.graph.contains(vertex)
                    else "shelf"
                )
                ax.add_patch(plt.Rectangle(
                    (x0 + col, y0 + row), 1, 1, facecolor=CELL_COLOUR[kind],
                    edgecolor=GRID if kind != "shelf" else CELL_COLOUR["shelf"],
                    linewidth=0.3,
                ))
        summary = warehouse.summary()
        lengths = [a.length for a in warehouse.aisles.values()]
        ax.text(x0, y0 - 3.4, short_map(name), fontsize=10.5, color=INK,
                fontweight="bold" if name in DESIGNED_FOR else "normal",
                va="bottom")
        ax.text(x0, y0 - 1.0, MAP_CLASS.get(name, "test floor"),
                fontsize=8.2, color=INK_2, va="bottom")
        ax.text(
            x0, y0 + warehouse.height + 1.4,
            f"{summary['size']} grid · {summary['vertices']} drivable cells\n"
            f"{summary['pickups']} pickup · {summary['deliveries']} delivery · "
            f"{summary['parking']} parking\n"
            f"aisle runs {min(lengths)}-{max(lengths)} cells\n"
            + (f"{summary['articulation_points']} cells whose occupant "
               f"splits the floor"
               if summary["articulation_points"]
               else "no cell splits the floor:\nevery aisle can be gone round"),
            fontsize=7.4, color=INK_2, va="top", linespacing=1.5,
        )

    ax.set_xlim(-1.5, total_w + 1.5)
    ax.set_ylim(-5.5, total_h)
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.set_xticks([])
    ax.set_yticks([])
    strip_frame(ax, keep=())

    handles = [
        Patch(facecolor=CELL_COLOUR["floor"], edgecolor=AXIS, label="drivable floor"),
        Patch(facecolor=CELL_COLOUR["shelf"], label="shelf / wall"),
        Patch(facecolor=CELL_COLOUR["pickup"], label="pickup station"),
        Patch(facecolor=CELL_COLOUR["delivery"], label="delivery station"),
        Patch(facecolor=CELL_COLOUR["parking"], label="parking bay"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=5, fontsize=8.4,
               bbox_to_anchor=(0.5, 0.005))

    top = header(
        fig,
        "The five warehouse floors every number on this page was measured on",
        "All drawn to one scale. Every aisle is one cell wide on all five; "
        "what differs is how long the aisles are, how many ways round there\n"
        "are, and whether a robot standing still can cut the floor in two. "
        "Only `bottleneck` can -- its two halves meet in one corridor.\n"
        "`corridors` is five 22-cell single-file runs joined at both ends; "
        "`narrow` is `medium` with the aisles twice as long and no extra way "
        "round.",
    )
    fig.tight_layout(rect=(0, 0.045, 1, top))
    return fig


#: The five figures, keyed by the filename they are written to. Numbered
#: because the order is the argument: how it compares to the published
#: planners, how that comparison moves with traffic, which of its own
#: mechanisms did it, what every knob inside them is worth, and what the
#: floors all of it was measured on actually look like.
#: `tests/test_docs_assets.py` checks that every key here has a committed SVG
#: and that every committed SVG is referenced by a page.
FIGURES: Dict[str, Callable[[], Any]] = {
    "01-vs-baselines": figure_vs_baselines,
    "02-throughput-vs-robots": figure_density,
    "03-ablation-ladder": figure_ablation_ladder,
    "04-knobs": figure_knobs,
    "05-the-maps": figure_maps,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", choices=sorted(FIGURES), default=None)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for name in FIGURES:
            print(name)
        return 0

    failures = 0
    style()
    for name in args.only or list(FIGURES):
        print(f"\n### {name}")
        try:
            save(FIGURES[name](), name)
        except MissingData as error:
            print(f"  skipped: {error}")
            failures += 1
    return 1 if failures and args.only else 0


sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    raise SystemExit(main())
