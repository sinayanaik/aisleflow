#!/usr/bin/env python3
"""Reproduce the spec section 34 ablation table on every bundled map.

Usage:
    python experiments/run_ablation.py [--quick]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.cli import _format_table  # noqa: E402
from lda_pibt.experiments import run_ablation_table  # noqa: E402

#: (map, robots, task arrival rate, horizon)
SCENARIOS = [
    ("warehouse_small", 10, 0.6, 400),
    ("warehouse_medium", 40, 1.5, 400),
    ("warehouse_narrow", 30, 1.2, 400),
    ("warehouse_corridors", 35, 1.0, 400),
    ("warehouse_bottleneck", 16, 0.8, 400),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="1 seed, short runs")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    seeds = 1 if args.quick else args.seeds
    args.out.mkdir(parents=True, exist_ok=True)
    everything = {}

    for name, robots, rate, horizon in SCENARIOS:
        horizon = 150 if args.quick else horizon
        print(f"\n### {name}  ({robots} robots, arrival rate {rate}, "
              f"{horizon} steps, {seeds} seed(s))")
        rows = run_ablation_table(
            ROOT / "maps" / f"{name}.map",
            n_robots=robots,
            timesteps=horizon,
            seeds=seeds,
            rate=rate,
        )
        print(_format_table(rows))
        assert all(r["collision_free"] for r in rows), "collision detected"
        everything[name] = rows

    path = args.out / "ablation.json"
    path.write_text(json.dumps(everything, indent=2))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
