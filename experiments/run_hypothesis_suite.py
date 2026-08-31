#!/usr/bin/env python3
"""Score every hypothesis on the metric it actually names.

The ablation ladder and the factorial designs both judge a mechanism by global
throughput. That is the wrong instrument for most of the hypotheses: H3 claims
*fewer head-on conflicts*, H1 claims *narrow aisles flow better*, H5 claims
*fewer very late deliveries*. A mechanism can deliver exactly what it promises
and still not move throughput, and the reverse is just as easy.

This script runs each hypothesis against the control that isolates it, on the
quantity it names, with a bootstrap CI and a permutation test, and prints a
verdict that reads the metric's own direction (`better`) rather than assuming
larger is better.

It also runs the single-factor comparisons in `config.PAIRED_DESIGNS`, which
isolate the mechanisms this pass repaired -- ranking aisle direction instead of
rejecting it, the bounded maximum green, and corroborated deadlock detection.

Usage:
    python experiments/run_hypothesis_suite.py [--seeds 10] [--quick]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.experiments import run_hypothesis_table, run_paired_table  # noqa: E402

#: (map, robots, rate) -- the same scenarios the ablation ladder uses, plus
#: `warehouse_narrow`, whose 5-cell single-file aisles are the case H1 and H3
#: are actually about and which the original tables never covered.
SCENARIOS = [
    ("warehouse_bottleneck", 16, 0.8),
    ("warehouse_corridors", 35, 1.0),
    ("warehouse_narrow", 35, 1.0),
    ("warehouse_medium", 40, 1.5),
]

VERDICT_MARK = {
    "supported": "SUPPORTED",
    "contradicted": "CONTRADICTED",
    "no measurable effect": "no effect",
}


def _p(value: float | None) -> str:
    if value is None:
        return "    -"
    return "<0.001" if value < 0.001 else f"{value:.3f}"


def _print_hypotheses(map_name: str, rows: list[dict]) -> None:
    print(f"\n### hypotheses on {map_name}  "
          f"[{rows[0]['n_robots']} robots, {rows[0]['seeds']} seeds]")
    print(f"{'':<4}{'metric':<28}{'treatment':>11}{'control':>11}"
          f"{'delta':>10}{'p':>8}  verdict")
    for row in rows:
        primary = row["fields"][row["metric"]]
        arrow = "lower is better" if row["better"] == "lower" else "higher is better"
        print(
            f"{row['hypothesis']:<4}{row['metric']:<28}"
            f"{primary['treatment_mean']:>11.3f}{primary['control_mean']:>11.3f}"
            f"{primary['delta']:>10.3f}{_p(primary['p_value']):>8}  "
            f"{VERDICT_MARK[row['verdict']]}  ({arrow})"
        )
        print(f"      {row['treatment']} vs {row['control']}")


def _print_pairs(map_name: str, rows: list[dict]) -> None:
    print(f"\n### isolated mechanisms on {map_name}")
    for row in rows:
        print(f"  {row['label']}  ({row['treatment']} vs {row['control']})")
        for field, d in row["fields"].items():
            print(
                f"    {field:<30}{d['treatment_mean']:>10.3f}"
                f"{d['control_mean']:>10.3f}{d['delta']:>10.3f}"
                f"  p={_p(d['p_value'])}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="1 seed, short runs")
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--horizon", type=int, default=400)
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    seeds = 1 if args.quick else args.seeds
    horizon = 150 if args.quick else args.horizon
    args.out.mkdir(parents=True, exist_ok=True)

    payload: dict = {"hypotheses": [], "paired": []}
    for map_name, robots, rate in SCENARIOS:
        map_path = ROOT / "maps" / f"{map_name}.map"
        rows = run_hypothesis_table(
            map_path, n_robots=robots, timesteps=horizon, seeds=seeds, rate=rate
        )
        for row in rows:
            row["map"] = map_name
            assert row["collision_free"], f"collision in {row['hypothesis']} on {map_name}"
        _print_hypotheses(map_name, rows)
        payload["hypotheses"].extend(rows)

        pairs = run_paired_table(
            map_path, n_robots=robots, timesteps=horizon,
            seeds=max(3, seeds // 2), rate=rate,
        )
        for row in pairs:
            row["map"] = map_name
        _print_pairs(map_name, pairs)
        payload["paired"].extend(pairs)

    path = args.out / "hypotheses.json"
    path.write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
