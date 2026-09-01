"""Lifelong, congestion-aware task assignment (spec section 19)."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from .config import Params
from .congestion import CongestionModel
from .robot import Robot
from .task import Task, TaskQueue
from .types import INF, RobotState, TaskStatus, Vertex
from .warehouse import Warehouse


class TaskAssigner:
    """Greedy minimum-cost matching between free robots and released tasks."""

    def __init__(
        self,
        warehouse: Warehouse,
        congestion: CongestionModel,
        params: Params,
    ):
        self.warehouse = warehouse
        self.congestion = congestion
        self.params = params
        self.assignments_made = 0

    # ------------------------------------------------------------- costing
    def blocking_estimate(self, robot: Robot, task: Task) -> float:
        r"""B(i, tau): penalise routes through bottlenecks and narrow aisles."""
        route = self.warehouse.graph.shortest_route(robot.position, task.pickup)
        if not route:
            return 0.0
        bottlenecks = sum(1 for v in route if self.warehouse.is_bottleneck(v))
        return bottlenecks / max(1, len(route))

    def assignment_cost(self, robot: Robot, task: Task, timestep: int) -> float:
        """What it would cost this robot to take this task; lower wins.

        Distance to the pickup, plus the trip that commits it, plus how
        crowded the way there is, minus how long the task has already waited.
        A term estimating delay from one-way aisles used to sit here too; it
        went with the aisle-direction layer.
        """
        p = self.params
        graph = self.warehouse.graph
        d_to_pickup = graph.route_distance(robot.position, task.pickup)
        if d_to_pickup == INF:
            return INF
        d_pickup_to_delivery = graph.route_distance(task.pickup, task.delivery)
        if d_pickup_to_delivery == INF:
            return INF

        congestion = (
            self.congestion.route_congestion(robot.position, task.pickup)
            if p.congestion_assignment
            else 0.0
        )
        # `waiting_time` grows without bound in a lifelong run. Uncapped it
        # dwarfs distance and congestion within ~100 steps and the match
        # degenerates to oldest-task-first, which is why congestion-aware
        # assignment could never show an effect.
        waiting = min(float(task.waiting_time(timestep)), p.cost_waiting_cap)
        blocking = (
            self.blocking_estimate(robot, task) if p.congestion_assignment else 0.0
        )

        return (
            p.cost_to_pickup * d_to_pickup
            + p.cost_pickup_to_delivery * d_pickup_to_delivery
            + p.cost_congestion * congestion
            + p.cost_waiting * waiting
            + p.cost_blocking * blocking
        )

    def assignment_is_feasible(self, robot: Robot, task: Task) -> bool:
        graph = self.warehouse.graph
        if graph.route_distance(robot.position, task.pickup) == INF:
            return False
        return graph.route_distance(task.pickup, task.delivery) != INF

    # ---------------------------------------------------------- assignment
    def assign_tasks_greedily(
        self, robots: Sequence[Robot], task_queue: TaskQueue, timestep: int
    ) -> int:
        """Spec section 19.2. Returns the number of assignments made."""
        free_robots = [r for r in robots if r.state in (RobotState.FREE, RobotState.PARKED)]
        unassigned = task_queue.available(timestep)
        if not free_robots or not unassigned:
            return 0

        limit = self.params.assignment_candidate_limit
        # Cheap pre-filter: only consider the oldest / nearest few tasks.
        unassigned = sorted(
            unassigned, key=lambda t: (t.release_time, t.id)
        )[: max(limit, len(free_robots))]

        made = 0
        pool_robots = list(free_robots)
        pool_tasks = list(unassigned)

        while pool_robots and pool_tasks:
            best_pair = None
            best_cost = INF
            for robot in pool_robots:
                for task in pool_tasks:
                    if not self.assignment_is_feasible(robot, task):
                        continue
                    cost = self.assignment_cost(robot, task, timestep)
                    if cost < best_cost:
                        best_cost = cost
                        best_pair = (robot, task)
            if best_pair is None:
                break
            robot, task = best_pair
            robot.task = task
            robot.parking_vertex = None
            robot.state = RobotState.TO_PICKUP
            robot.waypoint = task.pickup
            task.assignment = robot.id
            task.status = TaskStatus.TO_PICKUP
            pool_robots.remove(robot)
            pool_tasks.remove(task)
            made += 1
            self.assignments_made += 1
        return made


def update_waypoint(robot: Robot) -> None:
    """Spec section 20."""
    if robot.state is RobotState.FREE:
        robot.waypoint = robot.position
    elif robot.state is RobotState.TO_PICKUP:
        robot.waypoint = robot.task.pickup if robot.task else robot.position
    elif robot.state is RobotState.TO_DELIVERY:
        robot.waypoint = robot.task.delivery if robot.task else robot.position
    elif robot.state is RobotState.PARKED:
        robot.waypoint = robot.parking_vertex or robot.position
    elif robot.state is RobotState.RECOVERY:
        robot.waypoint = robot.recovery_vertex or robot.position


def update_task_state(robot: Robot, timestep: int) -> Optional[Task]:
    """Spec section 20.1. Returns the task if it was completed this step."""
    if robot.task is None:
        return None
    if robot.state is RobotState.TO_PICKUP:
        if robot.position == robot.task.pickup:
            robot.state = RobotState.TO_DELIVERY
            robot.task.status = TaskStatus.TO_DELIVERY
            robot.task.pickup_time = timestep
            robot.waypoint = robot.task.delivery
        return None
    if robot.state is RobotState.TO_DELIVERY:
        if robot.position == robot.task.delivery:
            task = robot.task
            task.status = TaskStatus.COMPLETED
            task.completion_time = timestep
            robot.task = None
            robot.state = RobotState.FREE
            robot.waypoint = robot.position
            robot.completed_tasks += 1
            return task
    return None


__all__ = ["TaskAssigner", "update_waypoint", "update_task_state"]
