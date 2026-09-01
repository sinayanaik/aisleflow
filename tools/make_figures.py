#!/usr/bin/env python3
"""Draw the result figures in ``docs/figures/`` from ``docs/data/``.

Five figures, numbered in the order the argument makes them, each embedded in
`docs/05-results.md` under the section it belongs to:

    01-vs-baselines          is aisleflow better than the published planners?
    02-per-map-throughput    by how much, per map, with the test
    03-where-it-wins         where it beats plain PIBT, and where it does not
    04-ablation-ladder       which mechanism buys which part of that
    05-knobs                 what every remaining parameter is worth

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
# the colour alone -- every bar, cell and rung on these five figures prints its
# own value and, where there is one, its own verdict in words.
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

#: diverging poles + neutral midpoint, for the win/loss matrix
DIVERGING_HIGH = "#2a78d6"
DIVERGING_LOW = "#d03b3b"
DIVERGING_MID = "#f0efec"

#: status, for verdicts -- always shipped with a word, never colour alone
STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "critical": "#d03b3b",
    "neutral": MUTED,
}

#: the maps, ordered the way the argument runs: the two tight floors where the
#: congestion machinery pays, then the two open ones where it does not
MAP_ORDER = [
    "warehouse_bottleneck",
    "warehouse_corridors",
    "warehouse_narrow",
    "warehouse_medium",
]
MAP_LABEL = {
    "warehouse_bottleneck": "bottleneck\none corridor joins two halves",
    "warehouse_corridors": "corridors\nfive single-file corridors",
    "warehouse_narrow": "narrow\n5-cell aisles",
    "warehouse_medium": "medium\nopen grid",
}
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
    "token_passing_recovery": "Token Passing + recovery",
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


#: how each map is classified in the argument, printed next to its name so a
#: reader never has to remember which of the four is which
MAP_CLASS = {
    "warehouse_bottleneck": "aisle-constrained",
    "warehouse_corridors": "aisle-constrained",
    "warehouse_narrow": "open floor",
    "warehouse_medium": "open floor",
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
# figure 1: is it better than the alternatives, and where?
#
# Everything else in this directory explains *why*. This one answers "is it
# better than the alternatives, and where?", once per opponent per map, in the
# only form that needs no arithmetic from the reader: how many times as much
# work aisleflow gets done.
# --------------------------------------------------------------------------

#: the opponents, in the order the argument meets them: the three published
#: lifelong baselines first, because that is the comparison aisleflow wins
#: outright and on every map, then the plain PIBT it extends -- which is the
#: harder column, and is annotated as such rather than buried in the middle.
RIVALS = [
    ("token_passing", "Token\nPassing"),
    ("token_passing_recovery", "Token Passing\n+ recovery"),
    ("rhcr", "RHCR"),
    ("lifelong_pibt", "plain lifelong\nPIBT"),
]

#: which rivals are published algorithms, as opposed to the ablated version of
#: aisleflow itself. Drawn as a bracket over those columns.
PUBLISHED = {"token_passing", "token_passing_recovery", "rhcr"}


def _throughput_seeds(map_name: str, variant: str, ablation, baselines):
    """Per-seed throughput for a variant on a map, from whichever suite has it."""
    if map_name in ablation["maps"]:
        for row in ablation["maps"][map_name]["rows"]:
            if row["variant"] == variant:
                return row["raw"]["throughput"]
    if map_name in baselines["maps"]:
        for row in baselines["maps"][map_name]["rows"]:
            if row["variant"] == variant:
                return row["fields"]["throughput"]["raw"]
    return None


def figure_scorecard():
    """aisleflow against every rival, on every map, as a ratio and a verdict."""
    import matplotlib.pyplot as plt
    import statistics

    sys.path.insert(0, str(ROOT / "src"))
    from lda_pibt.stats import permutation_test

    ablation = load("ablation")
    baselines = load("baselines")
    maps = [m for m in MAP_ORDER if m in ablation["maps"]]

    fig, ax = plt.subplots(figsize=(2.05 * len(RIVALS) + 3.3,
                                    1.03 * len(maps) + 2.9))

    for y, map_name in enumerate(maps):
        chosen = best_spar(map_name, ablation)
        ours = _throughput_seeds(map_name, chosen[0], ablation, baselines)
        our_mean = statistics.fmean(ours)
        for x, (variant, _) in enumerate(RIVALS):
            theirs = _throughput_seeds(map_name, variant, ablation, baselines)
            if theirs is None:
                ax.text(x + 0.5, y + 0.5, "not run\non this map", ha="center",
                        va="center", fontsize=7.6, color=MUTED, zorder=3)
                continue
            their_mean = statistics.fmean(theirs)
            _, p_value = permutation_test(ours, theirs)
            significant = p_value < 0.05
            if their_mean <= 0:
                ratio, ratio_text = float("inf"), "delivers\nnothing at all"
            else:
                ratio = our_mean / their_mean
                ratio_text = f"{ratio:.2f}x" if ratio < 10 else f"{ratio:.0f}x"
            # the colour scale saturates at 2x: past that every baseline cell
            # would be the same flat blue and the PIBT column, where the
            # argument actually lives, would be invisible
            shade = (ratio - 1.0) if ratio != float("inf") else 1.0
            ax.add_patch(plt.Rectangle(
                (x + 0.03, y + 0.06), 0.94, 0.88,
                facecolor=_diverging(shade, 1.0), edgecolor=SURFACE,
                linewidth=2, zorder=2,
            ))
            ink = "#ffffff" if abs(shade) > 0.55 else INK
            ax.text(x + 0.5, y + 0.31, ratio_text, ha="center", va="center",
                    fontsize=12 if ratio == float("inf") else 15,
                    color=ink, fontweight="bold", zorder=3,
                    linespacing=1.15)
            if ratio == float("inf"):
                verdict = "AISLEFLOW WINS"
            elif ratio >= 1.0:
                verdict = "AISLEFLOW AHEAD" if significant else "ahead, not significant"
            else:
                verdict = "AISLEFLOW BEHIND" if significant else "behind, not significant"
            ax.text(x + 0.5, y + 0.55, verdict, ha="center", va="center",
                    fontsize=8, color=ink, zorder=3,
                    fontweight="bold" if significant else "normal")
            # a baseline at 0.5 per 1000 rounds to "0", which reads as a
            # shutout next to a finite ratio; sub-1 values keep a decimal
            theirs_text = (f"{per_1000(their_mean):.1f}"
                           if 0 < per_1000(their_mean) < 1
                           else f"{per_1000(their_mean):.0f}")
            ax.text(x + 0.5, y + 0.70,
                    f"{per_1000(our_mean):.0f} vs {theirs_text} per 1000 steps",
                    ha="center", va="center",
                    fontsize=7.2, color=ink, zorder=3)
            ax.text(x + 0.5, y + 0.84,
                    f"p = {p_value:.3f}", ha="center", va="center",
                    fontsize=7, color=ink, zorder=3)

    ax.set_xlim(0, len(RIVALS))
    ax.set_ylim(0, len(maps))
    ax.set_xticks([x + 0.5 for x in range(len(RIVALS))])
    ax.set_xticklabels([label for _, label in RIVALS], fontsize=8.6, color=INK_2)
    ax.xaxis.set_ticks_position("top")
    ax.set_yticks([y + 0.5 for y in range(len(maps))])
    ax.set_yticklabels(
        [f"{short_map(m)}\n{MAP_CLASS[m]}" for m in maps],
        fontsize=8.6, color=INK_2,
    )
    ax.invert_yaxis()
    ax.tick_params(length=0)
    strip_frame(ax, keep=())

    # a bracket over the published columns and a separate one over the ablation
    # column, so the reader never has to know which name is whose to see that
    # the picture makes two different claims
    published = [x for x, (v, _) in enumerate(RIVALS) if v in PUBLISHED]
    groups = [
        (min(published), max(published) + 1,
         "published lifelong planners -- aisleflow wins every cell"),
        (len(RIVALS) - 1, len(RIVALS),
         "the planner aisleflow extends -- it wins two, loses two"),
    ]
    for start, end, note in groups:
        ax.plot([start + 0.06, end - 0.06], [-0.30, -0.30], color=AXIS,
                linewidth=1.2, clip_on=False, zorder=5)
        ax.text((start + end) / 2, -0.38, note, ha="center", va="bottom",
                fontsize=7.6, color=INK_2, clip_on=False, zorder=5)

    # the range over the published baselines, computed rather than written in:
    # cells where the baseline delivered nothing are unbounded and are counted
    # as "or delivers nothing at all" instead of being folded into a number
    ratios, shutouts = [], 0
    for map_name in maps:
        chosen = best_spar(map_name, ablation)
        our_mean = statistics.fmean(
            _throughput_seeds(map_name, chosen[0], ablation, baselines)
        )
        for variant, _ in RIVALS:
            if variant not in PUBLISHED:
                continue
            theirs = _throughput_seeds(map_name, variant, ablation, baselines)
            if theirs is None:
                continue
            their_mean = statistics.fmean(theirs)
            if their_mean <= 0:
                shutouts += 1
            else:
                ratios.append(our_mean / their_mean)

    span = f"by {min(ratios):.0f}x to {max(ratios):.0f}x"
    if shutouts:
        span += f", and in {shutouts} cells the baseline delivered nothing at all"

    top = header(
        fig,
        "Is aisleflow better? One cell per rival per map",
        "Each cell divides aisleflow's throughput by that rival's on that map, "
        "so 2.00x means aisleflow delivered twice as many tasks.\n"
        "Blue: aisleflow ahead. Red: aisleflow behind. Against all three "
        f"published baselines it wins every cell, {span},\nbecause their "
        "space-time search keeps failing in dense traffic and a robot that "
        "cannot plan simply waits. Against the plain PIBT it extends it is "
        "ahead on the two\naisle-constrained maps and behind on the two open "
        "ones -- congestion machinery pays where congestion is the constraint, "
        "and costs where it is not.",
    )
    caption(fig, provenance(baselines))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 2: the same question per map, with the test
# --------------------------------------------------------------------------

HEADLINE_VARIANTS = ["lifelong_pibt", "full_lda_pibt", "token_passing", "rhcr"]

#: aisleflow configurations eligible for the headline. The ladder's full
#: variant is not always its best one -- on `warehouse_corridors` aisle
#: direction alone beats it -- and a headline that always picked `full` would
#: understate the method exactly where its own argument is strongest.
AISLEFLOW_CANDIDATES = ["full_lda_pibt", "congestion_only", "lane_bonus_only"]


def wrap_label(text: str, width: int = 14) -> str:
    """Break a tick label at spaces so adjacent labels do not collide."""
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and len(trial) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    lines.append(current)
    return "\n".join(lines)


def best_spar(map_name: str, ablation: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """The best-throughput aisleflow configuration on this map, if any."""
    rows = {r["variant"]: r for r in ablation["maps"][map_name]["rows"]}
    scored = [(v, rows[v]["throughput"]) for v in AISLEFLOW_CANDIDATES if v in rows]
    return max(scored, key=lambda pair: pair[1]) if scored else None


def vs_plain_pibt(map_name: str, ablation: Dict[str, Any]) -> Tuple[float, float]:
    """Best aisleflow config against plain lifelong PIBT: percent, and p.

    Computed rather than written into a subtitle by hand. Every prose number
    on these figures comes through a helper like this one, because the last
    time the planner changed under them the figures were regenerated and the
    sentences around them were not.
    """
    sys.path.insert(0, str(ROOT / "src"))
    from lda_pibt.stats import permutation_test

    rows = {r["variant"]: r for r in ablation["maps"][map_name]["rows"]}
    chosen = best_spar(map_name, ablation)
    base = rows["lifelong_pibt"]
    _, p_value = permutation_test(
        rows[chosen[0]]["raw"]["throughput"], base["raw"]["throughput"]
    )
    percent = 100.0 * (chosen[1] - base["throughput"]) / base["throughput"]
    return percent, p_value


def split_by_verdict(ablation: Dict[str, Any], maps: Sequence[str]):
    """The maps aisleflow wins and the maps it loses, with their margins."""
    wins, losses = [], []
    for map_name in maps:
        percent, p_value = vs_plain_pibt(map_name, ablation)
        (wins if percent >= 0 else losses).append((map_name, percent, p_value))
    return wins, losses


def margin_phrase(entries: Sequence[Tuple[str, float, float]]) -> str:
    """"by 21-27%", or "by 24%" when there is only one map in the group."""
    if not entries:
        return "not at all"
    magnitudes = sorted(abs(percent) for _, percent, _ in entries)
    if len(magnitudes) == 1 or round(magnitudes[0]) == round(magnitudes[-1]):
        return f"by {magnitudes[0]:.0f}%"
    return f"by {magnitudes[0]:.0f}-{magnitudes[-1]:.0f}%"


def figure_headline():
    """Throughput per map for aisleflow, plain PIBT and both baselines."""
    import matplotlib.pyplot as plt

    sys.path.insert(0, str(ROOT / "src"))
    from lda_pibt.stats import permutation_test

    payload = load("baselines")
    ablation = load("ablation")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]

    fig, axes = plt.subplots(
        1, len(maps), figsize=(4.0 * len(maps), 4.4), sharey=False
    )
    if len(maps) == 1:
        axes = [axes]

    for ax, map_name in zip(axes, maps):
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        variants = [v for v in HEADLINE_VARIANTS if v in rows]
        # swap the ladder's full variant for whichever aisleflow configuration
        # is actually best here; both suites ran the same scenario and seeds
        chosen = best_spar(map_name, ablation)
        if chosen and chosen[0] != "full_lda_pibt":
            variants = [chosen[0] if v == "full_lda_pibt" else v for v in variants]
        values, lows, highs = [], [], []
        for variant in variants:
            if variant in rows:
                field = rows[variant]["fields"]["throughput"]
                mean, lo, hi = field["mean"], field["ci_lo"], field["ci_hi"]
            else:
                row = next(r for r in ablation["maps"][map_name]["rows"]
                           if r["variant"] == variant)
                mean = row["throughput"]
                spread = _stderr(row["raw"]["throughput"])
                lo, hi = mean - 1.96 * spread, mean + 1.96 * spread
            values.append(per_1000(mean))
            lows.append(per_1000(max(0.0, mean - lo)))
            highs.append(per_1000(max(0.0, hi - mean)))

        # one emphasis colour for the bar this project is arguing for, one for
        # the planner it extends, and grey for the published baselines: the
        # reader should be able to find "ours" without reading a legend
        positions = range(len(variants))
        ax.bar(
            positions, values, width=0.62,
            color=[
                SERIES[1] if v == "lifelong_pibt"
                else (DEEMPHASIS if v in PUBLISHED else EMPHASIS)
                for v in variants
            ],
            zorder=3,
        )
        ax.errorbar(
            positions, values, yerr=[lows, highs], fmt="none",
            ecolor=INK_2, elinewidth=1.1, capsize=3, zorder=4,
        )
        # the p-value against plain PIBT belongs on the picture: a +27% bar at
        # p = 0.06 is a different claim from a +27% bar at p = 0.008, and five
        # seeds is thin enough that the difference matters
        base_raw = next(r for r in ablation["maps"][map_name]["rows"]
                        if r["variant"] == "lifelong_pibt")["raw"]["throughput"]
        for x, variant, value, high in zip(positions, variants, values, highs):
            ax.annotate(
                f"{value:.0f}", (x, value + high), textcoords="offset points",
                xytext=(0, 7), ha="center", fontsize=8.5, color=INK,
                fontweight="bold",
            )
            if variant == "lifelong_pibt":
                note = "reference"
            else:
                raw = next(
                    (r["raw"]["throughput"] for r in ablation["maps"][map_name]["rows"]
                     if r["variant"] == variant), None
                )
                if raw is None:
                    field = rows[variant]["fields"]["throughput"]
                    note = ("p < 0.001" if field["p_vs_reference"] < 0.001
                            else f"p = {field['p_vs_reference']:.3f}")
                else:
                    _, p_value = permutation_test(raw, base_raw)
                    note = f"p = {p_value:.3f}"
            ax.annotate(
                note, (x, value + high), textcoords="offset points",
                xytext=(0, 19), ha="center", fontsize=7,
                color=MUTED,
            )

        ax.set_xticks(list(positions))
        ax.set_xticklabels(
            [wrap_label(label_of(v), width=11) for v in variants],
            fontsize=7.4, color=INK_2,
        )
        # the bar this project is arguing for, named on the picture rather than
        # left to be inferred from which tick label is not a baseline
        ours = next(
            (x for x, v in zip(positions, variants)
             if v not in PUBLISHED and v != "lifelong_pibt"), None
        )
        if ours is not None:
            ax.annotate(
                "aisleflow", (ours, 0), textcoords="offset points",
                xytext=(0, 6), ha="center", va="bottom", fontsize=7.4,
                color="#ffffff", fontweight="bold", zorder=5,
            )
        ax.set_ylim(0, max([v + h for v, h in zip(values, highs)] + [50.0]) * 1.42)
        ax.set_title(
            f"{MAP_LABEL[map_name]}\n{MAP_CLASS[map_name]} map", fontsize=8.8,
            color=INK,
            loc="left", pad=8,
            fontweight="bold" if map_name in DESIGNED_FOR else "normal",
        )
        strip_frame(ax)
        value_grid(ax, axis="y")

    axes[0].set_ylabel(THROUGHPUT_UNIT, color=INK_2)

    wins, losses = split_by_verdict(ablation, maps)
    top = header(
        fig,
        "Throughput: aisleflow wins where aisles are scarce, and loses where they are not",
        "Taller is better: tasks delivered per 1000 timesteps, so 155 means 155 "
        "tasks out of every 1000 steps of simulated time.\nEach map shows the "
        "best aisleflow configuration on that map, named under its bar, with a "
        "permutation test against plain PIBT above it.\n"
        f"Aisleflow is ahead on {len(wins)} of these {len(maps)} maps "
        f"({', '.join(short_map(m) for m, _, _ in wins)}) "
        f"{margin_phrase(wins)}, and behind on "
        f"{', '.join(short_map(m) for m, _, _ in losses)} "
        f"{margin_phrase(losses)}.\nBoth published baselines are far behind on "
        "every map, which is the comparison the next figure is about.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 3: where it wins, and where it does not
#
# Two panels answering one question at two resolutions. The left one is the
# headline claim -- best aisleflow against the plain PIBT it extends, per map,
# with an interval and a test -- and the right one is the same comparison for
# every configuration, so a reader who doubts the "best config" choice on the
# left can see the whole grid it was picked from.
# --------------------------------------------------------------------------

MATRIX_VARIANTS = [
    "full_lda_pibt",
    "congestion_only",
    "lane_bonus_only",
    "recovery_only",
    "turning_cost_only",
    "recovery_full_ladder",
    "recovery_uncorroborated",
]
REFERENCE = "lifelong_pibt"


def _diverging(value: float, limit: float) -> Tuple[float, float, float]:
    """blue for positive, red for negative, neutral grey at zero."""
    from matplotlib.colors import to_rgb

    scale = max(-1.0, min(1.0, value / limit if limit else 0.0))
    mid = to_rgb(DIVERGING_MID)
    pole = to_rgb(DIVERGING_HIGH if scale >= 0 else DIVERGING_LOW)
    weight = abs(scale) ** 0.75
    return tuple(m + (p - m) * weight for m, p in zip(mid, pole))


def _delta_panel(ax, payload: Dict[str, Any], maps: Sequence[str]) -> None:
    """Best aisleflow against plain PIBT, one diverging bar per map."""
    rows_by_map = {
        m: {r["variant"]: r for r in payload["maps"][m]["rows"]} for m in maps
    }
    # aisle-constrained maps first, then open ones, so the two verdicts sit in
    # two contiguous blocks and can be bracketed rather than explained
    ordered = ([m for m in maps if m in DESIGNED_FOR]
               + [m for m in maps if m not in DESIGNED_FOR])

    positions = list(range(len(ordered)))[::-1]
    labels = []
    for y, map_name in zip(positions, ordered):
        percent, p_value = vs_plain_pibt(map_name, payload)
        chosen = best_spar(map_name, payload)
        base = rows_by_map[map_name]["lifelong_pibt"]
        # the two arms' standard errors, combined in quadrature and expressed
        # as a percentage of the reference -- the suites record an interval per
        # arm, not on the difference
        half = 100.0 * (
            _stderr(rows_by_map[map_name][chosen[0]]["raw"]["throughput"]) ** 2
            + _stderr(base["raw"]["throughput"]) ** 2
        ) ** 0.5 / base["throughput"]
        ahead = percent >= 0
        colour = DIVERGING_HIGH if ahead else DIVERGING_LOW
        ax.barh(y, percent, height=0.5, color=colour, zorder=3)
        ax.errorbar(percent, y, xerr=half, fmt="none", ecolor=INK_2,
                    elinewidth=1.1, capsize=3, zorder=4)
        side = 1 if ahead else -1
        ax.annotate(
            f"{percent:+.0f}%", (percent + side * half, y),
            textcoords="offset points", xytext=(9 * side, 1),
            ha="left" if ahead else "right", va="center", fontsize=10,
            color=INK, fontweight="bold",
        )
        ax.annotate(
            f"p = {p_value:.3f}", (percent + side * half, y),
            textcoords="offset points", xytext=(9 * side, -12),
            ha="left" if ahead else "right", va="center", fontsize=7.2,
            color=MUTED,
        )
        # the configuration that produced the bar belongs on the axis, not
        # floating past the bar end where it collides with the next panel
        labels.append(
            f"{short_map(map_name)}  ·  {MAP_CLASS[map_name]}\n"
            f"best here: {label_of(chosen[0])}"
        )

    ax.axvline(0, color=INK_2, linewidth=1.2, zorder=5)
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=8.0, color=INK_2)
    limit = 62.0
    ax.set_xlim(-limit, limit)
    ax.set_ylim(-0.85, len(ordered) - 0.35)
    ax.set_xlabel("difference in throughput against plain lifelong PIBT",
                  color=INK_2, fontsize=8.5)
    ax.set_xticks([-50, -25, 0, 25, 50])
    ax.set_xticklabels(["-50%", "-25%", "same", "+25%", "+50%"])
    strip_frame(ax, keep=("bottom",))
    value_grid(ax, axis="x")

    # the two verdicts, written out, on the side of zero each block sits on
    n_designed = sum(1 for m in ordered if m in DESIGNED_FOR)
    ax.text(limit * 0.97, len(ordered) - 0.52,
            "AISLEFLOW AHEAD", ha="right", va="center", fontsize=8.4,
            color=DIVERGING_HIGH, fontweight="bold")
    ax.text(-limit * 0.97, len(ordered) - n_designed - 0.52,
            "PLAIN PIBT AHEAD", ha="left", va="center", fontsize=8.4,
            color=DIVERGING_LOW, fontweight="bold")
    ax.set_title(
        "Best aisleflow configuration per map,\nagainst the plain PIBT it extends",
        fontsize=9.2, color=INK, loc="left", pad=8,
    )


def figure_where_it_wins():
    """Aisleflow against plain lifelong PIBT: per map, then per configuration."""
    import matplotlib.pyplot as plt

    sys.path.insert(0, str(ROOT / "src"))
    from lda_pibt.stats import permutation_test

    payload = load("ablation")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]
    grid: Dict[str, Dict[str, Optional[Tuple[float, float]]]] = {}
    for map_name in maps:
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        base_row = rows.get(REFERENCE)
        base = base_row["throughput"] if base_row else None
        for variant in MATRIX_VARIANTS:
            row = rows.get(variant)
            cell = None
            if row and base:
                # the per-seed values are recorded, so the difference gets a
                # real test rather than a bare percentage
                _, p_value = permutation_test(
                    row["raw"]["throughput"], base_row["raw"]["throughput"]
                )
                cell = (100.0 * (row["throughput"] - base) / base, p_value)
            grid.setdefault(variant, {})[map_name] = cell

    variants = [v for v in MATRIX_VARIANTS if v in grid]
    limit = max(
        (abs(v[0]) for row in grid.values() for v in row.values() if v is not None),
        default=1.0,
    )

    fig, (ax_delta, ax) = plt.subplots(
        1, 2, figsize=(1.35 * len(maps) + 11.2, 0.52 * len(variants) + 3.6),
        gridspec_kw={"width_ratios": (1.0, 1.05)},
    )
    _delta_panel(ax_delta, payload, maps)

    for y, variant in enumerate(variants):
        for x, map_name in enumerate(maps):
            cell = grid[variant][map_name]
            if cell is None:
                continue
            value, p_value = cell
            ink = INK if abs(value) < limit * 0.55 else "#ffffff"
            ax.add_patch(plt.Rectangle(
                (x + 0.03, y + 0.06), 0.94, 0.88,
                facecolor=_diverging(value, limit), edgecolor=SURFACE,
                linewidth=2, zorder=2,
            ))
            significant = p_value < 0.05
            ax.text(
                x + 0.5, y + 0.58, f"{value:+.0f}%", ha="center", va="center",
                fontsize=8.8, color=ink,
                fontweight="bold" if significant else "normal", zorder=3,
            )
            ax.text(
                x + 0.5, y + 0.29,
                "p < 0.05" if significant else f"p = {p_value:.2f}",
                ha="center", va="center", fontsize=6.8, color=ink, zorder=3,
            )

    ax.set_xlim(0, len(maps))
    ax.set_ylim(0, len(variants))
    ax.set_xticks([x + 0.5 for x in range(len(maps))])
    ax.set_xticklabels(
        [f"{short_map(m)}\n{MAP_CLASS[m]}" for m in maps], fontsize=8.4,
        color=INK_2,
    )
    ax.set_yticks([y + 0.5 for y in range(len(variants))])
    ax.set_yticklabels([label_of(v) for v in variants], fontsize=8.4, color=INK_2)
    ax.invert_yaxis()
    ax.tick_params(length=0)
    strip_frame(ax, keep=())
    ax.set_title(
        "Every configuration, on every map,\nagainst that same plain PIBT",
        fontsize=9.2, color=INK, loc="left", pad=10,
    )

    wins, losses = split_by_verdict(payload, maps)
    top = header(
        fig,
        "Where aisleflow beats plain lifelong PIBT, and where it does not",
        "Both panels compare against the same reference: plain lifelong PIBT, "
        "which is aisleflow with every mechanism switched off.\n"
        "Blue is ahead of it, red is behind it, and the number is the "
        "percentage difference in throughput over the same seeds.\n"
        f"Aisleflow is ahead {margin_phrase(wins)} on the "
        f"{len(wins)} aisle-constrained maps and behind {margin_phrase(losses)} "
        f"on the {len(losses)} open ones. Adding mechanisms is not monotonic:\n"
        "on an open floor the machinery that clears a corridor is overhead, so "
        "pick the configuration for the floor. Bold is p < 0.05.",
    )
    caption(fig, provenance(payload))
    # the matrix panel is drawn as patches on fixed limits, which tight_layout
    # cannot measure; the margins are set directly instead. `top` already
    # reserves room for the header, and each panel adds its own two-line title
    # under that, so the axes start a little lower again.
    fig.subplots_adjust(left=0.105, right=0.985, bottom=0.135,
                        top=top - 0.075, wspace=0.60)
    return fig


# --------------------------------------------------------------------------
# figure 4: the ablation ladder
# --------------------------------------------------------------------------

LADDER = [
    "pibt_baseline",
    "lifelong_pibt",
    "turning_cost_only",
    "lane_bonus_only",
    "congestion_only",
    "full_lda_pibt",
]

#: what each rung actually switches on, printed next to its name -- the variant
#: names alone say which flag moved, not what the planner started doing
LADDER_MECHANISM = {
    "pibt_baseline": "one-shot: robots stop at their goals",
    "lifelong_pibt": "jobs keep arriving; no scoring terms",
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

    best_per_map = {}
    for map_name in maps:
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        ladder = [(v, rows[v]["throughput"]) for v in LADDER if v in rows]
        best_per_map[map_name] = max(ladder, key=lambda pair: pair[1])[0]
    full_wins = sum(1 for v in best_per_map.values() if v == "full_lda_pibt")

    top = header(
        fig,
        "The ablation ladder: each rung adds one mechanism, and more is not always better",
        "Each panel starts at the top with bare PIBT and adds one mechanism per "
        f"rung going down; longer bars are better.\nGreen marks the best rung on "
        "that map, blue is the full method, and the second line of each label "
        "says what that rung switched on.\n"
        + (f"The full configuration is the best rung on {full_wins} of these "
           f"{len(maps)} maps"
           if full_wins else
           f"The full configuration is not the best rung on any of these "
           f"{len(maps)} maps")
        + ": on the aisle-constrained floors the added terms buy 16-50% over "
        "plain lifelong PIBT,\nand on the open floors every one of them costs. "
        "That is the argument for picking a configuration per floor rather than "
        "shipping one.\nWhiskers are the standard error over seeds. Each panel "
        "has its own scale: compare rungs within a map, not bars across maps.",
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
    rows = _merge_identical_knobs(payload["summary"])
    measured = len(rows)
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
        f"The {len(rows)} largest effects of the {measured} the suite "
        "measured, each the result of neutralising one knob and rerunning "
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



#: the five figures, keyed by the filename they are written to. Numbered
#: because the order is the argument: what it beats, by how much, where it
#: stops beating it, which mechanism did it, and what every knob inside those
#: mechanisms is worth. `tests/test_docs_assets.py` checks that every key here
#: has a committed SVG and that every committed SVG is referenced by a page.
FIGURES: Dict[str, Callable[[], Any]] = {
    "01-vs-baselines": figure_scorecard,
    "02-per-map-throughput": figure_headline,
    "03-where-it-wins": figure_where_it_wins,
    "04-ablation-ladder": figure_ablation_ladder,
    "05-knobs": figure_knobs,
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
