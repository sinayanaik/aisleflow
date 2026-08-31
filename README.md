# AisleFlow

**Directional and congestion-aware PIBT for lifelong multi-agent pickup and delivery.**

AisleFlow is a complete, runnable Python implementation — and, as of this
benchmark pass, an independently-verified evaluation — of the *Lifelong
Aisle-Managed PIBT* (LDA-PIBT) proposal, an extension of
[Priority Inheritance with Backtracking](https://www.alphaxiv.org/abs/1901.11282)
(Okumura, Machida, Défago, Tamura) to lifelong warehouse MAPD.

The design principle from the proposal is preserved throughout:

> Direction, congestion, and turning cost determine which legal movement PIBT
> tries first. They do not replace PIBT's collision checks or backtracking.

and, at the architectural level:

> Robots generate directional requests, but aisles make directional decisions.

Zero dependencies for the simulator itself. `matplotlib` and `pillow` are only
needed for animations.

---

## What changed in this pass

The previous review reported that three of six hypotheses had failed, and named
three causes. Tracing each verdict back through the code found something else:
the verdicts were artifacts of the implementation and the experimental design,
not properties of the hypotheses. Six defects, each verified by running the
simulator:

1. **The aisle layer contradicted the project's own design principle.**
   `PIBTPlanner.feasible_candidates` applied aisle direction and reservations as
   *hard rejections*, deleting legal moves before scoring — while the principle
   quoted at the top of this file says the high level never replaces PIBT's
   collision checks or its backtracking. Priority inheritance works because a
   robot can always be pushed into an adjacent cell, and a one-way rule enforced
   by rejection removes exactly that. Direction is a score penalty now
   (`ζ_counterflow`, `ζ_reservation`), above every other soft term and below
   `α_progress`. Worth **1.9× to 3.5×** throughput, significant on all four maps.
   `hard_direction_constraints=True` runs the old form.
2. **`reservations` was a silent no-op** unless `direction_control == "aisle"` —
   and `reservations_only`, the variant that exists to isolate it, sets
   `direction_control="robot"`. Its runs were bit-identical to its own control,
   so H3's "exactly zero effect" was an empty treatment cell, and the 2×2 design
   built around it could not measure factor B at all.
3. **The aisle signal had no starvation freedom.** A committed direction was
   held indefinitely unless `|imbalance| > τ`, and a warehouse with pickups on
   one side and deliveries on the other produces balanced demand by
   construction. `maximum_aisle_lock_time` adds the maximum green a traffic
   signal needs alongside its minimum.
4. **Aisles were not straight.** Segmenting by connected component gave L-, U-
   and T-shaped "aisles" — `warehouse_corridors` had two 25-cell U shapes — on
   which a one-way rule blocks the corner in both senses. Segmentation cuts at
   every turn now.
5. **Recovery fired on healthy traffic.** Spec 28 names three stall signals; the
   code treated the weakest (no progress for `t_blocked` steps) as sufficient,
   which describes an ordinary queue. Detection now needs a wait-for cycle or a
   repeated configuration to corroborate it: 0.134 against 0.022 on corridors.
6. **The congestion term was mis-scaled.** `C_local` was a raw robot count mixed
   with two ratios, so `μ · C` measured mean 3.40 / p90 5.75 / max 9.60 against
   `α · Δ = 10` — congestion was the second-largest term in the score, not a
   modulator of the smaller ones. Normalised now, and `congestion_aware` is
   split into `congestion_scoring` and `congestion_assignment` since it bundled
   movement and matching.

Also in this pass: the metrics the hypotheses are actually about
(`head_on_conflicts`, `counterflow_moves`, `aisle_throughput_per_1000`,
`starvation_flips`); `experiments/run_hypothesis_suite.py`, which scores each
hypothesis on *its own* metric with a bootstrap CI and a permutation test; GUI
controls for every new flag, an aisle max-green readout, and a live hypothesis
panel. **179 tests**, up from 161.

Two things the previous review predicted that turned out not to hold, both now
implemented and measured so the null results are reproducible: coordinated
direction assignment across parallel aisles (`coordinate_aisle_directions`,
effect ±0.011) and route-spread directional demand (`demand_spread`, net
negative). Both ship off by default.

---


## Quickest start

No install, no virtualenv, no dependencies — the simulator is pure standard
library:

```bash
git clone <your-repo-url> aisleflow
cd aisleflow
python3 main.py                                       # opens the GUI on a bundled map
python3 main.py gui maps/warehouse_medium.map -n 40    # same CLI as `lda-pibt` below
```

`main.py` just puts `src/` on the import path and forwards to the same CLI as
an installed package — every command below works identically as
`python3 main.py <command> ...` with nothing installed.

## Install (optional)

Only needed for the `lda-pibt` console command, the test suite, or GIF export:

```bash
pip install -e ".[dev]"      # or: pip install -e .   (no viz, no pytest)
pytest                       # 179 tests
```

Python 3.10+.

## The GUI

```bash
lda-pibt gui maps/warehouse_medium.map -n 40 --rate 1.5
# or, with nothing installed:
python3 main.py gui maps/warehouse_medium.map -n 40 --rate 1.5
```

Opens a browser on `http://127.0.0.1:8000`. No extra dependencies — the backend
is `http.server` and the frontend is one self-contained HTML file, so it runs
over SSH with a forwarded port too (`--host 0.0.0.0`, `--no-browser`).

```
┌──────────────┬─────────────────────────────────────┬────────────────┐
│ map, variant │  ▶ Play  Step  +10  +100   Overlay  │ live metrics   │
│ robots, seed │ ┌─────────────────────────────────┐ │ + sparklines   │
│ arrival,rate │ │                                 │ │                │
│              │ │   warehouse canvas: robots,     │ │ INSPECTOR      │
│ ablation     │ │   aisle direction arrows,       │ │ robot #12      │
│ switches     │ │   routes, congestion heatmap    │ │ stalled 47     │
│              │ │                                 │ │ waiting for R7 │
│ α β γ λ μ ξ  │ └─────────────────────────────────┘ │ ─ why it moved │
│ sliders …    │  free · pickup · delivery · FORWARD │   there ─      │
└──────────────┴─────────────────────────────────────┴────────────────┘
```

**Click a robot and you get the answer to "why is it stuck?"** The inspector
lists every candidate cell with its score and the exact rule that rejected it —
`aisle-direction`, `no-reservation`, `vertex-conflict`, `kinematics` — in spec
§22.1 order. A stalled robot almost always shows the same reason on every
candidate, which names the culprit immediately. This is the diagnostic that
found all five bugs listed below; it is now a first-class feature
(`PIBTPlanner.explain_candidates`), usable from the library as well.

Also in there:

- **Aisle overlay** — every aisle tinted by state (OPEN / FORWARD / REVERSE /
  DRAINING) with flow arrows. Watch drain-before-reverse happen.
- **Heatmaps** — local congestion, or per-robot stall time, so jams are visible
  before you go looking for them.
- **Click an aisle** — state, direction, occupancy vs capacity, lock expiry,
  switch count, live reservation holders, and whether it is managed at all.
- **Live parameter sliders** — α, β, γ, λ, μ, ξ, `R_near`/`R_far`, `T_min`,
  `τ_switch`, capacity, drain timeout. Changing one restarts the run with the
  same seed, so A/B-ing a weight takes two seconds.
- **Ablation switches** as checkboxes: hysteresis, reservations, congestion,
  recovery, turning cost, direction-aware routing.
- Keyboard: `space` play/pause, `.` step, `r` reset.

Every control is also a JSON endpoint (`/api/state`, `/api/step`,
`/api/robot?id=N`, `/api/heatmap?kind=…`, `/api/config`), so you can drive the
simulator from a notebook or a script without the browser.

## Run it from the command line

`lda-pibt` below requires `pip install -e .`; swap in `python3 main.py` for
any of these with nothing installed — same commands, same flags.

```bash
# interactive GUI (see above)
lda-pibt gui maps/warehouse_medium.map -n 40

# structure of a warehouse: aisles, intersections, bottlenecks, reachability
lda-pibt inspect maps/warehouse_medium.map

# one lifelong simulation
lda-pibt run maps/warehouse_medium.map -n 40 -t 400 --rate 1.5 --variant full_lda_pibt

# the ablation table of spec section 34
lda-pibt ablate maps/warehouse_corridors.map -n 35 -t 400 --seeds 3

# ASCII frames, or an animated gif (needs matplotlib + pillow)
lda-pibt animate maps/warehouse_small.map -n 8 -t 120 --stride 20
lda-pibt animate maps/warehouse_small.map -n 8 -t 120 --out run.gif

# override any parameter
lda-pibt run maps/warehouse_narrow.map -n 30 --set minimum_aisle_lock_time=8 \
                                            --set direction_aware_routing=true
```

Everything is also a library:

```python
from lda_pibt import Warehouse, TaskGenerator, ablation, build_simulator, Params

params    = ablation("full_lda_pibt", Params(seed=7))
warehouse = Warehouse.from_file("maps/warehouse_medium.map", params)
tasks     = TaskGenerator(warehouse.pickup_vertices,
                          warehouse.delivery_vertices,
                          mode="poisson", rate=1.5, seed=7)

sim    = build_simulator(warehouse, n_robots=40, params=params, task_generator=tasks)
report = sim.run(max_timesteps=400)

print(report)                  # human-readable summary
report.save("results/run.json")
```

## Map format

Plain text, one character per cell. Lines starting with `//` are comments.

| char | meaning | | char | meaning |
|---|---|---|---|---|
| `.` | free floor | | `k` | parking bay |
| `@` `#` | shelf / obstacle | | `b` | bottleneck (also auto-detected) |
| `p` | pickup station | | `y` | passing bay |
| `d` | delivery station | | | |

Everything else — aisle boundaries, intersections, capacities, articulation
points, bridge edges, dead ends — is derived automatically.

Bundled maps: `warehouse_small`, `warehouse_medium`, `warehouse_narrow`,
`warehouse_corridors` (parallel head-on corridors), `warehouse_bottleneck`
(two halves joined by one long corridor), plus `corridor` and `loop` for tests.

## Architecture

Two planning layers, exactly as in the proposal.

```
        ┌─────────────────────── HIGH LEVEL ───────────────────────┐
task ──▶│ assignment ▶ waypoints ▶ routes ▶ directional demand      │
stream  │ ▶ aisle direction (hysteresis, drain) ▶ reservations      │
        │ ▶ priorities ▶ deadlock detection & local recovery        │
        └──────────────────────────┬───────────────────────────────┘
                                   │ constrains + ranks candidates
        ┌──────────────────────────▼─── LOW LEVEL (PIBT) ──────────┐
        │ candidates ▶ hard rejection ▶ score-sorted ▶ priority     │
        │ inheritance ▶ backtracking ▶ one synchronized timestep    │
        └───────────────────────────────────────────────────────────┘
```

| module | spec sections | what it does |
|---|---|---|
| `types.py` | 7, 10, 14 | enums, `Vertex`, `Reservation`, `PlanningError` |
| `config.py` | 33, 34 | `Params` + six ablation presets + six single-flag isolation variants (`FACTORIAL_DESIGNS`) |
| `graph.py` | 9.1, 9.3 | grid, BFS distance maps, articulation points, bridges |
| `warehouse.py` | 9.2, 10 | map parsing, vertex metadata, aisle segmentation |
| `task.py` | 7.2, 8, 35.4 | tasks, queue, four arrival processes |
| `robot.py` | 7 | robot state |
| `congestion.py` | 23.1, 32 | occupancy index, local / aisle / downstream congestion |
| `scoring.py` | 13, 14, 23, 24 | proximity modes, β/γ weights, turning cost, `S_i(v)` |
| `priority.py` | 21 | task-class + waiting + blocked priority |
| `aisle_manager.py` | 11–12, 16–18, 27 | demand, hysteresis, drain-before-reverse, reservations |
| `assignment.py` | 19, 20 | greedy `J(i,τ)` matching, waypoint and task transitions |
| `routing.py` | 2, 4 | direction-aware A\* (opt-in extension) |
| `pibt.py` | 22, 25, 26 | the PIBT recursion and hard rejection rules |
| `deadlock.py` | 28, 29 | wait-for cycles, repeated configurations, 7 recovery levels |
| `validate.py` | 26, 31 | vertex/swap validation, synchronized execution |
| `metrics.py` | 36 | throughput, service-time percentiles, Jain, runtime |
| `simulator.py` | 30 | the 16-step lifelong loop; planner is swappable (`planner_factory`) |
| `experiments.py` | 34, 35 | ablation table, density sweep, factorial and baseline-comparison drivers |
| `baselines/` | — | external MAPD algorithms (Token Passing, RHCR) for comparison, not ablation |
| `stats.py` | — | pure-Python bootstrap CI and permutation test |
| `gui/` | — | browser GUI: `http.server` backend + one HTML file |

### Aisle state machine (spec 10, 17)

```
     opposing demand exceeds τ_switch,  OR  held ≥ T_max with any opposing demand
   FORWARD ──────────────────────────────────────────────▶ DRAINING
      ▲                                                        │ occupancy reaches 0:
      │        demand imbalance + lock expired                 │ commit pending direction
   OPEN ◀─────────────── drain timed out ─────────────────  REVERSE
```

Two additions beyond the base rule.

A `DRAINING` aisle that has not emptied within `max_drain_time` steps is
reopened. Spec 16 allows an immediate switch when "the current direction is
infeasible", and an aisle that cannot drain is exactly that. Without it the
state machine has an absorbing deadlock. When it *does* empty, it commits the
direction that triggered the drain rather than re-running the imbalance test —
by then the demand has usually moved on, and re-testing would simply re-commit
the direction just drained.

And a **maximum green**, `maximum_aisle_lock_time`. Hysteresis is only half a
traffic signal: the dead band and the minimum lock bound how *soon* a direction
may change, and nothing bounds how long it may persist. In a warehouse with
pickups down one side and deliveries down the other, loaded and empty robots
want opposite directions in near-equal measure, so `|imbalance|` never leaves
the dead band and the aisle keeps its first committed direction for the rest of
the run. Past `T_max`, any opposing demand at all forces a drain and a flip.
That is what makes the aisle layer starvation-free rather than merely
non-flapping — and flips forced this way are counted separately as
`starvation_flips`, so "does hysteresis stop oscillation?" stays a distinct
question from "does anyone starve?".


## Results

`python experiments/run_ablation.py --seeds 5`, 400 timesteps, mean of 5 seeds.
`thr` = tasks completed per timestep, `svc` = mean service time, `sw/1k` =
aisle direction switches per 1000 steps, `ms` = mean planner runtime per step.
Full tables for all five maps land in `results/ablation.json`.

Read the ladder rows for the shape of the system and the isolation rows below
them for attribution: three of the ladder's six rungs flip two flags at once,
which is what `experiments/run_factorial_ablation.py` exists to unpick.

**warehouse_corridors** — 35 robots, five parallel single-file corridors:

| variant | thr | svc | p95 | sw/1k | ms |
|---|---:|---:|---:|---:|---:|
| lifelong_pibt | 0.131 | 157.0 | 304.3 | 0.00 | 0.79 |
| directional_pibt | 0.156 | 159.6 | 297.4 | 0.00 | 0.85 |
| hysteresis_pibt | 0.148 | 148.2 | 277.5 | 0.00 | 0.87 |
| aisle_managed_pibt | 0.095 | 136.1 | 273.5 | 18.0 | 1.21 |
| full_lda_pibt | 0.121 | 148.9 | 294.4 | 8.0 | 2.73 |
| — *isolation rows* | | | | | |
| **turning_cost_only** | **0.196** | 157.0 | 288.6 | 0.00 | 0.85 |
| aisle_direction_only | 0.158 | 163.0 | 297.4 | 13.5 | 1.29 |
| reservations_only | 0.125 | 145.7 | 286.8 | 0.00 | 1.02 |
| aisle_direction_hard | 0.084 | 159.7 | 299.7 | 3.0 | 1.22 |
| recovery_uncorroborated | 0.022 | 63.9 | 120.1 | 1.5 | 1.16 |

**warehouse_narrow** — 30 robots, four 5-cell single-file aisles per bank:

| variant | thr | svc | p95 | sw/1k | ms |
|---|---:|---:|---:|---:|---:|
| **lifelong_pibt** | **0.354** | 145.5 | 252.2 | 0.00 | 0.74 |
| directional_pibt | 0.189 | 166.9 | 314.2 | 0.00 | 0.78 |
| hysteresis_pibt | 0.258 | 160.2 | 297.6 | 0.00 | 0.79 |
| aisle_managed_pibt | 0.152 | 158.9 | 308.5 | 15.0 | 1.29 |
| full_lda_pibt | 0.194 | 148.5 | 305.2 | 41.0 | 2.90 |
| — *isolation rows* | | | | | |
| aisle_direction_only | 0.289 | 152.9 | 284.6 | 22.5 | 1.34 |
| no_direction_term | 0.289 | 155.1 | 287.2 | 31.0 | 1.38 |
| reservations_only | 0.150 | 112.9 | 240.2 | 0.00 | 0.93 |
| aisle_direction_hard | 0.078 | 121.2 | 241.5 | 15.5 | 1.20 |
| recovery_uncorroborated | 0.134 | 144.2 | 299.7 | 16.0 | 1.38 |

**warehouse_medium** — 40 robots, open grid warehouse:

| variant | thr | svc | p95 | sw/1k | ms |
|---|---:|---:|---:|---:|---:|
| **lifelong_pibt** | **0.502** | 139.0 | 246.0 | 0.00 | 1.00 |
| directional_pibt | 0.283 | 175.5 | 301.8 | 0.00 | 1.05 |
| hysteresis_pibt | 0.337 | 162.6 | 290.6 | 0.00 | 1.06 |
| aisle_managed_pibt | 0.217 | 143.7 | 264.7 | 5.5 | 1.87 |
| full_lda_pibt | 0.313 | 150.0 | 274.4 | 35.5 | 4.18 |
| — *isolation rows* | | | | | |
| no_direction_term | 0.433 | 146.1 | 264.8 | 18.5 | 1.97 |
| turning_cost_only | 0.411 | 152.2 | 269.4 | 0.00 | 1.03 |
| aisle_direction_only | 0.405 | 158.2 | 277.5 | 14.5 | 1.90 |
| aisle_direction_no_max_green | 0.360 | 149.6 | 286.1 | 5.0 | 1.88 |
| reservations_only | 0.262 | 152.7 | 290.7 | 0.00 | 1.28 |
| aisle_direction_hard | 0.161 | 159.8 | 310.5 | 32.5 | 1.94 |
| recovery_uncorroborated | 0.177 | 142.8 | 285.0 | 14.0 | 1.89 |

**warehouse_bottleneck** — 16 robots, two halves joined by one long corridor.
Every aisle-layer variant is identical to `hysteresis_pibt` here (`thr` 0.149,
`sw/1k` 0.00). With 16 robots spread over 25 aisles and each robot charged to
one aisle, the demand imbalance never crosses `τ_switch = 5.0`, so no aisle ever
commits a direction. **This map produces no treatment at all**, and any verdict
read off it is a verdict about an experiment that did not run. It is reported
here so that stays visible rather than reading as a null result.

### Baselines: comparison against external algorithms

Every table above is this codebase compared against itself: a feature flag on
or off, same PIBT core underneath. None of it says how LDA-PIBT or plain
lifelong PIBT stack up against independently-implemented algorithms from the
literature — until now, that comparison didn't exist in this repo.

`src/lda_pibt/baselines/` adds two: **Token Passing** (Ma, Kumar, Koenig &
Ayanian 2017 — the standard lifelong-MAPD baseline for exactly this
pickup-and-delivery setting) and **RHCR**, Rolling-Horizon Collision
Resolution (Li, Tinka, Kiesel, Durham, Kumar & Koenig 2021 — windowed
Conflict-Based Search, replanned periodically). Both reuse the *same*
`assignment.TaskAssigner` as every PIBT variant — task assignment is held
identical across all five planners compared below, so only the low-level
movement/collision-avoidance layer differs. Neither uses candidate scoring,
aisle direction, or reservations; they navigate via their own time-expanded,
reservation-table A\* (`baselines/space_time_search.py`), built from scratch
since nothing else in this codebase is collision-aware in that sense.

`python experiments/run_baseline_comparison.py --seeds 10`, 400 steps, same
maps/robot-counts/rates as the ablation table above. Reported per field:
mean, and a permutation-test p-value against `lifelong_pibt` (`stats.py`,
10000-permutation two-sided test; `token_passing_recovery` is Token Passing
with the same deadlock-recovery layer the PIBT variants get, since which
framing is "fairer" is a judgment call worth showing both sides of):

**warehouse_bottleneck** — 16 robots:

| variant | thr | svc | p (thr) | ms/step |
|---|---:|---:|---:|---:|
| lifelong_pibt (ref) | 0.11 | 150.3 | — | 0.32 |
| full_lda_pibt | 0.15 | 159.7 | 0.001 | 0.94 |
| token_passing | 0.00 | 22.0 | <0.001 | 26.1 |
| token_passing_recovery | 0.01 | 32.1 | <0.001 | 24.5 |
| rhcr | 0.01 | 21.4 | <0.001 | 51.9 |

**warehouse_corridors** — 35 robots:

| variant | thr | svc | p (thr) | ms/step |
|---|---:|---:|---:|---:|
| lifelong_pibt (ref) | 0.16 | 164.2 | — | 0.70 |
| full_lda_pibt | 0.02 | 134.8 | <0.001 | 1.58 |
| token_passing | 0.00 | 0.0 | <0.001 | 51.0 |
| token_passing_recovery | 0.00 | 0.0 | <0.001 | 32.2 |
| rhcr | 0.00 | 4.3 | <0.001 | 31.6 |

**warehouse_medium** — 40 robots, open grid:

| variant | thr | svc | p (thr) | ms/step |
|---|---:|---:|---:|---:|
| lifelong_pibt (ref) | 0.50 | 144.1 | — | 0.83 |
| full_lda_pibt | 0.20 | 165.0 | <0.001 | 2.40 |
| token_passing | 0.00 | 49.1 | <0.001 | 158.7 |
| token_passing_recovery | 0.01 | 100.3 | <0.001 | 134.2 |
| rhcr | 0.01 | 38.2 | <0.001 | 58.1 |

Full per-seed data and every `CORE_REPORT_FIELDS` column lands in
`results/baseline_comparison.json`.

**The headline is unambiguous and was not the expected result going in:
plain `lifelong_pibt` — the simplest configuration already in this
repo — beats both external baselines by a wide, statistically significant
margin (p < 0.001 on throughput in every cell above) on every map tested,
including the open `warehouse_medium` grid where neither baseline has any
obvious structural excuse.** Token Passing completes literally zero tasks on
`warehouse_corridors` across all 10 seeds. Inspecting `tp_path_not_found` and
`tp_forced_holds` (both exposed via `stats()`) confirms this is genuine
gridlock, not a crash or a silent no-op: on `warehouse_bottleneck`, all 16
robots queue nose-to-tail in the single connecting corridor and none move
again for the rest of the run. This is the mechanism PIBT was built to solve
— priority inheritance lets a blocked robot push the one ahead of it out of
the way; Token Passing has no such mechanism, so a queue that forms never
resolves. RHCR's windowed joint replanning does modestly better (it completes
some tasks on two of three maps where Token Passing completes none) but is
still nowhere close to PIBT, and is 30–190x more expensive per step
(`ms/step` above) than even the full LDA-PIBT variant.

Three caveats before reading too much into the margin:

1. **Task assignment is deliberately held constant** across all five
   planners (see above) — this isolates the movement/collision-avoidance
   layer's contribution, but it also means neither baseline was tested with
   the assignment policy its own paper pairs it with.
2. **RHCR's `window`/`replan_period` (10/5 steps, `Params.baseline_window`
   / `baseline_replan_period`) are untuned defaults**, not values selected to
   match the scale RHCR's own paper targets (hundreds of robots on much
   larger warehouses, where its rolling-horizon design is meant to pay off
   against full CBS). Poor performance here is a property of *this baseline
   at this scale with these defaults*, not necessarily a property of RHCR in
   general.
3. **Token Passing's gridlock in narrow corridors, on the other hand, is a
   structural property of the algorithm** (no priority inheritance, no
   backtracking), not a tuning artifact — the same failure mode is
   well-documented in the lifelong-MAPD literature and is unlikely to
   disappear with different hyperparameters.

### What the hypotheses actually did

`python experiments/run_hypothesis_suite.py --seeds 10`, 400 steps, four maps,
results in `results/hypotheses.json`.

Every hypothesis is now scored on **the quantity it actually claims to move**,
against the control that isolates its mechanism, with a bootstrap CI and a
10,000-permutation two-sided test. That matters more than it sounds: H3 claims
*fewer head-on conflicts* and H1 claims *narrow aisles flow better*, and both
were previously judged only by global throughput — an instrument that can miss a
mechanism doing exactly what it promises, and can credit one that is not.

The hypotheses are stated with the mechanism they depend on, because the earlier
generic wording ("aisle direction improves throughput") was satisfied by
configurations in which the mechanism could not act at all.

| | claim | metric | verdict |
|---|---|---|---|
| H1 | aisle-level direction control improves throughput in narrow aisles under dense traffic — as a soft ranking term, on straight-run aisles, with a starvation-free signal | aisle throughput /1000 | **not supported** on its own metric (contradicted on corridors, flat elsewhere); see below — it *does* raise global throughput |
| H2 | hysteresis reduces switching and prevents oscillation without reintroducing starvation | switches /1000 | **supported on every map**, p < 0.001, and by two orders of magnitude |
| H3 | entry capacity admission reduces head-on conflicts and deadlocks, independently of direction mode | head-on conflicts | **map-dependent**: −53% on corridors (p < 0.001), +31% on medium (p < 0.001), flat on narrow |
| H4 | congestion-aware *task assignment* cuts service time, separately from congestion in movement scoring | mean service time | **not supported**: flat on three maps, contradicted on narrow (p = 0.046) |
| H5 | proximity-dependent direction weights improve waypoint arrival | p95 service time | **no measurable effect** — the runs are bit-identical (see below) |
| H6 | localised progressive recovery beats a global replan, escalating only on a corroborated stall | throughput | **supported on corridors** (+41%, p = 0.016); flat elsewhere |

Per-map detail, treatment vs. control:

| map | H1 | H2 | H3 | H4 | H5 | H6 |
|---|---|---|---|---|---|---|
| bottleneck | 84.8 / 84.8 | 0.0 / 65.8 ✓ | 258 / 258 | 152.9 / 153.0 | 293.6 / 288.2 | 0.15 / 0.15 |
| corridors | 258 / 294 ✗ | 13.0 / 36.8 ✓ | 557 / 1177 ✓ | 157.4 / 144.6 | 302.3 / 302.3 | 0.13 / 0.09 ✓ |
| narrow | 205 / 207 | 33.8 / 1160 ✓ | 1645 / 1494 | 144.1 / 123.4 ✗ | 273.1 / 273.1 | 0.19 / 0.15 |
| medium | 212 / 217 | 13.5 / 2188 ✓ | 1883 / 1440 ✗ | 151.5 / 155.8 | 275.7 / 275.7 | 0.25 / 0.24 |

✓ supported, ✗ contradicted, blank = no measurable effect (p ≥ 0.05).

**`warehouse_bottleneck` produces no treatment.** Every aisle-layer variant is
identical to `hysteresis_pibt` there: 16 robots spread over 25 aisles never
push the demand imbalance past `τ_switch = 5.0`, so no aisle ever commits a
direction. Its whole row is an experiment that did not run, and it is listed
that way rather than as four null results.

**H1 measures worse than it performs.** On its own metric — robots cleared per
managed aisle — aisle direction is flat or slightly negative. On global
throughput it is the better configuration on three of four maps
(`aisle_direction_only` vs `hysteresis_pibt`: 0.158/0.148 corridors,
0.289/0.258 narrow, 0.405/0.337 medium). Both are true and not in tension:
one-way aisles route traffic through *fewer* aisle transits per delivered task,
so per-aisle flow falls while end-to-end flow rises. The lesson is about the
metric, not the mechanism — per-aisle throughput is the wrong denominator for a
claim about deliveries.

**H5's mechanism is inert.** Changing `r_near`/`r_far` produces runs identical
to the last decimal on every map. `β` only ever breaks ties among candidates
with *equal* progress, and the decay lowers it precisely in arrival mode, where
`α · Δ` already decides — so the schedule cannot change a ranking. The weight
itself is not inert, and is net harmful: `no_direction_term` (`β = 0`) beats
`aisle_direction_only` on medium (0.433 vs 0.405) and ties it on narrow.

### The mechanisms this pass repaired

Four single-factor comparisons (`config.PAIRED_DESIGNS`), 5 seeds, throughput:

| mechanism | bottleneck | corridors | narrow | medium |
|---|---|---|---|---|
| direction **ranks** vs **rejects** (`aisle_direction_only`) | 0.149 / 0.149 | **0.158 / 0.084** | **0.304 / 0.112** | **0.405 / 0.161** |
| same, with reservations (`aisle_managed_pibt`) | **0.149 / 0.060** | **0.095 / 0.043** | **0.135 / 0.043** | 0.217 / 0.088 |
| bounded maximum green | 0.149 / 0.149 | 0.158 / 0.159 | 0.304 / 0.311 | 0.405 / 0.360 |
| recovery needs corroboration | 0.149 / 0.148 | **0.134 / 0.022** | 0.201 / 0.157 | 0.245 / 0.177 |

Bold = p ≤ 0.05.

**Direction as a ranking term rather than a constraint is the single largest
effect in this repository** — 1.9× to 3.5× throughput, significant on all four
maps in one arrangement or the other. The spec lists "violates aisle direction"
among the hard rejection rules, and the project's own design principle says the
high level "never replaces PIBT's collision checks or its backtracking". Those
two cannot both hold: rejecting a counterflow move deletes it from the candidate
set, and priority inheritance works precisely because a robot can always be
pushed into an adjacent cell. Every collapse previously attributed to aisle
management traces to this. Counterflow is now priced at `ζ = 8.0` — above every
other soft term, below `α = 10` — so a robot drives the wrong way when that is
the only way to make progress, and pays for it. `hard_direction_constraints=True`
restores the old behaviour; those are the right-hand numbers above.

**Corroborated deadlock detection** is the second. Spec 28 names three stall
signals, and the implementation treated the weakest (no progress for `t_blocked`
steps) as sufficient — which in dense lifelong traffic describes an ordinary
queue. Recovery therefore escalated on healthy robots, and levels 5–7 (temporary
reverse, escape vertices, waypoint hijack) cost far more than they saved: 0.022
against 0.134 on corridors. This was invisible before, because the hard
constraints above manufactured real deadlocks for recovery to rescue.

**The maximum green is not significant on throughput, and that is itself the
finding.** It was the decisive fix while direction was a hard constraint —
without it, `warehouse_corridors` reached an absorbing deadlock (all 35 robots
stalled by t = 120, four aisles locked REVERSE, identical state at t = 199).
Once counterflow is merely expensive rather than forbidden, a robot can buy its
way out of a starved aisle, so liveness no longer depends on the signal alone.
The two fixes are partly redundant. It stays on because it is the mechanism that
makes the *aisle layer* correct rather than merely survivable — and its effect on
switching is significant (30.5 vs 5.0 per 1000 on narrow, p = 0.024), which is
what H2 is about.

**Coordinated direction assignment across parallel aisles was the wrong fix.**
The previous review called it the most promising next step. It is implemented
(`coordinate_aisle_directions`: commit in descending `|imbalance|`, roll back
anything that breaks strong connectivity of the directed residual graph) and its
measured effect is between ±0.000 and ±0.011. A single one-way aisle almost
never disconnects a ladder graph, so the guard barely fires.

### De-confounded findings

`python experiments/run_factorial_ablation.py --seeds 5`, results in
`results/factorial_ablation.json`.

**Aisle direction vs. entry admission** (the design that could not be read
before — `reservations_only` sets `direction_control="robot"`, and the
reservation layer used to be gated on `direction_control == "aisle"`, so factor
B was a bit-identical copy of the base and its main effect was *necessarily*
zero):

| map | base | aisle-direction alone | reservations alone | both | interaction |
|---|---:|---:|---:|---:|---:|
| warehouse_corridors | 0.148 | **0.158** | 0.125 | 0.095 | −0.040 |
| warehouse_medium | 0.337 | **0.405** | 0.262 | 0.217 | −0.112 |

The attribution reverses. Aisle-level direction control is the flag that *helps*
(+7% and +20% isolated); entry admission is the one that costs (−16% and −22%),
and the two interact strongly negatively. The earlier verdict — direction blamed,
reservations exonerated — was reading an empty cell.

**Congestion in movement vs. congestion in matching.** `congestion_aware`
bundled three mechanisms: the `μ · C` penalty in the movement score, the
congestion term in the assignment cost, and the assignment blocking term. H4 is
a claim about *matching* only, so the flag is split into `congestion_scoring`
and `congestion_assignment`. Neither half helps on its own: on medium,
`congestion_scoring_only` reaches 0.296 and `congestion_assignment_only` 0.229
against 0.217 for the base and 0.405 for aisle direction with no congestion
model at all.

**The congestion term was also mis-scaled.** `C_local` was a raw robot count
mixed with two ratios, so `μ · C` measured mean 3.40, p90 5.75, max 9.60 on
`warehouse_medium` — against `α · Δ = 10` for a whole step of progress and
`β ≤ 3`. Congestion was the second-largest term in the score, not a modulator of
the smaller ones, and the weight ordering the design claims did not hold at
runtime. It is normalised now (`congestion_normalisation`); the effect on
throughput is neutral to slightly negative, which is worth stating plainly: this
is a calibration fix, not a performance one.

### The honest headline

**Plain `lifelong_pibt` still wins on the open maps.** 0.502 on
`warehouse_medium` against 0.405 for the best aisle-managed configuration, 0.354
on `warehouse_narrow` against 0.289. The aisle layer is now competitive rather
than catastrophic — it was 15× behind and deadlocking — and it wins on
`warehouse_corridors` (0.158 vs 0.131), the map whose five parallel single-file
corridors are the case it was designed for. But it does not beat the greedy core
in general, and the cheapest mechanism in the whole system remains the one with
the least to do with aisles: `turning_cost_only`, a flat anti-zigzag penalty, is
the best variant on corridors (0.196) and second-best on medium (0.411).

The value of this pass is not that the hypotheses now pass. Three of six still
do not. It is that H1, H3, H4 and H6 were given a *valid* test for the first
time: H3's treatment cell was empty, H1's headline map produced no treatment,
and H6's support came from rescuing deadlocks the architecture manufactured for
it.

## Implementation notes and deviations

Ten places where a literal reading of the spec does not run, with what was done
instead. Each is a flag, so the spec behaviour is still reachable.

1. **Aisle direction ranks candidates; it does not reject them** (spec 22.1, 27).
   The spec lists "violates aisle direction" and "violates a reservation" among
   the hard rejection rules, but the design principle two sections earlier says
   the high level never replaces PIBT's collision checks or its backtracking.
   Both cannot hold: rejecting a move deletes it from the candidate set, and
   priority inheritance works because a robot can always be pushed into an
   adjacent cell. Both are `ζ` penalties in `S_i(v)` now, sized above every other
   soft term and below `α_progress`. `hard_direction_constraints=True` restores
   the spec-literal form; it costs 1.9× to 3.5× throughput.
2. **A maximum green** (spec 16). The spec gives an aisle a minimum lock and a
   dead band, which bound how *soon* a direction may change but not how long it
   may persist. With near-balanced demand — the normal case when pickups are on
   one side of the map and deliveries on the other — the imbalance never leaves
   the dead band, and the aisle holds one direction indefinitely.
   `maximum_aisle_lock_time` (default 40): past it, any opposing demand forces a
   drain and a flip to the opposite direction. `aisle_direction_no_max_green`
   runs without it.
3. **Aisles are maximal straight runs** (spec 9.2). Segmenting by connected
   component alone produces L-, U- and T-shaped aisles — `warehouse_corridors`
   had two 25-cell U shapes spanning a whole corridor plus both vertical links —
   on which `FORWARD`/`REVERSE` has no single compass meaning, and a one-way rule
   blocks the corner in both senses. Segmentation cuts at every turn;
   `Aisle.axis` reports `row` or `col`.
4. **Recovery escalates only on a corroborated stall** (spec 28). The spec names
   three stall signals; the implementation treated the weakest (no progress for
   `t_blocked` steps) as sufficient, which describes an ordinary queue in dense
   traffic. A group must now also show a wait-for cycle or a repeated
   configuration. `require_deadlock_corroboration=False` restores the old
   trigger.
5. **Entry admission is capacity control, not a direction rule** (spec 18). It
   used to be gated on `direction_control == "aisle"`, which made
   `reservations=True` a silent no-op in every robot-level configuration —
   including the variant that exists to isolate it. It is gated on
   `reservations` alone now. The occupancy cap applies to `OPEN` aisles too; the
   *ticket* is still required only once an aisle is directional.
6. **The congestion mixture is normalised** (spec 23.1). `C_local` was a raw
   robot count mixed with two ratios, so `μ · C` reached the scale of `α · Δ`
   and outranked the terms it is meant to modulate. It is an occupancy ratio
   now, and the `ω` weights are normalised to sum to 1, so `C_i(v) ∈ [0, 1]`.
   `congestion_normalisation=False` restores the count.
7. **Progress normalisation** (spec 23). The spec normalises route progress by
   the remaining distance, so `α·progress ≈ 10/d`. At `d = 20` that is 0.5,
   below `β_strong = 3.0` and `γ_strong = 2.0` — which silently inverts the
   recommended relation `α > β_strong > λ_turn` from spec 33. Default is
   `progress_normalization="step"` (Δ ∈ {−1, 0, +1}); set it to `"route"` for
   the literal formula.
8. **Aisle direction restricts entry, not interior movement** (spec 27 vs 10.2).
   The spec 27 pseudocode blocks any in-aisle move opposing the direction, which
   traps robots and means `DRAINING` never terminates. Spec 10.2 says the state
   governs who may *enter*. A robot inside may always move toward the nearer
   endpoint to leave.
9. **Drain timeout, and a drain that remembers why it started.** A `DRAINING`
   aisle that will not empty is reopened after `max_drain_time`, justified by
   spec 16's "current direction is infeasible". When it *does* empty, it commits
   the direction that triggered the drain rather than re-running the imbalance
   test — by then the demand has usually moved on, and re-testing would
   re-commit the direction just drained.
10. **Progressive recovery escalation.** Spec 29 runs all seven levels in one
    call with a progress check between them, but progress cannot be observed
    until the next timestep. Here a persisting group escalates one level per
    timestep, which is what "progressive" has to mean in a synchronous loop.

Two smaller ones kept from the earlier pass: short aisles and cut edges are
never made one-way (`directional_aisle_min_length`, default 4 — making a bridge
edge one-way destroys reachability, spec 9.3 / 38.2), and idle robots hold
position at the lowest priority class rather than driving to an intersection.

Additions beyond the spec, all off by default: `direction_aware_routing` (A\*
that routes around opposing aisles), `coordinate_aisle_directions` (assign
directions as a set under a strong-connectivity invariant — measured effect
±0.011, a null result), `demand_spread` (aggregate demand over every aisle a
route touches, per spec 12 — measured net negative), `parity_bias`, and
`aisle_capacity_model` in `{"length", "throughput"}`. The default capacity model
is `"drain"`: as many robots as fit, but never more than can clear the aisle
within `max_drain_time`.

## Development plan status (spec 40)

| phase | | phase | |
|---|---|---|---|
| 1 grid + PIBT + conflicts | done | 6 reservations | done |
| 2 lifelong tasks + metrics | done | 7 congestion-aware assignment | done |
| 3 aisle metadata | done | 8 deadlock recovery | done |
| 4 robot directional preference | done | 9 evaluation | done |
| 5 aisle-level management | done | | |

Not implemented: robot kinematics and footprints beyond a single cell,
asynchronous execution (spec 39.7), decentralised communication, and the
failure-condition scenarios of spec 35.5 (delayed and failed robots). The hooks
exist — `violates_kinematics`, `Robot.orientation` — but do nothing yet.

## Repository layout

```
maps/            warehouse maps
src/lda_pibt/    the package (see the module table above)
src/lda_pibt/gui/  browser GUI (server.py + static/index.html)
src/lda_pibt/baselines/  Token Passing and RHCR, independent of the PIBT machinery
tests/           179 tests: graph, PIBT, aisle manager, lifelong layer, GUI, baselines, stats
experiments/     run_ablation.py, run_density_sweep.py, run_factorial_ablation.py,
                 run_baseline_comparison.py, run_hypothesis_suite.py
results/         JSON output (git-ignored)
docs/            implementation notes, mathematical guide, project-review deck
```

## Citation

The underlying algorithm:

> K. Okumura, M. Machida, X. Défago, Y. Tamura.
> *Priority Inheritance with Backtracking for Iterative Multi-agent Path Finding.*
> arXiv:1901.11282.

The external baselines in `src/lda_pibt/baselines/` (see "Baselines" under
Results):

> H. Ma, D. Harabor, P. J. Stuckey, J. Li, S. Koenig.
> *Searching with Consistent Prioritization for Multi-Agent Path Finding*, and
> H. Ma, J. Li, T. K. S. Kumar, S. Koenig, *Lifelong Multi-Agent Path Finding
> for Online Pickup and Delivery Tasks*, AAMAS 2017 (Token Passing).

And:

> J. Li, A. Tinka, S. Kiesel, J. W. Durham, T. K. S. Kumar, S. Koenig.
> *Lifelong Multi-Agent Path Finding in Large-Scale Warehouses.* AAAI 2021
> (Rolling-Horizon Collision Resolution).

## License

MIT — see `LICENSE`.
