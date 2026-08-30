#!/usr/bin/env python3
"""De-confound the three bundled steps of the spec section 34 ablation ladder.

`run_ablation.py` reproduces the ladder as originally specified, but three of
its six rungs flip two flags at once (see `config.FACTORIAL_DESIGNS`), so a
throughput delta on those rungs cannot be attributed to either flag alone.
This script runs the four corners of each 2x2 design and reports the main
effect of each flag plus their interaction, on every map the corresponding
hypothesis (H1, H3, H4/H6) was originally judged against.

Usage:
    python experiments/run_factorial_ablation.py [--quick]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.experiments import run_factorial_table  # noqa: E402

#: (design, map, robots, rate) - horizon and seeds are shared, set by --quick.
#: Matches the (robots, rate) used for the same maps in run_ablation.py so
#: results are comparable to the original ladder table.
RUNS = [
    ("direction_vs_turning_cost", "warehouse_bottleneck", 16, 0.8),
    ("direction_vs_turning_cost", "warehouse_corridors", 35, 1.0),
    ("direction_vs_turning_cost", "warehouse_medium", 40, 1.5),
    ("aisle_direction_vs_reservations", "warehouse_bottleneck", 16, 0.8),
    ("aisle_direction_vs_reservations", "warehouse_corridors", 35, 1.0),
    ("aisle_direction_vs_reservations", "warehouse_medium", 40, 1.5),
    ("congestion_vs_recovery", "warehouse_bottleneck", 16, 0.8),
    ("congestion_vs_recovery", "warehouse_corridors", 35, 1.0),
    ("congestion_vs_recovery", "warehouse_medium", 40, 1.5),
]


def _fmt(row: dict) -> str:
    lines = [
        f"\n### {row['design']}  ({row['label_a']}  x  {row['label_b']})  "
        f"on {row['map']}  [{row['n_robots']} robots, {row['seeds']} seeds, "
        f"collision_free={row['collision_free']}]"
    ]
    header = (
        f"{'field':<28}{'base':>10}{'a_alone':>10}{'b_alone':>10}"
        f"{'both_obs':>10}{'additive':>10}{'interact':>10}"
    )
    lines.append(header)
    for field, d in row["decomposition"].items():
        lines.append(
            f"{field:<28}{d['base']:>10.3f}{d['a_alone']:>10.3f}{d['b_alone']:>10.3f}"
            f"{d['both']:>10.3f}{d['additive_prediction']:>10.3f}{d['interaction']:>10.3f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="1 seed, short runs")
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--horizon", type=int, default=400)
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    seeds = 1 if args.quick else args.seeds
    horizon = 150 if args.quick else args.horizon
    args.out.mkdir(parents=True, exist_ok=True)

    everything = []
    for design, map_name, robots, rate in RUNS:
        row = run_factorial_table(
            ROOT / "maps" / f"{map_name}.map",
            design,
            n_robots=robots,
            timesteps=horizon,
            seeds=seeds,
            rate=rate,
        )
        row["map"] = map_name
        print(_fmt(row))
        assert row["collision_free"], f"collision detected in {design} on {map_name}"
        everything.append(row)

    path = args.out / "factorial_ablation.json"
    path.write_text(json.dumps(everything, indent=2))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
