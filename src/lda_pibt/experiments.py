"""Experiment drivers (spec sections 34, 35).

`run_ablation_table` reproduces the ablation of spec section 34; `run_density_sweep`
covers the traffic-density scenarios of spec section 35.1.  Both average over
several seeds because a single lifelong run is noisy.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .baselines import RHCRPlanner, TokenPassingPlanner
from .config import ABLATIONS, BASELINE_PARAMS_PRESET, FACTORIAL_DESIGNS, Params, ablation
from .simulator import PlannerFactory, build_simulator
from .stats import bootstrap_ci, permutation_test
from .task import TaskGenerator
from .warehouse import Warehouse

#: `pibt_baseline` is one-shot MAPF: it has no task stream, so lifelong
#: throughput is undefined for it.  It is exercised by the one-shot tests and
#: by `Simulator(..., static_goals=...)` instead.
LIFELONG_VARIANTS = [name for name in ABLATIONS if name != "pibt_baseline"]

#: External (non-PIBT) baselines, keyed by variant name -> (planner factory,
#: whether this variant runs with the same deadlock-recovery layer the PIBT
#: variants get). Two Token Passing rows are kept deliberately: one faithful
#: to Ma et al. 2017 (no recovery), one with recovery enabled, since which
#: framing is "fairer" is itself part of what a reader needs to judge.
BASELINE_PLANNERS: Dict[str, Tuple[PlannerFactory, bool]] = {
    "token_passing": (TokenPassingPlanner, False),
    "token_passing_recovery": (TokenPassingPlanner, True),
    "rhcr": (RHCRPlanner, False),
}

#: Fields averaged across seeds and reported by the experiment drivers.
REPORT_FIELDS = (
    "completed_tasks",
    "throughput",
    "mean_service_time",
    "median_service_time",
    "p95_service_time",
    "max_service_time",
    "total_travel_distance",
    "max_waiting_time",
    "direction_switches",
    "direction_switches_per_1000",
    "deadlocks_detected",
    "deadlocks_recovered",
    "deadlocks_unrecovered",
    "jain_fairness",
    "pibt_recursive_calls",
    "pibt_backtracks",
    "candidate_evaluations",
    "mean_runtime_ms_per_step",
)


def run_once(
    map_path: str | Path,
    variant: str,
    n_robots: int,
    timesteps: int,
    seed: int,
    rate: float = 1.0,
    arrival: str = "poisson",
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a single configuration and return its metrics as a dict.

    `variant` is either a name from `config.ABLATIONS` (a PIBT/LDA-PIBT flag
    bundle) or a name from `BASELINE_PLANNERS` (an external planner, e.g.
    `"token_passing"`, `"rhcr"`) -- both slot into the same call, so a
    comparison table can mix PIBT variants and baselines freely.
    """
    planner_factory: Optional[PlannerFactory] = None
    if variant in BASELINE_PLANNERS:
        factory, recovery = BASELINE_PLANNERS[variant]
        planner_factory = factory
        params = Params(
            seed=seed, max_timesteps=timesteps, recovery=recovery,
            **BASELINE_PARAMS_PRESET,
        ).merged(**(overrides or {}))
    else:
        params = ablation(
            variant,
            Params(seed=seed, max_timesteps=timesteps),
            **(overrides or {}),
        )
    warehouse = Warehouse.from_file(map_path, params)
    generator = TaskGenerator(
        warehouse.pickup_vertices,
        warehouse.delivery_vertices,
        mode=arrival,
        rate=rate,
        seed=seed,
    )
    sim = build_simulator(
        warehouse, n_robots, params, task_generator=generator,
        planner_factory=planner_factory,
    )
    report = sim.run(max_timesteps=timesteps)
    result = report.to_dict()
    result["variant"] = variant
    result["seed"] = seed
    result["n_robots"] = n_robots
    return result


def _average(runs: Sequence[Dict[str, Any]], **labels: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = dict(labels)
    row["seeds"] = len(runs)
    row["collision_free"] = all(bool(r["collision_free"]) for r in runs)
    for field in REPORT_FIELDS:
        values = [float(r[field]) for r in runs]
        row[field] = statistics.fmean(values)
        if len(values) > 1:
            row[f"{field}_sd"] = statistics.stdev(values)
    return row


def run_ablation_table(
    map_path: str | Path,
    n_robots: int = 20,
    timesteps: int = 500,
    seeds: int = 3,
    rate: float = 1.0,
    arrival: str = "poisson",
    variants: Optional[Sequence[str]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Spec section 34. One row per variant, averaged over `seeds` seeds."""
    rows: List[Dict[str, Any]] = []
    for variant in variants or LIFELONG_VARIANTS:
        runs = [
            run_once(
                map_path,
                variant,
                n_robots,
                timesteps,
                seed,
                rate=rate,
                arrival=arrival,
                overrides=overrides,
            )
            for seed in range(seeds)
        ]
        rows.append(_average(runs, variant=variant, n_robots=n_robots))
    return rows


def run_density_sweep(
    map_path: str | Path,
    robot_counts: Sequence[int],
    variants: Sequence[str] = ("lifelong_pibt", "full_lda_pibt"),
    timesteps: int = 500,
    seeds: int = 3,
    rate: float = 1.0,
) -> List[Dict[str, Any]]:
    """Spec section 35.1: sparse to dense traffic."""
    rows: List[Dict[str, Any]] = []
    for n_robots in robot_counts:
        for variant in variants:
            runs = [
                run_once(map_path, variant, n_robots, timesteps, seed, rate=rate)
                for seed in range(seeds)
            ]
            rows.append(_average(runs, variant=variant, n_robots=n_robots))
    return rows


#: Metrics reported by the factorial decomposition (a subset of REPORT_FIELDS
#: relevant to the specific hypotheses each design was built to separate).
FACTORIAL_FIELDS = ("throughput", "mean_service_time", "direction_switches_per_1000")


def run_factorial_table(
    map_path: str | Path,
    design_name: str,
    n_robots: int,
    timesteps: int,
    seeds: int = 5,
    rate: float = 1.0,
    arrival: str = "poisson",
) -> Dict[str, Any]:
    """Run one 2x2 factorial (spec-ladder de-confounding) and decompose it.

    Each of the ladder's three bundled steps (lifelong_pibt->directional_pibt,
    hysteresis_pibt->aisle_managed_pibt, aisle_managed_pibt->full_lda_pibt)
    flips two flags together, so its throughput delta cannot be attributed to
    either flag. This runs all four corners of the corresponding 2x2 design
    (`config.FACTORIAL_DESIGNS`) and reports, for each field in
    `FACTORIAL_FIELDS`: the main effect of factor A alone, of factor B alone,
    their sum, the effect actually observed with both flags on, and the
    interaction (both_effect - main_a - main_b). A large interaction means
    the two flags do not combine additively and the ladder's "both" rung
    cannot be read as "A's contribution plus B's contribution."
    """
    design = FACTORIAL_DESIGNS[design_name]
    corners = {
        "base": design.base,
        "a": design.factor_a,
        "b": design.factor_b,
        "both": design.both,
    }
    runs: Dict[str, Dict[str, Any]] = {}
    for corner, variant in corners.items():
        results = [
            run_once(map_path, variant, n_robots, timesteps, seed, rate=rate, arrival=arrival)
            for seed in range(seeds)
        ]
        runs[corner] = _average(results, variant=variant, corner=corner, n_robots=n_robots)

    decomposition: Dict[str, Any] = {}
    for field in FACTORIAL_FIELDS:
        base_v = runs["base"][field]
        a_v = runs["a"][field]
        b_v = runs["b"][field]
        both_v = runs["both"][field]
        main_a = a_v - base_v
        main_b = b_v - base_v
        both_effect = both_v - base_v
        decomposition[field] = {
            "base": base_v,
            "a_alone": a_v,
            "b_alone": b_v,
            "both": both_v,
            "main_effect_a": main_a,
            "main_effect_b": main_b,
            "additive_prediction": base_v + main_a + main_b,
            "observed_both_effect": both_effect,
            "interaction": both_effect - main_a - main_b,
        }

    return {
        "design": design_name,
        "label_a": design.label_a,
        "label_b": design.label_b,
        "seeds": seeds,
        "n_robots": n_robots,
        "collision_free": all(r["collision_free"] for r in runs.values()),
        "runs": runs,
        "decomposition": decomposition,
    }


#: The subset of REPORT_FIELDS that means the same thing for every planner
#: (PIBT variant or external baseline) -- planner-internal counters like
#: `pibt_recursive_calls` or a baseline's own `tp_replans` are reported
#: separately per-variant, not compared apples-to-apples across planners.
CORE_REPORT_FIELDS = (
    "throughput",
    "mean_service_time",
    "median_service_time",
    "p95_service_time",
    "total_travel_distance",
    "jain_fairness",
    "mean_runtime_ms_per_step",
)


def run_comparison_table(
    map_path: str | Path,
    variants: Sequence[str],
    reference_variant: str,
    n_robots: int,
    timesteps: int,
    seeds: int = 10,
    rate: float = 1.0,
    arrival: str = "poisson",
    overrides: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Compare PIBT variants and/or external baselines with honest statistics.

    Unlike `run_ablation_table` (bare means, kept for backward compatibility),
    this keeps every seed's raw per-field value and reports, for each field in
    `CORE_REPORT_FIELDS`: `mean`, a 95% bootstrap confidence interval
    (`stats.bootstrap_ci`), and a two-sided permutation-test p-value against
    `reference_variant`'s raw values for the same field
    (`stats.permutation_test`) -- so "beats/loses to baseline" claims rest on
    more than a mean over a handful of seeds. `reference_variant` itself gets
    `p_value = None` (nothing to compare it to).
    """
    raw: Dict[str, List[Dict[str, Any]]] = {}
    for variant in variants:
        raw[variant] = [
            run_once(map_path, variant, n_robots, timesteps, seed, rate=rate,
                     arrival=arrival, overrides=overrides)
            for seed in range(seeds)
        ]
    if reference_variant not in raw:
        raw[reference_variant] = [
            run_once(map_path, reference_variant, n_robots, timesteps, seed, rate=rate,
                     arrival=arrival, overrides=overrides)
            for seed in range(seeds)
        ]

    rows: List[Dict[str, Any]] = []
    for variant in variants:
        runs = raw[variant]
        row: Dict[str, Any] = {
            "variant": variant,
            "n_robots": n_robots,
            "seeds": len(runs),
            "collision_free": all(bool(r["collision_free"]) for r in runs),
            "fields": {},
        }
        for field in CORE_REPORT_FIELDS:
            values = [float(r[field]) for r in runs]
            mean, lo, hi = bootstrap_ci(values)
            if variant == reference_variant:
                p_value = None
            else:
                reference_values = [float(r[field]) for r in raw[reference_variant]]
                _, p_value = permutation_test(values, reference_values)
            row["fields"][field] = {
                "mean": mean, "ci_lo": lo, "ci_hi": hi, "p_vs_reference": p_value,
            }
        rows.append(row)
    return rows


__all__ = [
    "LIFELONG_VARIANTS",
    "BASELINE_PLANNERS",
    "REPORT_FIELDS",
    "CORE_REPORT_FIELDS",
    "FACTORIAL_FIELDS",
    "run_once",
    "run_ablation_table",
    "run_density_sweep",
    "run_factorial_table",
    "run_comparison_table",
]
