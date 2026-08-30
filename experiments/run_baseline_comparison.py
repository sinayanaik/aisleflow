#!/usr/bin/env python3
"""Compare LDA-PIBT variants against external baselines (Token Passing, RHCR).

Every prior table in this repo is an internal ablation -- this is the first
comparison against independently-implemented algorithms from the literature.
Uses `experiments.run_comparison_table`, which keeps every seed's raw value
and reports mean, a 95% bootstrap CI, and a permutation-test p-value against
`lifelong_pibt` (pure PIBT with every LDA-specific mechanism off), so
"beats/loses to baseline" claims rest on more than a mean over a handful of
seeds.

Usage:
    python experiments/run_baseline_comparison.py [--quick]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.experiments import CORE_REPORT_FIELDS, run_comparison_table  # noqa: E402

VARIANTS = [
    "lifelong_pibt",
    "full_lda_pibt",
    "token_passing",
    "token_passing_recovery",
    "rhcr",
]
REFERENCE = "lifelong_pibt"

#: (map, robots, task arrival rate) -- matches run_ablation.py's SCENARIOS
#: for the three headline maps, so results are directly comparable.
SCENARIOS = [
    ("warehouse_bottleneck", 16, 0.8),
    ("warehouse_corridors", 35, 1.0),
    ("warehouse_medium", 40, 1.5),
]


def _fmt(rows: list) -> str:
    lines = []
    header = f"{'variant':<24}" + "".join(f"{f:>16}" for f in CORE_REPORT_FIELDS)
    lines.append(header)
    for row in rows:
        cells = [f"{row['variant']:<24}"]
        for field in CORE_REPORT_FIELDS:
            f = row["fields"][field]
            p = f["p_vs_reference"]
            p_str = "ref" if p is None else f"p={p:.3f}"
            cells.append(f"{f['mean']:>8.2f}({p_str})")
        lines.append("".join(f"{c:>16}" if i else c for i, c in enumerate(cells)))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="2 seeds, short runs")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--horizon", type=int, default=400)
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    seeds = 2 if args.quick else args.seeds
    horizon = 100 if args.quick else args.horizon
    args.out.mkdir(parents=True, exist_ok=True)

    everything = {}
    for map_name, robots, rate in SCENARIOS:
        print(f"\n### {map_name}  ({robots} robots, arrival rate {rate}, "
              f"{horizon} steps, {seeds} seeds)")
        rows = run_comparison_table(
            ROOT / "maps" / f"{map_name}.map",
            VARIANTS,
            REFERENCE,
            n_robots=robots,
            timesteps=horizon,
            seeds=seeds,
            rate=rate,
        )
        print(_fmt(rows))
        assert all(r["collision_free"] for r in rows), "collision detected"
        everything[map_name] = rows

    path = args.out / "baseline_comparison.json"
    path.write_text(json.dumps(everything, indent=2))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
