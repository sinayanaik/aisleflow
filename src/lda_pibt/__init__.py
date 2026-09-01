"""TOLL-PIBT: Tolled One-way Lanes over Priority Inheritance with Backtracking.

Directional and congestion-aware multi-agent pickup and delivery, built as an
extension of PIBT (Okumura et al., arXiv:1901.11282).

Quick start::

    from lda_pibt import Warehouse, Params, build_simulator

    warehouse = Warehouse.from_file("maps/warehouse_small.map")
    sim = build_simulator(warehouse, n_robots=10, params=Params())
    report = sim.run(max_timesteps=300)
    print(report)
"""

from .aisle_manager import AisleManager
from .assignment import TaskAssigner, update_task_state, update_waypoint
from .baselines import RHCRPlanner, TokenPassingPlanner
from .config import (
    ABLATIONS,
    BASELINE_PARAMS_PRESET,
    FACTORIAL_DESIGNS,
    FactorialDesign,
    Params,
    ablation,
)
from .experiments import (
    BASELINE_PLANNERS,
    LIFELONG_VARIANTS,
    run_ablation_table,
    run_comparison_table,
    run_density_sweep,
    run_factorial_table,
    run_once,
)
from .congestion import CongestionModel, OccupancyIndex
from .deadlock import DeadlockMonitor
from .graph import GridGraph
from .metrics import MetricsCollector, MetricsReport, jain_fairness, percentile
from .pibt import PIBTPlanner
from .priority import compute_priority, order_by_priority
from .robot import Robot
from .routing import Router
from .scoring import CandidateScorer
from .simulator import Simulator, build_simulator
from .stats import bootstrap_ci, permutation_test
from .task import Task, TaskGenerator, TaskQueue
from .types import (
    AisleDirection,
    AisleState,
    Compass,
    PIBTResult,
    PlanningError,
    ProximityMode,
    Reservation,
    RobotState,
    TaskStatus,
    Vertex,
)
from .validate import execute_moves, no_swap_conflicts, no_vertex_conflicts, validate_plan
from .warehouse import Aisle, VertexInfo, Warehouse

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "ABLATIONS",
    "Aisle",
    "AisleDirection",
    "AisleManager",
    "AisleState",
    "BASELINE_PARAMS_PRESET",
    "BASELINE_PLANNERS",
    "CandidateScorer",
    "Compass",
    "CongestionModel",
    "DeadlockMonitor",
    "FACTORIAL_DESIGNS",
    "FactorialDesign",
    "GridGraph",
    "LIFELONG_VARIANTS",
    "MetricsCollector",
    "MetricsReport",
    "OccupancyIndex",
    "PIBTPlanner",
    "PIBTResult",
    "Params",
    "PlanningError",
    "ProximityMode",
    "RHCRPlanner",
    "Reservation",
    "Robot",
    "Router",
    "RobotState",
    "Simulator",
    "Task",
    "TaskAssigner",
    "TaskGenerator",
    "TaskQueue",
    "TaskStatus",
    "TokenPassingPlanner",
    "Vertex",
    "VertexInfo",
    "Warehouse",
    "ablation",
    "bootstrap_ci",
    "build_simulator",
    "compute_priority",
    "execute_moves",
    "jain_fairness",
    "no_swap_conflicts",
    "no_vertex_conflicts",
    "order_by_priority",
    "percentile",
    "permutation_test",
    "run_ablation_table",
    "run_comparison_table",
    "run_density_sweep",
    "run_factorial_table",
    "run_once",
    "update_task_state",
    "update_waypoint",
    "validate_plan",
]
