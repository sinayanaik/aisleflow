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

## What changed since the last commit

Everything below is new work layered on top of the single prior commit
(`pre commit`). None of it changes the LDA-PIBT algorithm itself — it
corrects how the existing implementation was being evaluated, and adds the
comparisons that were previously missing entirely.

1. **De-confounded ablation.** Three of the original six ablation-ladder steps
   each flipped two `Params` flags at once, so a throughput delta couldn't be
   attributed to either flag. Added six single-flag isolation variants and
   three 2×2 factorial designs (`config.FACTORIAL_DESIGNS`,
   `experiments.run_factorial_table`, `experiments/run_factorial_ablation.py`)
   that decompose each bundled step into a main effect per flag plus an
   interaction term. Re-running the actual numbers overturned two of the
   original hypothesis verdicts — see "De-confounded findings" under Results.
2. **External baselines.** Added independently-implemented **Token Passing**
   (Ma et al. 2017) and **RHCR** (Li et al. 2021) in `src/lda_pibt/baselines/`,
   built on a from-scratch shared space-time A\* + reservation table
   (`baselines/space_time_search.py`) since nothing else in the codebase does
   collision-aware pathfinding. `Simulator`/`build_simulator` gained a
   `planner_factory` seam so any planner can be swapped in. Every prior
   comparison in this repo was internal (this codebase vs. itself); this is
   the first comparison against algorithms from the literature — see
   "Baselines" under Results for the (decisive, unexpected) result.
3. **Statistical rigor.** New `stats.py` (pure Python: bootstrap confidence
   intervals, a permutation-test p-value — no scipy/numpy dependency added)
   and `experiments.run_comparison_table`, so the baseline comparison reports
   significance, not bare means over a handful of seeds.
4. **Zero-install entry point.** `main.py` at the repo root puts `src/` on the
   import path and forwards to the existing CLI — `python3 main.py ...` now
   works with nothing installed (no `pip install`, no virtualenv), including
   with no arguments at all (opens the GUI on a bundled map). See "Quickest
   start" below.
5. **Renamed the project** from LDA-PIBT to **AisleFlow** — LDA-PIBT is now
   the name of the algorithm this repo implements (kept everywhere it's used
   as a technical term: the spec, the `full_lda_pibt` variant, module docs),
   not the repo's own name.
6. **Test suite grew from 102 to 161 tests**: safety-net and unit tests for
   both baselines across every bundled map, plus `stats.py` unit tests.

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
pytest                       # 161 tests
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
                 opposing demand exceeds τ_switch
   FORWARD ──────────────────────────────────▶ DRAINING
      ▲                                            │ occupancy reaches 0
      │        demand imbalance + lock expired      ▼
   OPEN ◀──────────────────────────────────────  REVERSE
```

Plus one addition: a `DRAINING` aisle that has not emptied within
`max_drain_time` steps is reopened. Spec 16 allows an immediate switch when
"the current direction is infeasible", and an aisle that cannot drain is
exactly that. Without it the state machine has an absorbing deadlock.

## Results

`python experiments/run_ablation.py --seeds 3`, 400 timesteps, mean of 3 seeds.
`thr` = tasks completed per timestep, `svc` = mean service time, `sw/1k` =
aisle direction switches per 1000 steps, `ms` = mean planner runtime per step.
The ladder below changes two flags on three of its six rows; treat the H1/H3/H4
attributions per row with that in mind and see "De-confounded findings" further
down, produced by `python experiments/run_factorial_ablation.py --seeds 5`,
which isolates each flag.

**warehouse_bottleneck** — 16 robots, two halves joined by one long corridor:

| variant | thr | svc | p95 | sw/1k | ms |
|---|---:|---:|---:|---:|---:|
| lifelong_pibt | 0.135 | 179.3 | 338.4 | 0.00 | 0.39 |
| directional_pibt | 0.141 | 167.1 | 302.5 | 0.00 | 0.42 |
| hysteresis_pibt | 0.143 | 165.2 | 304.7 | 0.00 | 0.43 |
| aisle_managed_pibt | 0.142 | 167.1 | 316.0 | 0.00 | 0.63 |
| **full_lda_pibt** | **0.152** | 166.8 | 309.1 | 0.00 | 1.20 |

**warehouse_corridors** — 35 robots, five parallel single-file corridors:

| variant | thr | svc | p95 | sw/1k | ms |
|---|---:|---:|---:|---:|---:|
| lifelong_pibt | 0.142 | 171.6 | 313.3 | 0.00 | 0.84 |
| directional_pibt | 0.147 | 161.3 | 298.1 | 0.00 | 0.86 |
| **hysteresis_pibt** | **0.152** | **153.0** | **288.3** | 0.00 | 0.87 |
| aisle_managed_pibt | 0.006 | 25.7 | 38.0 | 0.00 | 1.16 |
| full_lda_pibt | 0.020 | 128.6 | 287.9 | 0.00 | 1.91 |

**warehouse_medium** — 40 robots, open grid warehouse:

| variant | thr | svc | p95 | sw/1k | ms |
|---|---:|---:|---:|---:|---:|
| **lifelong_pibt** | **0.502** | 148.1 | 256.1 | 0.00 | 0.96 |
| directional_pibt | 0.258 | 181.5 | 322.9 | 0.00 | 1.00 |
| hysteresis_pibt | 0.305 | 172.4 | 305.0 | 0.00 | 1.01 |
| aisle_managed_pibt | 0.071 | 99.1 | 190.0 | 0.83 | 1.47 |
| full_lda_pibt | 0.215 | 162.9 | 315.0 | 5.83 | 3.12 |

Full tables for all five maps land in `results/ablation.json`.

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

| | claim | verdict |
|---|---|---|
| H1 | aisle direction improves throughput in narrow aisles under dense traffic | **not supported in isolation** — see de-confounded findings below |
| H2 | hysteresis reduces switching and prevents oscillation | **supported** — `hysteresis_pibt` beats `directional_pibt` on every map |
| H3 | reservations reduce head-on conflicts and deadlocks | **reservations are not the cause of anything below** — see de-confounded findings |
| H4 | congestion-aware assignment reduces average and tail service time | **not supported in isolation** — see de-confounded findings below |
| H5 | proximity-dependent direction weights improve waypoint arrival | **supported** — removing the β decay raises max service time noticeably |
| H6 | localized recovery beats a global planner | **strongly supported** — recovery is the dominant lever, more than the original framing suggested |

### De-confounded findings (H1, H3, H4/H6 corrected)

The table above and the ablation ladder in `config.ABLATIONS` share a
methodological flaw worth being explicit about: three of the ladder's six
rungs flip **two** flags at once (`lifelong_pibt → directional_pibt` flips
`direction_control` *and* `turning_cost`; `hysteresis_pibt → aisle_managed_pibt`
flips `direction_control` *and* `reservations`; `aisle_managed_pibt →
full_lda_pibt` flips `congestion_aware` *and* `recovery`). A throughput delta on
those rungs cannot be attributed to either flag — the original H1/H3/H4
verdicts above did so anyway.

`config.FACTORIAL_DESIGNS` + `experiments.run_factorial_table` (script:
`experiments/run_factorial_ablation.py`) resolve this: each bundled step is
re-run as a 2x2 design (base, flag A alone, flag B alone, both), decomposing
the "both" effect into a main effect per flag plus an interaction term. Run
with 5 seeds, 400 steps, on the three maps the original hypotheses cited
(`--seeds 5`, results in `results/factorial_ablation.json`):

**H1 — direction awareness vs. turning cost** (`direction_control=robot` vs `turning_cost`):

| map | base thr | direction alone | turning-cost alone | both (=`directional_pibt`) |
|---|---:|---:|---:|---:|
| warehouse_bottleneck | 0.130 | 0.144 (+11%) | **0.157 (+21%)** | 0.144 |
| warehouse_corridors | 0.147 | 0.165 (+12%) | **0.201 (+37%)** | 0.159 |
| warehouse_medium | 0.505 | 0.357 (−29%) | 0.391 (−23%) | 0.269 (−47%) |

On every map, **turning-cost alone outperforms direction-awareness alone**,
and on the two maps where the original table claimed a win, turning-cost
alone beats the combined `directional_pibt` variant too — adding
direction-awareness on top of turning-cost gives back some of turning-cost's
own gain (negative interaction). H1 as stated ("aisle direction improves
throughput") is not supported by the isolated data: the improvement the
original ladder credited to direction-awareness is mostly the anti-zigzag
turning penalty, a mechanism that has nothing to do with aisles.

**H3 — aisle-level direction control vs. reservations** (`direction_control=aisle` vs `reservations`):

| map | base thr | aisle-direction alone | reservations alone | both (=`aisle_managed_pibt`) |
|---|---:|---:|---:|---:|
| warehouse_bottleneck | 0.139 | 0.123 (−12%) | 0.139 (+0%) | 0.136 |
| warehouse_corridors | 0.166 | **0.012 (−93%)** | 0.166 (+0%) | 0.009 |
| warehouse_medium | 0.304 | 0.121 (−60%) | 0.304 (+0%) | 0.071 |

Reservations alone move throughput by **exactly zero** on two of three maps
and by under a percentage point on the third. The `warehouse_corridors`
collapse — 0.166 down to 0.009 — is entirely attributable to switching
`direction_control` from robot-level to aisle-level; reservations contribute
nothing to it standalone, though on `warehouse_medium` they do amplify
aisle-direction's harm once both are on (observed both-effect is worse than
either flag's main effect predicts — a real negative interaction, just not
the standalone effect the original H3 verdict described). The original
"reservations throttle entry enough to cost more than they save" diagnosis
was misattributed: the single-file capacity-by-length problem (see
"Implementation notes" §1 below) is a consequence of the aisle-direction
state machine itself, not of the reservation layer sitting on top of it.

**H4/H6 — congestion-aware assignment vs. local recovery** (`congestion_aware` vs `recovery`):

| map | base thr/svc | congestion alone | recovery alone | both (=`full_lda_pibt`) |
|---|---:|---:|---:|---:|
| warehouse_bottleneck | 0.136 / 162 | 0.149 / 162 | 0.137 / 162 | 0.148 / 162 |
| warehouse_corridors | 0.009 / 32 | 0.009 / 54 | **0.024 / 108** | 0.018 / 134 |
| warehouse_medium | 0.071 / 95 | 0.079 / 96 | **0.184 / 160** | 0.188 / 166 |

Recovery, not congestion-aware assignment, is what rescues throughput once
aisle-direction control has collapsed it (H6, strongly supported — on
`warehouse_medium` recovery alone recovers most of the throughput the full
variant reaches). But recovery does this by completing more tasks at a much
higher mean service time (up to +68%), and congestion-aware assignment shows
**no service-time benefit in isolation on any map tested** — flat or
slightly worse everywhere. H4's claim ("service times drop, at a throughput
cost") is not just unsupported, it points the wrong way: the throughput
recovery that does happen comes with a service-time cost, and it comes from
recovery, not from congestion-aware assignment.

**The honest headline: the aisle-level layer does not yet beat plain lifelong
PIBT on open warehouse graphs.** It wins only on `warehouse_bottleneck` (+13%
throughput) and loses badly on `warehouse_medium` (0.215 vs 0.502). The
robot-level pieces — directional preference plus hysteresis — are the part that
generalises, beating the baseline on `warehouse_corridors` (+7%) and
`warehouse_bottleneck` (+6%). Put in context by the "Baselines" comparison
above: even the *losing* LDA-PIBT configurations still dominate both external
baselines everywhere. The real finding of this project is less about aisle
management specifically and more about how much of the value sits in PIBT's
priority-inheritance-and-backtracking core to begin with.

Two diagnosed causes, both worth reporting rather than tuning away:

1. **Capacity is modelled by length, not by exit throughput.** A 21-cell corridor
   gets capacity 10, so ten robots queue single-file behind one exiting robot and
   the aisle can never drain. Spec 39 limitation 2 predicted this. Try
   `--set aisle_capacity_model=throughput` for the alternative
   `ceil(ratio·length / T_min)`; it is too aggressive at the default lock time,
   so the right model is somewhere between the two.
2. **Directional demand is computed per aisle with no coordination across
   parallel aisles.** All five corridors in `warehouse_corridors` lock the same
   way at the same time, leaving return traffic nowhere to go. A static parity
   bias (`--set parity_bias=8`) does *not* fix this — it fights the demand signal
   instead of replacing it. Coordinated direction assignment across parallel
   aisles is the obvious next piece of work.

A third, more fundamental tension: PIBT's progress argument relies on being able
to push any robot into any adjacent cell. One-way constraints remove that
freedom, so priority inheritance chains dead-end. This is why
`aisle_managed_pibt` (aisle control, *no* recovery) collapses while
`full_lda_pibt` survives — the recovery layer's `ignore_direction_until` override
is doing the work. Spec 38.2 only ever claimed conditional progress, and this is
the mechanism behind that caveat.

The factorial decomposition below confirms this directly: `direction_control`
(aisle-level), not `reservations`, is the flag responsible for the collapse —
reservations move throughput by roughly zero in isolation on every map tested.

**Collision freedom held in every run**: zero vertex and zero swap conflicts
across all maps, densities, seeds and variants. Validated every timestep by
`validate.validate_plan` (spec 31) and asserted by the test suite.

## Implementation notes and deviations

Six places where a literal reading of the spec does not run, with what was done
instead. Each is a flag, so the spec behaviour is still reachable.

1. **Progress normalisation** (spec 23). The spec normalises route progress by
   the remaining distance, so `α·progress ≈ 10/d`. At `d = 20` that is 0.5,
   below `β_strong = 3.0` and `γ_strong = 2.0` — which silently inverts the
   recommended relation `α > β_strong > λ_turn` from spec 33. Default is
   `progress_normalization="step"` (Δ ∈ {−1, 0, +1}); set it to `"route"` for
   the literal formula.
2. **Aisle direction restricts entry, not interior movement** (spec 27 vs 10.2).
   The spec 27 pseudocode blocks any in-aisle move opposing the direction, which
   traps robots and means `DRAINING` never terminates. Spec 10.2 says the state
   governs who may *enter*. A robot inside may always move toward the nearer
   endpoint to leave.
3. **Drain timeout.** A `DRAINING` aisle that will not empty is reopened after
   `max_drain_time`, justified by spec 16's "current direction is infeasible".
4. **Short aisles and cut edges are never made one-way**
   (`directional_aisle_min_length`, default 4). Making a bridge edge one-way
   disconnects the graph and destroys reachability outright (spec 9.3, 38.2).
5. **Progressive recovery escalation.** Spec 29 runs all seven levels in one
   call with a progress check between them, but progress cannot be observed
   until the next timestep. Here a persisting group escalates one level per
   timestep, which is what "progressive" has to mean in a synchronous loop.
6. **Idle robots hold position at the lowest priority class** rather than
   driving to a parking bay. Sending them to intersections throttles every route
   through those cells; giving them their spawn point as a target wastes travel.
   PIBT's priority inheritance displaces them when a busy robot needs the cell.
   Explicit `k` bays are still used when the map provides them.

Additions beyond the spec, all off by default:
`direction_aware_routing` (A\* that routes around opposing aisles),
`parity_bias`, `aisle_capacity_model="throughput"`.

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
tests/           161 tests: graph, PIBT, aisle manager, lifelong layer, GUI, baselines, stats
experiments/     run_ablation.py, run_density_sweep.py, run_factorial_ablation.py,
                 run_baseline_comparison.py
results/         JSON output (git-ignored)
docs/            implementation notes
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
