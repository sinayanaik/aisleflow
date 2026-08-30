"""Tasks, task queue and task arrival processes (spec sections 7.2, 8, 35.4)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from .types import INF, TaskStatus, Vertex


@dataclass
class Task:
    """A pickup-and-delivery task (spec section 7.2)."""

    id: int
    pickup: Vertex
    delivery: Vertex
    release_time: int = 0
    assignment: Optional[int] = None
    status: TaskStatus = TaskStatus.UNASSIGNED
    deadline: float = INF
    priority: float = 0.0
    pickup_time: Optional[int] = None
    completion_time: Optional[int] = None

    @property
    def service_time(self) -> Optional[int]:
        if self.completion_time is None:
            return None
        return self.completion_time - self.release_time

    def urgency(self, timestep: int) -> float:
        """Higher when the deadline is close (spec 21)."""
        if self.deadline == INF:
            return 0.0
        slack = self.deadline - timestep
        return 1.0 / max(1.0, slack)

    def waiting_time(self, timestep: int) -> int:
        return max(0, timestep - self.release_time)


class TaskQueue:
    """Container for all tasks, indexed by status for O(1)-ish scans."""

    def __init__(self, tasks: Iterable[Task] = ()):
        self.tasks: Dict[int, Task] = {}
        self._unassigned: List[Task] = []
        for task in tasks:
            self.add(task)

    def add(self, task: Task) -> None:
        self.tasks[task.id] = task
        if task.status is TaskStatus.UNASSIGNED:
            self._unassigned.append(task)

    def add_all(self, tasks: Iterable[Task]) -> None:
        for task in tasks:
            self.add(task)

    def available(self, timestep: int) -> List[Task]:
        """Released, still-unassigned tasks (spec 19.2)."""
        self._unassigned = [
            t for t in self._unassigned if t.status is TaskStatus.UNASSIGNED
        ]
        return [t for t in self._unassigned if t.release_time <= timestep]

    def mark_assigned(self, task: Task, robot_id: int) -> None:
        task.assignment = robot_id
        task.status = TaskStatus.TO_PICKUP

    @property
    def completed(self) -> List[Task]:
        return [t for t in self.tasks.values() if t.status is TaskStatus.COMPLETED]

    @property
    def pending(self) -> List[Task]:
        return [t for t in self.tasks.values() if t.status is not TaskStatus.COMPLETED]

    def __len__(self) -> int:
        return len(self.tasks)

    def __iter__(self):
        return iter(self.tasks.values())


class TaskGenerator:
    """Task arrival process (spec section 35.4).

    Modes:
      * ``periodic`` - `rate` tasks every `period` timesteps
      * ``poisson``  - Bernoulli/Poisson-like random arrivals with mean `rate`
      * ``bursty``   - quiet stretches punctuated by bursts
      * ``batch``    - a single finite batch released at t = 0
    """

    def __init__(
        self,
        pickups: Sequence[Vertex],
        deliveries: Sequence[Vertex],
        mode: str = "poisson",
        rate: float = 1.0,
        period: int = 5,
        burst_size: int = 12,
        burst_period: int = 40,
        total: Optional[int] = None,
        seed: int = 0,
        deadline_slack: Optional[int] = None,
    ):
        if not pickups or not deliveries:
            raise ValueError("need at least one pickup and one delivery vertex")
        self.pickups = list(pickups)
        self.deliveries = list(deliveries)
        self.mode = mode
        self.rate = rate
        self.period = max(1, period)
        self.burst_size = burst_size
        self.burst_period = max(1, burst_period)
        self.total = total
        self.deadline_slack = deadline_slack
        self.rng = random.Random(seed)
        self._next_id = 0
        self._emitted = 0

    def _make(self, timestep: int) -> Task:
        pickup = self.rng.choice(self.pickups)
        delivery = self.rng.choice(self.deliveries)
        deadline = (
            timestep + self.deadline_slack if self.deadline_slack is not None else INF
        )
        task = Task(
            id=self._next_id,
            pickup=pickup,
            delivery=delivery,
            release_time=timestep,
            deadline=deadline,
        )
        self._next_id += 1
        self._emitted += 1
        return task

    def receive_new_tasks(self, timestep: int) -> List[Task]:
        if self.total is not None and self._emitted >= self.total:
            return []
        count = 0
        if self.mode == "periodic":
            count = int(self.rate) if timestep % self.period == 0 else 0
        elif self.mode == "poisson":
            count = _poisson(self.rng, self.rate)
        elif self.mode == "bursty":
            count = self.burst_size if timestep % self.burst_period == 0 else 0
        elif self.mode == "batch":
            count = (self.total or 0) if timestep == 0 else 0
        else:
            raise ValueError(f"unknown arrival mode {self.mode!r}")
        if self.total is not None:
            count = min(count, self.total - self._emitted)
        return [self._make(timestep) for _ in range(max(0, count))]


def _poisson(rng: random.Random, mean: float) -> int:
    """Knuth's Poisson sampler (small means, deterministic given the rng)."""
    if mean <= 0:
        return 0
    import math

    limit = math.exp(-mean)
    k = 0
    p = 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
        if k > 1000:  # numerical guard
            return k


__all__ = ["Task", "TaskQueue", "TaskGenerator"]
