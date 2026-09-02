#!/usr/bin/env python3
"""Fill the generated blocks in `docs/deck/slides.html`.

Three slides in the deck show arithmetic -- which robot takes which task, what
each candidate cell scores, and how a pool of robots is ranked. Typed by hand,
those numbers are the kind that go stale the first time a default moves and
then quietly lie to an audience for a year. So they are not typed: this script
builds three small fixed scenarios on a real map and calls the real
`TaskAssigner`, the real `CandidateScorer` and the real `compute_priority`,
draws each scenario as a grid, and writes the picture and its table into the
deck between marker comments.

The measured results and the map picture come the same way: the result tables
are lifted verbatim out of the `<!-- generated:NAME -->` blocks that
`tools/make_docs_tables.py` writes into `docs/05-results.md`, so the deck and
the documents cannot disagree, and the map is drawn from `maps/*.map`.

    python3 tools/make_deck_figures.py

`tests/test_deck.py` fails if the blocks are stale.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.assignment import TaskAssigner  # noqa: E402
from lda_pibt.config import Params  # noqa: E402
from lda_pibt.congestion import CongestionModel, OccupancyIndex  # noqa: E402
from lda_pibt.priority import (  # noqa: E402
    CLASS_RANK,
    compute_priority,
    fairness_horizon,
    order_by_priority,
    task_class,
)
from lda_pibt.robot import Robot  # noqa: E402
from lda_pibt.scoring import (  # noqa: E402
    CandidateScorer,
    compute_aisle_bonus,
    compute_proximity_mode,
    turning_cost,
)
from lda_pibt.task import Task  # noqa: E402
from lda_pibt.types import Compass, RobotState, TaskStatus, Vertex, movement_direction  # noqa: E402
from lda_pibt.warehouse import Warehouse  # noqa: E402

DECK = ROOT / "docs" / "deck" / "slides.html"
RESULTS = ROOT / "docs" / "05-results.md"
MAPS = ROOT / "maps"

BEGIN = "<!-- generated:{} -->"
END = "<!-- /generated:{} -->"

#: the deck's palette, so a generated figure cannot drift from the CSS
INK = "#10161d"
DIM = "#4a5768"
FADE = "#7b8899"
RULE = "#ccd5df"
ACCENT = "#1a56c4"
WARM = "#9a5b00"
GOOD = "#0f7350"
BAD = "#b32218"
SHELF = "#d3dae2"
PICKUP = "#dbeaff"
DELIVER = "#d8f0e3"
PARK = "#fdeecb"
HILITE = "#fff3cd"

#: robot colours, matching the animation's legend so a reader who has watched
#: the GIF recognises them here
TO_PICKUP_C = ACCENT
LOADED_C = GOOD
IDLE_C = "#8593a4"
STUCK_C = BAD

SANS = "Helvetica, Arial"


# --------------------------------------------------------------------------
# grid drawing
# --------------------------------------------------------------------------


def cell_fill(wh: Warehouse, v: Vertex) -> str:
    if v in wh.pickup_vertices:
        return PICKUP
    if v in wh.delivery_vertices:
        return DELIVER
    if v in getattr(wh, "parking_vertices", ()):
        return PARK
    return "#ffffff"


class Grid:
    """An SVG of one warehouse floor, with things drawn on top of it."""

    def __init__(self, wh: Warehouse, cell: int = 34, pad: int = 2,
                 label_rows: bool = False):
        self.wh = wh
        self.cell = cell
        self.pad = pad
        self.label = label_rows
        self.left = 26 if label_rows else pad
        self.top = 22 if label_rows else pad
        self.parts: List[str] = []

    # ---------------------------------------------------------- geometry
    def x(self, col: int) -> float:
        return self.left + col * self.cell

    def y(self, row: int) -> float:
        return self.top + row * self.cell

    def cx(self, v: Vertex) -> float:
        return self.x(v[1]) + self.cell / 2

    def cy(self, v: Vertex) -> float:
        return self.y(v[0]) + self.cell / 2

    @property
    def width(self) -> float:
        return self.left + self.wh.width * self.cell + self.pad

    @property
    def height(self) -> float:
        return self.top + self.wh.height * self.cell + self.pad

    # ------------------------------------------------------------- floor
    def floor(self) -> None:
        c = self.cell
        for row in range(self.wh.height):
            for col in range(self.wh.width):
                v = (row, col)
                passable = v in self.wh.graph.vertex_set
                fill = cell_fill(self.wh, v) if passable else SHELF
                self.parts.append(
                    f'<rect x="{self.x(col):.0f}" y="{self.y(row):.0f}" '
                    f'width="{c}" height="{c}" fill="{fill}" '
                    f'stroke="{RULE}" stroke-width="1"/>'
                )
        if self.label:
            for col in range(self.wh.width):
                self.parts.append(
                    f'<text x="{self.x(col) + c / 2:.0f}" y="{self.top - 7}" '
                    f'text-anchor="middle" font-family="{SANS}" font-size="10" '
                    f'fill="{FADE}">{col}</text>'
                )
            for row in range(self.wh.height):
                self.parts.append(
                    f'<text x="{self.left - 8}" y="{self.y(row) + c / 2 + 4:.0f}" '
                    f'text-anchor="end" font-family="{SANS}" font-size="10" '
                    f'fill="{FADE}">{row}</text>'
                )

    # ------------------------------------------------------------ marks
    def tint(self, v: Vertex, colour: str, opacity: float = 1.0) -> None:
        self.parts.append(
            f'<rect x="{self.x(v[1]):.0f}" y="{self.y(v[0]):.0f}" '
            f'width="{self.cell}" height="{self.cell}" fill="{colour}" '
            f'opacity="{opacity}" stroke="{RULE}" stroke-width="1"/>'
        )

    def ring(self, v: Vertex, colour: str, width: float = 2.5,
             dashed: bool = False) -> None:
        dash = ' stroke-dasharray="4 3"' if dashed else ""
        self.parts.append(
            f'<rect x="{self.x(v[1]) + 1.5:.0f}" y="{self.y(v[0]) + 1.5:.0f}" '
            f'width="{self.cell - 3}" height="{self.cell - 3}" fill="none" '
            f'stroke="{colour}" stroke-width="{width}"{dash}/>'
        )

    def robot(self, v: Vertex, label: str, colour: str, r: Optional[float] = None) -> None:
        radius = r if r is not None else self.cell * 0.36
        self.parts.append(
            f'<circle cx="{self.cx(v):.0f}" cy="{self.cy(v):.0f}" r="{radius:.0f}" '
            f'fill="{colour}"/>'
            f'<text x="{self.cx(v):.0f}" y="{self.cy(v) + 4:.0f}" '
            f'text-anchor="middle" font-family="{SANS}" font-size="12" '
            f'fill="#ffffff" font-weight="700">{label}</text>'
        )

    def pin(self, v: Vertex, label: str, colour: str) -> None:
        """A square marker for a place rather than a robot -- a pickup, a goal."""
        s = self.cell * 0.62
        self.parts.append(
            f'<rect x="{self.cx(v) - s / 2:.0f}" y="{self.cy(v) - s / 2:.0f}" '
            f'width="{s:.0f}" height="{s:.0f}" rx="3" fill="{colour}"/>'
            f'<text x="{self.cx(v):.0f}" y="{self.cy(v) + 4:.0f}" '
            f'text-anchor="middle" font-family="{SANS}" font-size="11" '
            f'fill="#ffffff" font-weight="700">{label}</text>'
        )

    def text(self, v: Vertex, label: str, colour: str = INK, size: int = 11,
             dy: float = 0.0) -> None:
        self.parts.append(
            f'<text x="{self.cx(v):.0f}" y="{self.cy(v) + 4 + dy:.0f}" '
            f'text-anchor="middle" font-family="{SANS}" font-size="{size}" '
            f'fill="{colour}" font-weight="700">{label}</text>'
        )

    def at(self, x: float, y: float, label: str, colour: str = FADE,
           size: int = 12, anchor: str = "start", weight: str = "400") -> None:
        self.parts.append(
            f'<text x="{x:.0f}" y="{y:.0f}" text-anchor="{anchor}" '
            f'font-family="{SANS}" font-size="{size}" fill="{colour}" '
            f'font-weight="{weight}">{label}</text>'
        )

    def arrow(self, a: Vertex, b: Vertex, colour: str, marker: str,
              shrink: float = 0.42) -> None:
        x1, y1, x2, y2 = self.cx(a), self.cy(a), self.cx(b), self.cy(b)
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5 or 1.0
        off = self.cell * shrink
        self.parts.append(
            f'<path d="M{x1 + dx / length * off:.0f} {y1 + dy / length * off:.0f} '
            f'L{x2 - dx / length * off:.0f} {y2 - dy / length * off:.0f}" '
            f'stroke="{colour}" stroke-width="2.5" fill="none" '
            f'marker-end="url(#{marker})"/>'
        )

    # ------------------------------------------------------------ output
    def svg(self, label: str, height: Optional[float] = None,
            markers: Sequence[Tuple[str, str]] = ()) -> str:
        defs = "".join(
            f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto">'
            f'<path d="M0 0 L10 5 L0 10 z" fill="{colour}"/></marker>'
            for mid, colour in markers
        )
        h = height if height is not None else self.height
        return (
            f'<svg class="fig" viewBox="0 0 {self.width:.0f} {self.height:.0f}" '
            f'width="100%" height="{h:.0f}" role="img" aria-label="{label}">'
            f"{'<defs>' + defs + '</defs>' if defs else ''}"
            f"{''.join(self.parts)}</svg>"
        )


# --------------------------------------------------------------------------
# the shared fixture
#
# One map, one set of parameters, one timestep. Every scenario below is built
# on `warehouse_small` because it is the smallest floor with the structure that
# matters -- one-wide aisles between shelf blocks, pickups along the top,
# deliveries along the bottom, a through-corridor across the middle -- so a
# grid of it fits on a slide and still shows a real warehouse.
# --------------------------------------------------------------------------

MAP = "warehouse_small"
TIMESTEP = 20


def fixture() -> Tuple[Warehouse, Params]:
    return Warehouse.from_file(MAPS / f"{MAP}.map"), Params()


def place(wh: Warehouse, p: Params, robots: Sequence[Robot]):
    """Index and congestion model for a set of robots standing still."""
    index = OccupancyIndex(wh, p)
    congestion = CongestionModel(wh, index, p)
    index.rebuild(robots)
    congestion.begin_timestep()
    for robot in robots:
        if robot.waypoint is None:
            robot.waypoint = robot.position
        robot.route_distance_to_waypoint = wh.graph.route_distance(
            robot.position, robot.waypoint
        )
        robot.mode = compute_proximity_mode(robot.route_distance_to_waypoint, p)
        robot.aisle_bonus = compute_aisle_bonus(robot.mode, p)
        robot.current_aisle = wh.aisle_id(robot.position)
    return index, congestion


def num(value: float, places: int = 2) -> str:
    """A number with the minus sign a reader expects rather than a hyphen."""
    text = f"{value:.{places}f}"
    if text.startswith("-"):
        text = "&minus;" + text[1:]
    return text


def signed(value: float, places: int = 2, zero: str = "&mdash;") -> str:
    """A signed number, or `zero` where the term contributed nothing.

    A dash reads as "this term did not apply", which is what a reader wants for
    a lane bonus that is switched off. For progress, zero is a real answer --
    the move goes neither closer nor further -- so that column passes "0.0".
    """
    if abs(value) < 10 ** -(places + 1):
        return zero
    return ("+" if value > 0 else "&minus;") + f"{abs(value):.{places}f}"


# --------------------------------------------------------------------------
# 1. task assignment
# --------------------------------------------------------------------------

#: three idle robots and three released tasks. The positions are chosen so
#: that the greedy matching is not the matching each robot would pick for
#: itself -- which is the point slide 22 makes.
ASSIGN_ROBOTS: Tuple[Tuple[int, Vertex], ...] = (
    (1, (4, 2)),
    (2, (4, 10)),
    (3, (6, 16)),
)
ASSIGN_TASKS: Tuple[Tuple[int, Vertex, Vertex, int], ...] = (
    (1, (0, 0), (8, 8), 8),
    (2, (0, 8), (8, 12), 14),
    (3, (0, 16), (8, 0), 17),
)


def assignment_scenario():
    wh, p = fixture()
    robots = [Robot(id=rid, position=v) for rid, v in ASSIGN_ROBOTS]
    index, congestion = place(wh, p, robots)
    tasks = [
        Task(id=tid, pickup=pick, delivery=drop, release_time=released)
        for tid, pick, drop, released in ASSIGN_TASKS
    ]
    assigner = TaskAssigner(wh, congestion, p)
    cost = {
        (r.id, t.id): assigner.assignment_cost(r, t, TIMESTEP)
        for r in robots
        for t in tasks
    }
    return wh, p, robots, tasks, assigner, cost


def block_assign_pool() -> str:
    wh, p, robots, tasks, assigner, cost = assignment_scenario()
    grid = Grid(wh, cell=42)
    grid.floor()
    for task in tasks:
        grid.tint(task.pickup, PICKUP)
        grid.tint(task.delivery, DELIVER)
        grid.pin(task.pickup, f"P{task.id}", WARM)
        grid.pin(task.delivery, f"D{task.id}", FADE)
    for robot in robots:
        grid.robot(robot.position, f"R{robot.id}", IDLE_C)

    rows = "".join(
        f'<tr><td class="k">T{t.id}</td>'
        f"<td>pickup {t.pickup}, delivery {t.delivery}</td>"
        f'<td class="n">{t.release_time}</td>'
        f'<td class="n">{t.waiting_time(TIMESTEP)}</td></tr>'
        for t in tasks
    )
    return (
        '<div class="split pic">'
        "<div>"
        + grid.svg("Three idle robots and three released tasks on warehouse_small")
        + '<p class="figcap">'
        f"<span class=\"mono\">{MAP}</span>, {wh.height}&times;{wh.width}. "
        "Pickups along the top, deliveries along the bottom, one through-corridor "
        "across the middle. Every aisle is one cell wide.</p>"
        "</div>"
        "<div>"
        '<table class="calc"><caption>THE QUEUE AT t = '
        f"{TIMESTEP}</caption>"
        "<thead><tr><th>Task</th><th>From, to</th>"
        '<th class="n">Released</th><th class="n">Waited</th></tr></thead>'
        f"<tbody>{rows}</tbody></table>"
        '<div class="key">'
        f'<span><i style="background:{IDLE_C}"></i>idle robot</span>'
        f'<span><i class="sq" style="background:{WARM};border-radius:3px"></i>pickup</span>'
        f'<span><i class="sq" style="background:{FADE};border-radius:3px"></i>delivery</span>'
        f'<span><i class="sq" style="background:{SHELF}"></i>shelf</span>'
        "</div>"
        "</div></div>"
    )


def block_assign_rounds() -> str:
    wh, p, robots, tasks, assigner, cost = assignment_scenario()

    # replay the same rule `assign_tasks_greedily` uses: repeatedly commit the
    # single cheapest surviving pair
    free = {r.id for r in robots}
    open_tasks = {t.id for t in tasks}
    rounds: List[Dict] = []
    while free and open_tasks:
        best = min(
            ((rid, tid) for rid in free for tid in open_tasks),
            key=lambda pair: (cost[pair], pair),
        )
        rounds.append({"free": set(free), "open": set(open_tasks), "pick": best})
        free.discard(best[0])
        open_tasks.discard(best[1])

    panels = []
    for n, state in enumerate(rounds, start=1):
        pick = state["pick"]
        head = "".join(
            f'<th class="n">T{t.id}</th>' for t in tasks if t.id in state["open"]
        )
        body = []
        for robot in robots:
            if robot.id not in state["free"]:
                continue
            cells = []
            for task in tasks:
                if task.id not in state["open"]:
                    continue
                value = cost[(robot.id, task.id)]
                won = (robot.id, task.id) == pick
                style = (
                    f' style="background:{HILITE};color:{INK};font-weight:700"'
                    if won
                    else ""
                )
                cells.append(f'<td class="n"{style}>{num(value)}</td>')
            body.append(
                f'<tr><td class="k">R{robot.id}</td>' + "".join(cells) + "</tr>"
            )
        panels.append(
            "<div>"
            f'<table class="calc"><caption>ROUND {n} &mdash; '
            f"CHEAPEST IS R{pick[0]} &rarr; T{pick[1]}</caption>"
            f'<thead><tr><th style="width:52px"></th>{head}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table>"
            "</div>"
        )

    committed = ", ".join(f"R{r} &rarr; T{t}" for r, t in (s["pick"] for s in rounds))
    last_robot, last_task = rounds[-1]["pick"]
    own_best = min(
        (t.id for t in tasks), key=lambda tid: cost[(last_robot, tid)]
    )
    aside = ""
    if own_best != last_task:
        aside = (
            f' R{last_robot} would rather have had T{own_best} at '
            f"{num(cost[(last_robot, own_best)])} than the T{last_task} it ends up "
            f"with at {num(cost[(last_robot, last_task)])} &mdash; but T{own_best} "
            "was committed in an earlier round to a robot that wanted it more."
        )
    return (
        '<div class="cols3">' + "".join(panels) + "</div>"
        f'<p class="figcap" style="text-align:left;font-size:17px;margin-top:22px">'
        f"<b style=\"color:{INK}\">{committed}.</b>{aside}</p>"
    )


# --------------------------------------------------------------------------
# 2. the movement score
# --------------------------------------------------------------------------

#: one robot at a junction, carrying a task to the far delivery, with two
#: others near enough that crowding is not zero. A junction rather than an
#: aisle cell because only a junction has all five candidates -- every aisle on
#: every map here is one cell wide, so an in-aisle robot has three.
SCORE_HERO: Vertex = (4, 8)
SCORE_GOAL: Vertex = (8, 16)
SCORE_PREVIOUS = Compass.EAST
SCORE_OTHERS: Tuple[Vertex, ...] = ((4, 9), (3, 8))


def score_scenario():
    wh, p = fixture()
    hero = Robot(
        id=1,
        position=SCORE_HERO,
        previous_direction=SCORE_PREVIOUS,
        state=RobotState.TO_DELIVERY,
    )
    hero.waypoint = SCORE_GOAL
    others = [Robot(id=i + 2, position=v) for i, v in enumerate(SCORE_OTHERS)]
    robots = [hero] + others
    index, congestion = place(wh, p, robots)
    scorer = CandidateScorer(wh, congestion, p)

    candidates = [hero.position] + list(wh.graph.neighbors(hero.position))
    at_intersection = wh.is_intersection(hero.position)
    rows = []
    for v in candidates:
        progress = scorer.progress(hero, v)
        same_aisle = (
            0.0
            if at_intersection
            else (
                1.0
                if wh.aisle_id(v) == hero.current_aisle and hero.current_aisle is not None
                else 0.0
            )
        )
        movement = movement_direction(hero.position, v)
        turn = turning_cost(hero.previous_direction, movement, p)
        crowding = congestion.crowding(hero, v)
        rows.append(
            {
                "vertex": v,
                "move": movement,
                "progress": p.progress_reward * progress,
                "lane": hero.aisle_bonus * same_aisle,
                "turn": -p.turn_penalty * turn,
                "crowding": -p.crowding_penalty * crowding,
                "total": scorer.score(hero, v),
                "occupant": index.robot_at(v) if v != hero.position else None,
            }
        )
    rows.sort(key=lambda row: (-row["total"], row["vertex"]))
    return wh, p, hero, others, index, rows


MOVE_NAME = {
    Compass.STAY: "stay",
    Compass.NORTH: "north",
    Compass.SOUTH: "south",
    Compass.EAST: "east",
    Compass.WEST: "west",
}


def block_score_grid() -> str:
    wh, p, hero, others, index, rows = score_scenario()
    grid = Grid(wh, cell=42)
    grid.floor()
    grid.tint(SCORE_GOAL, DELIVER)
    grid.pin(SCORE_GOAL, "goal", GOOD)
    for row in rows:
        grid.ring(row["vertex"], ACCENT, width=2.5, dashed=row["vertex"] != hero.position)
    for other in others:
        grid.robot(other.position, f"R{other.id}", IDLE_C)
    grid.robot(hero.position, "R1", LOADED_C)
    # the direction it arrived from, so the turning cost has a visible cause
    came_from = (
        hero.position[0] - SCORE_PREVIOUS.delta[0],
        hero.position[1] - SCORE_PREVIOUS.delta[1],
    )
    grid.arrow(came_from, hero.position, FADE, "aFade", shrink=0.30)

    n_occupied = sum(1 for row in rows if row["occupant"] is not None)
    occupied = {1: "One", 2: "Two", 3: "Three", 4: "Four"}.get(n_occupied, str(n_occupied))

    return (
        '<div class="split pic">'
        "<div>"
        + grid.svg(
            "One robot at a junction with its five candidate cells ringed",
            markers=(("aFade", FADE),),
        )
        + '<p class="figcap">R1 is carrying a task to the delivery marked '
        "<i>goal</i>. It arrived travelling "
        f"{MOVE_NAME[SCORE_PREVIOUS]}. The five ringed cells are everything it "
        "may consider this timestep.</p>"
        "</div>"
        "<div>"
        f'<p style="font-size:19px">R1 is '
        f"{hero.route_distance_to_waypoint:.0f} cells from its goal by route "
        f"distance &mdash; the <span class=\"mono\">{hero.mode.value}</span> band, "
        f"which would be worth <b>{hero.aisle_bonus:.2f}</b> of lane bonus. It "
        "will get none of it, for the reason in the first card.</p>"
        '<div class="card tight warm" style="margin-top:16px">'
        f"<h3>It is standing on a {'junction' if wh.is_intersection(hero.position) else 'aisle cell'}</h3>"
        "<p>There is no aisle here to continue along, so the stay-in-lane term "
        "is switched off &mdash; otherwise it would reward an arbitrary one of "
        "the branches. Watch that column stay empty on the next slide.</p></div>"
        '<div class="card tight" style="margin-top:16px">'
        f"<h3>{occupied} of the five are occupied</h3>"
        "<p>Scored anyway. An occupied cell is never rejected: if the robot in "
        "it can be pushed, the move happens.</p></div>"
        '<div class="key">'
        f'<span><i style="background:{LOADED_C}"></i>carrying a task</span>'
        f'<span><i style="background:{IDLE_C}"></i>idle</span>'
        f'<span><i class="sq" style="background:#fff;border-color:{ACCENT}"></i>candidate</span>'
        "</div>"
        "</div></div>"
    )


def block_score_table() -> str:
    wh, p, hero, others, index, rows = score_scenario()
    body = []
    for n, row in enumerate(rows):
        occupied = (
            f' <span style="color:{FADE}">&mdash; R{row["occupant"].id} is here</span>'
            if row["occupant"] is not None
            else ""
        )
        body.append(
            f'<tr class="{"win" if n == 0 else ""}">'
            f'<td class="k">{MOVE_NAME[row["move"]]}{occupied}</td>'
            f'<td class="n">{signed(row["progress"], 1, zero="0.0")}</td>'
            f'<td class="n">{signed(row["lane"])}</td>'
            f'<td class="n">{signed(row["turn"])}</td>'
            f'<td class="n">{signed(row["crowding"], 3)}</td>'
            f'<td class="n tot">{num(row["total"], 3)}</td></tr>'
        )

    tiers: Dict[float, List[str]] = {}
    for row in rows:
        tiers.setdefault(round(row["progress"], 3), []).append(MOVE_NAME[row["move"]])
    top = max(tiers)
    contested = tiers[top]
    spread = max(r["total"] for r in rows if abs(r["progress"] - top) < 1e-9) - min(
        r["total"] for r in rows if abs(r["progress"] - top) < 1e-9
    )

    ladder = []
    for value in sorted(tiers, reverse=True):
        ladder.append(
            f'<div style="display:flex;gap:14px;align-items:baseline">'
            f'<span class="mono" style="width:5.5em;text-align:right;color:{INK};'
            f'font-weight:700">{signed(value, 1, zero="0.0")}</span>'
            f'<span style="color:{DIM};font-size:16px">{", ".join(tiers[value])}</span>'
            "</div>"
        )

    return (
        '<div class="split calc">'
        "<div>"
        '<table class="calc">'
        "<caption>EVERY TERM, FOR EVERY CANDIDATE</caption>"
        '<thead><tr><th>Move</th><th class="n">Progress</th><th class="n">Lane</th>'
        '<th class="n">Turn</th><th class="n">Crowd</th>'
        '<th class="n">S(v)</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
        "</div>"
        "<div>"
        f'<div class="card tight"><h3>The tiers, ten points apart</h3>'
        f'<div style="margin-top:12px">{"".join(ladder)}</div></div>'
        f'<p style="font-size:18px;margin-top:20px">The top tier holds '
        f"<b>{len(contested)}</b> candidates, separated by "
        f"<b>{spread:.3f}</b> &mdash; turning cost and crowding, and nothing "
        "else. Every tie-break in this planner is a contest for that "
        "margin.</p>"
        f'<p style="font-size:16px;color:var(--ink-dim);margin-top:14px">'
        "The lane column is empty for every candidate because R1 is standing on "
        "a junction, where there is no aisle to continue along. In an aisle it "
        f"would be worth {hero.aisle_bonus:.2f} to the cells that stay in "
        "one.</p>"
        "</div></div>"
    )


# --------------------------------------------------------------------------
# 3. priority
# --------------------------------------------------------------------------

#: six robots covering four of the five classes, two of them in the same class
#: so the in-aisle bonus is visibly what separates them, and an idle robot that
#: has waited long enough to be interesting without overtaking anything.
PRIORITY_POOL: Tuple[Tuple[int, Vertex, RobotState, Optional[TaskStatus], int], ...] = (
    (1, (2, 4), RobotState.TO_DELIVERY, TaskStatus.TO_DELIVERY, 0),
    (2, (4, 8), RobotState.TO_DELIVERY, TaskStatus.TO_DELIVERY, 1),
    (3, (6, 12), RobotState.TO_PICKUP, TaskStatus.TO_PICKUP, 3),
    (4, (4, 6), RobotState.TO_PICKUP, TaskStatus.TO_PICKUP, 0),
    (5, (4, 0), RobotState.PARKED, None, 2),
    (6, (4, 14), RobotState.FREE, None, 9),
)

CLASS_COLOUR = {
    "recovery": STUCK_C,
    "loaded": LOADED_C,
    "pickup": TO_PICKUP_C,
    "repositioning": WARM,
    "free": IDLE_C,
}


def priority_scenario():
    wh, p = fixture()
    robots = []
    for rid, v, state, status, waited in PRIORITY_POOL:
        task = None
        if status is not None:
            task = Task(id=900 + rid, pickup=(0, 0), delivery=(8, 0), release_time=0)
            task.status = status
        robot = Robot(id=rid, position=v, state=state, task=task, waiting_time=waited)
        robot.current_aisle = wh.aisle_id(v)
        robots.append(robot)
    for robot in robots:
        robot.priority = compute_priority(robot, TIMESTEP, p)
    return wh, p, robots


def block_priority_pool() -> str:
    wh, p, robots = priority_scenario()
    ordered = order_by_priority(list(robots))
    place_of = {r.id: n + 1 for n, r in enumerate(ordered)}

    grid = Grid(wh, cell=42)
    grid.floor()
    for robot in robots:
        grid.robot(robot.position, f"R{robot.id}", CLASS_COLOUR[task_class(robot)])

    body = []
    for robot in ordered:
        klass = task_class(robot)
        inside = robot.current_aisle is not None
        body.append(
            f'<tr class="{"win" if place_of[robot.id] == 1 else ""}">'
            f'<td class="n">{place_of[robot.id]}</td>'
            f'<td class="k">R{robot.id}</td>'
            f'<td><span style="color:{CLASS_COLOUR[klass]};font-weight:600">{klass}</span></td>'
            f'<td class="n">{CLASS_RANK[klass] * p.priority_class_spread:.0f}</td>'
            f'<td class="n">{"+%.0f" % p.priority_inside_aisle if inside else "&mdash;"}</td>'
            f'<td class="n">{"+%.0f" % (p.waiting_weight * robot.waiting_time) if robot.waiting_time else "&mdash;"}</td>'
            f'<td class="n tot">{robot.priority:.4f}</td></tr>'
        )

    return (
        '<div class="split calc">'
        "<div>"
        + grid.svg("Six robots of four different classes on warehouse_small")
        + '<div class="key">'
        + "".join(
            f'<span><i style="background:{CLASS_COLOUR[k]}"></i>{k}</span>'
            for k in ("loaded", "pickup", "repositioning", "free")
        )
        + "</div></div>"
        "<div>"
        '<table class="calc"><caption>THE ORDER THEY WILL PLAN IN</caption>'
        '<thead><tr><th class="n">#</th><th></th><th>Class</th>'
        '<th class="n">Class</th><th class="n">Aisle</th>'
        '<th class="n">Waited</th><th class="n">Priority</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
        f'<p class="figcap" style="text-align:left;margin-top:14px">'
        "The fourth term, the tie-breaker, is the robot id times a "
        "ten-thousandth &mdash; it is the digits after the decimal point, and it "
        "exists only so that two identical robots get a reproducible order.</p>"
        "</div></div>"
    )


def block_fairness() -> str:
    wh, p, robots = priority_scenario()
    horizon = fairness_horizon(p)
    top = max(CLASS_RANK.values()) * p.priority_class_spread

    # the two robots the pool makes the point with: the lowest-ranked one, and
    # the one immediately above it
    ordered = order_by_priority(list(robots))
    last, above = ordered[-1], ordered[-2]
    gap = above.priority - last.priority
    steps_to_overtake = int(gap // p.waiting_weight) + 1

    width, height = 1020, 168
    axis_y = 104
    scale = (width - 120) / horizon

    ticks = []
    for value in (0, horizon / 4, horizon / 2, 3 * horizon / 4, horizon):
        x = 60 + value * scale
        ticks.append(
            f'<line x1="{x:.0f}" y1="{axis_y - 7}" x2="{x:.0f}" y2="{axis_y + 7}" '
            f'stroke="{RULE}" stroke-width="1.5"/>'
            f'<text x="{x:.0f}" y="{axis_y + 28}" text-anchor="middle" '
            f'font-family="{SANS}" font-size="12" fill="{FADE}">{value:.0f}</text>'
        )
    overtake_x = 60 + steps_to_overtake * scale
    svg = (
        f'<svg class="fig" viewBox="0 0 {width} {height}" width="100%" height="{height}" '
        f'role="img" aria-label="Waiting time buys rank at a fixed rate">'
        f'<rect x="60" y="{axis_y - 13}" width="{horizon * scale:.0f}" height="26" '
        f'rx="4" fill="{PICKUP}"/>'
        f'<line x1="60" y1="{axis_y}" x2="{60 + horizon * scale:.0f}" y2="{axis_y}" '
        f'stroke="{ACCENT}" stroke-width="3"/>'
        + "".join(ticks)
        + f'<line x1="{overtake_x:.0f}" y1="{axis_y - 34}" x2="{overtake_x:.0f}" '
        f'y2="{axis_y + 13}" stroke="{WARM}" stroke-width="2" stroke-dasharray="4 3"/>'
        f'<text x="{overtake_x:.0f}" y="{axis_y - 42}" text-anchor="middle" '
        f'font-family="{SANS}" font-size="13" fill="{WARM}" font-weight="700">'
        f"R{last.id} passes R{above.id} at {steps_to_overtake}</text>"
        f'<text x="{60 + horizon * scale:.0f}" y="{axis_y - 26}" text-anchor="end" '
        f'font-family="{SANS}" font-size="13" fill="{ACCENT}" font-weight="700">'
        f"at {horizon:.0f} steps it outranks anything, whatever class</text>"
        f'<text x="60" y="{axis_y + 52}" font-family="{SANS}" font-size="13" '
        f'fill="{FADE}">timesteps a robot has been waiting</text>'
        "</svg>"
    )

    return (
        svg
        + '<div class="cols3" style="margin-top:26px">'
        f'<div class="card tight accent"><h3>{p.waiting_weight:.0f} a step</h3>'
        "<p>Every timestep a robot fails to move and is not already at its "
        "waypoint adds this much to its rank. Any move at all resets it to "
        "zero.</p></div>"
        f'<div class="card tight accent"><h3>{top:.0f} to catch up</h3>'
        "<p>The whole class ladder, bottom to top &mdash; the largest head "
        "start any robot can have over any other.</p></div>"
        f'<div class="card tight warm"><h3>{horizon:.0f} steps</h3>'
        f"<p>{top:.0f} &divide; {p.waiting_weight:.0f}. After this long, the "
        "lowest-ranked robot on the floor outranks a robot in recovery that "
        "just arrived.</p></div>"
        "</div>"
        f'<p class="figcap" style="text-align:left;font-size:17px;margin-top:20px">'
        f"In the pool on the last slide, R{last.id} sits "
        f"{gap:.0f} below R{above.id}. It does not need the full horizon to pass "
        f"it &mdash; <b>{steps_to_overtake} more steps of waiting</b> is enough. "
        "The measured maximum wait in a normal run is two or three steps, so "
        "neither number is ever reached; they are the bound, not the "
        "behaviour.</p>"
    )


# --------------------------------------------------------------------------
# 4. the floor itself
# --------------------------------------------------------------------------


def block_map_bottleneck() -> str:
    wh = Warehouse.from_file(MAPS / "warehouse_bottleneck.map")
    grid = Grid(wh, cell=40, label_rows=False)
    grid.floor()

    articulation = sorted(wh.graph.articulation_points)
    for v in articulation:
        grid.tint(v, "#fbe6e3")
        grid.ring(v, BAD, width=1.5)
    for v in wh.graph.vertices:
        if wh.is_bottleneck(v):
            grid.text(v, "b", WARM, size=13)

    summary = [
        (f"{wh.height} &times; {wh.width}", "grid"),
        (f"{len(wh.graph.vertices)}", "drivable cells"),
        (f"{len(wh.pickup_vertices)} / {len(wh.delivery_vertices)}", "pickups / deliveries"),
        (f"{len(wh.aisles)}", "aisles, every one 1 wide"),
        (f"{len(articulation)}", "cells that split the floor"),
    ]
    stats = "".join(
        f'<div><div style="font-size:30px;font-weight:700;color:{INK};'
        f'font-family:Helvetica,Arial;letter-spacing:-.02em">{value}</div>'
        f'<div style="font-size:13px;color:{FADE};letter-spacing:.1em;'
        f'text-transform:uppercase;font-weight:600;margin-top:6px">{label}</div></div>'
        for value, label in summary
    )

    return (
        '<div class="split pic">'
        "<div>"
        + grid.svg("warehouse_bottleneck drawn to scale, articulation points tinted")
        + '<p class="figcap"><span class="mono">warehouse_bottleneck</span>, drawn '
        "to scale. The tinted cells are the ones whose removal would cut the floor "
        "in two.</p>"
        "</div>"
        "<div>"
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:26px 30px">{stats}</div>'
        f'<div class="card tight bad" style="margin-top:26px">'
        "<h3>Two halves, one way across</h3>"
        "<p>Pickups on the left, deliveries on the right, and a single row of "
        "cells joining them. Every task crosses it one way and crosses back the "
        "other, forever &mdash; so the corridor carries the entire throughput of "
        "the floor in both directions at once.</p></div>"
        "</div></div>"
    )


# --------------------------------------------------------------------------
# 5. the measured results, lifted from the documents
#
# The deck must not restate a number the documents generate; it quotes the
# same generated block, converted to HTML, so `tools/make_docs_tables.py` and
# this script cannot disagree.
# --------------------------------------------------------------------------


def markdown_block(path: Path, name: str) -> List[str]:
    text = path.read_text(encoding="utf-8")
    start = text.index(BEGIN.format(name)) + len(BEGIN.format(name))
    end = text.index(END.format(name))
    return [line for line in text[start:end].strip().splitlines() if line.strip()]


def inline_markdown(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    text = re.sub(r"`(.+?)`", r'<span class="mono">\1</span>', text)
    return text.replace("--", "&ndash;").replace("−", "&minus;")


def split_row(line: str) -> List[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def markdown_table(
    lines: Sequence[str],
    caption: str = "",
    highlight: Sequence[int] = (),
    limit: Optional[int] = None,
) -> str:
    """Render one generated Markdown table as the deck's HTML table.

    The alignment row (`---:`) decides which columns are numeric, exactly as it
    does when GitHub renders the document.
    """
    header = split_row(lines[0])
    aligns = split_row(lines[1])
    numeric = [a.endswith(":") for a in aligns]
    rows = [split_row(line) for line in lines[2:] if line.strip().startswith("|")]
    note = " ".join(line for line in lines[2:] if not line.strip().startswith("|"))
    if limit is not None:
        rows = rows[:limit]

    head = "".join(
        f'<th class="{"n" if numeric[i] else ""}">{inline_markdown(cell)}</th>'
        for i, cell in enumerate(header)
    )
    body = []
    for n, row in enumerate(rows):
        cells = "".join(
            f'<td class="{"n" if numeric[i] else ""}'
            f'{" k" if i == 0 else ""}">{inline_markdown(cell)}</td>'
            for i, cell in enumerate(row)
        )
        body.append(f'<tr class="{"hi" if n in highlight else ""}">{cells}</tr>')

    cap = f"<caption>{caption}</caption>" if caption else ""
    out = (
        f'<table class="calc">{cap}<thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
    )
    if note:
        out += (
            f'<p class="figcap" style="text-align:left;margin-top:16px">'
            f"{inline_markdown(note.strip().strip('*'))}</p>"
        )
    return out


def block_featured() -> str:
    return markdown_table(
        markdown_block(RESULTS, "featured"),
        caption="EVERY PLANNER ON THE SAME MAP, SEEDS AND JOB STREAM",
        highlight=(0,),
    )


def block_ladder() -> str:
    return markdown_table(
        markdown_block(RESULTS, "ladder"),
        caption="EACH ROW ADDS ONE MECHANISM TO THE ROW ABOVE IT",
    )


def block_sensitivity_top() -> str:
    return markdown_table(
        markdown_block(RESULTS, "sensitivity"),
        caption="THE SIX MOST LOAD-BEARING KNOBS, OF 24 MEASURED",
        highlight=(0, 1),
        limit=6,
    )


def block_runtime() -> str:
    """The planner's measured cost per timestep, out of the baseline suite."""
    import json

    payload = json.loads((ROOT / "docs" / "data" / "baselines.json").read_text())
    best = None
    for map_name, block in payload["maps"].items():
        for row in block["rows"]:
            if row["variant"] != "full_lda_pibt":
                continue
            field = row["fields"].get("mean_runtime_ms_per_step")
            if not field:
                continue
            robots = block.get("scenario", {}).get("n_robots")
            if best is None or (robots or 0) > (best[2] or 0):
                best = (map_name, field["mean"], robots)
    if best is None:
        return ""
    map_name, ms, robots = best
    who = f"{robots} robots on " if robots else ""
    return (
        f"Measured at <b>{ms:.2f} ms/step</b> for {who}"
        f'<span class="mono">{map_name}</span>, single-threaded pure Python.'
    )


def block_title_stats() -> str:
    """Three numbers about the code, counted rather than remembered."""
    import dataclasses

    p = Params()
    n_params = len(dataclasses.fields(p))
    featured = markdown_block(RESULTS, "featured")
    rows = [split_row(line) for line in featured[2:] if line.strip().startswith("|")]
    lead = rows[0][1] if rows else "?"
    runner_up = max(int(r[1]) for r in rows[1:]) if len(rows) > 1 else 0
    stats = [
        (lead, "tasks / 1000 steps", f"against {runner_up} for the best baseline"),
        ("4", "terms in the movement score", "down from nine"),
        (str(n_params), "parameters", "down from sixty"),
    ]
    return '<div class="statrow" style="margin-top:52px">' + "".join(
        f'<div class="stat"><div class="n small">{value}</div>'
        f'<div class="l">{label}</div>'
        f'<div style="font-size:14px;color:var(--ink-fade);margin-top:8px">{note}</div>'
        "</div>"
        for value, label, note in stats
    ) + "</div>"


# --------------------------------------------------------------------------
# writing it out
# --------------------------------------------------------------------------

BLOCKS = {
    "title-stats": block_title_stats,
    "runtime": block_runtime,
    "map-bottleneck": block_map_bottleneck,
    "assign-pool": block_assign_pool,
    "assign-rounds": block_assign_rounds,
    "score-grid": block_score_grid,
    "score-table": block_score_table,
    "priority-pool": block_priority_pool,
    "fairness": block_fairness,
    "featured": block_featured,
    "ladder": block_ladder,
    "sensitivity-top": block_sensitivity_top,
}


def replace_block(text: str, name: str, body: str) -> str:
    begin, end = BEGIN.format(name), END.format(name)
    if begin not in text or end not in text:
        raise SystemExit(f"docs/deck/slides.html has no {begin} ... {end} markers")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{body}\n{end}{tail}"


def main() -> int:
    text = DECK.read_text(encoding="utf-8")
    for name, build in BLOCKS.items():
        text = replace_block(text, name, build())
    DECK.write_text(text, encoding="utf-8")
    print(f"  wrote {len(BLOCKS)} blocks into {DECK.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
