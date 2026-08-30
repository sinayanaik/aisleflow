"""Experiment drivers (spec sections 34, 35).

`run_ablation_table` reproduces the ablation of spec section 34; `run_density_sweep`
covers the traffic-density scenarios of spec section 35.1.  Both average over
several seeds because a single lifelong run is noisy.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .config import ABLATIONS, Params, ablation
from .simulator import build_simulator
from .task import TaskGenerator
from .warehouse import Warehouse

#: `pibt_baseline` is one-shot MAPF: it has no task stream, so lifelong
#: throughput is undefined for it.  It is exercised by the one-shot tests and
#: by `Simulator(..., static_goals=...)` instead.
LIFELONG_VARIANTS = [name for name in ABLATIONS if name != "pibt_baseline"]

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
    """Run a single configuration and return its metrics as a dict."""
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
    sim = build_simulator(warehouse, n_robots, params, task_generator=generator)
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


__all__ = [
    "LIFELONG_VARIANTS",
    "REPORT_FIELDS",
    "run_once",
    "run_ablation_table",
    "run_density_sweep",
]
