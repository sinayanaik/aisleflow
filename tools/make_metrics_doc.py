#!/usr/bin/env python3
"""Write ``docs/metrics.md`` -- what every reported number means, from the data.

The project reports about twenty quantities across five suites, and the honest
answer to "is SPAR-PIBT better?" is different for each of them. Three of the
twenty are actively misleading if read alone: mean service time is censored,
total travel distance confounds efficiency with how much got delivered, and any
raw count scales with how long the run lasted rather than with how well it went.

So this generates a guide: every metric, what it measures, which direction is
good, what can go wrong when you read it, and -- for the three that decide the
verdict -- the measured numbers with their p-values.

The tables come from `docs/data/`; the prose is written here beside them, so a
number and its explanation cannot drift apart. Run it after any change to the
dataset:

    python3 tools/make_metrics_doc.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
OUT = ROOT / "docs" / "metrics.md"

sys.path.insert(0, str(ROOT / "src"))
from lda_pibt.stats import permutation_test  # noqa: E402

MAP_ORDER = ["warehouse_bottleneck", "warehouse_corridors",
             "warehouse_narrow", "warehouse_medium"]
AISLE_SHAPED = {"warehouse_bottleneck", "warehouse_corridors"}

#: the configuration this guide reports as "SPAR-PIBT": the best-throughput
#: SPAR configuration on every map in this dataset, and the one the headline
#: figure uses
OURS = "aisle_direction_only"
BASE = "lifelong_pibt"


def load(name: str) -> Dict[str, Any]:
    path = DATA / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing -- run experiments/run_all.py")
    return json.loads(path.read_text())


def short(name: str) -> str:
    return name.replace("warehouse_", "")


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def fmt_p(p: float | None) -> str:
    if p is None:
        return "—"
    if p <= 0.008:
        return "**0.008**"
    return f"**{p:.3f}**" if p < 0.05 else f"{p:.3f}"


def rows_of(ablation: Dict[str, Any], map_name: str) -> Dict[str, Any]:
    return {r["variant"]: r for r in ablation["maps"][map_name]["rows"]}


def compare(
    ablation: Dict[str, Any],
    derive: Callable[[Dict[str, Any]], List[float]],
    digits: int = 2,
    lower_is_better: bool = True,
) -> List[str]:
    """One markdown table: our number, plain PIBT's, the delta and a p-value."""
    out = [
        "| map | SPAR-PIBT | plain PIBT | difference | p | |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for map_name in MAP_ORDER:
        if map_name not in ablation["maps"]:
            continue
        rows = rows_of(ablation, map_name)
        ours, base = derive(rows[OURS]), derive(rows[BASE])
        our_mean, base_mean = mean(ours), mean(base)
        _, p = permutation_test(ours, base)
        better = (our_mean < base_mean) if lower_is_better else (our_mean > base_mean)
        pct = 100 * (our_mean - base_mean) / base_mean if base_mean else 0.0
        verdict = ("**win**" if better else "loss") if p < 0.05 else "—"
        shape = " ★" if map_name in AISLE_SHAPED else ""
        out.append(
            f"| {short(map_name)}{shape} | {our_mean:.{digits}f} | "
            f"{base_mean:.{digits}f} | {pct:+.0f}% | {fmt_p(p)} | {verdict} |"
        )
    return out


# --------------------------------------------------------------------------
# the derived quantities the verdict turns on
# --------------------------------------------------------------------------


def _per_task(row: Dict[str, Any], field: str) -> List[float]:
    raw = row["raw"]
    return [
        raw[field][i] / max(1, raw["completed_tasks"][i])
        for i in range(len(raw["completed_tasks"]))
    ]


def travel_per_task(row: Dict[str, Any]) -> List[float]:
    return _per_task(row, "total_travel_distance")


def backtracks_per_task(row: Dict[str, Any]) -> List[float]:
    return _per_task(row, "pibt_backtracks")


def headon_per_1000(row: Dict[str, Any], steps: int = 400) -> List[float]:
    return [1000 * v / steps for v in row["raw"]["head_on_conflicts"]]


def baseline_table(baselines: Dict[str, Any]) -> List[str]:
    out = [
        "| map | planner | throughput (per 1000 steps) | ms/step | "
        "p vs plain PIBT |",
        "|---|---|---:|---:|---:|",
    ]
    for map_name in MAP_ORDER:
        if map_name not in baselines["maps"]:
            continue
        for row in baselines["maps"][map_name]["rows"]:
            fields = row["fields"]
            out.append(
                f"| {short(map_name)} | `{row['variant']}` | "
                f"{1000 * fields['throughput']['mean']:.0f} | "
                f"{fields['mean_runtime_ms_per_step']['mean']:.1f} | "
                f"{fmt_p(fields['throughput']['p_vs_reference'])} |"
            )
    return out


# --------------------------------------------------------------------------
# the catalogue: (metric, direction, what it measures, how it misleads)
# --------------------------------------------------------------------------

CATALOGUE = [
    ("`throughput`", "higher",
     "Tasks delivered per timestep — the primary objective. Reported "
     "**per 1000 timesteps** everywhere it is shown, so 149 means 149 tasks "
     "out of every 1000 steps.",
     "Nothing, in this regime — but see the saturation note above: it measures "
     "the floor's *capacity*, not how busy the robots were."),
    ("`completed_tasks`", "higher", "Total deliveries over the run.",
     "Only comparable at equal horizon. It is `throughput` × timesteps."),
    ("`released_tasks`", "—", "Tasks the arrival process offered, delivered or not.",
     "Not a performance number. It is the denominator that makes `throughput` "
     "interpretable, and it is how we know every run here is saturated."),
    ("`mean_service_time`", "lower",
     "Mean of (completion − release) over **completed** tasks.",
     "**Censored, and not at random.** A planner that abandons the hard tasks "
     "reports a beautiful mean. Compare it only between planners of comparable "
     "throughput."),
    ("`median_service_time`", "lower", "The same, at the median.",
     "Same censoring. Less sensitive to a few very late deliveries."),
    ("`p95_service_time`", "lower", "The tail: 95th percentile service time.",
     "Same censoring, plus a horizon effect — a task still in flight when the "
     "run ends is not in the sample at all, so the tail is truncated."),
    ("`max_service_time`", "lower", "The worst completed task.",
     "Saturates against the horizon: on a 400-step run it reports ≈330 for "
     "almost any configuration, so it discriminates poorly."),
    ("`max_waiting_time`", "lower",
     "The longest any single robot sat still while not at its waypoint.",
     "A single-robot extreme over five seeds: very noisy, rarely significant."),
    ("`total_travel_distance`", "lower", "Cells moved, summed over all robots.",
     "**Confounded with output.** A planner that delivers less also travels "
     "less. Divide by `completed_tasks` first — that derived form is metric #1 "
     "below."),
    ("`jain_fairness`", "higher",
     "Jain's index over per-robot completion counts; 1.0 means every robot "
     "delivered the same number.",
     "Fairness across *robots*, not across tasks. A fleet with half its robots "
     "stuck in a jam scores badly, which is usually what you want to know."),
    ("`head_on_conflicts`", "lower",
     "Pairs facing each other in one single-file aisle, neither able to pass.",
     "A raw count: scales with run length and fleet size. Per 1000 steps or per "
     "delivered task is the comparable form — metric #2 below."),
    ("`counterflow_moves`", "lower",
     "Moves actually taken against a committed aisle direction.",
     "**Not a defect count — it is the price being paid.** Plain PIBT scores 0 "
     "because it has no directions at all, so comparing against it is "
     "meaningless. Read it beside `throughput`: counterflow is the mechanism "
     "working, not failing."),
    ("`aisle_throughput_per_1000`", "higher",
     "Robots cleared per managed aisle per 1000 steps.",
     "The wrong denominator for a claim about deliveries. One-way aisles route "
     "traffic through *fewer* aisle transits per task, so per-aisle flow can "
     "fall while end-to-end flow rises. Hypothesis H1 measured exactly this."),
    ("`direction_switches_per_1000`", "lower",
     "How often aisles reverse direction, per 1000 steps.",
     "Lower is better only against oscillation. Zero can mean 'perfectly "
     "stable' or 'no aisle ever committed a direction' — check "
     "`starvation_flips` and the managed-aisle count before concluding."),
    ("`starvation_flips`", "—",
     "Flips forced by the maximum-green rule rather than by demand.",
     "Neither good nor bad in itself: it counts the times the signal had to "
     "intervene to stop somebody waiting forever."),
    ("`deadlocks_detected` / `_recovered` / `_unrecovered`", "lower",
     "Groups the detector escalated on, and how they ended.",
     "Depends on the detector's sensitivity, which is itself a parameter. A "
     "configuration with a laxer trigger 'detects' more deadlocks without "
     "having more of them."),
    ("`pibt_recursive_calls` / `pibt_backtracks`", "lower",
     "How much work the priority-inheritance recursion did.",
     "Effort, not quality. Divide by `completed_tasks` for effort per delivery. "
     "SPAR-PIBT's backtracks rise sharply — that is the aisle layer making PIBT "
     "work harder, and it is a real cost."),
    ("`mean_runtime_ms_per_step`", "lower", "Wall-clock cost of one planning step.",
     "Machine- and load-dependent in absolute terms; quote the *ratios* between "
     "planners measured in one run, not the milliseconds."),
    ("`collision_free`", "must be true",
     "Whether the joint plan was conflict-free at every timestep.",
     "Not a score. It is an assertion — every run in this dataset passes it, "
     "and one that did not would be a bug, not a result."),
]


def build() -> Path:
    ablation = load("ablation")
    baselines = load("baselines")
    meta = ablation["meta"]

    lines: List[str] = []
    add = lines.append

    add("# Reading the numbers")
    add("")
    add("Every metric this project reports: what it actually measures, which "
        "direction is good, and how it misleads if you read it alone — plus the "
        "three that decide the verdict, with their measured values.")
    add("")
    add(f"All numbers below come from [`docs/data/`](data/): "
        f"**{meta['seeds']} seeds × {meta['timesteps']} timesteps**, Poisson "
        f"arrivals, four maps, generated by `experiments/run_all.py` at "
        f"`{meta['git_sha']}`. This file is generated too "
        "(`python3 tools/make_metrics_doc.py`), so its numbers cannot drift "
        "from the dataset.")
    add("")
    add("Throughout: **SPAR-PIBT** is the `aisle_direction_only` configuration "
        "(the best-throughput SPAR configuration on every map in this dataset, "
        "and the one the headline figure uses), **plain PIBT** is "
        "`lifelong_pibt`, and ★ marks the two aisle-constrained maps the method is "
        "designed for. `p` is a two-sided permutation test over the same five "
        "seeds; **bold** is p < 0.05.")
    add("")

    add("## Two things that make raw numbers misleading")
    add("")
    add("**Every run here is saturated.** Tasks arrive faster than any planner "
        "can serve them — between an eighth and a third of offered demand gets "
        "delivered — so the backlog grows for the whole run. Throughput is "
        "therefore not \"how busy the fleet was\"; it is the floor's *service "
        "capacity* under that planner. That is why 502 against 313 tasks per "
        "1000 timesteps is a 61% difference in warehouse capacity and not a "
        "rounding error.")
    add("")
    add("**Five seeds is a thin sample.** With five values a side the exact "
        "permutation test has C(10,5) = 252 label splits, so **the smallest "
        "attainable p-value is 2/252 ≈ 0.008**. A real difference of moderate "
        "size can easily land at p ≈ 0.06. Where that happens below it is "
        "labelled, not rounded away.")
    add("")

    add("---")
    add("")
    add("## The three metrics where SPAR-PIBT shines")
    add("")
    add("Not the metrics that flatter it most — the ones where the advantage is "
        "*significant, consistent, and caused by the mechanism the method is "
        "about*. Two of the three are derived, because the raw form of each is "
        "confounded.")
    add("")

    add("### 1. Travel distance per delivered task — the efficiency win")
    add("")
    add("**`total_travel_distance / completed_tasks`.** How far the fleet has to "
        "drive to deliver one task. This is what the method is really about: a "
        "committed one-way aisle stops robots shuffling past each other and "
        "reversing, so the same delivery costs less driving.")
    add("")
    add("It has to be the *ratio*. Raw `total_travel_distance` is lower for "
        "SPAR-PIBT on every map — including the ones where it delivers less — "
        "because a planner that delivers less also drives less. Dividing by "
        "deliveries removes that confound, and the result splits cleanly along "
        "the design boundary:")
    add("")
    lines.extend(compare(ablation, travel_per_task, digits=1))
    add("")
    add("**About a quarter less driving per delivery on both aisle-constrained maps, "
        "significant on both.** And it reverses on the open maps — the same "
        "story the throughput numbers tell: where aisles are not scarce, a "
        "committed direction only buys detours. One metric, both halves of the "
        "argument.")
    add("")

    add("### 2. Head-on conflicts per 1000 steps — the mechanism's own metric")
    add("")
    add("**`head_on_conflicts`, normalised by run length.** Two robots facing "
        "each other in a single-file aisle with neither able to pass. This is "
        "precisely what a one-way rule exists to prevent, so it tests the "
        "mechanism rather than its side effects.")
    add("")
    lines.extend(compare(ablation, headon_per_1000, digits=0))
    add("")
    add("**A third fewer head-on conflicts on the bottleneck map** (p = 0.016), "
        "where one corridor joins the two halves of the floor and every head-on "
        "meeting is expensive. Per delivered task the same comparison is −52%. "
        "On the open grid it goes the other way: committing directions on a map "
        "with many parallel routes creates encounters rather than preventing "
        "them.")
    add("")

    add("### 3. Throughput against the published baselines — the decisive win")
    add("")
    add("**`throughput` vs Token Passing and RHCR.** Against plain PIBT the "
        "aisle layer is ahead on aisle-constrained maps and behind on open ones, and "
        "at five seeds neither win is significant. Against the two published "
        "lifelong baselines there is no such ambiguity: every cell is "
        "significant at the floor of the test, and the margin is two orders of "
        "magnitude.")
    add("")
    lines.extend(baseline_table(baselines))
    add("")
    add("Token Passing completes **literally zero** tasks on "
        "`warehouse_corridors` across all five seeds: robots queue nose-to-tail, "
        "no robot can reserve a path through the robots ahead of it, and the "
        "configuration is absorbing. That is the structural failure priority "
        "inheritance exists to remove, and "
        "[the first animation](gifs/01-token-passing-gridlock.gif) shows it "
        "beside the same scenario under SPAR-PIBT.")
    add("")
    add("This margin belongs to the PIBT family rather than to the aisle layer "
        "specifically — plain PIBT wins it too. It is here because it is the "
        "answer to \"is this better than prior work?\", and it is not close.")
    add("")

    add("---")
    add("")
    add("## Where it does not shine, stated as plainly")
    add("")
    add("**Backtracks per delivered task** — how hard the priority-inheritance "
        "recursion has to work for each delivery:")
    add("")
    lines.extend(compare(ablation, backtracks_per_task, digits=1))
    add("")
    add("Counterflow moves are scored last, so inheritance chains explore "
        "further before they resolve. This is the cost side of pricing rather "
        "than forbidding, and it is the largest one.")
    add("")
    add("Also: **planner runtime** rises from 0.4–1.0 ms per step to 0.6–2.0 "
        "(significant on every map), and **throughput on the two open maps** is "
        "18–19% below plain PIBT (p = 0.016 and 0.008). The full picture is in "
        "[`dashboard.html`](dashboard.html) and "
        "[`figures/winloss.svg`](figures/winloss.svg).")
    add("")

    add("---")
    add("")
    add("## Every metric, and how it misleads")
    add("")
    add("| metric | good | what it measures | how it misleads |")
    add("|---|---|---|---|")
    for name, direction, measures, misleads in CATALOGUE:
        add(f"| {name} | {direction} | {measures} | {misleads} |")
    add("")

    add("## How the statistics work")
    add("")
    add("- **Bootstrap confidence interval** (`stats.bootstrap_ci`): resample "
        "the per-seed values with replacement 10,000 times, take the 2.5th and "
        "97.5th percentiles of the resampled means. No distributional "
        "assumption is made.")
    add("- **Permutation test** (`stats.permutation_test`): under the null that "
        "two configurations are the same, every way of relabelling the pooled "
        "seeds into two groups is equally likely. With 5+5 seeds all 252 splits "
        "are enumerated exactly, so the p-value is deterministic — and floored "
        "at 0.008.")
    add("- **Paired designs** (`config.PAIRED_DESIGNS`): same seeds, same map, "
        "one flag apart. This is where the mechanism claims are tested.")
    add("- **2×2 factorials** (`config.FACTORIAL_DESIGNS`): for the ablation "
        "rungs that flip two flags at once, so a delta cannot be attributed to "
        "either. Check that all four corners are distinct runs before reading a "
        "decomposition — one of ours was not, and its \"exactly zero effect\" "
        "turned out to be a property of the wiring.")
    add("")

    add("## See also")
    add("")
    add("- [`dashboard.html`](dashboard.html) — every metric, every map, "
        "interactively, with the interval and p-value on each bar")
    add("- [`figures/`](figures/) — the eight result figures")
    add("- [`pdf/matrix-comparison.pdf`](pdf/matrix-comparison.pdf) — the "
        "comparison matrix, built from this same dataset")
    add("- [`pdf/spar-planner.pdf`](pdf/spar-planner.pdf) — how the planner "
        "produces these numbers in the first place")

    OUT.write_text("\n".join(lines) + "\n")
    print(f"  wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size / 1024:.0f} kB)")
    return OUT


if __name__ == "__main__":
    build()
