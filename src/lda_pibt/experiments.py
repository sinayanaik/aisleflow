"""Experiment drivers (spec sections 34, 35).

`run_ablation_table` reproduces the ablation of spec section 34; `run_density_sweep`
covers the traffic-density scenarios of spec section 35.1.  Both average over
several seeds because a single lifelong run is noisy.
"""

from __future__ import annotations

import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .baselines import RHCRPlanner, TokenPassingPlanner
from .config import (
    SENSITIVITY,
    SENSITIVITY_BASE,
    SensitivityVariant,
    ABLATIONS,
    BASELINE_PARAMS_PRESET,
    FACTORIAL_DESIGNS,
    PAIRED_DESIGNS,
    Params,
    ablation,
)
from .deadlock import RECOVERY_LEVEL_COUNT
from .simulator import PlannerFactory, build_simulator
from .stats import (
    bootstrap_ci,
    paired_bootstrap_ci,
    paired_permutation_test,
    permutation_test,
)
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
    #: every task the arrival process released, completed or not -- the offered
    #: load these runs are measured against. Without it, `throughput` alone
    #: cannot say whether a run served most of its demand or a tenth of it.
    "released_tasks",
    "throughput",
    "mean_service_time",
    "median_service_time",
    "p95_service_time",
    "max_service_time",
    "total_travel_distance",
    "max_waiting_time",
    "head_on_conflicts",
    "deadlocks_detected",
    "deadlocks_recovered",
    "deadlocks_unrecovered",
    "jain_fairness",
    "pibt_recursive_calls",
    "pibt_backtracks",
    "candidate_evaluations",
    "mean_runtime_ms_per_step",
)


def build_run(
    map_path: str | Path,
    variant: str,
    n_robots: int,
    timesteps: int,
    seed: int,
    rate: float = 1.0,
    arrival: str = "poisson",
    overrides: Optional[Dict[str, Any]] = None,
    record_history: bool = False,
):
    """Build (but do not run) one configured `Simulator`.

    `variant` is either a name from `config.ABLATIONS` (a PIBT/SPAR-PIBT flag
    bundle) or a name from `BASELINE_PLANNERS` (an external planner, e.g.
    `"token_passing"`, `"rhcr"`) -- both slot into the same call, so a
    comparison table can mix PIBT variants and baselines freely.

    Split out of `run_once` so the visualiser can run the *same* configuration
    with `record_history=True` and animate it. Two panels of a comparison GIF
    are only comparable if they were set up by identical code, which is what
    sharing this function buys.
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
    return build_simulator(
        warehouse, n_robots, params, task_generator=generator,
        planner_factory=planner_factory,
        record_history=record_history,
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
    """Run a single configuration and return its metrics as a dict."""
    sim = build_run(
        map_path, variant, n_robots, timesteps, seed,
        rate=rate, arrival=arrival, overrides=overrides,
    )
    report = sim.run(max_timesteps=timesteps)
    result = report.to_dict()
    result["variant"] = variant
    result["seed"] = seed
    result["n_robots"] = n_robots
    return result


def _average(
    runs: Sequence[Dict[str, Any]], include_raw: bool = False, **labels: Any
) -> Dict[str, Any]:
    row: Dict[str, Any] = dict(labels)
    row["seeds"] = len(runs)
    row["collision_free"] = all(bool(r["collision_free"]) for r in runs)
    raw: Dict[str, List[float]] = {}
    for field in REPORT_FIELDS:
        values = [float(r[field]) for r in runs]
        row[field] = statistics.fmean(values)
        if len(values) > 1:
            row[f"{field}_sd"] = statistics.stdev(values)
        if include_raw:
            raw[field] = values
    if include_raw:
        # Per-seed values, so a figure can draw a real interval rather than
        # a mean with an error bar invented from a standard deviation.
        row["raw"] = raw
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
    include_raw: bool = False,
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
        rows.append(
            _average(runs, include_raw=include_raw, variant=variant, n_robots=n_robots)
        )
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
FACTORIAL_FIELDS = ("throughput", "mean_service_time", "p95_service_time")


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

    The ladder's remaining bundled step (lane_bonus_only->full_lda_pibt)
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
    fields: Sequence[str] = CORE_REPORT_FIELDS,
    include_raw: bool = False,
) -> List[Dict[str, Any]]:
    """Compare PIBT variants and/or external baselines with honest statistics.

    Unlike `run_ablation_table` (bare means, kept for backward compatibility),
    this keeps every seed's raw per-field value and reports, for each field in
    `fields` (`CORE_REPORT_FIELDS` by default): `mean`, a 95% bootstrap
    confidence interval (`stats.bootstrap_ci`), and a two-sided
    permutation-test p-value against `reference_variant`'s raw values for the
    same field (`stats.permutation_test`) -- so "beats/loses to baseline"
    claims rest on more than a mean over a handful of seeds.
    `reference_variant` itself gets `p_value = None` (nothing to compare it
    to).

    `include_raw` adds each variant's per-seed values under `"raw"`. The
    figures in `tools/make_figures.py` need them: a confidence interval drawn
    from five points should show the five points.
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
        for field in fields:
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
            if include_raw:
                row["fields"][field]["raw"] = values
        rows.append(row)
    return rows


@dataclass(frozen=True)
class Hypothesis:
    """One hypothesis, the pair that isolates it, and the metric it names.

    Each hypothesis is judged on the quantity it actually claims to move, not
    on global throughput: H3 says "fewer head-on conflicts", so it is scored on
    `head_on_conflicts`, and H1 says "narrow aisles flow better", so it is
    scored on per-aisle throughput.  `better` records which direction counts as
    support, so a verdict never depends on remembering the sign.
    """

    key: str
    claim: str
    treatment: str
    control: str
    metric: str
    better: str  #: "higher" or "lower"
    secondary: Tuple[str, ...] = ()
    #: Parameter overrides applied to the *control* run. Some mechanisms are
    #: parameters rather than flags (the beta decay, the hysteresis dead band),
    #: so their control is the same variant with the mechanism disabled.
    control_overrides: Optional[Tuple[Tuple[str, Any], ...]] = None
    control_label: str = ""


#: The sharpened hypotheses. Each names the mechanism it depends on, because
#: the earlier generic wording ("aisle direction improves throughput") was
#: compatible with configurations in which the mechanism could not act at all.
HYPOTHESES: Tuple[Hypothesis, ...] = (
    Hypothesis(
        key="H1",
        claim=(
            "Rewarding a robot for staying in the lane it is already in "
            "reduces head-on encounters in single-file corridors."
        ),
        treatment="lane_bonus_only",
        control="turning_cost_only",
        metric="head_on_conflicts",
        better="lower",
        secondary=("throughput", "mean_service_time"),
    ),
    Hypothesis(
        key="H2",
        claim=(
            "Pricing crowding into the movement score raises throughput, by "
            "steering robots out of jams before they form."
        ),
        treatment="full_lda_pibt",
        control="full_lda_pibt",
        control_overrides=(("congestion_scoring", False),),
        control_label="no crowding term in the movement score",
        metric="throughput",
        better="higher",
        secondary=("p95_service_time", "deadlocks_detected"),
    ),
    Hypothesis(
        key="H3",
        claim=(
            "Pricing crowding into the task match raises throughput, by not "
            "sending robots into the busiest part of the map for a task."
        ),
        treatment="full_lda_pibt",
        control="full_lda_pibt",
        control_overrides=(("congestion_assignment", False),),
        control_label="no crowding term in the assignment cost",
        metric="throughput",
        better="higher",
        secondary=("mean_service_time", "jain_fairness"),
    ),
    Hypothesis(
        key="H4",
        claim=(
            "Requiring a wait-for cycle or a repeated configuration before "
            "escalating recovery beats treating any lack of progress as a "
            "deadlock: ordinary queueing is not a jam."
        ),
        treatment="recovery_only",
        control="recovery_uncorroborated",
        metric="throughput",
        better="higher",
        secondary=("deadlocks_detected", "deadlocks_unrecovered"),
    ),
    Hypothesis(
        key="H5",
        claim=(
            "Stopping the recovery ladder before its strongest remedies beats "
            "running all of them: tearing up routes costs more than the jam."
        ),
        treatment="full_lda_pibt",
        control="recovery_full_ladder",
        metric="throughput",
        better="higher",
        secondary=("deadlocks_unrecovered", "p95_service_time"),
    ),
    Hypothesis(
        key="H6",
        claim=(
            "Charging for turns shortens the distance robots actually travel, "
            "because a straight path through a corridor beats a zigzag."
        ),
        treatment="turning_cost_only",
        control="lifelong_pibt",
        metric="total_travel_distance",
        better="lower",
        secondary=("throughput", "mean_service_time"),
    ),
)


def run_hypothesis_table(
    map_path: str | Path,
    n_robots: int,
    timesteps: int,
    seeds: int = 10,
    rate: float = 1.0,
    arrival: str = "poisson",
    hypotheses: Optional[Sequence[Hypothesis]] = None,
) -> List[Dict[str, Any]]:
    """Score every hypothesis on the metric it names, with a p-value.

    Returns one row per hypothesis: the treatment and control means for its own
    metric, a 95% bootstrap CI on each, a two-sided permutation-test p-value,
    and a verdict that reads `better` rather than assuming higher is good.
    """
    cache: Dict[Tuple[str, Any], List[Dict[str, Any]]] = {}

    def runs(
        variant: str, overrides: Optional[Tuple[Tuple[str, Any], ...]]
    ) -> List[Dict[str, Any]]:
        key = (variant, overrides)
        if key not in cache:
            cache[key] = [
                run_once(
                    map_path, variant, n_robots, timesteps, seed,
                    rate=rate, arrival=arrival,
                    overrides=dict(overrides) if overrides else None,
                )
                for seed in range(seeds)
            ]
        return cache[key]

    rows: List[Dict[str, Any]] = []
    for hypothesis in hypotheses or HYPOTHESES:
        treatment_runs = runs(hypothesis.treatment, None)
        control_runs = runs(hypothesis.control, hypothesis.control_overrides)

        fields: Dict[str, Any] = {}
        for field in (hypothesis.metric,) + tuple(hypothesis.secondary):
            treated = [float(r[field]) for r in treatment_runs]
            control = [float(r[field]) for r in control_runs]
            t_mean, t_lo, t_hi = bootstrap_ci(treated)
            c_mean, c_lo, c_hi = bootstrap_ci(control)
            _, p_value = permutation_test(treated, control)
            fields[field] = {
                "treatment_mean": t_mean, "treatment_ci": [t_lo, t_hi],
                "control_mean": c_mean, "control_ci": [c_lo, c_hi],
                "delta": t_mean - c_mean, "p_value": p_value,
            }

        primary = fields[hypothesis.metric]
        improved = (
            primary["delta"] > 0
            if hypothesis.better == "higher"
            else primary["delta"] < 0
        )
        significant = primary["p_value"] is not None and primary["p_value"] < 0.05
        if not significant:
            verdict = "no measurable effect"
        else:
            verdict = "supported" if improved else "contradicted"

        rows.append({
            "hypothesis": hypothesis.key,
            "claim": hypothesis.claim,
            "treatment": hypothesis.treatment,
            "control": hypothesis.control
            + (f" ({hypothesis.control_label})" if hypothesis.control_label else ""),
            "metric": hypothesis.metric,
            "better": hypothesis.better,
            "verdict": verdict,
            "seeds": seeds,
            "n_robots": n_robots,
            "collision_free": all(
                bool(r["collision_free"]) for r in treatment_runs + control_runs
            ),
            "fields": fields,
        })
    return rows


def run_paired_table(
    map_path: str | Path,
    n_robots: int,
    timesteps: int,
    seeds: int = 5,
    rate: float = 1.0,
    arrival: str = "poisson",
    fields: Sequence[str] = FACTORIAL_FIELDS,
) -> List[Dict[str, Any]]:
    """Run the single-factor comparisons in `config.PAIRED_DESIGNS`.

    These isolate the mechanisms repaired in this pass -- ranking direction
    versus rejecting it, bounded maximum green, corroborated recovery -- where a
    2x2 is impossible because the second factor is undefined when the first is
    off.
    """
    rows: List[Dict[str, Any]] = []
    for name, design in PAIRED_DESIGNS.items():
        treated = [
            run_once(map_path, design["treatment"], n_robots, timesteps, seed,
                     rate=rate, arrival=arrival)
            for seed in range(seeds)
        ]
        control = [
            run_once(map_path, design["control"], n_robots, timesteps, seed,
                     rate=rate, arrival=arrival)
            for seed in range(seeds)
        ]
        measured: Dict[str, Any] = {}
        for field in fields:
            t = [float(r[field]) for r in treated]
            c = [float(r[field]) for r in control]
            t_mean, t_lo, t_hi = bootstrap_ci(t)
            c_mean, c_lo, c_hi = bootstrap_ci(c)
            _, p_value = permutation_test(t, c)
            measured[field] = {
                "treatment_mean": t_mean, "treatment_ci": [t_lo, t_hi],
                "control_mean": c_mean, "control_ci": [c_lo, c_hi],
                "delta": t_mean - c_mean, "p_value": p_value,
            }
        rows.append({
            "design": name,
            "label": design["label"],
            "treatment": design["treatment"],
            "control": design["control"],
            "seeds": seeds,
            "n_robots": n_robots,
            "fields": measured,
        })
    return rows


# --------------------------------------------------------------------------
# sensitivity: what is every tunable in the planner actually worth?
# --------------------------------------------------------------------------

#: Reported for every sensitivity variant. Wider than `FACTORIAL_FIELDS`
#: because a knob can pay for itself in something other than throughput -- a
#: term that costs nothing in tasks per step but halves the deadlock count is
#: still earning its place -- and narrower than `REPORT_FIELDS` because the
#: study is already 55 variants wide.
SENSITIVITY_FIELDS: Tuple[str, ...] = (
    "throughput",
    "mean_service_time",
    "p95_service_time",
    "deadlocks_unrecovered",
    "head_on_conflicts",
    "mean_runtime_ms_per_step",
)

#: Per-level recovery counters, reported alongside the recovery family so the
#: ladder can be trimmed on evidence rather than taste.
RECOVERY_LEVEL_FIELDS: Tuple[str, ...] = tuple(
    f"recovery_l{i}_{kind}"
    for i in range(1, RECOVERY_LEVEL_COUNT + 1)
    for kind in ("fires", "resolved")
)


def _sensitivity_job(
    args: Tuple[str, int, int, int, float, str, Optional[Dict[str, Any]]]
) -> Dict[str, Any]:
    """Picklable worker: one run, for `ProcessPoolExecutor`."""
    map_path, n_robots, timesteps, seed, rate, arrival, overrides = args
    return run_once(
        map_path, SENSITIVITY_BASE, n_robots, timesteps, seed,
        rate=rate, arrival=arrival, overrides=overrides,
    )


def run_sensitivity_table(
    map_path: str | Path,
    n_robots: int,
    timesteps: int,
    seeds: int = 10,
    rate: float = 1.0,
    arrival: str = "poisson",
    variants: Sequence[SensitivityVariant] = SENSITIVITY,
    fields: Sequence[str] = SENSITIVITY_FIELDS,
    jobs: int = 1,
) -> List[Dict[str, Any]]:
    """Measure what each tunable in the planner is worth.

    Every variant is the full planner with exactly one knob neutralised, run on
    the same seeds as the control, so the arms are paired: seed `k` sees an
    identical task stream in both. That is what lets
    `paired_permutation_test` be used instead of `permutation_test` -- pooling
    the arms and relabeling them would throw the shared task stream away and
    need several times the seeds for the same power.

    `relative_delta` is the number the cut rule reads: the fraction of the
    control's throughput that removing this knob costs (negative) or gains
    (positive).
    """
    all_jobs: List[Tuple[str, int, int, int, float, str, Optional[Dict[str, Any]]]] = []
    for seed in range(seeds):
        all_jobs.append((str(map_path), n_robots, timesteps, seed, rate, arrival, None))
    for variant in variants:
        for seed in range(seeds):
            all_jobs.append(
                (str(map_path), n_robots, timesteps, seed, rate, arrival, dict(variant.overrides))
            )

    if jobs > 1:
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(_sensitivity_job, all_jobs, chunksize=1))
    else:
        results = [_sensitivity_job(job) for job in all_jobs]

    control = results[:seeds]
    rows: List[Dict[str, Any]] = []
    for i, variant in enumerate(variants):
        start = seeds * (i + 1)
        treated = results[start : start + seeds]

        measured: Dict[str, Any] = {}
        for field in fields:
            t_values = [float(r[field]) for r in treated]
            c_values = [float(r[field]) for r in control]
            delta, lo, hi = paired_bootstrap_ci(t_values, c_values)
            _, p_value = paired_permutation_test(t_values, c_values)
            control_mean = statistics.fmean(c_values)
            measured[field] = {
                "treatment_mean": statistics.fmean(t_values),
                "control_mean": control_mean,
                "delta": delta,
                "paired_ci": [lo, hi],
                "relative_delta": delta / control_mean if control_mean else 0.0,
                "p_value": p_value,
                # A CI straddling zero is the study's evidence of "this knob
                # does nothing measurable", so keep the raw values for a figure.
                "raw_treatment": t_values,
                "raw_control": c_values,
            }

        recovery: Dict[str, float] = {}
        if variant.family == "recovery" or variant.name == SENSITIVITY_BASE:
            for field in RECOVERY_LEVEL_FIELDS:
                recovery[field] = statistics.fmean(float(r[field]) for r in treated)

        rows.append({
            "variant": variant.name,
            "family": variant.family,
            "knob": variant.knob,
            "seeds": seeds,
            "n_robots": n_robots,
            "collision_free": all(
                bool(r["collision_free"]) for r in treated + control
            ),
            "fields": measured,
            "recovery_levels": recovery,
        })

    # The control's own recovery-ladder profile: which remedies the full
    # planner actually reaches, and how often each is followed by the jam
    # clearing. This is the evidence for trimming the ladder.
    control_recovery = {
        field: statistics.fmean(float(r[field]) for r in control)
        for field in RECOVERY_LEVEL_FIELDS
    }
    rows.append({
        "variant": SENSITIVITY_BASE,
        "family": "control",
        "knob": "(nothing removed)",
        "seeds": seeds,
        "n_robots": n_robots,
        "collision_free": all(bool(r["collision_free"]) for r in control),
        "fields": {
            field: {
                "treatment_mean": statistics.fmean(float(r[field]) for r in control),
                "control_mean": statistics.fmean(float(r[field]) for r in control),
                "delta": 0.0,
                "paired_ci": [0.0, 0.0],
                "relative_delta": 0.0,
                "p_value": 1.0,
                "raw_treatment": [float(r[field]) for r in control],
                "raw_control": [float(r[field]) for r in control],
            }
            for field in fields
        },
        "recovery_levels": control_recovery,
    })
    return rows


__all__ = [
    "LIFELONG_VARIANTS",
    "build_run",
    "BASELINE_PLANNERS",
    "REPORT_FIELDS",
    "CORE_REPORT_FIELDS",
    "FACTORIAL_FIELDS",
    "Hypothesis",
    "HYPOTHESES",
    "run_once",
    "run_ablation_table",
    "run_density_sweep",
    "run_factorial_table",
    "run_comparison_table",
    "run_hypothesis_table",
    "run_paired_table",
    "run_sensitivity_table",
    "SENSITIVITY_FIELDS",
    "RECOVERY_LEVEL_FIELDS",
]
