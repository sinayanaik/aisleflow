#!/usr/bin/env python3
"""Render the committed comparison animations in ``docs/gifs/``.

Five scenarios, each one a claim this project makes shown as a picture rather
than a number. Every panel of every scenario is a real, seeded run of the same
simulator the results tables use (`experiments.build_run`), on the same map
with the same robot count, arrival rate and task stream, so the two sides of a
frame differ in exactly one thing: the planner.

Usage::

    python3 tools/make_gifs.py                    # all five
    python3 tools/make_gifs.py --only gridlock    # one, by name
    python3 tools/make_gifs.py --list
    python3 tools/make_gifs.py --quick            # short runs, for checking layout

Needs `pillow` (``pip install -e ".[viz]"``). Each file is held under a 5 MB
budget by `viz_compare.save_comparison`, which raises rather than committing
something enormous.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.viz_compare import run_panel, save_comparison  # noqa: E402

OUT_DIR = ROOT / "docs" / "gifs"


@dataclass(frozen=True)
class PanelSpec:
    variant: str
    title: str
    subtitle: str


@dataclass(frozen=True)
class Scenario:
    """One GIF: a map, a robot count, and the planners to put side by side."""

    key: str
    filename: str
    map_name: str
    robots: int
    rate: float
    timesteps: int
    stride: int
    title: str
    caption: str
    panels: Tuple[PanelSpec, ...]
    #: one paragraph for docs/gifs/README.md -- what a reader should watch for
    watch_for: str
    seed: int = 0


SCENARIOS: Tuple[Scenario, ...] = (
    Scenario(
        key="gridlock",
        filename="01-token-passing-gridlock.gif",
        map_name="warehouse_bottleneck",
        robots=16,
        rate=0.8,
        timesteps=400,
        stride=4,
        title="A queue that never resolves, and one that does",
        caption=(
            "warehouse_bottleneck, 16 robots, two halves joined by one corridor. "
            "Same map, same seed, same tasks."
        ),
        panels=(
            PanelSpec(
                "token_passing",
                "Token Passing",
                "Ma et al. 2017 - reserve a whole path, or wait",
            ),
            PanelSpec(
                "full_lda_pibt",
                "AisleFlow (LDA-PIBT)",
                "a blocked robot pushes the robot ahead of it",
            ),
        ),
        watch_for=(
            "The single clearest picture in the project. Token Passing plans each "
            "robot a collision-free path through a reservation table and holds "
            "position when it cannot find one. In a one-corridor map every robot "
            "eventually queues nose-to-tail in that corridor, no robot can reserve "
            "a path through the robots ahead of it, and nothing ever moves again: "
            "watch the left panel go entirely red-ringed and stay there while its "
            "delivered count stops. The right panel is the same instant of the same "
            "scenario under priority inheritance, where a blocked robot pushes the "
            "robot ahead of it out of the way and the queue drains. This is the "
            "failure mode PIBT was invented to remove, and it is structural: no "
            "amount of tuning removes it from Token Passing."
        ),
    ),
    Scenario(
        key="hard-vs-soft",
        filename="02-hard-vs-soft-direction.gif",
        map_name="warehouse_corridors",
        robots=35,
        rate=1.0,
        timesteps=400,
        stride=4,
        title="One-way as a constraint, one-way as a price",
        caption=(
            "warehouse_corridors, 35 robots, five parallel single-file corridors. "
            "The same aisle directions, enforced two different ways."
        ),
        panels=(
            PanelSpec(
                "aisle_direction_hard",
                "Direction as a hard rule",
                "counterflow moves deleted from the candidate set",
            ),
            PanelSpec(
                "aisle_direction_only",
                "Direction as a price (AisleFlow)",
                "counterflow costs 8; a step of progress is worth 10",
            ),
        ),
        watch_for=(
            "The core argument of the method, as a picture. Both panels commit the "
            "same aisle directions; they differ only in what a committed direction "
            "does to a move that opposes it. On the left it removes the move from "
            "the candidate set, which is what the specification literally says and "
            "what most one-way schemes do - and priority inheritance needs a robot "
            "to always have somewhere to be pushed, so deleting that option strands "
            "whole corridors. On the right the same move survives and simply costs "
            "8, less than the 10 a step of progress is worth, so a robot drives the "
            "wrong way when that is the only way through and pays for it. Measured "
            "over five seeds this is worth between 1.9x and 3.1x throughput - the "
            "largest single effect in the repository."
        ),
    ),
    Scenario(
        key="max-green",
        filename="03-maximum-green-starvation.gif",
        map_name="warehouse_narrow",
        robots=30,
        rate=1.2,
        timesteps=400,
        stride=4,
        title="An aisle that never flips, and one that must",
        caption=(
            "warehouse_narrow, 30 robots, four 5-cell single-file aisles per bank. "
            "Aisle tint is the committed direction; the arrow is the way it flows."
        ),
        panels=(
            PanelSpec(
                "aisle_direction_no_max_green",
                "Hysteresis only",
                "held until the imbalance breaks the dead band",
            ),
            PanelSpec(
                "aisle_direction_only",
                "Hysteresis + maximum green (AisleFlow)",
                "past T_max, opposing demand forces a drain and flip",
            ),
        ),
        watch_for=(
            "Hysteresis is only half a traffic signal. A dead band and a minimum "
            "lock bound how soon an aisle may change direction and say nothing "
            "about how long it may keep one - and a warehouse with pickups down one "
            "side and deliveries down the other produces near-balanced demand by "
            "construction, so the imbalance never breaks the band. On the left the "
            "aisle tints settle and stop changing: robots wanting the other "
            "direction wait, and keep waiting. On the right the same aisles reach "
            "their maximum green, turn purple as they DRAIN, and commit the "
            "opposite direction once empty. Drain-before-reverse is visible in "
            "every flip: the aisle empties before it turns, so no two robots ever "
            "meet head-on inside it. This is what makes the aisle layer "
            "starvation-free rather than merely non-flapping."
        ),
    ),
    Scenario(
        key="recovery",
        filename="04-recovery-corroboration.gif",
        map_name="warehouse_corridors",
        robots=35,
        rate=1.0,
        timesteps=400,
        stride=4,
        title="Rescuing a deadlock, and rescuing a queue",
        caption=(
            "warehouse_corridors, 35 robots. Both panels run the same seven-level "
            "recovery; they differ in what is allowed to trigger it."
        ),
        panels=(
            PanelSpec(
                "recovery_uncorroborated",
                "One stall signal is enough",
                "no progress for t_blocked steps fires recovery",
            ),
            PanelSpec(
                "recovery_only",
                "Corroborated stalls only (AisleFlow)",
                "also needs a wait-for cycle or repeated configuration",
            ),
        ),
        watch_for=(
            "In dense lifelong traffic, 'this robot has not progressed for a while' "
            "does not describe a deadlock - it describes an ordinary queue. On the "
            "left that signal alone escalates recovery, whose upper levels reverse "
            "robots, send them to escape vertices and hijack their waypoints; "
            "healthy queues get taken apart and rebuilt continuously and the "
            "delivered count barely moves. On the right the same detector must also "
            "see a wait-for cycle or a repeated configuration before it fires. "
            "Measured on this map: 0.134 tasks per step against 0.022, a six-fold "
            "difference produced entirely by refusing to act on the weakest of the "
            "three stall signals."
        ),
    ),
    Scenario(
        key="open-map",
        filename="05-open-map-honesty.gif",
        map_name="warehouse_medium",
        robots=40,
        rate=1.5,
        timesteps=400,
        stride=4,
        title="Where the aisle layer costs more than it earns",
        caption=(
            "warehouse_medium, 40 robots, an open grid warehouse. "
            "This is the case AisleFlow loses, shown as plainly as the ones it wins."
        ),
        panels=(
            PanelSpec(
                "full_lda_pibt",
                "AisleFlow (LDA-PIBT)",
                "aisles commit directions; detours are the price",
            ),
            PanelSpec(
                "lifelong_pibt",
                "Plain lifelong PIBT",
                "no direction, no reservations, no aisle layer",
            ),
        ),
        watch_for=(
            "Every mechanism in this project buys something and costs something. On "
            "a map with many parallel routes and no scarce single-file aisle, the "
            "thing aisle management buys - orderly flow through a contended "
            "corridor - is not scarce, while the thing it costs is: a robot whose "
            "shortest route runs against a committed direction either detours or "
            "pays the counterflow penalty, and there was no congestion to justify "
            "either. The right panel simply delivers more, throughout. Over five "
            "seeds it is 0.50 against 0.31 tasks per step. The honest summary of "
            "this project is that its aisle layer wins on aisle-shaped maps and "
            "loses on open ones, and this GIF is the losing half."
        ),
    ),
)

BY_KEY: Dict[str, Scenario] = {s.key: s for s in SCENARIOS}


def render(scenario: Scenario, quick: bool = False) -> Path:
    timesteps = 120 if quick else scenario.timesteps
    stride = 3 if quick else scenario.stride
    map_path = ROOT / "maps" / f"{scenario.map_name}.map"

    panels = []
    for spec in scenario.panels:
        started = time.time()
        panels.append(
            run_panel(
                map_path,
                spec.variant,
                spec.title,
                spec.subtitle,
                n_robots=scenario.robots,
                timesteps=timesteps,
                seed=scenario.seed,
                rate=scenario.rate,
            )
        )
        print(f"    {spec.variant:<28} {time.time() - started:5.1f}s  "
              f"{panels[-1].completed:>4} delivered")

    path = save_comparison(
        panels,
        OUT_DIR / scenario.filename,
        title=scenario.title,
        caption=scenario.caption,
        stride=stride,
    )
    print(f"    -> {path.relative_to(ROOT)}  ({path.stat().st_size / 1e6:.2f} MB)")
    return path


def write_readme(rendered: Sequence[Scenario]) -> Path:
    """Regenerate docs/gifs/README.md from the scenario definitions.

    The prose lives next to the run that produces it, so a GIF and its
    explanation cannot drift apart.
    """
    lines: List[str] = [
        "# Comparison animations",
        "",
        "Five animations, each a claim this project makes shown as a picture.",
        "Every panel is a real run of the simulator in this repository, and the two",
        "panels of a frame share a map, a seed, a robot count, an arrival rate and a",
        "task stream -- they differ in the planner and nothing else.",
        "",
        "Regenerate them all with:",
        "",
        "```bash",
        "python3 tools/make_gifs.py            # needs pillow: pip install -e \".[viz]\"",
        "```",
        "",
        "In every frame: a filled dot is a robot, coloured by what it is doing; a red",
        "ring means that robot has not moved for 15 timesteps; a tinted aisle has",
        "committed a direction, and its arrow points the way it flows. The number",
        "under each panel is tasks delivered so far, and the bar under that compares",
        "it with the leading panel.",
        "",
    ]
    for scenario in rendered:
        panel_desc = " vs ".join(f"`{p.variant}`" for p in scenario.panels)
        lines += [
            f"## {scenario.title}",
            "",
            f"![{scenario.title}]({scenario.filename})",
            "",
            f"**{scenario.map_name}**, {scenario.robots} robots, arrival rate "
            f"{scenario.rate}, {scenario.timesteps} timesteps, seed {scenario.seed}. "
            f"{panel_desc}.",
            "",
            scenario.watch_for,
            "",
            "```bash",
            f"python3 tools/make_gifs.py --only {scenario.key}",
            "```",
            "",
        ]
    path = OUT_DIR / "README.md"
    path.write_text("\n".join(lines))
    print(f"  wrote {path.relative_to(ROOT)}")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="*", choices=sorted(BY_KEY), default=None)
    parser.add_argument("--quick", action="store_true",
                        help="120-step runs, for checking layout")
    parser.add_argument("--list", action="store_true", help="list the scenarios")
    parser.add_argument("--no-readme", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for scenario in SCENARIOS:
            print(f"{scenario.key:<14} {scenario.filename:<34} {scenario.title}")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wanted = [BY_KEY[k] for k in args.only] if args.only else list(SCENARIOS)
    for scenario in wanted:
        print(f"\n### {scenario.key}  ({scenario.map_name}, "
              f"{scenario.robots} robots)")
        render(scenario, quick=args.quick)

    if not args.no_readme and not args.quick:
        write_readme(SCENARIOS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
