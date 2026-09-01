#!/usr/bin/env python3
"""Produce the one dataset every document, figure and dashboard reads.

The other four scripts in this directory each answer one question and write
into `results/` (git-ignored). This one runs all of them over a single set of
scenarios and writes into **`docs/data/`**, which *is* committed, because the
paper's tables, `tools/make_figures.py` and `docs/dashboard.html` must all be
generated from the same measured numbers rather than from four runs that
happened on four different afternoons.

Nothing here re-implements an experiment: every suite is a call into
`lda_pibt.experiments`. What this script adds is a provenance header on each
file -- seeds, horizon, scenarios, git SHA, wall-clock date -- so a number in
the documents can always be traced back to the run that produced them.

Usage::

    python3 experiments/run_all.py                  # 5 seeds, 400 steps
    python3 experiments/run_all.py --seeds 10
    python3 experiments/run_all.py --quick          # smoke test -> results/quick/
    python3 experiments/run_all.py --only baselines
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.experiments import (  # noqa: E402
    LIFELONG_VARIANTS,
    REPORT_FIELDS,
    run_ablation_table,
    run_comparison_table,
    run_factorial_table,
    run_hypothesis_table,
    run_paired_table,
)

OUT_DIR = ROOT / "docs" / "data"

#: `--quick` writes here instead. A two-seed smoke test that overwrote the
#: committed dataset would silently invalidate every figure, the comparison matrix
#: and the dashboard, all of which read `docs/data/` -- so it cannot.
QUICK_DIR = ROOT / "results" / "quick"

#: (map, robots, arrival rate). These are exactly the scenarios the four
#: existing scripts already use for these maps, so this dataset stays
#: comparable with every table the README already reports.
SCENARIOS = [
    ("warehouse_bottleneck", 16, 0.8),
    ("warehouse_corridors", 35, 1.0),
    ("warehouse_narrow", 30, 1.2),
    ("warehouse_medium", 40, 1.5),
]

#: Maps the external baselines are run on. Token Passing costs ~320 ms/step on
#: `warehouse_medium`, so the baseline suite dominates the wall clock; these
#: three are the ones the README's baseline table already covers.
BASELINE_MAPS = ("warehouse_bottleneck", "warehouse_corridors", "warehouse_medium")

BASELINE_VARIANTS = [
    "lifelong_pibt",
    "full_lda_pibt",
    "token_passing",
    "token_passing_recovery",
    "rhcr",
]
BASELINE_REFERENCE = "lifelong_pibt"

#: The 2x2 designs, on the maps whose structure makes their factors act.
FACTORIAL_RUNS = [
    ("direction_vs_turning_cost", "warehouse_corridors"),
    ("direction_vs_turning_cost", "warehouse_medium"),
    ("aisle_direction_vs_reservations", "warehouse_corridors"),
    ("aisle_direction_vs_reservations", "warehouse_medium"),
    ("congestion_vs_recovery", "warehouse_corridors"),
    ("congestion_vs_recovery", "warehouse_medium"),
]

SUITES = ("ablation", "baselines", "hypotheses", "paired", "factorial")


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # pragma: no cover - provenance is best-effort
        return "unknown"


def header(suite: str, seeds: int, horizon: int, scenarios: Sequence) -> Dict[str, Any]:
    """The block every consumer of this dataset is entitled to read."""
    return {
        "suite": suite,
        "seeds": seeds,
        "timesteps": horizon,
        "arrival": "poisson",
        "scenarios": [
            {"map": m, "robots": n, "rate": r} for m, n, r in scenarios
        ],
        "git_sha": git_sha(),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "experiments/run_all.py",
    }


def write(name: str, payload: Dict[str, Any], out_dir: Path = OUT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    size = path.stat().st_size
    shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
    print(f"  wrote {shown}  ({size / 1024:.0f} kB)")
    return path


def scenarios_for(maps: Sequence[str]) -> List:
    return [s for s in SCENARIOS if s[0] in maps]


# --------------------------------------------------------------------------
# suites
# --------------------------------------------------------------------------


def suite_ablation(seeds: int, horizon: int) -> Dict[str, Any]:
    """The full ablation ladder plus every isolation variant, per map."""
    payload: Dict[str, Any] = {"meta": header("ablation", seeds, horizon, SCENARIOS)}
    payload["maps"] = {}
    for map_name, robots, rate in SCENARIOS:
        print(f"  {map_name}: {len(LIFELONG_VARIANTS)} variants")
        rows = run_ablation_table(
            ROOT / "maps" / f"{map_name}.map",
            n_robots=robots, timesteps=horizon, seeds=seeds, rate=rate,
            include_raw=True,
        )
        assert all(r["collision_free"] for r in rows), f"collision on {map_name}"
        payload["maps"][map_name] = {"robots": robots, "rate": rate, "rows": rows}
    return payload


def suite_baselines(seeds: int, horizon: int) -> Dict[str, Any]:
    """SPAR against the two literature baselines, with CIs and p-values."""
    scenarios = scenarios_for(BASELINE_MAPS)
    payload: Dict[str, Any] = {
        "meta": header("baselines", seeds, horizon, scenarios),
        "reference": BASELINE_REFERENCE,
        "maps": {},
    }
    for map_name, robots, rate in scenarios:
        print(f"  {map_name}: {len(BASELINE_VARIANTS)} planners (slow)")
        rows = run_comparison_table(
            ROOT / "maps" / f"{map_name}.map",
            BASELINE_VARIANTS, BASELINE_REFERENCE,
            n_robots=robots, timesteps=horizon, seeds=seeds, rate=rate,
            # every field, not just the cross-planner core: the figures need
            # released_tasks (offered load) and completed_tasks (censoring).
            fields=REPORT_FIELDS, include_raw=True,
        )
        assert all(r["collision_free"] for r in rows), f"collision on {map_name}"
        payload["maps"][map_name] = {"robots": robots, "rate": rate, "rows": rows}
    return payload


def suite_hypotheses(seeds: int, horizon: int) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "meta": header("hypotheses", seeds, horizon, SCENARIOS),
        "rows": [],
    }
    for map_name, robots, rate in SCENARIOS:
        print(f"  {map_name}: H1-H6")
        rows = run_hypothesis_table(
            ROOT / "maps" / f"{map_name}.map",
            n_robots=robots, timesteps=horizon, seeds=seeds, rate=rate,
        )
        for row in rows:
            row["map"] = map_name
            assert row["collision_free"], f"collision in {row['hypothesis']}"
        payload["rows"].extend(rows)
    return payload


def suite_paired(seeds: int, horizon: int) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "meta": header("paired", seeds, horizon, SCENARIOS),
        "rows": [],
    }
    for map_name, robots, rate in SCENARIOS:
        print(f"  {map_name}: single-factor pairs")
        rows = run_paired_table(
            ROOT / "maps" / f"{map_name}.map",
            n_robots=robots, timesteps=horizon, seeds=seeds, rate=rate,
        )
        for row in rows:
            row["map"] = map_name
        payload["rows"].extend(rows)
    return payload


def suite_factorial(seeds: int, horizon: int) -> Dict[str, Any]:
    maps = sorted({m for _, m in FACTORIAL_RUNS})
    payload: Dict[str, Any] = {
        "meta": header("factorial", seeds, horizon, scenarios_for(maps)),
        "rows": [],
    }
    by_map = {m: (n, r) for m, n, r in SCENARIOS}
    for design, map_name in FACTORIAL_RUNS:
        robots, rate = by_map[map_name]
        print(f"  {map_name}: {design}")
        row = run_factorial_table(
            ROOT / "maps" / f"{map_name}.map", design,
            n_robots=robots, timesteps=horizon, seeds=seeds, rate=rate,
        )
        row["map"] = map_name
        assert row["collision_free"], f"collision in {design} on {map_name}"
        payload["rows"].append(row)
    return payload


RUNNERS = {
    "ablation": suite_ablation,
    "baselines": suite_baselines,
    "hypotheses": suite_hypotheses,
    "paired": suite_paired,
    "factorial": suite_factorial,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--horizon", type=int, default=400)
    parser.add_argument("--quick", action="store_true",
                        help="2 seeds, 120 steps -- a smoke test, not a result")
    parser.add_argument("--only", nargs="*", choices=SUITES, default=None,
                        help="run a subset of the suites")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"where to write (default {OUT_DIR.relative_to(ROOT)}, "
                             f"or {QUICK_DIR.relative_to(ROOT)} with --quick)")
    args = parser.parse_args(argv)

    seeds = 2 if args.quick else args.seeds
    horizon = 120 if args.quick else args.horizon
    # resolved, because the guard below compares directories and a relative
    # --out docs/data must not slip past it
    out_dir = (args.out.resolve() if args.out
               else (QUICK_DIR if args.quick else OUT_DIR))
    wanted = args.only or list(SUITES)

    if args.quick and out_dir == OUT_DIR:
        raise SystemExit(
            "refusing to write a --quick smoke test into docs/data/: those "
            "files are the dataset the comparison matrix, the figures and "
            "the dashboard are generated from. Drop --out, or pass a "
            "different directory."
        )

    started = time.time()
    for name in wanted:
        print(f"\n### {name}  ({seeds} seeds, {horizon} steps)")
        suite_started = time.time()
        write(name, RUNNERS[name](seeds, horizon), out_dir)
        print(f"  {name} took {time.time() - suite_started:.0f}s")

    print(f"\ndone in {(time.time() - started) / 60:.1f} min -> {out_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
