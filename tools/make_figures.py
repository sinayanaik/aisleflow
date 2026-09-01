#!/usr/bin/env python3
"""Draw the result figures in ``docs/figures/`` from ``docs/data/``.

The results in this project are honest and hard to read: five sections of the
README carry raw means with no intervals, on four maps, for eighteen variants
and three external baselines, and the single question a reader actually has --
"is this better?" -- has a different answer per map. These figures answer it on
sight, and answer it in both directions, because the method wins on two maps
and loses on one.

Every figure reads `docs/data/*.json`, written by `experiments/run_all.py`, and
nothing here computes a simulation or invents a number: the intervals are the
bootstrap intervals the experiment recorded, and the p-values are its
permutation tests.

Usage::

    python3 tools/make_figures.py                # every figure it has data for
    python3 tools/make_figures.py --only forest headline
    python3 tools/make_figures.py --list
    python3 tools/make_figures.py --dashboard    # also write docs/dashboard.html

Each figure is written twice: `.svg`, which GitHub renders inline, and `.pdf`,
which the LaTeX paper includes. Needs `matplotlib` (``pip install -e ".[viz]"``).
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
# this surface), and the scatter, which needs all pairs to separate, uses only
# the first three. Two of the four sit below 3:1 against the surface, so every
# figure that uses them carries visible direct labels rather than relying on
# the colour alone.
#
# These figures are deliberately single-mode: they are embedded in a printed
# PDF and rendered on GitHub, neither of which follows a viewer theme, so the
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

#: the maps, ordered the way the argument runs: the two the aisle layer was
#: designed for, then the two where it is not the right tool
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
#: which maps the aisle layer is actually for -- drawn as a band, not asserted
DESIGNED_FOR = {"warehouse_bottleneck", "warehouse_corridors"}

VARIANT_LABEL = {
    "lifelong_pibt": "plain lifelong PIBT",
    "full_lda_pibt": "AisleFlow (full)",
    "aisle_direction_only": "AisleFlow (aisle direction)",
    "hysteresis_pibt": "PIBT + hysteresis",
    "aisle_managed_pibt": "AisleFlow (aisle managed)",
    "directional_pibt": "PIBT + robot direction",
    "token_passing": "Token Passing",
    "token_passing_recovery": "Token Passing + recovery",
    "rhcr": "RHCR",
    "turning_cost_only": "PIBT + turning cost",
    "reservations_only": "PIBT + entry admission",
    "aisle_direction_hard": "aisle direction, enforced",
    "recovery_uncorroborated": "recovery, uncorroborated",
    "no_direction_term": "aisle direction, beta = 0",
    "aisle_direction_no_max_green": "aisle direction, no max green",
    "congestion_only": "PIBT + congestion",
    "recovery_only": "PIBT + recovery",
    "congestion_scoring_only": "congestion in movement",
    "congestion_assignment_only": "congestion in matching",
    "direction_control_only": "PIBT + direction control",
    "aisle_managed_hard": "aisle managed, enforced",
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


def save(fig, name: str) -> List[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for suffix in (".svg", ".pdf"):
        path = OUT_DIR / f"{name}{suffix}"
        fig.savefig(path, bbox_inches="tight", pad_inches=0.16)
        written.append(path)
    import matplotlib.pyplot as plt

    plt.close(fig)
    print(f"  wrote {', '.join(str(p.relative_to(ROOT)) for p in written)}")
    return written


# --------------------------------------------------------------------------
# figure 1: the headline
# --------------------------------------------------------------------------

HEADLINE_VARIANTS = ["lifelong_pibt", "full_lda_pibt", "token_passing", "rhcr"]

#: AisleFlow configurations eligible for the headline. The ladder's full
#: variant is not always its best one -- on `warehouse_corridors` aisle
#: direction alone beats it -- and a headline that always picked `full` would
#: understate the method exactly where its own argument is strongest.
AISLEFLOW_CANDIDATES = ["full_lda_pibt", "aisle_direction_only", "aisle_managed_pibt"]


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


def best_aisleflow(map_name: str, ablation: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """The best-throughput AisleFlow configuration on this map, if any."""
    rows = {r["variant"]: r for r in ablation["maps"][map_name]["rows"]}
    scored = [(v, rows[v]["throughput"]) for v in AISLEFLOW_CANDIDATES if v in rows]
    return max(scored, key=lambda pair: pair[1]) if scored else None


def figure_headline():
    """Throughput per map for AisleFlow, plain PIBT and both baselines."""
    import matplotlib.pyplot as plt

    sys.path.insert(0, str(ROOT / "src"))
    from lda_pibt.stats import permutation_test

    payload = load("baselines")
    ablation = load("ablation")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]

    fig, axes = plt.subplots(
        1, len(maps), figsize=(3.4 * len(maps), 4.1), sharey=False
    )
    if len(maps) == 1:
        axes = [axes]

    for ax, map_name in zip(axes, maps):
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        variants = [v for v in HEADLINE_VARIANTS if v in rows]
        # swap the ladder's full variant for whichever AisleFlow configuration
        # is actually best here; both suites ran the same scenario and seeds
        chosen = best_aisleflow(map_name, ablation)
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
            values.append(mean)
            lows.append(max(0.0, mean - lo))
            highs.append(max(0.0, hi - mean))

        positions = range(len(variants))
        ax.bar(
            positions, values, width=0.62,
            color=[SERIES[i] for i in range(len(variants))],
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
                f"{value:.2f}", (x, value + high), textcoords="offset points",
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
            [wrap_label(label_of(v)) for v in variants], fontsize=7.4, color=INK_2,
        )
        ax.set_ylim(0, max([v + h for v, h in zip(values, highs)] + [0.05]) * 1.42)
        kind = "aisle-shaped" if map_name in DESIGNED_FOR else "open"
        ax.set_title(
            f"{MAP_LABEL[map_name]}\n{kind} map", fontsize=8.8, color=INK,
            loc="left", pad=8,
            fontweight="bold" if map_name in DESIGNED_FOR else "normal",
        )
        strip_frame(ax)
        value_grid(ax, axis="y")

    axes[0].set_ylabel("tasks delivered per timestep", color=INK_2)
    top = header(
        fig,
        "Throughput: AisleFlow wins where aisles are scarce, and loses where they are not",
        "Each map shows the best AisleFlow configuration on that map, named "
        "under its bar, with a permutation test against plain PIBT.\nThe two "
        "wins are ahead by 21-27% but land at p = 0.06 on five seeds; the two "
        "losses are significant. Both baselines are far behind everywhere.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 2: the forest plot of mechanisms
# --------------------------------------------------------------------------


def figure_forest():
    """Every repaired mechanism's effect on throughput, with its interval."""
    import matplotlib.pyplot as plt

    payload = load("paired")
    rows = [r for r in payload["rows"] if "throughput" in r["fields"]]
    rows.sort(key=lambda r: (r["design"], MAP_ORDER.index(r["map"])))

    labels, deltas, spreads, colours = [], [], [], []
    for row in rows:
        field = row["fields"]["throughput"]
        delta = field["delta"]
        t_lo, t_hi = field["treatment_ci"]
        c_lo, c_hi = field["control_ci"]
        # the suites record an interval per arm, not on the difference; adding
        # the two half-widths in quadrature is the usual independent-arms
        # approximation, and the caption says so rather than implying the
        # experiment measured it directly
        half = (((t_hi - t_lo) / 2) ** 2 + ((c_hi - c_lo) / 2) ** 2) ** 0.5
        significant = field["p_value"] is not None and field["p_value"] < 0.05
        labels.append(f"{row['label']}  ·  {short_map(row['map'])}")
        deltas.append(delta)
        spreads.append(half)
        colours.append(EMPHASIS if significant else DEEMPHASIS)

    fig, ax = plt.subplots(figsize=(8.6, 0.34 * len(labels) + 2.3))
    positions = list(range(len(labels)))[::-1]
    ax.axvline(0, color=AXIS, linewidth=1.1, zorder=2)
    for x, y, half, colour in zip(deltas, positions, spreads, colours):
        ax.plot([x - half, x + half], [y, y], color=colour, linewidth=1.6,
                solid_capstyle="butt", zorder=3)
        for bound in (x - half, x + half):
            ax.plot([bound, bound], [y - 0.2, y + 0.2], color=colour,
                    linewidth=1.6, zorder=3)
    ax.scatter(deltas, positions, s=46, color=colours, zorder=4,
               edgecolor=SURFACE, linewidth=1.4)
    for x, y, half, colour in zip(deltas, positions, spreads, colours):
        significant = colour == EMPHASIS
        ax.annotate(
            f"{x:+.3f}", (x + half, y), textcoords="offset points",
            xytext=(7, -3), ha="left", fontsize=7.8,
            color=INK if significant else MUTED,
            fontweight="bold" if significant else "normal",
        )

    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=8.2, color=INK_2)
    ax.set_xlabel("change in throughput, tasks per timestep  (right is better)",
                  color=INK_2)
    strip_frame(ax, keep=("bottom",))
    value_grid(ax, axis="x")

    ax.scatter([], [], s=46, color=EMPHASIS, label="p < 0.05")
    ax.scatter([], [], s=46, color=DEEMPHASIS, label="not significant")
    # above the plot: every corner of the plot area carries either a row label
    # or a whisker
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, fontsize=8)

    top = header(
        fig,
        "Which mechanisms actually earn their cost",
        "Each row is one mechanism against the same configuration without it: "
        "same seeds, same map, one flag apart.\nThe p-value is the "
        "permutation test on the difference; the whisker adds the two arms' "
        "bootstrap intervals in quadrature.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 3: the win/loss matrix
# --------------------------------------------------------------------------

MATRIX_VARIANTS = [
    "full_lda_pibt",
    "aisle_direction_only",
    "aisle_managed_pibt",
    "hysteresis_pibt",
    "directional_pibt",
    "turning_cost_only",
    "reservations_only",
    "aisle_direction_hard",
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


def figure_winloss():
    """Every variant against plain lifelong PIBT, on every map."""
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

    fig, ax = plt.subplots(figsize=(1.55 * len(maps) + 4.2, 0.42 * len(variants) + 2.3))
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
    ax.set_xticklabels([short_map(m) for m in maps], fontsize=8.8, color=INK_2)
    ax.set_yticks([y + 0.5 for y in range(len(variants))])
    ax.set_yticklabels([label_of(v) for v in variants], fontsize=8.4, color=INK_2)
    ax.invert_yaxis()
    ax.tick_params(length=0)
    strip_frame(ax, keep=())

    top = header(
        fig,
        "Every configuration against plain lifelong PIBT",
        "Blue beats plain PIBT on that map; red loses to it. Percentage "
        "difference in throughput, with a two-sided permutation test over the "
        "same seeds; bold is p < 0.05.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 4: cost against benefit
# --------------------------------------------------------------------------

#: three families, so every pair of colours separates under CVD in a scatter
FAMILY = {
    "lifelong_pibt": ("PIBT family", SERIES[0]),
    "full_lda_pibt": ("PIBT family", SERIES[0]),
    "token_passing": ("Token Passing", SERIES[1]),
    "token_passing_recovery": ("Token Passing", SERIES[1]),
    "rhcr": ("RHCR", SERIES[2]),
}


def figure_cost_benefit():
    """What each planner costs per timestep, against what it delivers."""
    import matplotlib.pyplot as plt

    payload = load("baselines")
    fig, ax = plt.subplots(figsize=(7.6, 4.6))

    seen: Dict[str, bool] = {}
    cluster: List[Tuple[float, float]] = []
    labelled: List[str] = []
    for map_name in [m for m in MAP_ORDER if m in payload["maps"]]:
        for row in payload["maps"][map_name]["rows"]:
            variant = row["variant"]
            if variant not in FAMILY:
                continue
            family, colour = FAMILY[variant]
            x = row["fields"]["mean_runtime_ms_per_step"]["mean"]
            y = row["fields"]["throughput"]["mean"]
            ax.scatter(
                x, y, s=64, color=colour, zorder=4,
                edgecolor=SURFACE, linewidth=1.6,
                label=family if family not in seen else None,
            )
            seen[family] = True
            if family == "PIBT family":
                # the baselines all land in one cluster in the bottom right,
                # where per-point labels would sit on top of each other; that
                # cluster is annotated once, below.
                # alternate above/below: the PIBT points cluster tightly at
                # the cheap end and same-side labels sit on each other
                below = len(labelled) % 2 == 1
                ax.annotate(
                    f"{label_of(variant)} · {short_map(map_name)}",
                    (x, y), textcoords="offset points",
                    xytext=(8, -11 if below else 4),
                    fontsize=7.4, color=INK_2,
                )
                labelled.append(variant)
            else:
                cluster.append((x, y))

    if cluster:
        xs = [x for x, _ in cluster]
        ys = [y for _, y in cluster]
        ax.annotate(
            f"every Token Passing and RHCR run:\n"
            f"{min(xs):.0f} to {max(xs):.0f} ms per timestep,\n"
            f"at most {max(ys):.2f} tasks per timestep",
            (max(xs), max(ys)), textcoords="offset points", xytext=(-18, 44),
            ha="right", fontsize=8, color=INK_2,
            arrowprops=dict(arrowstyle="->", color=AXIS, lw=1.0),
            bbox=dict(boxstyle="round,pad=0.45", facecolor=SURFACE,
                      edgecolor=GRID, linewidth=0.9),
        )

    ax.set_xscale("log")
    ax.set_xlabel("planner cost: mean milliseconds per timestep (log scale)",
                  color=INK_2)
    ax.set_ylabel("throughput, tasks per timestep", color=INK_2)
    strip_frame(ax)
    value_grid(ax, axis="both")
    ax.legend(loc="upper right", fontsize=8.5)

    top = header(
        fig,
        "Cost against benefit: the cheap planners are also the productive ones",
        "Up and to the left is better. Both baselines cost 20x to 300x more per "
        "timestep and deliver less.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 5: offered load against served load
# --------------------------------------------------------------------------


def figure_load():
    """How much of the offered demand each planner actually serves."""
    import matplotlib.pyplot as plt

    payload = load("baselines")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]
    fig, axes = plt.subplots(len(maps), 1, figsize=(7.8, 1.55 * len(maps) + 2.1))
    if len(maps) == 1:
        axes = [axes]

    for ax, map_name in zip(axes, maps):
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        variants = [v for v in HEADLINE_VARIANTS if v in rows]
        offered = max(
            rows[v]["fields"]["released_tasks"]["mean"] for v in variants
        )
        positions = list(range(len(variants)))[::-1]
        for y, variant in zip(positions, variants):
            served = rows[variant]["fields"]["completed_tasks"]["mean"]
            share = served / offered if offered else 0.0
            ax.barh(y, offered, height=0.55, color=GRID, zorder=2)
            ax.barh(y, served, height=0.55, color=EMPHASIS, zorder=3)
            ax.annotate(
                f"{served:.0f} of {offered:.0f} tasks  ·  {share:.0%} served",
                (offered, y), textcoords="offset points", xytext=(8, -3),
                fontsize=8, color=INK_2,
            )
        ax.set_yticks(positions)
        ax.set_yticklabels([label_of(v) for v in variants], fontsize=8.2,
                           color=INK_2)
        ax.set_xlim(0, offered * 1.55)
        ax.set_title(short_map(map_name), fontsize=9, color=INK, loc="left")
        ax.tick_params(length=0)
        strip_frame(ax, keep=())
        ax.set_xticks([])

    top = header(
        fig,
        "Every planner here is in saturation",
        "Grey is the demand the arrival process released; blue is what got "
        "delivered. Nothing serves most of its demand, which is why throughput "
        "measures capacity here and service time does not.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 6: the service-time trap
# --------------------------------------------------------------------------


def figure_censoring():
    """Why a low mean service time next to a low throughput is a symptom."""
    import matplotlib.pyplot as plt

    payload = load("baselines")
    # the starkest case: the map where the worst-throughput planner reports one
    # of the best service times. Picked by that gap rather than named, so the
    # figure keeps working if the numbers move.
    def gap(map_name: str) -> float:
        rows = payload["maps"][map_name]["rows"]
        scored = [
            (r["fields"]["throughput"]["mean"], r["fields"]["mean_service_time"]["mean"])
            for r in rows if r["fields"]["mean_service_time"]["mean"] > 0
        ]
        if len(scored) < 2:
            return -1.0
        worst = min(scored, key=lambda pair: pair[0])
        best = max(scored, key=lambda pair: pair[0])
        return best[1] - worst[1]

    maps = [m for m in MAP_ORDER if m in payload["maps"]]
    map_name = max(maps, key=gap)
    rows = payload["maps"][map_name]["rows"]
    order = sorted(rows, key=lambda r: -r["fields"]["throughput"]["mean"])
    variants = [r["variant"] for r in order]

    fig, axes = plt.subplots(1, 2, figsize=(9.8, 4.3))
    panels = [
        ("throughput", "tasks delivered per timestep", "higher is better", 3),
        ("mean_service_time", "mean service time, timesteps", "lower looks better", 0),
    ]
    #: the planner the figure is about: the flattering service time belongs to
    #: whichever planner reports the *lowest* one while having finished
    #: something at all. A planner that finished nothing has no service time to
    #: flatter it, which is its own point and is labelled separately below.
    finished = [
        r for r in order if r["fields"]["mean_service_time"]["mean"] > 0
    ]
    culprit = min(
        finished, key=lambda r: r["fields"]["mean_service_time"]["mean"]
    )["variant"] if finished else variants[-1]

    for ax, (metric, label, direction, digits) in zip(axes, panels):
        values = [
            next(r for r in rows if r["variant"] == v)["fields"][metric]["mean"]
            for v in variants
        ]
        colours = [
            STATUS["critical"] if v == culprit else DEEMPHASIS for v in variants
        ]
        positions = list(range(len(variants)))[::-1]
        ax.barh(positions, values, height=0.6, color=colours, zorder=3)
        for y, value in zip(positions, values):
            text = f"{value:.{digits}f}"
            if metric == "mean_service_time" and value == 0:
                text = "no tasks finished: undefined"
            ax.annotate(text, (value, y), textcoords="offset points",
                        xytext=(6, -3), fontsize=8.2, color=INK_2)
        ax.set_yticks(positions)
        ax.set_yticklabels([label_of(v) for v in variants], fontsize=8, color=INK_2)
        ax.set_xlim(0, max(values) * 1.55)
        ax.set_title(f"{label}\n{direction}", fontsize=9, color=INK, loc="left",
                     pad=54)
        strip_frame(ax, keep=("bottom",))
        value_grid(ax, axis="x")

    culprit_row = next(r for r in rows if r["variant"] == culprit)
    axes[1].annotate(
        f"{label_of(culprit)} reports the best service time here\n"
        f"({culprit_row['fields']['mean_service_time']['mean']:.0f} steps) while "
        f"delivering "
        f"{culprit_row['fields']['throughput']['mean']:.3f} tasks per timestep.\n"
        "The only tasks it finishes are the easy early ones.",
        (0.02, 1.20), xycoords="axes fraction", ha="left", va="top",
        fontsize=8, color=INK,
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#fbf3ec",
                  edgecolor=STATUS["critical"], linewidth=0.9),
    )

    top = header(
        fig,
        "Service time is censored: it counts only the tasks that finished",
        f"Both panels are the same five planners on {short_map(map_name)}, in the "
        "same order.\nCompare service times only between planners of comparable "
        "throughput; on its own, the metric rewards giving up.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 7: the ablation ladder
# --------------------------------------------------------------------------

LADDER = [
    "lifelong_pibt",
    "directional_pibt",
    "hysteresis_pibt",
    "aisle_managed_pibt",
    "full_lda_pibt",
]


def figure_ablation_ladder():
    """The cumulative ladder, one panel per map, with intervals."""
    import matplotlib.pyplot as plt
    import statistics

    payload = load("ablation")
    maps = [m for m in MAP_ORDER if m in payload["maps"]]
    fig, axes = plt.subplots(1, len(maps), figsize=(2.85 * len(maps), 4.3))
    if len(maps) == 1:
        axes = [axes]

    for ax, map_name in zip(axes, maps):
        rows = {r["variant"]: r for r in payload["maps"][map_name]["rows"]}
        variants = [v for v in LADDER if v in rows]
        values = [rows[v]["throughput"] for v in variants]
        errors = []
        for v in variants:
            raw = rows[v].get("raw", {}).get("throughput")
            errors.append(
                statistics.stdev(raw) / (len(raw) ** 0.5) if raw and len(raw) > 1
                else 0.0
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
        for y, value in zip(positions, values):
            ax.annotate(f"{value:.3f}", (value, y), textcoords="offset points",
                        xytext=(6, -3), fontsize=8, color=INK_2)
        ax.set_yticks(positions)
        ax.set_yticklabels([label_of(v) for v in variants], fontsize=7.8,
                           color=INK_2)
        ax.set_xlim(0, max(values) * 1.45)
        ax.set_title(short_map(map_name), fontsize=9.5, color=INK, loc="left")
        strip_frame(ax, keep=("bottom",))
        value_grid(ax, axis="x")

    top = header(
        fig,
        "The ablation ladder: adding mechanisms does not monotonically help",
        "Green is the best configuration on that map; blue is the full method. "
        "Bars are throughput; whiskers are the standard error over seeds.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


# --------------------------------------------------------------------------
# figure 8: the hypotheses
# --------------------------------------------------------------------------

VERDICT_STATUS = {
    "supported": ("good", "supported"),
    "contradicted": ("critical", "contradicted"),
    "no measurable effect": ("neutral", "no effect"),
}


def figure_hypotheses():
    """Each hypothesis scored on the quantity it actually claims to move."""
    import matplotlib.pyplot as plt

    payload = load("hypotheses")
    rows = payload["rows"]
    keys = sorted({r["hypothesis"] for r in rows})
    maps = [m for m in MAP_ORDER if any(r["map"] == m for r in rows)]

    fig, ax = plt.subplots(figsize=(1.7 * len(maps) + 4.6, 0.52 * len(keys) + 2.6))
    for y, key in enumerate(keys):
        for x, map_name in enumerate(maps):
            row = next(
                (r for r in rows if r["hypothesis"] == key and r["map"] == map_name),
                None,
            )
            if row is None:
                continue
            status, word = VERDICT_STATUS[row["verdict"]]
            field = row["fields"][row["metric"]]
            ax.add_patch(plt.Rectangle(
                (x + 0.03, y + 0.08), 0.94, 0.84,
                facecolor=SURFACE, edgecolor=STATUS[status], linewidth=1.6,
                zorder=2,
            ))
            ax.text(x + 0.5, y + 0.62, word, ha="center", va="center",
                    fontsize=8.4, color=STATUS[status], fontweight="bold",
                    zorder=3)
            ax.text(
                x + 0.5, y + 0.3,
                f"{field['treatment_mean']:.3g} vs {field['control_mean']:.3g}",
                ha="center", va="center", fontsize=7.6, color=INK_2, zorder=3,
            )

    labels = []
    for key in keys:
        row = next(r for r in rows if r["hypothesis"] == key)
        arrow = "lower is better" if row["better"] == "lower" else "higher is better"
        labels.append(f"{key}  ·  {row['metric']}\n{arrow}")

    ax.set_xlim(0, len(maps))
    ax.set_ylim(0, len(keys))
    ax.set_xticks([x + 0.5 for x in range(len(maps))])
    ax.set_xticklabels([short_map(m) for m in maps], fontsize=8.8, color=INK_2)
    ax.set_yticks([y + 0.5 for y in range(len(keys))])
    ax.set_yticklabels(labels, fontsize=7.8, color=INK_2)
    ax.invert_yaxis()
    ax.tick_params(length=0)
    strip_frame(ax, keep=())

    top = header(
        fig,
        "Six hypotheses, each scored on the quantity it actually claims to move",
        "Treatment against the control that isolates its mechanism. "
        "A verdict needs p < 0.05 on the hypothesis's own metric; "
        "each cell prints treatment vs control.",
    )
    caption(fig, provenance(payload))
    fig.tight_layout(rect=(0, 0.028, 1, top))
    return fig


FIGURES: Dict[str, Callable[[], Any]] = {
    "headline": figure_headline,
    "forest": figure_forest,
    "winloss": figure_winloss,
    "cost-benefit": figure_cost_benefit,
    "load": figure_load,
    "censoring": figure_censoring,
    "ablation-ladder": figure_ablation_ladder,
    "hypotheses": figure_hypotheses,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", choices=sorted(FIGURES), default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--dashboard", action="store_true",
                        help="also write docs/dashboard.html")
    parser.add_argument("--dashboard-only", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for name in FIGURES:
            print(name)
        return 0

    failures = 0
    if not args.dashboard_only:
        style()
        for name in args.only or list(FIGURES):
            print(f"\n### {name}")
            try:
                save(FIGURES[name](), name)
            except MissingData as error:
                print(f"  skipped: {error}")
                failures += 1

    if args.dashboard or args.dashboard_only:
        from dashboard import build_dashboard  # noqa: E402

        print("\n### dashboard")
        build_dashboard()
    return 1 if failures and args.only else 0


sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    raise SystemExit(main())
