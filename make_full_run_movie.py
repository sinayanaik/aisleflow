#!/usr/bin/env python3
"""Render the whole lifelong pipeline of one seeded `full_caap` run.

Everything the loop in `Simulator.step` does, on one frame:

  * **task arrival**   -- the pending queue, plotted live and counted
  * **task allocation** -- every robot's assigned task id, its stage
                          (PICK / DROP), and an event ticker of the
                          assign / pickup / deliver moments as they happen
  * **path choosing**  -- each robot's current shortest route to its
                          waypoint, drawn as a polyline in that robot's own
                          colour, nudged sideways by robot id so parallel
                          routes down one corridor stay separable
  * **aisle flow**     -- per-aisle net direction of travel, measured over a
                          sliding window, drawn as an arrow on the aisle
  * **crowding**       -- per-aisle occupancy / capacity, as a red tint

Note on aisle *direction*: this repo has no committed direction layer left to
draw. `config.REMOVED_NAMES` retired `direction_control`, `hysteresis`,
`direction_aware_routing` and the rest after the layer measured -0.3%
(p = 0.95). The arrows here are therefore *measured* flow -- where the traffic
in each aisle is actually going right now -- not a policy the planner is
enforcing.

Usage::

    python3 tools/make_full_run_movie.py                     # mp4 + gif
    python3 tools/make_full_run_movie.py --timesteps 300     # quick check
    python3 tools/make_full_run_movie.py --robots 40 --rate 1.5   # paper config
    python3 tools/make_full_run_movie.py --no-gif

Needs matplotlib and pillow; the mp4 additionally needs ffmpeg on PATH.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402

from caap.experiments import build_run  # noqa: E402
from caap.types import RobotState, Vertex  # noqa: E402

Colour = Tuple[float, float, float]

#: What to call a variant on the frame. The flag-bundle keys are how the
#: experiment code names configurations; they are not what the planner is
#: called, so the headline shows the planner's name instead.
DISPLAY_NAMES = {
    "full_caap": "CAAP",
    "full_lda_pibt": "CAAP",
    "pibt_baseline": "PIBT (one-shot baseline)",
    "lifelong_pibt": "lifelong PIBT",
    "token_passing": "Token Passing",
    "rhcr": "RHCR",
}


def display_name(variant: str) -> str:
    return DISPLAY_NAMES.get(variant, variant)

# ---------------------------------------------------------------- palette
BG = "#12141a"
PANEL = "#191c24"
INK = "#e8eaf0"
DIM = "#8b93a7"

CELL_FREE = (0.93, 0.94, 0.96)
CELL_WALL = (0.13, 0.14, 0.18)
CELL_PICK = (0.78, 0.92, 0.80)
CELL_DROP = (0.79, 0.86, 0.96)
CELL_PARK = (0.92, 0.89, 0.78)
CROWD = np.array([0.95, 0.35, 0.30])


def robot_palette(n: int) -> List[Colour]:
    """`n` visually distinct colours, bright enough to read on a light grid."""
    base = list(plt.get_cmap("tab20").colors) + list(plt.get_cmap("tab20b").colors)
    out: List[Colour] = []
    for i in range(n):
        r, g, b = base[i % len(base)][:3]
        # darken a little so white id text on top stays legible
        k = 0.82 if i < len(base) else 0.66
        out.append((r * k, g * k, b * k))
    return out


# ---------------------------------------------------------------- capture
class Frame:
    """One timestep, flattened into exactly what the renderer draws."""

    __slots__ = (
        "t", "pos", "goal", "kind", "task_id", "route", "blocked",
        "completed", "pending", "assigned", "carrying",
        "aisle_load", "aisle_flow", "events",
    )

    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


def goal_of(robot) -> Tuple[Optional[Vertex], str, Optional[int]]:
    """Where this robot is headed, and why -- read after the move.

    `update_task_state` runs at the top of the *next* step, so a robot standing
    on its pickup still has `waypoint == pickup` here. Resolve that forward, or
    the frame shows a robot sitting on a goal it has already reached.
    """
    task = robot.task
    if task is not None:
        if robot.state is RobotState.TO_PICKUP:
            if robot.position == task.pickup:
                return task.delivery, "drop", task.id
            return task.pickup, "pick", task.id
        if robot.state is RobotState.TO_DELIVERY:
            return task.delivery, "drop", task.id
    if robot.state is RobotState.RECOVERY:
        return robot.recovery_vertex, "recover", None
    if robot.parking_vertex is not None and robot.position != robot.parking_vertex:
        return robot.parking_vertex, "park", None
    return None, "idle", None


def simulate(
    map_name: str,
    variant: str,
    robots: int,
    timesteps: int,
    rate: float,
    seed: int,
    flow_window: int,
) -> Tuple[object, List[Frame]]:
    """Run the sim step by step, capturing a rich frame after each step."""
    sim = build_run(
        ROOT / "maps" / f"{map_name}.map",
        variant,
        robots,
        timesteps,
        seed=seed,
        rate=rate,
    )
    wh = sim.warehouse
    graph = wh.graph
    stall = sim.params.stall_steps

    # sliding window of per-aisle signed movement, for the flow arrows
    window: deque = deque(maxlen=flow_window)
    events: deque = deque(maxlen=6)
    frames: List[Frame] = []

    prev_task: Dict[int, Optional[int]] = {r.id: None for r in sim.robots}
    prev_state: Dict[int, RobotState] = {r.id: r.state for r in sim.robots}

    for t in range(timesteps):
        sim.step()

        # -- aisle flow: signed projection of each move onto its aisle axis --
        step_flow: Dict[int, Tuple[float, float]] = {}
        for robot in sim.robots:
            u, v = robot.previous_position, robot.position
            if u == v:
                continue
            aisle_id = wh.aisle_id(u)
            if aisle_id is None:
                continue
            aisle = wh.aisles[aisle_id]
            axis = aisle.axis
            if axis == "col":
                sign = float(v[0] - u[0])
            elif axis == "row":
                sign = float(v[1] - u[1])
            else:
                continue
            got, cnt = step_flow.get(aisle_id, (0.0, 0.0))
            step_flow[aisle_id] = (got + sign, cnt + 1.0)
        window.append(step_flow)

        totals: Dict[int, List[float]] = {}
        for snap in window:
            for aisle_id, (got, cnt) in snap.items():
                acc = totals.setdefault(aisle_id, [0.0, 0.0])
                acc[0] += got
                acc[1] += cnt
        aisle_flow = {
            a: (got / cnt, cnt / max(1.0, len(window)))
            for a, (got, cnt) in totals.items()
            if cnt > 0
        }
        aisle_load = {
            a: aisle.occupancy / max(1, aisle.capacity)
            for a, aisle in wh.aisles.items()
            if aisle.occupancy
        }

        # -- allocation events -------------------------------------------
        for robot in sim.robots:
            tid = robot.task.id if robot.task else None
            was, now = prev_state[robot.id], robot.state
            if tid is not None and prev_task[robot.id] != tid:
                events.append((t, robot.id, "assigned", tid))
            elif (
                was is RobotState.TO_PICKUP and now is RobotState.TO_DELIVERY
            ):
                events.append((t, robot.id, "picked up", tid))
            elif prev_task[robot.id] is not None and tid is None and was is (
                RobotState.TO_DELIVERY
            ):
                events.append((t, robot.id, "delivered", prev_task[robot.id]))
            prev_task[robot.id] = tid
            prev_state[robot.id] = now

        # -- per-robot draw state ----------------------------------------
        pos, goal, kind, task_id, route, blocked = {}, {}, {}, {}, {}, set()
        for robot in sim.robots:
            pos[robot.id] = robot.position
            g, k, tid = goal_of(robot)
            goal[robot.id] = g
            kind[robot.id] = k
            task_id[robot.id] = tid
            route[robot.id] = (
                graph.shortest_route(robot.position, g)
                if g is not None and g != robot.position
                else [robot.position]
            )
            if robot.waiting_time >= stall:
                blocked.add(robot.id)

        frames.append(
            Frame(
                t=t,
                pos=pos,
                goal=goal,
                kind=kind,
                task_id=task_id,
                route=route,
                blocked=blocked,
                completed=len(sim.metrics.completed),
                pending=len(sim.task_queue.pending),
                assigned=sum(1 for r in sim.robots if r.task is not None),
                carrying=sum(1 for r in sim.robots if r.is_loaded),
                aisle_load=aisle_load,
                aisle_flow=aisle_flow,
                events=list(events),
            )
        )
    return sim, frames


# ----------------------------------------------------------------- render
class Renderer:
    def __init__(self, sim, frames: List[Frame], n_robots: int, timesteps: int,
                 title: str, subtitle: str, flow_arrows: bool = False):
        self.sim = sim
        self.frames = frames
        self.wh = sim.warehouse
        self.n = n_robots
        self.T = timesteps
        self.flow_arrows = flow_arrows
        self.colours = robot_palette(n_robots)

        wh = self.wh
        # A short, wide map (the bottleneck floor is 23x7) leaves most of a
        # side-panel frame empty, so pick the layout from the map's aspect:
        # wide maps get the grid across the top and the panels in a band under
        # it, taller ones keep the grid left and the panels down the right.
        self.wide = (wh.width / max(1, wh.height)) >= 2.4
        self.fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=BG)
        if self.wide:
            self.ax = self.fig.add_axes([0.020, 0.415, 0.960, 0.560])
            self.ax_chart = self.fig.add_axes([0.400, 0.085, 0.268, 0.230])
            self.ax_roster = self.fig.add_axes([0.700, 0.085, 0.282, 0.230])
        else:
            self.ax = self.fig.add_axes([0.025, 0.055, 0.600, 0.855])
            self.ax_chart = self.fig.add_axes([0.665, 0.470, 0.315, 0.140])
            self.ax_roster = self.fig.add_axes([0.665, 0.048, 0.315, 0.365])
        for axis in (self.ax, self.ax_chart, self.ax_roster):
            axis.set_facecolor(PANEL)
            axis.set_xticks([])
            axis.set_yticks([])
            for spine in axis.spines.values():
                spine.set_color("#2c3140")

        # ---------------- static grid ----------------------------------
        base = np.zeros((wh.height, wh.width, 3))
        for r in range(wh.height):
            for c in range(wh.width):
                v = (r, c)
                if not wh.graph.contains(v):
                    base[r, c] = CELL_WALL
                    continue
                info = wh.info[v]
                if info.is_pickup_area:
                    base[r, c] = CELL_PICK
                elif info.is_delivery_area:
                    base[r, c] = CELL_DROP
                elif info.is_parking_area:
                    base[r, c] = CELL_PARK
                else:
                    base[r, c] = CELL_FREE
        self.base = base
        self.image = self.ax.imshow(
            base, interpolation="nearest", zorder=0,
            extent=(-0.5, wh.width - 0.5, wh.height - 0.5, -0.5),
        )
        self.ax.set_xlim(-0.75, wh.width - 0.25)
        self.ax.set_ylim(wh.height - 0.25, -1.35)

        for c in range(wh.width + 1):
            self.ax.axvline(c - 0.5, color="#ffffff", lw=0.35, alpha=0.30, zorder=1)
        for r in range(wh.height + 1):
            self.ax.axhline(r - 0.5, color="#ffffff", lw=0.35, alpha=0.30, zorder=1)
        for v in wh.pickup_vertices:
            self._cell_letter(v, "P", "#2f6b3a")
        for v in wh.delivery_vertices:
            self._cell_letter(v, "D", "#2b5384")
        for v in wh.parking_vertices:
            self._cell_letter(v, "K", "#7a6428")

        # ---------------- aisle flow arrows (opt-in) -------------------
        self.arrow_aisles = [] if not flow_arrows else [
            a for a in wh.aisles.values() if a.axis in ("row", "col") and a.length >= 3
        ]
        xs, ys = [], []
        for aisle in self.arrow_aisles:
            rows = [v[0] for v in aisle.vertices]
            cols = [v[1] for v in aisle.vertices]
            ys.append(sum(rows) / len(rows))
            xs.append(sum(cols) / len(cols))
        self.quiver = None if not flow_arrows else self.ax.quiver(
            xs or [0], ys or [0],
            np.zeros(max(1, len(xs))), np.zeros(max(1, len(xs))),
            angles="xy", scale_units="xy", scale=1.0,
            width=0.0045, headwidth=4.2, headlength=4.8,
            color=np.zeros((max(1, len(xs)), 4)), zorder=2,
        )

        # ---------------- dynamic artists ------------------------------
        self.routes = LineCollection([], linewidths=1.8, zorder=3)
        self.ax.add_collection(self.routes)
        self.goal_pick = self.ax.scatter(
            [], [], s=190, marker="*", linewidths=1.2,
            edgecolors="#101218", zorder=4,
        )
        self.goal_drop = self.ax.scatter(
            [], [], s=105, marker="s", linewidths=1.2,
            edgecolors="#101218", zorder=4,
        )
        self.goal_other = self.ax.scatter(
            [], [], s=80, marker="X", linewidths=1.0,
            edgecolors="#101218", zorder=4,
        )
        self.bots = self.ax.scatter(
            [], [], s=330, marker="o", linewidths=1.6,
            edgecolors="#0d0f14", zorder=7,
        )
        self.bot_ids = [
            self.ax.text(0, 0, "", ha="center", va="center", fontsize=7.6,
                         color="white", fontweight="bold", zorder=8,
                         path_effects=[pe.withStroke(linewidth=1.6,
                                                     foreground="#0d0f14")])
            for _ in range(self.n)
        ]
        self.goal_ids = [
            self.ax.text(0, 0, "", ha="center", va="center", fontsize=6.8,
                         color="#101218", fontweight="bold", zorder=5,
                         path_effects=[pe.withStroke(linewidth=1.8,
                                                     foreground="white")])
            for _ in range(self.n)
        ]

        self.headline = self.ax.text(
            -0.5, -1.02, "", fontsize=13, color=INK, fontweight="bold", va="center",
        )
        self.subhead = self.ax.text(
            wh.width - 0.5, -1.02, subtitle, fontsize=8.5, color=DIM,
            va="center", ha="right",
        )
        self.title = title

        # ---------------- side panels ----------------------------------
        self._build_chart()
        self._build_roster()
        self._build_hud()
        self._build_legend()

    # ---------------------------------------------------------- helpers
    def _cell_letter(self, v: Vertex, ch: str, colour: str) -> None:
        self.ax.text(v[1], v[0], ch, ha="center", va="center", fontsize=6.5,
                     color=colour, alpha=0.85, zorder=2)

    def _build_hud(self) -> None:
        heading = "CAAP  ·  full lifelong pipeline"
        strap = "task arrival → allocation → routing → PIBT"
        if self.flow_arrows:
            strap += " → aisle flow"
        if self.wide:
            self.fig.text(0.020, 0.378, heading, fontsize=13, color=INK,
                          fontweight="bold", va="top")
            self.fig.text(0.020, 0.352, strap, fontsize=8.5, color=DIM,
                          va="top")
            self.hud = self.fig.text(
                0.020, 0.322, "", fontsize=10.0, color=INK, va="top",
                family="monospace", linespacing=1.55,
            )
            self.fig.text(0.220, 0.322, "recent events", fontsize=8.5,
                          color=DIM, va="top", fontweight="bold")
            self.ticker = self.fig.text(
                0.220, 0.297, "", fontsize=7.4, color=DIM, va="top",
                family="monospace", linespacing=1.5,
            )
            return
        self.fig.text(0.665, 0.958, heading, fontsize=13, color=INK,
                      fontweight="bold", va="top")
        self.fig.text(0.665, 0.930, strap, fontsize=8.5, color=DIM, va="top")
        self.hud = self.fig.text(
            0.665, 0.893, "", fontsize=10.0, color=INK, va="top",
            family="monospace", linespacing=1.55,
        )
        self.fig.text(0.665, 0.723, "recent events", fontsize=8.5, color=DIM,
                      va="top", fontweight="bold")
        self.ticker = self.fig.text(
            0.665, 0.701, "", fontsize=7.4, color=DIM, va="top",
            family="monospace", linespacing=1.5,
        )

    def _build_chart(self) -> None:
        ax = self.ax_chart
        ax.set_xlim(0, self.T)
        top = max(f.completed for f in self.frames) or 1
        top = max(top, max(f.pending for f in self.frames))
        ax.set_ylim(0, top * 1.12)
        ax.tick_params(colors=DIM, labelsize=7)
        ax.set_xticks(np.linspace(0, self.T, 6))
        ax.set_yticks(np.linspace(0, int(top * 1.12), 4).astype(int))
        ax.grid(color="#2c3140", lw=0.5)
        (self.line_done,) = ax.plot([], [], color="#4fd18b", lw=2.0,
                                    label="delivered")
        (self.line_queue,) = ax.plot([], [], color="#f0a35e", lw=1.6,
                                     label="queue")
        self.cursor = ax.axvline(0, color=DIM, lw=0.9, alpha=0.7)
        leg = ax.legend(loc="upper left", fontsize=7.5, frameon=False)
        for text in leg.get_texts():
            text.set_color(DIM)
        ax.set_title("tasks over the run", fontsize=8.5, color=DIM, pad=4)

    def _build_roster(self) -> None:
        ax = self.ax_roster
        ax.set_xlim(0, 3)
        ax.set_ylim(0, 1)
        ax.set_title("robots · id → task · stage", fontsize=8.5, color=DIM, pad=4)
        # a small fleet in three columns leaves the panel mostly empty
        ncols = 1 if self.n <= 6 else 2 if self.n <= 12 else 3
        rows = (self.n + ncols - 1) // ncols
        ax.set_xlim(0, ncols)
        self.roster_text: List = []
        self.roster_chip: List = []
        for i in range(self.n):
            col, row = divmod(i, rows)
            x = col + 0.06
            y = 0.955 - (row + 0.5) / max(rows, 1) * 0.94
            chip = Rectangle((x, y - 0.018), 0.10, 0.036,
                             facecolor=self.colours[i], edgecolor="none")
            ax.add_patch(chip)
            self.roster_chip.append(chip)
            self.roster_text.append(
                ax.text(x + 0.14, y, "", fontsize=7.0, color=INK, va="center",
                        family="monospace")
            )

    def _build_legend(self) -> None:
        parts = [
            "circle = robot (id inside)",
            "★ = its pickup goal",
            "■ = its delivery goal",
            "line = the route it is following (same colour)",
            "red ring = stalled",
            "red tint = crowded aisle",
        ]
        if self.flow_arrows:
            parts.append("arrow = measured aisle flow")
        self.fig.text(
            0.020, 0.394 if self.wide else 0.012, "   ".join(parts),
            fontsize=8.0, color=DIM, va="bottom",
        )

    # ----------------------------------------------------------- drawing
    def draw(self, index: int) -> None:
        frame = self.frames[index]
        wh = self.wh

        # crowding tint
        canvas = self.base.copy()
        for aisle_id, load in frame.aisle_load.items():
            k = 0.55 * min(1.0, load)
            for r, c in wh.aisles[aisle_id].vertices:
                canvas[r, c] = canvas[r, c] * (1 - k) + CROWD * k
        self.image.set_data(canvas)

        # aisle flow arrows
        U, V, C = [], [], []
        for aisle in self.arrow_aisles if self.quiver is not None else ():
            flow, density = frame.aisle_flow.get(aisle.id, (0.0, 0.0))
            mag = min(1.0, abs(flow)) * min(1.0, density * 2.2)
            if mag < 0.12:
                U.append(0.0)
                V.append(0.0)
                C.append((0, 0, 0, 0))
                continue
            length = 0.7 + 1.1 * mag
            sign = 1.0 if flow > 0 else -1.0
            if aisle.axis == "col":
                U.append(0.0)
                V.append(sign * length)
            else:
                U.append(sign * length)
                V.append(0.0)
            C.append((0.10, 0.16, 0.30, 0.28 + 0.34 * mag))
        if U and self.quiver is not None:
            self.quiver.set_UVC(np.array(U), np.array(V))
            self.quiver.set_color(np.array(C))

        # routes, goals, robots
        segments, seg_colours = [], []
        gp, gpc, gd, gdc, go, goc = [], [], [], [], [], []
        bx, by, bcol, bedge, bwidth = [], [], [], [], []

        for rid in range(self.n):
            colour = self.colours[rid]
            r, c = frame.pos[rid]
            bx.append(c)
            by.append(r)
            bcol.append(colour)
            stalled = rid in frame.blocked
            bedge.append((0.95, 0.25, 0.22) if stalled else (0.05, 0.06, 0.08))
            bwidth.append(2.6 if stalled else 1.4)

            self.bot_ids[rid].set_position((c, r))
            self.bot_ids[rid].set_text(f"{rid}")

            path = frame.route[rid]
            if len(path) > 1:
                # nudge parallel routes apart so a shared corridor is readable
                off = ((rid % 7) - 3) * 0.085
                pts = [(v[1] + off, v[0] + off) for v in path]
                segments.append(pts)
                seg_colours.append(colour + (0.72,))

            goal = frame.goal[rid]
            kind = frame.kind[rid]
            if goal is None:
                self.goal_ids[rid].set_text("")
                continue
            self.goal_ids[rid].set_position((goal[1] + 0.30, goal[0] - 0.30))
            self.goal_ids[rid].set_text(f"{rid}")
            if kind == "pick":
                gp.append((goal[1], goal[0]))
                gpc.append(colour)
            elif kind == "drop":
                gd.append((goal[1], goal[0]))
                gdc.append(colour)
            else:
                go.append((goal[1], goal[0]))
                goc.append(colour)

        self.routes.set_segments([np.array(s) for s in segments])
        self.routes.set_color(seg_colours)
        for scatter, pts, cols in (
            (self.goal_pick, gp, gpc),
            (self.goal_drop, gd, gdc),
            (self.goal_other, go, goc),
        ):
            scatter.set_offsets(np.array(pts) if pts else np.empty((0, 2)))
            scatter.set_facecolor(cols if cols else "none")
        self.bots.set_offsets(np.column_stack([bx, by]))
        self.bots.set_facecolor(bcol)
        self.bots.set_edgecolor(bedge)
        self.bots.set_linewidth(bwidth)

        # headline + hud
        self.headline.set_text(f"{self.title}    t = {frame.t:4d} / {self.T}")
        per_1000 = 1000.0 * frame.completed / max(1, frame.t + 1)
        self.hud.set_text(
            f"delivered   {frame.completed:5d}\n"
            f"queue       {frame.pending:5d}\n"
            f"assigned    {frame.assigned:5d} / {self.n}\n"
            f"carrying    {frame.carrying:5d}\n"
            f"stalled     {len(frame.blocked):5d}\n"
            f"throughput  {per_1000:7.1f} / 1000 steps"
        )
        lines = [
            f"t={t:<4d} R{rid:<2d} {what:<9s} "
            f"{('T' + str(tid)) if tid is not None else '':>5s}"
            for t, rid, what, tid in list(reversed(frame.events))[:4]
        ]
        self.ticker.set_text("\n".join(lines))

        # chart
        upto = self.frames[: index + 1]
        xs = [f.t for f in upto]
        self.line_done.set_data(xs, [f.completed for f in upto])
        self.line_queue.set_data(xs, [f.pending for f in upto])
        self.cursor.set_xdata([frame.t, frame.t])

        # roster
        for rid in range(self.n):
            tid = frame.task_id[rid]
            kind = frame.kind[rid]
            stage = {"pick": "PICK", "drop": "DROP", "park": "park",
                     "recover": "RECOV", "idle": "idle"}[kind]
            label = f"T{tid}" if tid is not None else "--"
            self.roster_text[rid].set_text(f"{rid:>2d} {label:<5s} {stage}")
            self.roster_text[rid].set_color(
                "#ff6b5e" if rid in frame.blocked else INK
            )

    def rgba(self) -> np.ndarray:
        self.fig.canvas.draw()
        return np.asarray(self.fig.canvas.buffer_rgba())


# ------------------------------------------------------------------ output
def write_mp4(renderer: Renderer, path: Path, fps: int, stride: int) -> Path:
    frame = renderer.rgba()
    h, w = frame.shape[:2]
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "-", "-an", "-vcodec", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "20", "-movflags", "+faststart", str(path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    started = time.time()
    indices = range(0, len(renderer.frames), stride)
    total = len(list(indices))
    for n, i in enumerate(range(0, len(renderer.frames), stride)):
        renderer.draw(i)
        proc.stdin.write(renderer.rgba().tobytes())
        if n % 50 == 0:
            print(f"    mp4 {n:4d}/{total}  ({time.time() - started:5.1f}s)",
                  flush=True)
    proc.stdin.close()
    proc.wait()
    return path


def write_gif(renderer: Renderer, path: Path, fps: int, stride: int,
              scale: float, colours: int = 128) -> Path:
    from PIL import Image

    images = []
    started = time.time()
    total = len(range(0, len(renderer.frames), stride))
    for n, i in enumerate(range(0, len(renderer.frames), stride)):
        renderer.draw(i)
        img = Image.fromarray(renderer.rgba()).convert("RGB")
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)), Image.LANCZOS
        )
        images.append(img.quantize(colors=colours, method=Image.MEDIANCUT))
        if n % 50 == 0:
            print(f"    gif {n:4d}/{total}  ({time.time() - started:5.1f}s)",
                  flush=True)
    images[0].save(
        path, save_all=True, append_images=images[1:],
        duration=int(1000 / fps), loop=0, optimize=True, disposal=2,
    )
    return path


# -------------------------------------------------------------------- main
def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--map", default="warehouse_medium")
    p.add_argument("--variant", default="full_caap")
    p.add_argument("--robots", type=int, default=24)
    p.add_argument("--timesteps", type=int, default=1000)
    p.add_argument("--rate", type=float, default=0.35)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--flow-window", type=int, default=40)
    p.add_argument("--fps", type=int, default=20)
    p.add_argument("--duration", type=float, default=None,
                   help="total seconds for both outputs; overrides --fps and "
                        "--gif-fps, which are then derived from the frame count")
    p.add_argument("--flow-arrows", action="store_true",
                   help="draw the measured per-aisle flow direction "
                        "(off by default)")
    p.add_argument("--mp4-stride", type=int, default=1)
    p.add_argument("--gif-stride", type=int, default=5)
    p.add_argument("--gif-fps", type=int, default=12)
    p.add_argument("--gif-scale", type=float, default=0.45)
    p.add_argument("--gif-colors", type=int, default=128)
    p.add_argument("--out", default=str(ROOT / "docs" / "gifs"))
    p.add_argument("--name", default="caap-full-run")
    p.add_argument("--no-gif", action="store_true")
    p.add_argument("--no-mp4", action="store_true")
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"simulating {args.variant} on {args.map}: {args.robots} robots, "
          f"rate {args.rate}, {args.timesteps} steps, seed {args.seed}")
    started = time.time()
    sim, frames = simulate(
        args.map, args.variant, args.robots, args.timesteps,
        args.rate, args.seed, args.flow_window,
    )
    last = frames[-1]
    print(f"  {time.time() - started:.1f}s  delivered={last.completed}  "
          f"queue={last.pending}  "
          f"throughput={1000 * last.completed / args.timesteps:.1f}/1000")

    renderer = Renderer(
        sim, frames, args.robots, args.timesteps,
        title=f"{display_name(args.variant)} · {args.map}",
        subtitle=(f"{args.robots} robots · {args.rate} tasks/step · seed "
                  f"{args.seed} · lifelong MAPD"),
        flow_arrows=args.flow_arrows,
    )

    # a requested wall-clock duration fixes the frame rate, not the other way
    # round: the frame count is already decided by the run and the stride
    mp4_fps, gif_fps = args.fps, args.gif_fps
    if args.duration:
        n_mp4 = len(range(0, len(frames), args.mp4_stride))
        n_gif = len(range(0, len(frames), args.gif_stride))
        mp4_fps = max(1, round(n_mp4 / args.duration))
        gif_fps = max(1, round(n_gif / args.duration))
        print(f"  duration {args.duration:.0f}s -> mp4 {mp4_fps} fps "
              f"({n_mp4} frames), gif {gif_fps} fps ({n_gif} frames)")

    written: List[Path] = []
    if not args.no_mp4:
        path = write_mp4(renderer, out_dir / f"{args.name}.mp4",
                         mp4_fps, args.mp4_stride)
        print(f"  -> {path}  ({path.stat().st_size / 1e6:.2f} MB)")
        written.append(path)
    if not args.no_gif:
        path = write_gif(renderer, out_dir / f"{args.name}.gif",
                         gif_fps, args.gif_stride, args.gif_scale,
                         args.gif_colors)
        print(f"  -> {path}  ({path.stat().st_size / 1e6:.2f} MB)")
        written.append(path)

    plt.close(renderer.fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
