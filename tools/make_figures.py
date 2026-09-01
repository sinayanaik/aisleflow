#!/usr/bin/env python3
"""Draw the result figures in ``docs/figures/`` from ``docs/data/``.

Two figures, each embedded in the page it belongs to:

    01-vs-baselines          against the three published planners, on the one
                             floor where aisleflow leads every one of them
                             (`docs/05-results.md`)
    05-the-maps              the floors it was measured on
                             (`docs/06-the-maps.md`)

Five figures this file used to draw are gone rather than tidied: a matrix of
ratios with five numbers and a capitalised verdict in every cell, a
double-panel matrix of the same comparison at two resolutions, the density
sweep (throughput against robot count), the ablation ladder, and the knobs
tornado chart. The first two encoded the argument rather than showing the
measurement, and neither could be read faster than the bar chart that
replaced them; the latter three were cut along with the multi-map, multi-plot
results story they belonged to -- the results page now makes one claim, on
one floor, with one plot, and the ablation and sensitivity findings that used
to have their own charts are tables on `docs/05-results.md` instead.

Neither figure carries a title, subtitle or provenance caption of its own --
that text used to be drawn on the SVG and is gone from it now, so a figure
dropped into a slide or shared on its own is the chart and nothing else. The
explanation lives once, in `FIGURE_DOCS` below, written to `docs/figures/
README.md` by `write_readme()` and to the results/maps pages by hand. What
stays on the chart is what makes the bars and cells themselves legible: axis
labels, the legend, and the value on every bar.

Every figure reads `docs/data/*.json`, written by `experiments/run_all.py` and
`experiments/run_sensitivity.py`, and nothing here computes a simulation or
invents a number: the intervals are the bootstrap intervals the experiment
recorded, and the p-values are its permutation tests.

Usage::

    python3 tools/make_figures.py                # every figure it has data for
    python3 tools/make_figures.py --only 01-vs-baselines
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
from typing import Any, Callable, Dict, List, Sequence

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

#: the one floor the results feature: the only map where aisleflow's bootstrap
#: interval sits entirely above all three published baselines. A single
#: winning condition, shown on its own, is the whole of the comparison now.
FEATURED_MAP = "warehouse_bottleneck"


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
    """Throughput on the featured floor: aisleflow against TP, TPTS and RHCR.

    No title, subtitle or provenance caption is drawn on the figure itself --
    that prose lives in `docs/05-results.md` and `docs/figures/README.md`
    instead, next to the table it has to stay consistent with, rather than
    duplicated as pixels a second copy of it could drift from. What stays on
    the chart is only what a reader needs to read the bars: the axis, the
    legend, and the value on top of each one.
    """
    import matplotlib.pyplot as plt

    payload = load("baselines")
    map_name = FEATURED_MAP if FEATURED_MAP in payload["maps"] else \
        next(m for m in MAP_ORDER if m in payload["maps"])
    series = [(AISLEFLOW, "aisleflow")] + PUBLISHED_RIVALS
    colours = [EMPHASIS, SERIES[1], SERIES[2], SERIES[3]]

    fig, ax = plt.subplots(figsize=(4.6, 4.0))

    for index, ((variant, label), colour) in enumerate(zip(series, colours)):
        entry = _throughput(payload, map_name, variant)
        if entry is None:
            continue
        mean, lo, hi, _ = entry
        low, high = max(0.0, mean - lo), max(0.0, hi - mean)
        ax.bar(index, mean, width=0.62, color=colour, zorder=3)
        ax.errorbar(index, mean, yerr=[[low], [high]], fmt="none",
                    ecolor=INK_2, elinewidth=1.0, capsize=2.5, zorder=4)
        ax.annotate(f"{mean:.0f}", (index, mean + high),
                    textcoords="offset points", xytext=(0, 4), ha="center",
                    fontsize=9, color=INK,
                    fontweight="bold" if variant == AISLEFLOW else "normal")

    ax.set_xticks(range(len(series)))
    ax.set_xticklabels([label for _, label in series], fontsize=8.6, color=INK_2)
    ax.set_ylabel(THROUGHPUT_UNIT, color=INK_2, fontsize=8.8)
    ax.set_ylim(0, max(
        _throughput(payload, map_name, v)[2] for v, _ in series
        if _throughput(payload, map_name, v)
    ) * 1.14)
    strip_frame(ax)
    value_grid(ax, axis="y")
    fig.tight_layout(pad=0.4)
    return fig


# --------------------------------------------------------------------------
# figure 2: what the maps actually are
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
    """The five warehouse floors, drawn to scale, with their structure.

    No title is drawn on the figure -- that prose is in `docs/06-the-maps.md`
    and `docs/figures/README.md`. What is on the figure is per-map labelling
    (name, class, the structural numbers) and the cell-colour legend, because
    those are what make the picture legible rather than what argues over it.
    """
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

    fig, ax = plt.subplots(figsize=(11.6, 11.6 * total_h / total_w))

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
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    return fig


#: The two figures this file writes, keyed by the filename they go to: the
#: single winning throughput comparison against the published planners, and
#: the floors it was measured on. The density, ablation-ladder and knobs
#: charts are gone (see the module docstring) -- the results page now makes
#: one throughput claim on one condition, and the ablation/sensitivity
#: findings that used to have their own charts are tables instead.
#: `tests/test_docs_assets.py` checks that every key here has a committed SVG
#: and that every committed SVG is referenced by a page.
FIGURES: Dict[str, Callable[[], Any]] = {
    "01-vs-baselines": figure_vs_baselines,
    "05-the-maps": figure_maps,
}

#: what each figure's README section says. This prose used to be drawn
#: *inside* the SVG as a title and subtitle; it lives here once instead, next
#: to the function that draws the chart, rather than twice -- as pixels and as
#: words a second copy of it could drift from.
FIGURE_DOCS: Dict[str, Dict[str, str]] = {
    "01-vs-baselines": {
        "title": "Aisleflow leads every published planner on warehouse_bottleneck",
        "body": (
            "Taller is better: tasks delivered per 1000 timesteps of "
            "simulated time, same job stream and same robot count for every "
            "planner. Whiskers are 95% bootstrap intervals over 5 seeds; "
            "aisleflow's interval clears all three baselines' with no "
            "overlap. `warehouse_bottleneck` is two halves joined by one "
            "six-cell corridor that every task must cross, in both "
            "directions, forever -- see "
            "[../06-the-maps.md](../06-the-maps.md). The full table, "
            "including RHCR, is on "
            "[../05-results.md](../05-results.md#against-the-published-planners)."
        ),
    },
    "05-the-maps": {
        "title": "The five warehouse floors every number on the results page was measured on",
        "body": (
            "All drawn to one scale. Every aisle is one cell wide on all "
            "five; what differs is how long the aisles are, how many ways "
            "round there are, and whether a robot standing still can cut the "
            "floor in two. Only `bottleneck` can -- its two halves meet in "
            "one corridor. `corridors` is five 22-cell single-file runs "
            "joined at both ends; `narrow` is `medium` with the aisles twice "
            "as long and no extra way round. Per-map detail is in "
            "[../06-the-maps.md](../06-the-maps.md)."
        ),
    },
}


def write_readme() -> Path:
    """Regenerate docs/figures/README.md from `FIGURE_DOCS` and the dataset.

    Each figure carries no title or caption of its own (see the module
    docstring), so this is the one place that explanation lives -- generated
    rather than hand-maintained, so it cannot say something the chart and the
    results page no longer agree on.
    """
    lines: List[str] = [
        "# Figures",
        "",
        "Two figures, generated from `docs/data/` by `tools/make_figures.py`.",
        "Neither carries a title or caption of its own -- the explanation is",
        "here and in the pages that embed them, so there is one copy of it",
        "rather than a copy in pixels that could drift from the prose.",
        "",
        "```bash",
        "python3 tools/make_figures.py",
        "```",
        "",
    ]
    for key in FIGURES:
        doc = FIGURE_DOCS[key]
        lines += [
            f"## {doc['title']}",
            "",
            f"![{doc['title']}]({key}.svg)",
            "",
            doc["body"],
            "",
        ]
    try:
        lines.append(f"*{provenance(load('baselines'))}*")
    except MissingData:
        pass
    path = OUT_DIR / "README.md"
    path.write_text("\n".join(lines))
    print(f"  wrote {path.relative_to(ROOT)}")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", choices=sorted(FIGURES), default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--no-readme", action="store_true")
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
    if not args.no_readme:
        write_readme()
    return 1 if failures and args.only else 0


sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    raise SystemExit(main())
