"""Command line interface: ``python -m lda_pibt ...``"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .config import ABLATIONS, LEGACY_NAMES, REMOVED_NAMES, Params, ablation
from .simulator import build_simulator
from .task import TaskGenerator
from .warehouse import Warehouse


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("map", type=Path, help="path to a .map file")
    parser.add_argument("-n", "--robots", type=int, default=10)
    parser.add_argument("-t", "--timesteps", type=int, default=500)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--arrival",
        choices=("poisson", "periodic", "bursty", "batch"),
        default="poisson",
    )
    parser.add_argument("--rate", type=float, default=1.0)
    parser.add_argument("--total-tasks", type=int, default=None)
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="override any Params field, e.g. --set progress_reward=8 "
             "(pre-simplification names still resolve)",
    )


def _params_from_args(args: argparse.Namespace, variant: str) -> Params:
    params = ablation(variant, Params(seed=args.seed, max_timesteps=args.timesteps))
    overrides = {}
    for item in args.set:
        key, _, raw = item.partition("=")
        # Resolve pre-simplification names here rather than rejecting them:
        # `Params` accepts them, so the CLI refusing them first would make the
        # alias table useless exactly where people meet it.
        key = LEGACY_NAMES.get(key, key)
        if key in REMOVED_NAMES:
            raise SystemExit(
                f"parameter {key!r} no longer exists: the term it weighted was "
                f"removed after it measured as having no effect or a negative "
                f"one. See docs/04-parameters.md."
            )
        current = getattr(params, key, None)
        if current is None and key not in Params.__dataclass_fields__:
            raise SystemExit(f"unknown parameter: {key}")
        if isinstance(current, bool):
            value = raw.lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            value = int(raw)
        elif isinstance(current, float):
            value = float(raw)
        else:
            value = raw
        overrides[key] = value
    return params.merged(**overrides) if overrides else params


def _build(args: argparse.Namespace, variant: str, record: bool = False):
    params = _params_from_args(args, variant)
    warehouse = Warehouse.from_file(args.map, params)
    if not warehouse.pickup_vertices or not warehouse.delivery_vertices:
        raise SystemExit(
            f"{args.map} has no pickup ('p') or delivery ('d') cells"
        )
    generator = TaskGenerator(
        warehouse.pickup_vertices,
        warehouse.delivery_vertices,
        mode=args.arrival,
        rate=args.rate,
        total=args.total_tasks,
        seed=args.seed,
    )
    return build_simulator(
        warehouse,
        args.robots,
        params,
        task_generator=generator,
        record_history=record,
    )


def cmd_run(args: argparse.Namespace) -> int:
    sim = _build(args, args.variant)
    print(f"map      : {args.map}")
    for key, value in sim.warehouse.summary().items():
        print(f"  {key:28s}: {value}")
    print(f"variant  : {args.variant}\nrobots   : {args.robots}\n")
    report = sim.run(max_timesteps=args.timesteps, progress=args.progress)
    print(report)
    if args.json:
        report.save(args.json)
        print(f"\nwrote {args.json}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    params = _params_from_args(args, args.variant)
    warehouse = Warehouse.from_file(args.map, params)
    for key, value in warehouse.summary().items():
        print(f"{key:28s}: {value}")
    print("\naisle id  length  capacity  axis  managed  endpoints")
    for aisle in warehouse.aisles.values():
        print(
            f"{aisle.id:8d}  {aisle.length:6d}  {aisle.capacity:8d}  "
            f"{aisle.axis or '-':4s}  "
            f"{aisle.start_vertex} -> {aisle.end_vertex}"
        )
    bent = [a.id for a in warehouse.aisles.values() if a.length > 1 and not a.axis]
    if bent:
        print(f"\nwarning: {len(bent)} aisle(s) are not straight runs: {bent}")
    return 0


def cmd_animate(args: argparse.Namespace) -> int:
    from .viz import render_ascii_frames, save_animation

    sim = _build(args, args.variant, record=True)
    sim.run(max_timesteps=args.timesteps)
    if args.out:
        save_animation(sim, args.out, fps=args.fps)
        print(f"wrote {args.out}")
    else:
        for frame in render_ascii_frames(sim, stride=args.stride):
            print(frame)
            print()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import SimulationSession, serve

    overrides = _params_from_args(args, args.variant).to_dict()
    defaults = ablation(args.variant, Params()).to_dict()
    changed = {k: v for k, v in overrides.items() if defaults[k] != v}
    session = SimulationSession(
        map_path=args.map,
        n_robots=args.robots,
        variant=args.variant,
        rate=args.rate,
        arrival=args.arrival,
        seed=args.seed,
        overrides=changed,
    )
    serve(session, host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_ablate(args: argparse.Namespace) -> int:
    from .experiments import LIFELONG_VARIANTS, run_ablation_table

    rows = run_ablation_table(
        map_path=args.map,
        n_robots=args.robots,
        timesteps=args.timesteps,
        seeds=args.seeds,
        rate=args.rate,
        arrival=args.arrival,
        variants=args.variants or LIFELONG_VARIANTS,
    )
    print(json.dumps(rows, indent=2) if args.json_stdout else _format_table(rows))
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))
        print(f"\nwrote {args.json}")
    return 0


def _format_table(rows: List[dict]) -> str:
    header = (
        f"{'variant':22s} {'thr':>7s} {'svc':>8s} {'p95':>8s} "
        f"{'travel':>8s} {'sw/1k':>7s} {'dead':>6s} {'fair':>6s} {'ms':>6s}"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        lines.append(
            f"{row['variant']:22s} {row['throughput']:7.3f} "
            f"{row['mean_service_time']:8.1f} {row['p95_service_time']:8.1f} "
            f"{row['total_travel_distance']:8.0f} "
            f"{row['deadlocks_detected']:6.0f} {row['jain_fairness']:6.3f} "
            f"{row['mean_runtime_ms_per_step']:6.2f}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lda_pibt",
        description="SPAR-PIBT: soft-priced aisle reversal for lifelong warehouse MAPD",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one simulation")
    _add_common(run)
    run.add_argument("--variant", choices=sorted(ABLATIONS), default="full_lda_pibt")
    run.add_argument("--json", type=Path, default=None)
    run.add_argument("--progress", action="store_true")
    run.set_defaults(func=cmd_run)

    inspect = sub.add_parser("inspect", help="print warehouse structure")
    _add_common(inspect)
    inspect.add_argument("--variant", choices=sorted(ABLATIONS), default="full_lda_pibt")
    inspect.set_defaults(func=cmd_inspect)

    animate = sub.add_parser("animate", help="ASCII frames or an mp4/gif")
    _add_common(animate)
    animate.add_argument("--variant", choices=sorted(ABLATIONS), default="full_lda_pibt")
    animate.add_argument("--out", type=Path, default=None)
    animate.add_argument("--fps", type=int, default=8)
    animate.add_argument("--stride", type=int, default=10)
    animate.set_defaults(func=cmd_animate)

    gui = sub.add_parser("gui", help="interactive browser GUI")
    _add_common(gui)
    gui.add_argument("--variant", choices=sorted(ABLATIONS), default="full_lda_pibt")
    gui.add_argument("--host", default="127.0.0.1")
    gui.add_argument("--port", type=int, default=8000)
    gui.add_argument("--no-browser", action="store_true")
    gui.set_defaults(func=cmd_gui)

    ablate = sub.add_parser("ablate", help="run the spec section 34 ablation table")
    _add_common(ablate)
    ablate.add_argument("--seeds", type=int, default=3)
    ablate.add_argument("--variants", nargs="*", default=None)
    ablate.add_argument("--json", type=Path, default=None)
    ablate.add_argument("--json-stdout", action="store_true")
    ablate.set_defaults(func=cmd_ablate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
