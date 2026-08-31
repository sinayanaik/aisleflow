# Spec → code traceability

Every numbered section of *Lifelong Aisle-Managed PIBT for Directional and
Congestion-Aware Multi-Agent Pickup and Delivery*, and where it lives.

| § | topic | implementation |
|---|---|---|
| 5 | two-layer architecture | `simulator.Simulator.step` |
| 6.1 | vertex conflict | `validate.contains_vertex_conflict`, `pibt.PIBTPlanner.creates_vertex_conflict` |
| 6.2 | swap conflict | `validate.contains_swap_conflict`, `pibt.PIBTPlanner.creates_swap_conflict` |
| 6.3 | lifelong objective | `metrics.MetricsReport` |
| 7 | robot state | `robot.Robot` |
| 7.1 / 7.2 | robot and task states | `types.RobotState`, `types.TaskStatus` |
| 8 | task lifecycle | `assignment.update_task_state` |
| 9.1 | distance maps | `graph.GridGraph.compute_bfs_distance_map` (cached in `.distance_map`) |
| 9.2 | warehouse metadata | `warehouse.VertexInfo`, `warehouse.Aisle` |
| 9.3 | graph features | `graph.GridGraph.articulation_points`, `.bridges`, `.dead_ends`, `.satisfies_pibt_reachability` |
| 10 | aisle states | `types.AisleState` |
| 11 | why direction is managed per aisle | `aisle_manager.AisleManager` (robots request, aisles decide) |
| 12 | directional demand `S_a^±` | `AisleManager.compute_directional_demand`, `._robot_demand` |
| 13 | proximity modes | `scoring.compute_proximity_mode` |
| 13.4 | smooth direction weight `β_i` | `scoring.compute_direction_weight` |
| 14 | preferred route direction | `scoring.compute_route_direction` |
| 15 | next critical aisle | `Simulator._find_next_critical_aisle` |
| 16 | direction hysteresis | `AisleManager.update_aisle_direction`, `scoring.apply_direction_hysteresis` |
| 17 | drain-before-reverse | `AisleManager.update_aisle_direction` (DRAINING branch) |
| 18 | aisle entry reservations | `AisleManager.update_aisle_reservations`, `.can_enter_aisle` |
| 19 | assignment cost `J(i,τ)` | `assignment.TaskAssigner.assignment_cost` |
| 19.1 | directional delay | `TaskAssigner.directional_delay` |
| 19.2 | greedy assignment | `TaskAssigner.assign_tasks_greedily` |
| 20 | waypoint updates | `assignment.update_waypoint` |
| 21 | priority function | `priority.compute_priority` |
| 21.1 | fairness condition | `priority.fairness_horizon` |
| 22 | candidate generation and rejection | `PIBTPlanner.candidates`, `.feasible_candidates` |
| 23 | candidate score `S_i(v)` | `scoring.CandidateScorer.score` |
| 23.1 | congestion terms | `congestion.CongestionModel` |
| 25 | PIBT recursion | `PIBTPlanner.pibt` |
| 26 | conflict checks | `pibt.py`, `validate.py` |
| 27 | aisle direction constraints | `AisleManager.violates_aisle_direction`, priced by `CandidateScorer.aisle_penalty` (not rejected — see below) |
| 28 | deadlock detection | `deadlock.DeadlockMonitor.detect_deadlocked_groups` |
| 28.1 | dependency graph | `DeadlockMonitor.build_dependency_graph`, `.find_cycles` |
| 29 | seven recovery levels | `DeadlockMonitor.recover_from_deadlock` |
| 30 | complete lifelong algorithm | `Simulator.step` (16 numbered blocks) |
| 31 | move execution | `validate.execute_moves` |
| 32 | complexity / data structures | `congestion.OccupancyIndex` |
| 33 | initial parameters | `config.Params` |
| 34 | ablation table | `config.ABLATIONS`, `experiments.run_ablation_table` |
| 35 | experimental scenarios | `maps/`, `task.TaskGenerator`, `experiments.run_density_sweep` |
| 36 | evaluation metrics | `metrics.py` |
| 38.1 | collision freedom | asserted every timestep; `tests/test_pibt.py` |
| 38.3 | fairness | `tests/test_lifelong.py::test_waiting_eventually_overcomes_the_class_gap` |
| — | GUI + candidate explainer | `gui/server.py`, `gui/static/index.html`, `PIBTPlanner.explain_candidates` |
| 38.4 | direction stability | `tests/test_aisle_manager.py::test_minimum_lock_time_blocks_an_immediate_flip` |

## Deliberate deviations from the spec

Four, each with the measurement that motivated it. All are flagged, so the
spec-literal behaviour stays runnable.

1. **Aisle direction ranks candidates; it does not reject them** (spec 22.1,
   27). The spec lists "violates aisle direction" and "violates a reservation"
   among the hard rejection rules. Implementing them that way deletes legal
   moves from the candidate set, and PIBT's progress argument rests on being
   able to push any robot into an adjacent cell — so inheritance chains
   dead-end against the constraint. Both are now `zeta` penalties in
   `S_i(v)`, sized between the other soft terms and `alpha_progress`.
   Measured: `aisle_direction_only` goes from 0.010 to 0.153 tasks/step on
   `warehouse_corridors`, 0.071 to 0.346 on `warehouse_narrow`, 0.169 to 0.370
   on `warehouse_medium`. Restore with `hard_direction_constraints=True`.

2. **A maximum green** (spec 16). The spec gives an aisle a minimum lock and a
   dead band, which bound how soon a direction may change but not how long it
   may persist. With near-balanced demand — the normal case when pickups are on
   one side of the map and deliveries on the other — the imbalance never leaves
   the dead band and the aisle holds one direction forever. `T_max =
   maximum_aisle_lock_time`: past it, any opposing demand forces a drain and a
   flip. Set it very large to recover the spec behaviour
   (`aisle_direction_no_max_green` does exactly that).

3. **Aisles are straight runs** (spec 9.2). Segmenting by connected component
   alone yields L-, U- and T-shaped aisles, on which `FORWARD`/`REVERSE` has no
   single compass meaning and a one-way rule blocks the corner both ways.
   Segmentation now cuts at every turn; `Aisle.axis` reports `row` or `col`.

4. **Recovery needs corroboration** (spec 28). The spec names three stall
   signals; the implementation treated the weakest (no progress for
   `t_blocked` steps) as sufficient, which fires constantly in dense lifelong
   traffic. A group must now also show a wait-for cycle or a repeated
   configuration. Restore with `require_deadlock_corroboration=False`.

## Complexity

The PIBT timestep cost is `O(|A|(Δ(G) + F + log|A|))`. This implementation keeps
`F` constant per candidate:

- vertex occupancy: hash map, `O(1)`
- aisle occupancy and capacity: counters, `O(1)`
- local robot density: spatial hash bucketed at `local_congestion_radius`
- downstream congestion: memoised per `(vertex, waypoint)` for the timestep
- distance maps: precomputed per station, then `O(1)` lookups

The high-level layer adds, per timestep: `O(|aisles|)` for direction decisions,
`O(|A|)` for queues and reservations, and `O(free robots × candidate tasks)` for
greedy assignment — capped by `assignment_candidate_limit`.

Measured on `warehouse_medium` with 40 robots: 0.96 ms/step for
`lifelong_pibt`, 3.12 ms/step for `full_lda_pibt`.

## Known gaps

- Kinematics, turning radius and multi-cell footprints (spec 39.8). Hooks exist
  in `PIBTPlanner.violates_kinematics` and `Robot.orientation`.
- Asynchronous execution (spec 39.7) — the model is fully synchronous.
- Failure scenarios of spec 35.5: delayed robots, failed robots, blocked aisles,
  communication delay.
- Intersection reservations. Spec 22.1 lists "violates a locked intersection";
  aisle reservations are implemented, intersection locking is not.
- Entry capacity admission does not yet earn its cost. It is implemented and,
  for the first time, measurable (`reservations` no longer requires
  `direction_control="aisle"`), but on every bundled map it reduces throughput
  without a consistent reduction in head-on conflicts. Either the admission
  rule or the capacity model needs rethinking; the mechanism is not the
  problem, the policy is.
- The robot-level direction term `beta` is net negative on two of three maps
  (`no_direction_term` beats `aisle_direction_only`), and its proximity decay
  is inert — changing `r_near`/`r_far` produces bit-identical runs, because
  `beta` only ever breaks ties among candidates with equal progress and the
  decay lowers it exactly where progress already decides.

## Closed since the last review

- **Coordinated direction assignment across parallel aisles** was called the
  most promising next step. It is now implemented
  (`coordinate_aisle_directions`: commit in descending `|imbalance|`, roll back
  anything that breaks strong connectivity of the directed residual graph) and
  its measured effect on the bundled maps is between ±0.000 and ±0.011 — a null
  result. A single one-way aisle almost never disconnects a ladder graph, so the
  guard barely fires. The collapse it was meant to cure was a liveness failure,
  fixed by the maximum green above.
