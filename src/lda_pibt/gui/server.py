"""Browser GUI backend.

A dependency-free control surface for the simulator, built on `http.server`.
The browser owns the clock: it asks the server to advance N timesteps and gets
the resulting state back, so playback speed, pausing and stepping all work
without any streaming machinery.

Start it with ``lda-pibt gui maps/warehouse_medium.map -n 40``.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from ..config import ABLATIONS, Params, ablation
from ..simulator import Simulator, build_simulator
from ..task import TaskGenerator
from ..types import PlanningError, RobotState
from ..warehouse import Warehouse

STATIC = Path(__file__).parent / "static"

#: Fields the GUI exposes as live sliders / inputs. Plain names, because the
#: Greek was never doing the reader a favour -- see docs/03-the-math.md for
#: the formal symbols.
TUNABLE = [
    ("progress_reward", "progress reward", 0.0, 20.0, 0.5),
    ("aisle_bonus", "stay-in-aisle bonus", 0.0, 10.0, 0.5),
    ("aisle_bonus_near", "  ...near the waypoint", 0.0, 10.0, 0.5),
    ("turn_penalty", "turn penalty", 0.0, 5.0, 0.1),
    ("reverse_multiplier", "reverse costs N turns", 1.0, 6.0, 0.5),
    ("crowding_penalty", "crowding penalty", 0.0, 5.0, 0.1),
    ("local_congestion_radius", "crowding radius", 1, 10, 1),
    ("r_near", "near distance", 0, 20, 1),
    ("r_far", "far distance", 1, 40, 1),
    ("aisle_capacity", "aisle capacity", 1, 30, 1),
    ("max_drain_time", "max drain time", 1, 120, 1),
    ("priority_class_spread", "priority class spread", 0.0, 400.0, 10.0),
    ("waiting_weight", "rank per step waited", 0.0, 40.0, 0.5),
    ("stall_steps", "steps before stalled", 1, 60, 1),
    ("recovery_max_level", "recovery ladder depth", 0, 5, 1),
    ("cost_congestion", "assignment: crowding", 0.0, 40.0, 1.0),
]

TOGGLES = [
    ("congestion_scoring", "crowding in movement"),
    ("congestion_assignment", "crowding in matching"),
    ("recovery", "deadlock recovery"),
    ("require_deadlock_corroboration", "corroborated deadlocks"),
    ("turning_cost", "turning cost"),
    ("park_when_idle", "park when idle"),
]


class SimulationSession:
    """Holds one simulator and rebuilds it when the configuration changes."""

    def __init__(
        self,
        map_path: Path,
        n_robots: int = 20,
        variant: str = "full_lda_pibt",
        rate: float = 1.0,
        arrival: str = "poisson",
        seed: int = 0,
        overrides: Optional[Dict[str, Any]] = None,
    ):
        self.lock = threading.Lock()
        self.map_dir = map_path.parent
        self.config = {
            "map": map_path.name,
            "n_robots": n_robots,
            "variant": variant,
            "rate": rate,
            "arrival": arrival,
            "seed": seed,
            "overrides": overrides or {},
        }
        self.error: Optional[str] = None
        self.sim: Simulator = self._build()

    # ------------------------------------------------------------- building
    def _build(self) -> Simulator:
        cfg = self.config
        params = ablation(
            cfg["variant"],
            Params(seed=int(cfg["seed"])),
            **cfg["overrides"],
        )
        warehouse = Warehouse.from_file(self.map_dir / cfg["map"], params)
        generator = None
        if params.lifelong and warehouse.pickup_vertices and warehouse.delivery_vertices:
            generator = TaskGenerator(
                warehouse.pickup_vertices,
                warehouse.delivery_vertices,
                mode=cfg["arrival"],
                rate=float(cfg["rate"]),
                seed=int(cfg["seed"]),
            )
        self.error = None
        return build_simulator(
            warehouse,
            int(cfg["n_robots"]),
            params,
            task_generator=generator,
        )

    def reconfigure(self, changes: Dict[str, Any]) -> None:
        with self.lock:
            overrides = dict(self.config["overrides"])
            overrides.update(changes.pop("overrides", {}) or {})
            self.config.update(changes)
            self.config["overrides"] = overrides
            self.sim = self._build()

    def reset(self) -> None:
        with self.lock:
            self.sim = self._build()

    # -------------------------------------------------------------- driving
    def advance(self, steps: int) -> None:
        with self.lock:
            for _ in range(max(0, steps)):
                try:
                    self.sim.step()
                except PlanningError as exc:  # pragma: no cover - safety net
                    self.error = str(exc)
                    return

    # --------------------------------------------------------------- layout
    def layout(self) -> Dict[str, Any]:
        """Static data: sent once, never changes for a given map."""
        wh = self.sim.warehouse
        return {
            "width": wh.width,
            "height": wh.height,
            "grid": wh.grid,
            "passable": [
                [wh.graph.contains((r, c)) for c in range(wh.width)]
                for r in range(wh.height)
            ],
            "cells": {
                "pickup": [list(v) for v in wh.pickup_vertices],
                "delivery": [list(v) for v in wh.delivery_vertices],
                "parking": [list(v) for v in wh.parking_vertices],
                "intersection": [list(v) for v in sorted(wh.graph.intersections)],
                "bottleneck": [
                    list(v) for v, info in wh.info.items() if info.is_bottleneck
                ],
            },
            "aisles": [
                {
                    "id": a.id,
                    "vertices": [list(v) for v in a.vertices],
                    "capacity": a.capacity,
                }
                for a in wh.aisles.values()
            ],
            "summary": {k: str(v) for k, v in wh.summary().items()},
        }

    def options(self) -> Dict[str, Any]:
        return {
            "maps": sorted(p.name for p in self.map_dir.glob("*.map")),
            "variants": sorted(ABLATIONS),
            "arrivals": ["poisson", "periodic", "bursty", "batch"],
            "tunable": [
                {"key": k, "label": l, "min": lo, "max": hi, "step": st}
                for k, l, lo, hi, st in TUNABLE
            ],
            "toggles": [{"key": k, "label": l} for k, l in TOGGLES],
        }

    # ---------------------------------------------------------------- state
    def state(self) -> Dict[str, Any]:
        sim = self.sim
        wh = sim.warehouse
        params = sim.params
        record = sim.metrics.records[-1] if sim.metrics.records else None
        report = sim.report()

        robots = []
        for robot in sim.robots:
            # The planner route is produced before synchronized execution in
            # ``Simulator.step``.  Rebuild the presentation route from the
            # robot's *current* position so the GUI line never appears one
            # cell behind the robot after a step.
            display_route = sim.warehouse.graph.shortest_route(
                robot.position, robot.waypoint
            )
            robots.append(
                {
                    "id": robot.id,
                    "pos": list(robot.position),
                    "prev": list(robot.previous_position or robot.position),
                    "state": robot.state.value,
                    "waypoint": list(robot.waypoint) if robot.waypoint else None,
                    "route": [list(v) for v in display_route],
                    "aisle": robot.current_aisle,
                    "priority": round(robot.priority, 3),
                    "waiting": robot.waiting_time,
                    "blocked": robot.blocked_time,
                    "stalled": robot.no_progress_steps,
                    "mode": robot.mode.value,
                    "task": None if robot.task is None else robot.task.id,
                    "completed": robot.completed_tasks,
                    "travel": robot.travel_distance,
                    "waiting_for": (
                        robot.waiting_for_robot.id
                        if robot.waiting_for_robot is not None
                        else None
                    ),
                    "is_stuck": sim.deadlocks.is_blocked(robot),
                }
            )

        aisles = []
        for aisle in wh.aisles.values():
            aisles.append(
                {
                    "id": aisle.id,
                    "occupancy": aisle.occupancy,
                    "capacity": aisle.capacity,
                    "axis": aisle.axis,
                }
            )

        history = sim.metrics.records[-240:]
        return {
            "timestep": sim.timestep,
            "error": self.error,
            "config": self.config,
            "params": {
                k: getattr(params, k)
                for k, *_ in TUNABLE + [(t[0],) for t in TOGGLES]
            },
            "robots": robots,
            "aisles": aisles,
            "metrics": {
                "completed": report.completed_tasks,
                "pending": len(sim.task_queue.pending),
                "throughput": round(report.throughput, 4),
                "mean_service": round(report.mean_service_time, 1),
                "p95_service": round(report.p95_service_time, 1),
                "max_wait": report.max_waiting_time,
                "travel": report.total_travel_distance,
                "head_on": report.head_on_conflicts,
                "deadlocks": report.deadlocks_detected,
                "recovered": report.deadlocks_recovered,
                "fairness": round(report.jain_fairness, 3),
                "backtracks": report.pibt_backtracks,
                "ms": round(record.runtime_ms, 2) if record else 0.0,
                "moving": record.moving_robots if record else 0,
                "blocked": record.blocked_robots if record else 0,
                "collision_free": report.collision_free,
            },
            "hypotheses": self._hypothesis_rows(report),
            "series": {
                "t": [r.timestep for r in history],
                "completed": [r.completed_tasks for r in history],
                "moving": [r.moving_robots for r in history],
                "blocked": [r.blocked_robots for r in history],
            },
        }

    def _hypothesis_rows(self, report) -> List[Dict[str, Any]]:
        """Live value of the metric each surviving mechanism is judged on.

        The offline study compares these against a control run; here they are
        shown for the configuration on screen, so flipping a toggle and
        watching the row move is the same evidence the tables report.
        """
        params = self.sim.params
        return [
            {
                "key": "lane",
                "mechanism": "stay-in-lane bonus",
                "metric": "head-on conflicts",
                "value": report.head_on_conflicts,
                "active": params.aisle_bonus > 0.0,
                "better": "lower",
            },
            {
                "key": "crowd",
                "mechanism": "crowding in movement + matching",
                "metric": "throughput",
                "value": round(report.throughput, 4),
                "active": params.congestion_scoring or params.congestion_assignment,
                "better": "higher",
            },
            {
                "key": "recover",
                "mechanism": "corroborated deadlock recovery",
                "metric": "unrecovered deadlocks",
                "value": report.deadlocks_unrecovered,
                "active": params.recovery,
                "better": "lower",
            },
            {
                "key": "turn",
                "mechanism": "turning cost",
                "metric": "total travel distance",
                "value": report.total_travel_distance,
                "active": params.turning_cost,
                "better": "lower",
            },
        ]

    def inspect_robot(self, robot_id: int) -> Dict[str, Any]:
        sim = self.sim
        robot = sim.robots_by_id.get(robot_id)
        if robot is None:
            return {"error": f"no robot {robot_id}"}
        timestep = max(0, sim.timestep - 1)
        return {
            "id": robot.id,
            "candidates": sim.planner.explain_candidates(robot, timestep),
            "direction_weight": round(robot.direction_weight, 3),
            "aisle_bonus": round(robot.aisle_bonus, 3),
            "route_distance": (
                None
                if robot.route_distance_to_waypoint == float("inf")
                else robot.route_distance_to_waypoint
            ),
        }

    def heatmap(self, kind: str) -> List[List[float]]:
        sim = self.sim
        wh = sim.warehouse
        grid = [[0.0] * wh.width for _ in range(wh.height)]
        if kind == "congestion":
            for v in wh.graph.vertices:
                grid[v[0]][v[1]] = float(sim.index.local_density(v))
        elif kind == "stall":
            for robot in sim.robots:
                r, c = robot.position
                grid[r][c] = float(robot.no_progress_steps)
        elif kind == "priority":
            for robot in sim.robots:
                r, c = robot.position
                grid[r][c] = float(robot.priority)
        return grid


class Handler(BaseHTTPRequestHandler):
    session: SimulationSession  # injected by `serve`

    def log_message(self, *args) -> None:  # silence per-request logging
        pass

    # ------------------------------------------------------------- plumbing
    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        kind = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript",
            ".css": "text/css",
        }.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        session = self.session
        if url.path in ("/", "/index.html"):
            self._send_file(STATIC / "index.html")
        elif url.path == "/api/init":
            self._send_json(
                {
                    "layout": session.layout(),
                    "options": session.options(),
                    "state": session.state(),
                }
            )
        elif url.path == "/api/state":
            self._send_json(session.state())
        elif url.path == "/api/robot":
            self._send_json(session.inspect_robot(int(query["id"][0])))
        elif url.path == "/api/heatmap":
            self._send_json({"grid": session.heatmap(query.get("kind", ["none"])[0])})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        session = self.session
        body = self._body()
        if url.path == "/api/step":
            session.advance(int(body.get("n", 1)))
            self._send_json(session.state())
        elif url.path == "/api/reset":
            session.reset()
            self._send_json({"layout": session.layout(), "state": session.state()})
        elif url.path == "/api/config":
            try:
                session.reconfigure(body)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json({"layout": session.layout(), "state": session.state()})
        else:
            self.send_error(404)


def serve(
    session: SimulationSession,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
) -> None:
    """Block, serving the GUI. Ctrl-C to stop."""
    handler = type("BoundHandler", (Handler,), {"session": session})
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"LDA-PIBT GUI on {url}   (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


__all__ = ["SimulationSession", "serve", "TUNABLE", "TOGGLES"]
