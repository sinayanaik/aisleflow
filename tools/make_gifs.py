#!/usr/bin/env python3
"""Render the committed animation in ``docs/gifs/``.

One animation: aisleflow itself, on the floor where it leads every published
planner. It is a real, seeded run of the same simulator the results table uses
(`experiments.build_run`), on the same map with the same robot count, arrival
rate and task stream, so the picture and the number on the results page are the
same run rather than two things that resemble each other.

Usage::

    python3 tools/make_gifs.py                    # the animation
    python3 tools/make_gifs.py --only bottleneck  # by name (there is one)
    python3 tools/make_gifs.py --list
    python3 tools/make_gifs.py --quick            # short run, for checking layout

Needs `pillow` (``pip install -e ".[viz]"``). The file is held under a 5 MB
budget by `viz_compare.save_comparison`, which raises rather than committing
something enormous.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.viz_compare import Beat, run_panel, save_comparison  # noqa: E402

OUT_DIR = ROOT / "docs" / "gifs"
DATA_DIR = ROOT / "docs" / "data"


# --------------------------------------------------------------------------
# measured numbers
#
# The narration quotes throughput -- "147 tasks per 1000 steps" -- and it used
# to quote it as a literal in the source. Every one of those literals was wrong
# by the time anyone read it: the dataset was regenerated, the sentences were
# not, and a GIF ended up telling a viewer a number the run playing underneath
# it did not deliver. The number is now looked up from `docs/data/` at render
# time and substituted into `{left}` in the text, so it cannot drift.
# --------------------------------------------------------------------------

_MEASURED: Dict[str, Dict] = {}


def _dataset(name: str) -> Optional[Dict]:
    if name not in _MEASURED:
        path = DATA_DIR / f"{name}.json"
        _MEASURED[name] = json.loads(path.read_text()) if path.exists() else None
    return _MEASURED[name]


def measured_per_1000(map_name: str, variant: str) -> Optional[Dict[str, float]]:
    """One planner's throughput on one map, per 1000 timesteps, from `docs/data/`.

    Returns the mean and, where the suite recorded per-seed values, the
    interval across them. Checks the baseline suite first (it has the
    published planners) and then the ablation suite (it has every
    configuration of this one).
    """
    for suite, mean_of in (
        ("baselines", lambda row: row["fields"]["throughput"]["mean"]),
        ("ablation", lambda row: row["throughput"]),
    ):
        payload = _dataset(suite)
        if not payload or map_name not in payload.get("maps", {}):
            continue
        for row in payload["maps"][map_name]["rows"]:
            if row["variant"] != variant:
                continue
            raw = (row.get("raw") or row.get("fields", {}).get("throughput", {})).get(
                "throughput", row.get("fields", {}).get("throughput", {}).get("raw")
            )
            seeds = [1000.0 * v for v in raw] if raw else []
            return {
                "mean": 1000.0 * mean_of(row),
                "lo": min(seeds) if seeds else 0.0,
                "hi": max(seeds) if seeds else 0.0,
            }
    return None


def fill(text: str, scenario: "Scenario") -> str:
    """Substitute the measured numbers into a narration string.

    `{left}` is the mean throughput of the (single) panel; `{left_range}` is
    its range across seeds. A sentence whose number is missing from the dataset
    is dropped rather than printed with a hole in it.
    """
    if "{" not in text:
        return text
    values = {
        side: measured_per_1000(scenario.map_name, spec.variant)
        for side, spec in zip(("left", "right"), scenario.panels)
    }
    if any(value is None for value in values.values()):
        return ""
    fields = {}
    for side, value in values.items():
        fields[side] = f"{value['mean']:.0f}"
        fields[f"{side}_range"] = f"{value['lo']:.0f} to {value['hi']:.0f}"
    return text.format(**fields)


@dataclass(frozen=True)
class PanelSpec:
    variant: str
    title: str
    subtitle: str


@dataclass(frozen=True)
class Scenario:
    """One GIF: a map, a robot count, and the planner(s) to show."""

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
    #: the on-screen narration, cued to the timestep it becomes true. These
    #: are what make a GIF explain rather than merely show: at any moment a
    #: first-time viewer can read one sentence saying what is happening and
    #: why. Keep them short enough to fit one line and specific enough to be
    #: checkable against the frame they appear on.
    beats: Tuple[Beat, ...] = ()
    #: which quantity the shared chart plots -- "delivered", or "flips" where
    #: throughput is not what the scenario is arguing about
    chart_series: str = "delivered"
    #: frames a second. Slower than a GIF usually runs, deliberately: the file
    #: is not decoration, it is an argument, and a viewer has to be able to
    #: follow sixteen robots through a six-cell corridor and see that the
    #: corridor drains. At ten it was a blur.
    fps: int = 5
    seed: int = 0


SCENARIOS: Tuple[Scenario, ...] = (
    Scenario(
        key="bottleneck",
        filename="01-aisleflow-bottleneck.gif",
        map_name="warehouse_bottleneck",
        robots=16,
        rate=0.8,
        timesteps=400,
        stride=2,
        title="Aisleflow clearing the one corridor every task must cross",
        caption=(
            "warehouse_bottleneck, 16 robots, 0.8 jobs/step. Two halves joined "
            "by a single six-cell corridor; every task crosses it, both ways, "
            "forever."
        ),
        panels=(
            PanelSpec(
                "full_lda_pibt",
                "aisleflow",
                "a blocked robot pushes the robot ahead of it",
            ),
        ),
        watch_for=(
            "Sixteen robots on `warehouse_bottleneck`: two halves of the floor "
            "joined by a single six-cell corridor, so every task must cross it "
            "one way and cross back the other. Aisleflow never plans a path it "
            "has to reserve -- a blocked robot lends its rank to the robot in "
            "its way and pushes, and an idle robot is simply displaced by the "
            "first busy one that needs its cell -- so the corridor drains "
            "instead of jamming. Nothing on the frame stays red for long: the "
            "queue keeps moving through the chokepoint. Measured over five "
            "seeds on this map it delivers {left} tasks per 1000 timesteps, "
            "ahead of every published planner. The full comparison, and the "
            "three baselines it beats, is on [../05-results.md](../05-results.md)."
        ),
        beats=(
            Beat(0, "warehouse_bottleneck: 16 robots, two halves joined by one "
                    "six-cell corridor. Every task crosses it, both ways."),
            Beat(60, "Aisleflow plans no path it has to reserve, so there is no "
                     "search to fail as the floor fills."),
            Beat(150, "A blocked robot lends its rank to the robot ahead and "
                      "pushes; an idle robot is simply displaced."),
            Beat(250, "So the one corridor drains instead of gridlocking -- watch "
                      "the queue keep moving through it."),
            Beat(330, "Five seeds on this map: {left} tasks per 1000 steps, ahead "
                      "of every published planner."),
        ),
    ),
)

BY_KEY: Dict[str, Scenario] = {s.key: s for s in SCENARIOS}


def render(scenario: Scenario, quick: bool = False) -> Path:
    timesteps = 120 if quick else scenario.timesteps
    stride = 2 if quick else scenario.stride
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

    # the title, caption and beat-by-beat narration are documentation, not
    # picture -- they go to docs/gifs/README.md (via write_readme) rather than
    # onto the frame, so the GIF itself carries only what a viewer is watching
    # right now: the grid, the live count, the chart and the legend
    path = save_comparison(
        panels,
        OUT_DIR / scenario.filename,
        title="",
        caption="",
        stride=stride,
        fps=scenario.fps,
        chart_series=scenario.chart_series,
    )
    print(f"    -> {path.relative_to(ROOT)}  ({path.stat().st_size / 1e6:.2f} MB)")
    return path


def write_readme(rendered: Sequence[Scenario]) -> Path:
    """Regenerate docs/gifs/README.md from the scenario definition.

    The prose lives next to the run that produces it, so a GIF and its
    explanation cannot drift apart.
    """
    # the pace is a property of the render, so read it off the scenario rather
    # than writing it into the sentence, where it would be free to drift
    stride = rendered[0].stride if rendered else 2
    fps = rendered[0].fps if rendered else 5
    lines: List[str] = [
        "# Animation",
        "",
        "One animation of aisleflow itself, on the floor where it leads every",
        "published planner. It is a real, seeded run of the same simulator the",
        "results table uses (`experiments.build_run`) -- same map, robot count,",
        "arrival rate and task stream -- so the picture and the number in",
        "[../05-results.md](../05-results.md) are the same run.",
        "",
        "The frame itself carries no title, caption or narration -- that",
        "explanation is here instead, generated from the same scenario",
        "definition that renders the GIF, so the two cannot say different",
        "things. The throughput this page quotes is read out of `../data/`",
        "when the animation is rendered, so it is the same number as on the",
        "results page and cannot be left behind by a regenerated dataset. It",
        "is a mean over five seeds; a single seeded run is one draw from",
        "that, so the GIF shows the mechanism rather than the average.",
        "",
        "Regenerate it with:",
        "",
        "```bash",
        "python3 tools/make_gifs.py            # needs pillow: pip install -e \".[viz]\"",
        "```",
        "",
        "## How to read it",
        "",
        f"It is built to be followed on a first watch: {stride} timesteps a frame",
        f"at {fps} frames a second, with a pause on the opening frame to read the",
        "setup and a longer one on the last to read the outcome.",
        "",
        "| On the frame | Means |",
        "| --- | --- |",
        "| Blue dot | a robot on its way to a pickup |",
        "| Teal dot | a robot carrying a task to a delivery |",
        "| Grey dot | a robot with no task yet |",
        "| **Red dot** | that robot has not moved for 15 timesteps |",
        "| Bar across the top | how far into the run this frame is |",
        "| Big number | tasks delivered so far |",
        "| Chart | tasks delivered over the whole run, drawn as it plays |",
        "",
        "One colour rule carries most of the argument: **red means stuck**, and",
        "nothing else on the frame is red. A floor that jammed would fill with",
        "red and the chart under it would flatten; watch that this one does not.",
        "The beat-by-beat narration below says, in words and cued to a",
        "timestep, what the mechanism is doing at each stage of the run.",
        "",
    ]
    for scenario in rendered:
        panel_desc = ", ".join(f"`{p.variant}`" for p in scenario.panels)
        lines += [
            f"## {scenario.title}",
            "",
            f"![{scenario.title}]({scenario.filename})",
            "",
            f"**{scenario.map_name}**, {scenario.robots} robots, arrival rate "
            f"{scenario.rate}, {scenario.timesteps} timesteps, seed {scenario.seed}. "
            f"{panel_desc}.",
            "",
            fill(scenario.watch_for, scenario),
            "",
        ]
        narration = [
            (beat.timestep, filled)
            for beat in scenario.beats
            if (filled := fill(beat.text, scenario))
        ]
        if narration:
            lines += ["<details><summary>The narration, beat by beat</summary>", ""]
            lines += [f"- **t = {t}** -- {text}" for t, text in narration]
            lines += ["", "</details>", ""]
        lines += [
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
                        help="120-step run, for checking layout")
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
