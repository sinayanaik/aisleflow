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
# The narration quotes throughput -- "156 against 17 tasks per 1000 steps" --
# and it used to quote it as a literal in the source. Every one of those
# literals was wrong by the time anyone read it: the dataset was regenerated,
# the sentences were not, and a GIF ended up telling a viewer that a planner
# delivered nothing when the run playing underneath the caption delivered
# plenty. The numbers are now looked up from `docs/data/` at render time and
# substituted into `{left}` / `{right}` in the text, so they cannot drift.
# --------------------------------------------------------------------------

_MEASURED: Dict[str, Dict] = {}


def _dataset(name: str) -> Optional[Dict]:
    if name not in _MEASURED:
        path = DATA_DIR / f"{name}.json"
        _MEASURED[name] = json.loads(path.read_text()) if path.exists() else None
    return _MEASURED[name]


def measured_per_1000(map_name: str, variant: str) -> Optional[float]:
    """Tasks per 1000 timesteps for one planner on one map, from `docs/data/`.

    Checks the baseline suite first (it has the published planners) and then
    the ablation suite (it has every configuration of this one).
    """
    for suite, reader in (
        ("baselines", lambda row: row["fields"]["throughput"]["mean"]),
        ("ablation", lambda row: row["throughput"]),
    ):
        payload = _dataset(suite)
        if not payload or map_name not in payload.get("maps", {}):
            continue
        for row in payload["maps"][map_name]["rows"]:
            if row["variant"] == variant:
                return 1000.0 * reader(row)
    return None


def fill(text: str, scenario: "Scenario") -> str:
    """Substitute `{left}` and `{right}` with the measured throughputs.

    A sentence whose number is missing from the dataset is dropped rather than
    printed with a hole in it -- the GIF is still correct without it, and a
    caption reading "156 against {right}" is worse than one less caption.
    """
    if "{left}" not in text and "{right}" not in text:
        return text
    values = {
        side: measured_per_1000(scenario.map_name, spec.variant)
        for side, spec in zip(("left", "right"), scenario.panels)
    }
    if any(value is None for value in values.values()):
        return ""
    return text.format(**{k: f"{v:.0f}" for k, v in values.items()})


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
    #: the on-screen narration, cued to the timestep it becomes true. These
    #: are what make a GIF explain rather than merely show: at any moment a
    #: first-time viewer can read one sentence saying what is happening and
    #: why. Keep them short enough to fit one line and specific enough to be
    #: checkable against the frame they appear on.
    beats: Tuple[Beat, ...] = ()
    #: which quantity the shared chart plots -- "delivered", or "flips" where
    #: throughput is not what the scenario is arguing about
    chart_series: str = "delivered"
    seed: int = 0


SCENARIOS: Tuple[Scenario, ...] = (
    Scenario(
        key="gridlock",
        filename="01-token-passing-gridlock.gif",
        map_name="warehouse_bottleneck",
        robots=16,
        rate=0.8,
        timesteps=400,
        stride=2,
        title="Two ways of getting past a robot that is in the way",
        caption=(
            "warehouse_bottleneck, 16 robots, two halves joined by one corridor. "
            "Same map, same seed, same tasks."
        ),
        panels=(
            PanelSpec(
                "token_passing",
                "Token Passing",
                "Ma et al. 2017 - plan a whole path, then rest at its end",
            ),
            PanelSpec(
                "full_lda_pibt",
                "aisleflow",
                "a blocked robot pushes the robot ahead of it",
            ),
        ),
        watch_for=(
            "The clearest picture of the one structural difference between "
            "these two families. Token Passing hands an agent a task only if "
            "it can plan a whole collision-free path through pickup and "
            "delivery against every other agent's committed path, and an "
            "agent with no task rests where it stopped. On this map -- two "
            "halves joined by a single six-cell corridor, with two parking "
            "bays for sixteen robots -- resting robots regularly sit in the "
            "corridor, and while one does, nobody on the left can plan a path "
            "to anywhere on the right. Watch the left panel go quiet in "
            "stretches: those are the intervals where no agent could be given "
            "work at all. PIBT never plans a path it has to reserve, so there "
            "is no search to fail: a blocked robot lends its rank to the robot "
            "in its way and pushes, and a resting robot is simply displaced. "
            "Measured over five seeds on this map: {right} tasks per 1000 "
            "timesteps against {left}. Ma et al. prove Token Passing complete "
            "on *well-formed* instances -- one parking endpoint per agent -- "
            "which this map does not provide, and that is exactly the "
            "assumption you are watching run out."
        ),
        beats=(
            Beat(0, "Same map, same 16 robots, same tasks. One corridor joins the "
                    "two halves; there are two parking bays for sixteen robots."),
            Beat(40, "Left: Token Passing gives an agent a task only if it can plan "
                     "the whole path first, against everyone else's."),
            Beat(90, "Left: an agent with no task rests where it stopped -- and here "
                     "that is often inside the one corridor."),
            Beat(150, "While it sits there, nobody can plan a path across the map, so "
                      "no task can be handed out at all."),
            Beat(230, "Right: PIBT plans no path to reserve. A blocked robot lends "
                      "its rank to the robot ahead and pushes through."),
            Beat(320, "Five seeds on this map: {right} against {left} tasks per 1000 "
                      "steps. The difference is pushing, not scoring."),
        ),
    ),
    Scenario(
        key="turning-cost",
        filename="02-turning-cost-on-a-tight-floor.gif",
        map_name="warehouse_corridors",
        robots=35,
        rate=1.0,
        timesteps=400,
        stride=2,
        title="The cheapest term in the score, and what it buys",
        caption=(
            "warehouse_corridors, 35 robots, five parallel single-file corridors. "
            "One term of the movement score apart; everything else identical."
        ),
        panels=(
            PanelSpec(
                "lifelong_pibt",
                "Plain lifelong PIBT",
                "progress only: no turning cost, no lane bonus, no crowding",
            ),
            PanelSpec(
                "turning_cost_only",
                "One term added: turning cost (aisleflow)",
                "reversing costs a fraction of a step of progress",
            ),
        ),
        watch_for=(
            "The largest single win in the repository, and it is one term. Both "
            "panels run the same lifelong PIBT on the same corridors with the same "
            "jobs; the right one additionally charges a robot for reversing "
            "direction. In a single-file corridor a robot that oscillates blocks "
            "everything behind it in both directions, and the whole queue spends "
            "its time undoing the previous step. The penalty is small by "
            "construction - it can only break ties within a tier, never outrank a "
            "step of progress - and that is the point: it settles the ties that "
            "decide whether a corridor drains or thrashes. Measured over five "
            "seeds on this map: {right} tasks per 1000 timesteps against {left}, "
            "from the cheapest term in the score."
        ),
        beats=(
            Beat(0, "Same map, same 35 robots, same jobs. One term of the movement "
                    "score apart."),
            Beat(50, "Both sides score a step toward the goal at 10. The right side "
                     "also charges a robot for reversing."),
            Beat(120, "Left: in a single-file corridor an oscillating robot blocks "
                      "everything behind it, both ways."),
            Beat(200, "Right: the turning cost breaks that tie, so corridors commit "
                      "to a direction and drain."),
            Beat(290, "The penalty never outranks progress — it is far too small. It "
                      "only decides ties, and the ties are what mattered."),
            Beat(350, "Five seeds on this map: {right} against {left} tasks per "
                      "1000 steps, from one term."),
        ),
    ),
    Scenario(
        key="rhcr",
        filename="03-rhcr-replanning-stalls.gif",
        map_name="warehouse_corridors",
        robots=35,
        rate=1.0,
        timesteps=400,
        stride=2,
        title="Re-solving a window, against resolving each conflict in place",
        caption=(
            "warehouse_corridors, 35 robots. RHCR re-solves a windowed instance "
            "every few steps; aisleflow resolves each conflict where it happens."
        ),
        panels=(
            PanelSpec(
                "rhcr",
                "RHCR",
                "Li et al. 2021 - replan every h steps over a w-step window (PBS)",
            ),
            PanelSpec(
                "full_lda_pibt",
                "aisleflow",
                "a blocked robot pushes the robot ahead of it",
            ),
        ),
        watch_for=(
            "The strongest of the three published baselines, and the one whose "
            "assumptions this warehouse does not break. RHCR replans every "
            "agent together every few timesteps over a short window, resolving "
            "collisions inside it with priority-based search and ignoring "
            "everything beyond it; between replans the agents follow the plan "
            "they were given. That is a genuinely different design from "
            "PIBT's, and on five single-file corridors it is competitive: "
            "{left} tasks per 1000 timesteps against aisleflow's {right}. "
            "What the two panels differ in is *when* the work happens. RHCR "
            "pays a large, periodic cost -- a joint search over all 35 agents "
            "-- and then moves for free until the next one; aisleflow pays a "
            "small cost every timestep and never searches at all. Watch how "
            "similar the two floors look. That is the point: the interesting "
            "comparison in this project is not against RHCR, it is against "
            "the plain PIBT in the ablation ladder."
        ),
        beats=(
            Beat(0, "Same map, same 35 robots, same jobs. Two ways of avoiding a "
                    "collision."),
            Beat(50, "Left: RHCR replans every agent together, every few steps, over "
                     "a short window."),
            Beat(120, "Collisions are resolved only inside that window. Beyond it, "
                      "the paths are left to be somebody's problem later."),
            Beat(200, "Right never solves an instance at all: a blocked robot lends "
                      "its rank to the robot in the way and pushes."),
            Beat(280, "Both floors keep moving. This baseline is not the one that "
                      "struggles here -- Token Passing is (see GIF 01)."),
            Beat(350, "Five seeds: {right} against {left} tasks per 1000 steps. Close "
                      "enough that the ablation ladder is the argument, not this."),
        ),
    ),
    Scenario(
        key="recovery",
        filename="04-recovery-corroboration.gif",
        map_name="warehouse_corridors",
        robots=35,
        rate=1.0,
        timesteps=400,
        stride=2,
        title="Rescuing a deadlock, and rescuing a queue",
        caption=(
            "warehouse_corridors, 35 robots. Both panels run the same seven-level "
            "recovery; they differ in what is allowed to trigger it."
        ),
        panels=(
            PanelSpec(
                "recovery_uncorroborated",
                "One stall signal is enough",
                "no progress for stall_steps steps fires recovery",
            ),
            PanelSpec(
                "recovery_only",
                "Corroborated stalls only (aisleflow)",
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
            "Measured over five seeds on this map: {right} tasks per 1000 "
            "timesteps against {left}, produced entirely by refusing to act on "
            "the weakest of the three stall signals. Pooled over all four "
            "maps the sensitivity suite puts the corroboration rule at -54% "
            "(p < 0.001), which makes it the second most load-bearing thing in "
            "the planner."
        ),
        beats=(
            Beat(0, "Both sides run the SAME seven-level recovery. They differ only "
                    "in what is allowed to trigger it."),
            Beat(50, "Left fires on one signal alone: no progress for stall_steps "
                     "steps."),
            Beat(120, "But in dense lifelong traffic that signal describes an "
                      "ordinary queue, not a deadlock."),
            Beat(200, "Left: healthy queues are reversed, sent to escape vertices and "
                      "rebuilt — continuously. The delivered count barely moves."),
            Beat(280, "Right also needs a wait-for cycle or a repeated configuration "
                      "before it acts, so queues are left to drain."),
            Beat(350, "Across five seeds: {right} against {left} tasks per 1000 steps, "
                      "from refusing to act on the weakest of three stall signals."),
        ),
    ),
    Scenario(
        key="open-map",
        filename="05-open-map-honesty.gif",
        map_name="warehouse_medium",
        robots=40,
        rate=1.5,
        timesteps=400,
        stride=2,
        title="Where the congestion machinery costs more than it earns",
        caption=(
            "warehouse_medium, 40 robots, an open grid warehouse. "
            "This is the case aisleflow loses, shown as plainly as the ones it wins."
        ),
        panels=(
            PanelSpec(
                "full_lda_pibt",
                "aisleflow",
                "turning cost, lane bonus, crowding and recovery, all on",
            ),
            PanelSpec(
                "lifelong_pibt",
                "Plain lifelong PIBT",
                "progress only: every scoring term switched off",
            ),
        ),
        watch_for=(
            "Every mechanism in this project buys something and costs something. On "
            "a map with many parallel routes and no scarce single-file aisle, the "
            "thing this machinery buys - orderly flow through a contended corridor "
            "- is not scarce, while the thing it costs is: a robot that declines to "
            "turn, keeps to its lane and steers around a crowd is taking a longer "
            "route than it needed to, and there was no congestion to justify it. "
            "The right panel simply delivers more, throughout. Over five seeds it "
            "is {right} against {left} tasks per 1000 timesteps. The honest summary "
            "of this project is that its congestion machinery wins where every "
            "route crosses one chokepoint and loses where there is a way "
            "round, and this GIF is the losing half."
        ),
        beats=(
            Beat(0, "The case this project LOSES, shown as plainly as the ones it "
                    "wins."),
            Beat(50, "An open grid: many parallel routes, and no scarce single-file "
                     "aisle to fight over."),
            Beat(130, "Left still pays to keep its lane and avoid the crowd — with no "
                      "congestion to justify either."),
            Beat(220, "Right scores progress and nothing else, and simply keeps "
                      "delivering more, throughout."),
            Beat(300, "Nothing here is stuck on either side. The cost is pure "
                      "overhead, not gridlock."),
            Beat(350, "It wins where every route crosses one chokepoint, and loses "
                      "where there is a way round. Five seeds: {left} against {right}."),
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

    beats = tuple(
        Beat(beat.timestep, filled)
        for beat in scenario.beats
        if (filled := fill(beat.text, scenario))
    )
    path = save_comparison(
        panels,
        OUT_DIR / scenario.filename,
        title=scenario.title,
        caption=scenario.caption,
        stride=stride,
        beats=beats,
        chart_series=scenario.chart_series,
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
        "The numbers quoted below are read out of `../data/` when the animations",
        "are rendered, so they are the same numbers as in",
        "[../05-results.md](../05-results.md) and cannot be left behind by a",
        "regenerated dataset. They are means over five seeds; a single seeded run",
        "is one draw from that, so a GIF shows the mechanism rather than the",
        "average.",
        "",
        "Regenerate them all with:",
        "",
        "```bash",
        "python3 tools/make_gifs.py            # needs pillow: pip install -e \".[viz]\"",
        "```",
        "",
        "## How to read one",
        "",
        "They are built to be followed on a first watch, at two timesteps per frame,",
        "with a pause on the opening frame to read the setup and a longer one on the",
        "last to read the outcome.",
        "",
        "| On the frame | Means |",
        "| --- | --- |",
        "| Blue dot | a robot on its way to a pickup |",
        "| Teal dot | a robot carrying a task to a delivery |",
        "| Grey dot | a robot with no task yet |",
        "| **Red dot** | that robot has not moved for 15 timesteps |",
        "| **Red frame + GRIDLOCKED** | most of that panel's robots are stuck |",
        "| Big number | tasks delivered so far, green on whichever side is ahead |",
        "| Chart | tasks delivered over the whole run, drawn as it plays |",
        "| Band along the bottom | what is happening in this part of the run, and why |",
        "",
        "One colour rule carries most of the argument: **red means stuck**, and",
        "nothing else on the frame is red. A side that fills with red has stopped",
        "delivering, and the chart under it flattens at the same moment.",
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
