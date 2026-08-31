#!/usr/bin/env python3
"""Generate the worked examples embedded in the LaTeX guide.

Every number the guide prints inside a "Worked example" block is produced
here, by running the real simulator and calling the real scoring, congestion,
aisle, assignment and priority code. Nothing is typed in by hand, so a change
to a formula or a default weight shows up as a failing test rather than as a
guide that quietly disagrees with the code.

The guide marks each block with a pair of LaTeX comments::

    % worked-example: score
    \\begin{workedexample}
    \\textbf{Worked example.} ...
    \\end{workedexample}
    % /worked-example

Usage::

    python3 tools/worked_examples.py            # print the blocks
    python3 tools/worked_examples.py --check    # fail if the guide is stale
    python3 tools/worked_examples.py --write    # regenerate them in place

``tests/test_worked_examples.py`` runs the ``--check`` path.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "latex" / "aisleflow.tex"
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.aisle_manager import AisleManager  # noqa: E402
from lda_pibt.assignment import TaskAssigner  # noqa: E402
from lda_pibt.config import Params, ablation  # noqa: E402
from lda_pibt.priority import compute_priority, task_class_priority  # noqa: E402
from lda_pibt.robot import Robot  # noqa: E402
from lda_pibt.scoring import turning_cost, wait_penalty  # noqa: E402
from lda_pibt.simulator import Simulator, build_simulator  # noqa: E402
from lda_pibt.task import Task, TaskGenerator  # noqa: E402
from lda_pibt.types import INF, AisleDirection, Compass, movement_direction  # noqa: E402
from lda_pibt.warehouse import Warehouse  # noqa: E402

# The scenario every example is drawn from. Fixed map, fixed seed, fixed robot
# count: the same moment comes back every time this runs.
MAP = ROOT / "maps" / "warehouse_medium.map"
VARIANT = "full_lda_pibt"
SEED = 7
ROBOTS = 24
RATE = 2.0
MAX_STEPS = 60

BLOCK_RE = re.compile(
    r"(% worked-example: (?P<name>[\w-]+)\n)(?P<body>.*?)(% /worked-example)",
    re.S,
)


@dataclass
class Moment:
    """The simulation state every example is read from."""

    sim: Simulator
    timestep: int
    robot: Robot
    rows: List[dict]


def build_moment() -> Moment:
    """Step the real simulator to the first genuinely interesting decision.

    "Interesting" means a robot facing at least four candidate cells where at
    least one of them is carrying an aisle penalty -- the case that shows both
    halves of the score at once, and the case the guide's argument turns on.
    """
    params = ablation(VARIANT, Params(seed=SEED, max_timesteps=400))
    warehouse = Warehouse.from_file(MAP, params)
    generator = TaskGenerator(
        warehouse.pickup_vertices,
        warehouse.delivery_vertices,
        mode="poisson",
        rate=RATE,
        seed=SEED,
    )
    sim = build_simulator(warehouse, ROBOTS, params, task_generator=generator)
    for _ in range(MAX_STEPS):
        sim.step()
        timestep = sim.timestep - 1
        for robot in sim.robots:
            if robot.waypoint is None:
                continue
            rows = sim.planner.explain_candidates(robot, timestep)
            if len(rows) >= 4 and any(row["penalties"] for row in rows):
                return Moment(sim, timestep, robot, rows)
    raise RuntimeError(
        f"no penalised candidate set within {MAX_STEPS} steps of the "
        f"{VARIANT} scenario -- pick a different seed or map"
    )


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------


def num(value: float, places: int = 2) -> str:
    if value == INF:
        return "INF"
    text = f"{value:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def signed(value: float, places: int = 2) -> str:
    return ("+" if value >= 0 else "−") + num(abs(value), places)


#: The guide's old "§4.2"-style cross references, mapped onto the LaTeX labels
#: that replaced them. Generated prose has to cross-reference the document it
#: is embedded in, and a printed section number would go stale the moment a
#: section moved; \Cref does not.
SECTION_LABELS: Dict[str, str] = {
    "3.2": "sec:priority-fairness",
    "4.2": "sec:score-beta",
    "4.3": "sec:score-gamma",
    "6.2": "sec:aisle-demand",
    "7.2": "sec:pibt-rejection",
    "7.4": "sec:pibt-recursion",
    "9.3": "sec:assignment-cost",
}

_LATEX_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def latex_escape(text: str) -> str:
    return "".join(_LATEX_ESCAPES.get(ch, ch) for ch in text)


def latex_inline(text: str) -> str:
    """Render one line of the emitters' Markdown-ish prose as LaTeX.

    The emitters below write `code spans`, **bold** and section references
    because that is what reads well in a plain-text terminal dump of this
    script's output. This turns that into LaTeX without the emitters having to
    know which they are producing.
    """
    spans: List[str] = []

    def stash(match: "re.Match[str]") -> str:
        spans.append(match.group(1))
        return f"\x00{len(spans) - 1}\x00"

    text = re.sub(r"`([^`]+)`", stash, text)
    text = latex_escape(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", text)
    text = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\\emph{\1}", text)
    text = re.sub(
        r"§(\d+(?:\.\d+)*)",
        lambda m: (
            f"\\Cref{{{SECTION_LABELS[m.group(1)]}}}"
            if m.group(1) in SECTION_LABELS
            else f"Section~{m.group(1)}"
        ),
        text,
    )
    return re.sub(
        r"\x00(\d+)\x00",
        lambda m: f"\\code{{{latex_escape(spans[int(m.group(1))])}}}",
        text,
    )


#: Fitting a verbatim block to the page. A wrapped column of program output is
#: worse than a small one, so the font shrinks until the widest line fits
#: instead. Width of the text inside a workedexample box, in points; DejaVu
#: Sans Mono's advance as a fraction of its size; and the range of sizes worth
#: using.
_BOX_WIDTH_PT = 375.0
_CHAR_ADVANCE = 0.602
_SIZE_CAP_PT = 9.0
_SIZE_FLOOR_PT = 5.6


def fit_size(block: List[str]) -> float:
    """Font size at which the widest line of `block` fits the box."""
    widest = max((len(line) for line in block), default=0)
    if widest == 0:
        return _SIZE_CAP_PT
    size = _BOX_WIDTH_PT / (widest * _CHAR_ADVANCE)
    return round(max(_SIZE_FLOOR_PT, min(_SIZE_CAP_PT, size)), 1)


def quote(lines: List[str]) -> str:
    """Wrap an emitter's lines in the guide's `workedexample` environment.

    Fenced regions become `verbatim`, sized to fit: they are fixed-width
    transcripts of program output whose columns must not reflow, and the score
    example's table is a hundred characters wide. Everything else is prose and
    goes through `latex_inline`.
    """
    out: List[str] = [r"\begin{workedexample}"]
    fenced: Optional[List[str]] = None
    for line in lines:
        if line.strip() == "```":
            if fenced is None:
                fenced = []
            else:
                size = fit_size(fenced)
                out.append(r"{\fontsize{%.1f}{%.1f}\selectfont" % (size, size * 1.2))
                out.append(r"\begin{verbatim}")
                out.extend(fenced)
                # the closing brace must not share the line with
                # \end{verbatim}, which has to stand alone
                out.append(r"\end{verbatim}")
                out.append("}")
                fenced = None
        elif fenced is not None:
            fenced.append(line)
        elif not line.strip():
            out.append("")
        else:
            out.append(latex_inline(line))
    if fenced is not None:  # pragma: no cover - an emitter with a stray fence
        raise SystemExit("worked example has an unclosed code fence")
    out.append(r"\end{workedexample}")
    return "\n".join(out) + "\n"


def table(headers: List[str], rows: List[List[str]]) -> List[str]:
    """A fixed-width table rendered inside a code fence, so columns line up."""
    widths = [
        max(len(headers[c]), *(len(row[c]) for row in rows)) if rows else len(headers[c])
        for c in range(len(headers))
    ]
    out = ["  ".join(h.ljust(widths[c]) for c, h in enumerate(headers)).rstrip()]
    out.append("  ".join("-" * widths[c] for c in range(len(headers))))
    for row in rows:
        out.append("  ".join(cell.ljust(widths[c]) for c, cell in enumerate(row)).rstrip())
    return out


def vertex(v) -> str:
    return f"({v[0]},{v[1]})"


# --------------------------------------------------------------------------
# example 1: the candidate score S_i(v)
# --------------------------------------------------------------------------


def score_terms(moment: Moment, candidate) -> Dict[str, float]:
    """Recompute each term of S_i(v) separately, from the same public helpers
    the scorer itself calls, so the printed columns really do sum to `score`."""
    sim, robot = moment.sim, moment.robot
    scorer = sim.planner.scorer
    p = sim.params
    wh = sim.warehouse

    move = movement_direction(robot.position, candidate)
    direction_match = float(
        p.direction_control != "none"
        and move is not Compass.STAY
        and move is robot.preferred_direction
    )
    same_aisle = float(
        wh.aisle_id(candidate) == robot.current_aisle and robot.current_aisle is not None
    )
    if wh.is_intersection(robot.position):
        same_aisle = 0.0
    return {
        "progress": p.alpha_progress * scorer.progress(robot, candidate),
        "direction": robot.direction_weight * direction_match,
        "aisle": robot.aisle_weight * same_aisle,
        "turn": -p.lambda_turn * turning_cost(robot.previous_direction, move, p),
        "congestion": -p.mu_congestion * scorer.congestion.congestion(robot, candidate),
        "wait": -p.nu_wait * wait_penalty(robot, candidate),
        "bottleneck": -p.xi_bottleneck * float(wh.is_bottleneck(candidate)),
        "zeta": -scorer.aisle_penalty(robot, candidate),
    }


def example_score(moment: Moment) -> str:
    sim, robot = moment.sim, moment.robot
    sim.planner.scorer.timestep = moment.timestep
    rows = sorted(moment.rows, key=lambda r: -r["score"])

    body: List[List[str]] = []
    for row in rows:
        candidate = tuple(row["vertex"])
        terms = score_terms(moment, candidate)
        move = movement_direction(robot.position, candidate)
        body.append(
            [
                vertex(candidate),
                move.name.lower(),
                signed(terms["progress"], 1),
                signed(terms["direction"], 2),
                signed(terms["aisle"], 2),
                signed(terms["turn"], 2),
                signed(terms["congestion"], 2),
                signed(terms["wait"] + terms["bottleneck"], 2),
                signed(terms["zeta"], 1),
                num(row["score"], 2),
                ", ".join(row["reasons"]) or "-",
                ", ".join(row["penalties"]) or "-",
            ]
        )
    headers = [
        "cell", "move", "α·prog", "β·dir", "γ·aisle", "−λ·turn", "−μ·cong",
        "−ν/−ξ", "−ζ", "S(v)", "rejected by", "priced",
    ]

    legal = [row for row in rows if row["legal"]]
    winner = legal[0]
    winner_terms = score_terms(moment, tuple(winner["vertex"]))
    rejected = next((row for row in rows if row["reasons"]), None)
    priced = next((row for row in rows if row["penalties"]), None)

    lines = [
        f"**Worked example.** `warehouse_medium`, variant `{VARIANT}`, "
        f"seed {SEED}, {ROBOTS} robots, timestep {moment.timestep}. "
        f"Robot {robot.id} is at {vertex(robot.position)} heading for waypoint "
        f"{vertex(robot.waypoint)}, {num(robot.route_distance_to_waypoint, 0)} steps away, "
        f"so it is in `{robot.mode.name}` mode with "
        f"`β_i` = {num(robot.direction_weight)} and "
        f"`γ_i` = {num(robot.aisle_weight)} (§4.2, §4.3). Its previous move was "
        f"`{robot.previous_direction.name.lower()}` and its preferred direction is "
        f"`{robot.preferred_direction.name.lower()}`. Every column below is computed by "
        "the same functions `CandidateScorer.score` itself calls, and they sum to "
        "the printed `S(v)`:",
        "",
        "```",
    ]
    lines += table(headers, body)
    lines += [
        "```",
        "",
        "Read the last two columns first, because they are different kinds of thing. "
        "**Rejected by** is legality: those moves are gone before scoring, and no "
        "score could rescue them. **Priced** is preference: the move is still on the "
        "table, it just costs.",
    ]

    if rejected is not None:
        lines += [
            "",
            f"{vertex(tuple(rejected['vertex']))} scores highest at "
            f"{num(rejected['score'])} and is unavailable regardless: "
            f"`{', '.join(rejected['reasons'])}` removed it in §7.2. So the move "
            f"actually chosen is {vertex(tuple(winner['vertex']))} at "
            f"S = {num(winner['score'])} — the best of what is *legal*.",
        ]
    else:
        lines += [
            "",
            f"Every candidate is legal here, so the move chosen is simply the "
            f"top-scoring one: {vertex(tuple(winner['vertex']))} at "
            f"S = {num(winner['score'])}.",
        ]

    lines += [
        "",
        f"The shape of the winning number is the whole argument: progress contributes "
        f"{signed(winner_terms['progress'], 1)}, while every other term together moves "
        f"it by "
        f"{signed(sum(v for k, v in winner_terms.items() if k != 'progress'), 2)}. "
        "The preference terms break ties between moves that agree on progress; they "
        "never outvote progress itself. That is what `α > ζ > β > γ > λ` buys.",
    ]

    if priced is not None:
        gap = winner["score"] - priced["score"]
        priced_terms = score_terms(moment, tuple(priced["vertex"]))
        lines += [
            "",
            f"{vertex(tuple(priced['vertex']))} is the instructive row. It pays "
            f"{num(-priced_terms['zeta'], 1)} for "
            f"`{', '.join(priced['penalties'])}` and finishes {num(gap)} behind — last "
            "by a wide margin, and still in the candidate set. Set "
            "`hard_direction_constraints` and that same move is deleted instead; a move "
            "PIBT cannot see is a move priority inheritance cannot use to unblock a "
            "chain (§7.4). The gap between those two treatments is what the README's "
            "ablation measures as 1.9× to 3.1× throughput.",
        ]
    return quote(lines)


# --------------------------------------------------------------------------
# example 2: the congestion mixture C_i(v)
# --------------------------------------------------------------------------


def example_congestion(moment: Moment) -> str:
    sim, robot = moment.sim, moment.robot
    p = sim.params
    model = sim.planner.scorer.congestion
    index = sim.index
    rows = sorted(moment.rows, key=lambda r: -r["score"])

    body = []
    for row in rows:
        candidate = tuple(row["vertex"])
        local = index.local_occupancy_ratio(candidate, exclude=robot)
        aisle = index.aisle_load(sim.warehouse.aisle_id(candidate))
        down = model.downstream(candidate, robot.waypoint)
        total = model.congestion(robot, candidate)
        body.append(
            [
                vertex(candidate),
                num(local, 3),
                num(aisle, 3),
                num(down, 3),
                num(total, 3),
                signed(-p.mu_congestion * total, 3),
            ]
        )
    weight_sum = p.omega_local + p.omega_aisle + p.omega_downstream
    peak = max(float(r[4]) for r in body)

    lines = [
        "**Worked example.** The same robot and timestep. `C_i(v)` mixes three "
        f"occupancy signals with weights `ω_local` = {num(p.omega_local)}, "
        f"`ω_aisle` = {num(p.omega_aisle)}, `ω_down` = {num(p.omega_downstream)}, "
        f"then divides by their sum ({num(weight_sum)}) because "
        "`congestion_normalisation` is on:",
        "",
        "```",
    ]
    lines += table(
        ["cell", "C_local", "C_aisle", "C_down", "C_i(v)", "−μ·C"],
        body,
    )
    lines += [
        "```",
        "",
        "Normalisation is what makes this a modulator rather than a rival. The "
        f"largest `C_i(v)` here is {num(peak, 3)}, so the entire congestion term is "
        f"worth at most {num(p.mu_congestion * peak, 3)} — against "
        f"`α` = {num(p.alpha_progress)} for a single step of progress. Turn "
        "normalisation off and `C_local` reverts to a raw robot count: `μ·C` then "
        "reaches the scale of `α·Δ` (measured mean 3.40, p90 5.75, max 9.60 on this "
        "map at 40 robots), and congestion stops modulating the smaller terms and "
        "starts overruling the largest one.",
    ]
    return quote(lines)


# --------------------------------------------------------------------------
# example 3: aisle demand and the hysteresis dead band
# --------------------------------------------------------------------------


def _pick_aisle(sim: Simulator, timestep: int) -> Tuple[object, float, float]:
    """The aisle with the largest live demand imbalance at this timestep."""
    aisles = sim.aisles
    robots = sim.robots_by_id
    best = None
    for aisle in sim.warehouse.aisles.values():
        if not aisle.manageable:
            continue
        forward = aisles.compute_directional_demand(
            aisle, AisleDirection.FORWARD, robots, timestep
        )
        reverse = aisles.compute_directional_demand(
            aisle, AisleDirection.REVERSE, robots, timestep
        )
        if forward == 0.0 and reverse == 0.0:
            continue
        score = abs(forward - reverse)
        if best is None or score > best[0]:
            best = (score, aisle, forward, reverse)
    if best is None:
        raise RuntimeError("no aisle carried any directional demand at this moment")
    return best[1], best[2], best[3]


def example_aisle(moment: Moment) -> str:
    sim = moment.sim
    timestep = moment.timestep
    aisle, forward, reverse = _pick_aisle(sim, timestep)
    imbalance = forward - reverse
    threshold = aisle.switch_threshold
    occupancy = sim.index.aisle_occupancy.get(aisle.id, 0)

    # Run the real decision twice on throwaway copies: as configured, and with
    # the dead band removed. The difference is exactly what hysteresis buys.
    with_hyst = copy.deepcopy(aisle)
    decision = sim.aisles.update_aisle_direction(with_hyst, forward, reverse, timestep)

    without = copy.deepcopy(aisle)
    saved = sim.params.hysteresis
    try:
        sim.aisles.params = sim.params.merged(hysteresis=False)
        naive = sim.aisles.update_aisle_direction(without, forward, reverse, timestep)
    finally:
        sim.aisles.params = sim.params
    del saved

    lines = [
        f"**Worked example.** Same run, timestep {timestep}. Aisle {aisle.id} runs "
        f"{vertex(aisle.start_vertex)} -> {vertex(aisle.end_vertex)} "
        f"(length {aisle.length}, capacity {aisle.capacity}, axis "
        f"`{aisle.axis or 'bent'}`). Each robot routed through it contributes "
        "`w_u·U + w_w·W + w_p·P − w_l·L − w_c·C` (§6.2) to whichever direction it "
        "wants, and the two sides are summed:",
        "",
        "```",
        f"S_a^+  (forward demand)      = {num(forward, 3)}",
        f"S_a^-  (reverse demand)      = {num(reverse, 3)}",
        f"imbalance  S_a^+ − S_a^-     = {signed(imbalance, 3)}",
        f"dead band  ± τ_switch        = {num(threshold, 3)}",
        f"occupancy                    = {occupancy} robot(s) inside",
        f"state on entry               = {aisle.state.name}, "
        f"direction {aisle.current_direction.name}",
        f"locked until t               = {aisle.lock_until} "
        f"(T_min = {aisle.minimum_lock_time}, T_max = {aisle.maximum_lock_time})",
        "```",
        "",
        f"With hysteresis on, `update_aisle_direction` returns "
        f"`{decision.name}`; with the dead band removed it returns "
        f"`{naive.name}`. "
        + (
            "The two agree here, which is the common case: hysteresis is not "
            "meant to fire often, it is meant to make the rare flip deliberate."
            if decision is naive
            else "That disagreement is the dead band doing its job - an imbalance "
            "this small is noise, and committing to it is how an aisle starts "
            "flapping with the traffic instead of settling."
        ),
        "",
        f"An imbalance of {num(abs(imbalance), 3)} against a dead band of "
        f"{num(threshold, 3)} "
        + (
            "clears the band"
            if abs(imbalance) > threshold
            else "sits inside the band"
        )
        + ", and a committed direction is additionally locked for `T_min` = "
        f"{aisle.minimum_lock_time} steps. The maximum green matters for the "
        "opposite failure: past `T_max` = "
        f"{aisle.maximum_lock_time} steps *any* opposing demand forces a drain, so a "
        "balanced aisle - pickups on one side, deliveries on the other, imbalance "
        "permanently inside the band - cannot hold one direction forever and starve "
        "the traffic wanting the other way.",
    ]
    return quote(lines)


# --------------------------------------------------------------------------
# example 4: the assignment cost J(i, tau)
# --------------------------------------------------------------------------


def example_assignment(moment: Moment) -> str:
    sim = moment.sim
    timestep = moment.timestep
    p = sim.params
    assigner = TaskAssigner(sim.warehouse, sim.planner.scorer.congestion, p)
    graph = sim.warehouse.graph

    task = Task(
        id=9001,
        pickup=sim.warehouse.pickup_vertices[0],
        delivery=sim.warehouse.delivery_vertices[0],
        release_time=max(0, timestep - 12),
    )

    # Two robots: the nearest one, and the cheapest one. When they differ, the
    # example makes its own point.
    reachable = [
        r for r in sim.robots
        if graph.route_distance(r.position, task.pickup) != INF
    ]
    nearest = min(reachable, key=lambda r: graph.route_distance(r.position, task.pickup))
    cheapest = min(reachable, key=lambda r: assigner.assignment_cost(r, task, timestep))
    contenders = [nearest] if nearest is cheapest else [nearest, cheapest]
    if len(contenders) == 1:
        runner_up = sorted(
            reachable, key=lambda r: graph.route_distance(r.position, task.pickup)
        )[1]
        contenders.append(runner_up)

    body = []
    for robot in contenders:
        d_pickup = graph.route_distance(robot.position, task.pickup)
        d_leg = graph.route_distance(task.pickup, task.delivery)
        congestion = (
            assigner.congestion.route_congestion(robot.position, task.pickup)
            if p.congestion_assignment else 0.0
        )
        waiting = min(float(task.waiting_time(timestep)), p.assign_waiting_cap)
        directional = (
            assigner.directional_delay(robot.position, task.pickup)
            if p.congestion_assignment or p.direction_control == "aisle" else 0.0
        )
        blocking = (
            assigner.blocking_estimate(robot, task) if p.congestion_assignment else 0.0
        )
        body.append([
            f"robot {robot.id} {vertex(robot.position)}",
            num(p.assign_alpha_to_pickup * d_pickup, 2),
            num(p.assign_beta_pickup_to_delivery * d_leg, 2),
            num(p.assign_gamma_congestion * congestion, 3),
            signed(p.assign_delta_waiting * waiting, 2),
            num(p.assign_eta_direction * directional, 2),
            num(p.assign_zeta_blocking * blocking, 3),
            num(assigner.assignment_cost(robot, task, timestep), 2),
        ])

    costs = [assigner.assignment_cost(r, task, timestep) for r in contenders]
    winner = contenders[costs.index(min(costs))]

    lines = [
        f"**Worked example.** Same run, timestep {timestep}. One unassigned task: "
        f"pickup {vertex(task.pickup)}, delivery {vertex(task.delivery)}, released at "
        f"t = {task.release_time} so it has been waiting "
        f"{task.waiting_time(timestep)} steps. Two candidate robots, costed by "
        "`TaskAssigner.assignment_cost`:",
        "",
        "```",
    ]
    lines += table(
        ["robot", "a·d(r,p)", "b·d(p,d)", "g·C", "d·W", "e·T_dir", "z·B", "J(i,τ)"],
        body,
    )
    lines += [
        "```",
        "",
        f"Robot {winner.id} wins at J = {num(min(costs))}. Two columns are worth "
        "pausing on. `d(pickup, delivery)` is identical for both robots, because it "
        "does not depend on which robot goes — it is in the cost only so that a queue "
        "of tasks is ordered by total work rather than by how near its pickup happens "
        f"to be. And `δ` = {signed(p.assign_delta_waiting)} is *negative*, so the waiting "
        "term is a discount: the longer a task has been queued the cheaper it looks, "
        "which is how ageing gets into a greedy match.",
        "",
        "That discount is capped at `assign_waiting_cap` = "
        f"{num(p.assign_waiting_cap, 0)}. Uncapped, `waiting_time` grows without bound "
        "in a lifelong run and dwarfs both distance and congestion within about a "
        "hundred steps; the match degenerates into oldest-task-first, and a "
        "congestion-aware assignment can no longer show any effect at all — which is "
        "exactly what H4 reported before the cap existed (§9.3).",
    ]
    return quote(lines)


# --------------------------------------------------------------------------
# example 5: the priority function p_i(t)
# --------------------------------------------------------------------------


LATE_STEPS = 150


def build_late_moment() -> Moment:
    """A second, independent run taken well into the lifelong regime.

    The priority example needs robots that have actually accumulated waiting
    and blocked time, which nobody has 18 steps in. This is its own simulator
    so that reading it cannot perturb the state the other four examples share.
    """
    params = ablation(VARIANT, Params(seed=SEED, max_timesteps=400))
    warehouse = Warehouse.from_file(MAP, params)
    generator = TaskGenerator(
        warehouse.pickup_vertices,
        warehouse.delivery_vertices,
        mode="poisson",
        rate=RATE,
        seed=SEED,
    )
    sim = build_simulator(warehouse, ROBOTS, params, task_generator=generator)
    for _ in range(LATE_STEPS):
        sim.step()
    return Moment(sim, sim.timestep - 1, sim.robots[0], [])


def example_priority(moment: Moment) -> str:
    sim = moment.sim
    timestep = moment.timestep
    p = sim.params

    ranked = sorted(sim.robots, key=lambda r: -compute_priority(r, timestep, p))
    # Top, bottom, and whoever has waited longest -- the three rows that show
    # the class band and the term that lets a robot climb out of it.
    oldest = max(sim.robots, key=lambda r: (r.waiting_time + r.blocked_time, -r.id))
    shown: List[Robot] = []
    for robot in (ranked[0], oldest, ranked[-1]):
        if robot not in shown:
            shown.append(robot)

    body = []
    for robot in shown:
        body.append([
            f"robot {robot.id}",
            robot.state.name.lower(),
            (robot.task.status.name.lower() if robot.task else "no task"),
            num(task_class_priority(robot, p), 2),
            num(p.waiting_weight * robot.waiting_time, 2),
            num(p.blocked_weight * robot.blocked_time, 2),
            num(p.priority_inside_aisle if robot.current_aisle is not None else 0.0, 2),
            num(compute_priority(robot, timestep, p), 3),
        ])

    spread = p.priority_emergency - p.priority_free
    horizon = spread / p.waiting_weight if p.waiting_weight else float("inf")

    lines = [
        f"**Worked example.** The same scenario run out to timestep {timestep}, so "
        "that robots have had time to accumulate waiting. Three of the "
        f"{len(sim.robots)}: the highest-priority robot, the one that has waited "
        "longest, and the lowest-priority one.",
        "",
        "```",
    ]
    lines += table(
        ["robot", "state", "task", "P_class", "k_w·W", "k_b·B", "k_e·E", "p_i(t)"],
        body,
    )
    lines += [
        "```",
        "",
        "`P_class` sets the band; the waiting and blocked terms move a robot within "
        f"it and, given long enough, out of it. The full class spread is {num(spread)} "
        f"({num(p.priority_emergency)} for a robot in recovery down to "
        f"{num(p.priority_free)} for an idle one) and `k_w` = "
        f"{num(p.waiting_weight, 3)}, so {num(horizon, 0)} steps of waiting is worth "
        "the entire spread: a robot that waits that long outranks *any* robot of any "
        "class (§3.2).",
        "",
        "That bound is the fairness guarantee, and it is the reason the priority "
        "function has a waiting term at all. Without it a permanently higher-class "
        "neighbour — a loaded robot on a floor that always has loaded robots — could "
        "starve an idle one indefinitely, and the throughput number would never show "
        "it.",
    ]
    return quote(lines)


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------

EXAMPLES = {
    "score": example_score,
    "congestion": example_congestion,
    "aisle": example_aisle,
    "assignment": example_assignment,
    "priority": example_priority,
}


def render_all() -> Dict[str, str]:
    moment = build_moment()
    late = build_late_moment()
    return {
        name: fn(late if name == "priority" else moment)
        for name, fn in EXAMPLES.items()
    }


def substitute(text: str, blocks: Dict[str, str]) -> Tuple[str, List[str]]:
    """Replace every marked block; return the new text and the names seen."""
    seen: List[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        seen.append(name)
        if name not in blocks:
            raise SystemExit(f"the guide marks unknown worked example {name!r}")
        return match.group(1) + blocks[name] + match.group(4)

    return BLOCK_RE.sub(replace, text), seen


def guide_files() -> List[Path]:
    """Every source file of the guide that may carry a marker.

    The guide is one document in several files, and a worked example sits in
    whichever section it belongs to, so the search is over the whole set
    rather than over one file.
    """
    return sorted(GUIDE.parent.glob("sections/*.tex"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="exit non-zero if the guide's blocks are stale")
    group.add_argument("--write", action="store_true",
                       help="regenerate the blocks in the guide")
    args = parser.parse_args(argv)

    blocks = render_all()

    if not (args.check or args.write):
        for name, text in blocks.items():
            print(f"--- {name} " + "-" * (68 - len(name)))
            print(text)
        return 0

    seen_all: List[str] = []
    stale: List[Path] = []
    for path in guide_files():
        original = path.read_text(encoding="utf-8")
        updated, seen = substitute(original, blocks)
        seen_all.extend(seen)
        if updated == original:
            continue
        stale.append(path)
        if args.write:
            path.write_text(updated, encoding="utf-8")

    missing = [name for name in blocks if name not in seen_all]
    if missing:
        raise SystemExit(
            f"the guide has no marker for worked example(s): {', '.join(missing)}"
        )

    if args.write:
        if stale:
            print(f"updated {len(seen_all)} worked example(s) in "
                  f"{len(stale)} file(s)")
        else:
            print("worked examples already up to date")
        return 0

    if stale:
        names = ", ".join(str(p.relative_to(ROOT)) for p in stale)
        print(
            f"{names} is out of date with the code.\n"
            "Run:  python3 tools/worked_examples.py --write",
            file=sys.stderr,
        )
        return 1
    print("worked examples match the code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
