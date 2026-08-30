#!/usr/bin/env python3
"""Traffic-density sweep (spec section 35.1)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.experiments import run_density_sweep  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", default="warehouse_medium")
    parser.add_argument("--counts", type=int, nargs="*", default=[10, 20, 40, 60, 80])
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--timesteps", type=int, default=400)
    parser.add_argument(
        "--variants",
        nargs="*",
        default=["lifelong_pibt", "hysteresis_pibt", "full_lda_pibt"],
    )
    parser.add_argument("--out", type=Path, default=ROOT / "results" / "density.json")
    args = parser.parse_args()

    rows = run_density_sweep(
        ROOT / "maps" / f"{args.map}.map",
        robot_counts=args.counts,
        variants=args.variants,
        timesteps=args.timesteps,
        seeds=args.seeds,
    )
    header = f"{'robots':>7s} {'variant':22s} {'thr':>7s} {'svc':>8s} {'p95':>8s}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['n_robots']:7d} {row['variant']:22s} {row['throughput']:7.3f} "
            f"{row['mean_service_time']:8.1f} {row['p95_service_time']:8.1f}"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=2))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
