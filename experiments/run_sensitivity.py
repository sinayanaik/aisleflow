#!/usr/bin/env python3
"""Measure what every tunable in the planner is actually worth.

The planner exposes tunables across seven separate models -- the candidate
score, the congestion mixture, the priority function, the aisle directional
demand vote, the aisle signal's timing and capacity, the assignment cost and
the deadlock recovery ladder.  Most were chosen by hand and never questioned.
This suite runs the full planner with exactly one of them neutralised at a
time, on the same seeds as the control, so each knob gets a measured price
instead of an assertion.

Arms are paired by seed (`experiments.build_run` feeds one seed to both
`Params.seed` and the `TaskGenerator`, so seed `k` is the same task stream in
both arms), which is why this reports `paired_permutation_test` rather than
the unpaired test the older suites use.

    python3 experiments/run_sensitivity.py                # docs/data/sensitivity.json
    python3 experiments/run_sensitivity.py --quick        # results/quick/, 2 seeds
    python3 experiments/run_sensitivity.py --jobs 4       # parallel
    python3 experiments/run_sensitivity.py --family score
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.config import (  # noqa: E402
    SENSITIVITY,
    SENSITIVITY_BASE,
    SENSITIVITY_FAMILIES,
)
from lda_pibt.experiments import run_sensitivity_table  # noqa: E402
from lda_pibt.stats import paired_permutation_test  # noqa: E402

OUT_DIR = ROOT / "docs" / "data"
QUICK_DIR = ROOT / "results" / "quick"

#: The same four scenarios `run_all.py` uses, so this dataset is directly
#: comparable with the ablation and baseline tables.
SCENARIOS = [
    ("warehouse_bottleneck", 16, 0.8),
    ("warehouse_corridors", 35, 1.0),
    ("warehouse_narrow", 30, 1.2),
    ("warehouse_medium", 40, 1.5),
]

#: The metric the cut rule reads.
PRIMARY = "throughput"


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # pragma: no cover - provenance is best-effort
        return "unknown"


def pool_across_maps(
    per_map: Dict[str, List[Dict[str, Any]]], variant: str
) -> Dict[str, Any]:
    """Combine one variant's four per-map results into a single verdict.

    Pooling concatenates the per-seed values across maps rather than averaging
    the per-map means, so a knob that helps on one map and hurts on another
    shows up as a wide interval instead of a misleading zero.  `worst_map` is
    reported alongside because the cut rule needs to know whether *any* map
    was significantly hurt, not just the average.
    """
    treatment: List[float] = []
    control: List[float] = []
    per_map_relative: Dict[str, float] = {}
    worst_map, worst_relative = "", 0.0
    significant_losses: List[str] = []

    for map_name, rows in per_map.items():
        row = next(r for r in rows if r["variant"] == variant)
        measured = row["fields"][PRIMARY]
        treatment.extend(measured["raw_treatment"])
        control.extend(measured["raw_control"])
        relative = measured["relative_delta"]
        per_map_relative[map_name] = relative
        if relative < worst_relative:
            worst_relative, worst_map = relative, map_name
        if measured["p_value"] < 0.05 and measured["delta"] < 0:
            significant_losses.append(map_name)

    control_mean = statistics.fmean(control) if control else 0.0
    delta, p_value = paired_permutation_test(treatment, control)
    return {
        "pooled_delta": delta,
        "pooled_relative_delta": delta / control_mean if control_mean else 0.0,
        "pooled_p_value": p_value,
        "per_map_relative_delta": per_map_relative,
        "worst_map": worst_map,
        "worst_relative_delta": worst_relative,
        "significant_losses": significant_losses,
    }


#: The rule, fixed before the numbers were read.  `inert` is what gets cut;
#: `costly` is what has to be reported before anything is removed.
CUT_THRESHOLD = 0.05
KEEP_THRESHOLD = 0.15


def verdict(pooled: Dict[str, Any]) -> str:
    loss = -pooled["pooled_relative_delta"]  # positive == removing it hurt
    if loss < CUT_THRESHOLD and not pooled["significant_losses"]:
        return "inert"
    if loss < KEEP_THRESHOLD:
        return "cheap"
    return "load-bearing"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--horizon", type=int, default=400)
    parser.add_argument("--quick", action="store_true",
                        help="2 seeds, 120 steps, written to results/quick/")
    parser.add_argument("--jobs", type=int, default=1,
                        help="parallel worker processes")
    parser.add_argument("--family", nargs="+", choices=list(SENSITIVITY_FAMILIES),
                        help="only these families (default: all)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    seeds = 2 if args.quick else args.seeds
    horizon = 120 if args.quick else args.horizon
    out_dir = (args.out.resolve() if args.out
               else (QUICK_DIR if args.quick else OUT_DIR))

    if args.quick and out_dir == OUT_DIR:
        raise SystemExit(
            "refusing to write a --quick smoke test into docs/data/: that "
            "file is what the parameter tables and the sensitivity figure are "
            "generated from. Drop --out, or pass a different directory."
        )

    variants = [v for v in SENSITIVITY
                if not args.family or v.family in args.family]
    print(f"{len(variants)} variants x {len(SCENARIOS)} maps x {seeds} seeds "
          f"x {horizon} steps  (jobs={args.jobs})")

    started = time.time()
    per_map: Dict[str, List[Dict[str, Any]]] = {}
    for map_name, robots, rate in SCENARIOS:
        map_started = time.time()
        per_map[map_name] = run_sensitivity_table(
            ROOT / "maps" / f"{map_name}.map",
            n_robots=robots, timesteps=horizon, seeds=seeds, rate=rate,
            variants=variants, jobs=args.jobs,
        )
        print(f"  {map_name:24s} {time.time() - map_started:6.0f}s")

    summary = []
    for variant in variants:
        pooled = pool_across_maps(per_map, variant.name)
        summary.append({
            "variant": variant.name,
            "family": variant.family,
            "knob": variant.knob,
            "verdict": verdict(pooled),
            **pooled,
        })
    summary.sort(key=lambda row: row["pooled_relative_delta"])

    payload = {
        "meta": {
            "suite": "sensitivity",
            "seeds": seeds,
            "timesteps": horizon,
            "arrival": "poisson",
            "scenarios": [{"map": m, "robots": n, "rate": r}
                          for m, n, r in SCENARIOS],
            "git_sha": git_sha(),
            "generated_utc": datetime.now(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "experiments/run_sensitivity.py",
            "base_variant": SENSITIVITY_BASE,
            "primary_metric": PRIMARY,
            "cut_rule": {
                "inert_below": CUT_THRESHOLD,
                "load_bearing_above": KEEP_THRESHOLD,
                "note": "relative throughput lost by removing the knob, "
                        "pooled over all maps and seeds; 'inert' also "
                        "requires no map with a significant paired loss",
            },
        },
        "summary": summary,
        "maps": per_map,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "sensitivity.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nwrote {path}  ({path.stat().st_size / 1024:.0f} kB) "
          f"in {(time.time() - started) / 60:.1f} min")

    print(f"\n{'verdict':14s} {'family':14s} {'knob':44s} {'pooled':>8s}  worst map")
    for row in summary:
        print(f"{row['verdict']:14s} {row['family']:14s} {row['knob'][:44]:44s} "
              f"{row['pooled_relative_delta'] * 100:+7.1f}%  "
              f"{row['worst_map']} ({row['worst_relative_delta'] * 100:+.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
