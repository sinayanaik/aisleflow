"""Evaluation metrics (spec section 36)."""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .robot import Robot
from .task import Task, TaskQueue


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0, 100])."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * (q / 100.0)
    lower = int(pos)
    upper = min(lower + 1, len(ordered) - 1)
    frac = pos - lower
    return float(ordered[lower] * (1 - frac) + ordered[upper] * frac)


def jain_fairness(values: Sequence[float]) -> float:
    r"""J = (sum T_i)^2 / (n * sum T_i^2)  (spec 36.7)."""
    if not values:
        return 1.0
    total = sum(values)
    square_total = sum(v * v for v in values)
    if square_total == 0:
        return 1.0
    return (total * total) / (len(values) * square_total)


@dataclass
class TimestepRecord:
    timestep: int
    completed_tasks: int
    pending_tasks: int
    moving_robots: int
    idle_robots: int
    blocked_robots: int
    aisle_states: Dict[str, int] = field(default_factory=dict)
    runtime_ms: float = 0.0


@dataclass
class MetricsReport:
    """Aggregated run statistics."""

    timesteps: int = 0
    completed_tasks: int = 0
    released_tasks: int = 0
    throughput: float = 0.0
    makespan: Optional[int] = None
    mean_service_time: float = 0.0
    median_service_time: float = 0.0
    p95_service_time: float = 0.0
    max_service_time: float = 0.0
    total_travel_distance: int = 0
    mean_travel_distance: float = 0.0
    total_waiting_steps: int = 0
    max_waiting_time: int = 0
    direction_switches: int = 0
    direction_switches_per_1000: float = 0.0
    deadlocks_detected: int = 0
    deadlocks_recovered: int = 0
    deadlocks_unrecovered: int = 0
    mean_recovery_time: float = 0.0
    jain_fairness: float = 1.0
    pibt_recursive_calls: int = 0
    pibt_backtracks: int = 0
    pibt_invalid_results: int = 0
    candidate_evaluations: int = 0
    mean_runtime_ms_per_step: float = 0.0
    max_runtime_ms_per_step: float = 0.0
    collision_free: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))

    def summary_lines(self) -> List[str]:
        return [
            f"timesteps                 : {self.timesteps}",
            f"completed tasks           : {self.completed_tasks}",
            f"throughput (tasks/step)   : {self.throughput:.4f}",
            f"service time mean/med/p95 : "
            f"{self.mean_service_time:.1f} / {self.median_service_time:.1f} / "
            f"{self.p95_service_time:.1f}",
            f"max service time          : {self.max_service_time:.0f}",
            f"travel distance (total)   : {self.total_travel_distance}",
            f"direction switches /1000  : {self.direction_switches_per_1000:.2f}",
            f"deadlocks det/rec/unrec   : {self.deadlocks_detected} / "
            f"{self.deadlocks_recovered} / {self.deadlocks_unrecovered}",
            f"Jain fairness             : {self.jain_fairness:.4f}",
            f"max waiting time          : {self.max_waiting_time}",
            f"PIBT calls / backtracks   : {self.pibt_recursive_calls} / "
            f"{self.pibt_backtracks}",
            f"runtime mean/max per step : {self.mean_runtime_ms_per_step:.2f} ms / "
            f"{self.max_runtime_ms_per_step:.2f} ms",
            f"collision free            : {self.collision_free}",
        ]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return "\n".join(self.summary_lines())


class MetricsCollector:
    """Accumulates per-timestep records and produces a `MetricsReport`."""

    def __init__(self) -> None:
        self.records: List[TimestepRecord] = []
        self.completed: List[Task] = []

    def log_timestep(self, record: TimestepRecord) -> None:
        self.records.append(record)

    def record_completion(self, task: Task) -> None:
        self.completed.append(task)

    def build(
        self,
        robots: Sequence[Robot],
        task_queue: TaskQueue,
        timesteps: int,
        extra: Dict[str, Any],
    ) -> MetricsReport:
        service_times = [
            float(t.service_time)
            for t in self.completed
            if t.service_time is not None
        ]
        completion_times = [
            t.completion_time for t in self.completed if t.completion_time is not None
        ]
        runtimes = [r.runtime_ms for r in self.records]
        travel = sum(r.travel_distance for r in robots)
        per_robot_tasks = [float(r.completed_tasks) for r in robots]

        report = MetricsReport(
            timesteps=timesteps,
            completed_tasks=len(self.completed),
            released_tasks=len(task_queue),
            throughput=len(self.completed) / max(1, timesteps),
            makespan=max(completion_times) if completion_times else None,
            mean_service_time=statistics.fmean(service_times) if service_times else 0.0,
            median_service_time=(
                statistics.median(service_times) if service_times else 0.0
            ),
            p95_service_time=percentile(service_times, 95.0),
            max_service_time=max(service_times) if service_times else 0.0,
            total_travel_distance=travel,
            mean_travel_distance=travel / max(1, len(robots)),
            total_waiting_steps=sum(r.waiting_time for r in robots),
            max_waiting_time=max((r.waiting_time for r in robots), default=0),
            jain_fairness=jain_fairness(per_robot_tasks),
            mean_runtime_ms_per_step=statistics.fmean(runtimes) if runtimes else 0.0,
            max_runtime_ms_per_step=max(runtimes) if runtimes else 0.0,
        )
        for key, value in extra.items():
            if hasattr(report, key):
                setattr(report, key, value)
        report.direction_switches_per_1000 = (
            1000.0 * report.direction_switches / max(1, timesteps)
        )
        return report


__all__ = [
    "MetricsCollector",
    "MetricsReport",
    "TimestepRecord",
    "percentile",
    "jain_fairness",
]
