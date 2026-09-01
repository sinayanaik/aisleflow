# Results

## The headline

Tasks delivered per timestep, against every planner we can compare with.

<!-- generated:headline -->
| Planner | bottleneck | corridors | medium |
| --- | ---: | ---: | ---: |
| **This planner** | 0.145 | 0.121 | 0.313 |
| Plain lifelong PIBT | 0.118 | 0.131 | 0.502 |
| Token Passing | 0.007 | 0.000 | 0.004 |
| Token Passing + recovery | 0.008 | 0.000 | 0.027 |
| RHCR | 0.011 | 0.001 | 0.014 |

*Tasks delivered per timestep; higher is better. 5 seeds x 400 steps, identical job streams across planners. git `e38a849`.*
<!-- /generated:headline -->

**Read the baselines honestly.** Token Passing and RHCR score close to zero on
these maps. That is not a scalp: both are published algorithms that starve
here, because a robot whose space-time search fails simply waits, and in dense
lifelong traffic it keeps failing. The comparison that carries information is
the **plain lifelong PIBT** row — same collision resolution, none of the
scoring, matching or recovery machinery. That is the number to judge this
planner by.

## What this pass changed

The planner had ~45 hand-chosen numbers across seven separate models, none with
published justification. Every one was measured. The result:

| | before | after |
|---|---:|---:|
| terms in the movement score | 9 | 4 |
| tunable parameters | 60 | 37 |
| lines of planner source | ~3,900 | ~2,200 |
| throughput (4 maps, vs. before) | — | **+1% to +51%** |

Every run in every experiment reports `collision_free: true`, before and after.
Nothing removed had anything to do with collision safety — only PIBT's vertex
and swap checks provide that.

### The three findings that drove it

**1. The score was mostly decorative.** Progress is worth 10 and a move changes
distance by exactly ±1, so candidates fall into tiers ten points apart.
Every other term totalled under 4, so it could only reorder *within* a tier.
Five of the nine terms were smaller than a tie-break and never changed a
decision: runs without them were **bit-identical** on every deterministic
metric, every seed, every map.

**2. The one-way aisle mechanism cost throughput.** The planner's headline
feature charged a robot for moving against its aisle's committed direction, and
for entering one without a permit. Removing the permit penalty alone raised
throughput 33.6% (p < 0.0001); removing it together with the counterflow
penalty and the heading term raised it 39.6% with no map made worse.

**3. With those gone, the whole aisle-direction layer measured nothing.** The
demand vote, the green/drain state machine, maximum green, direction-aware
routing — 450 lines — came to **−0.3% (p = 0.95)**, swinging between −12% and
+9% by map. That is noise, not a mechanism, so it was deleted. Aisles remain as
*geometry*: the score still rewards staying in one lane, and crowding is still
measured per corridor.

### Two bugs found by measuring

- **Maximum green never fired for negative voters.** The starvation rule tested
  `opposing_demand > 0`, but the demand score is signed — a long remaining route
  and a crowded aisle subtract from it — so legitimate traffic routinely voted
  negative and was not counted. Aisles held one direction for 130 steps against
  robots that plainly wanted through.
- **A rename silently ate overrides.** `LEGACY_NAMES` was itself caught by the
  rename sweep, so its keys became the *new* names mapping to themselves. The
  alias expansion pops every key it recognises, so `merged(turn_penalty=1.5)`
  returned the default 0.5 — no error, no warning. A test now forbids the
  collision.

## Every knob, measured

Negative means removing the knob costs throughput; positive means the planner
is better without it.

<!-- generated:sensitivity -->
| Family | Knob neutralised | Pooled effect | p | Worst map |
| --- | --- | ---: | ---: | --- |
| score | `progress_reward` | -100.0% | 0.000 | bottleneck (-100.0%) |
| recovery | `require_deadlock_corroboration` | -54.1% | 0.000 | corridors (-90.2%) |
| score | `reverse_multiplier` | -17.2% | 0.000 | narrow (-24.5%) |
| score | `turn_penalty` | -15.1% | 0.004 | corridors (-28.9%) |
| priority | `waiting_weight` | -10.0% | 0.034 | corridors (-23.8%) |
| congestion | `local_congestion_radius=1` | -7.9% | 0.070 | corridors (-22.0%) |
| assignment | `cost_to_pickup` | -6.4% | 0.086 | narrow (-15.1%) |
| congestion | `local_congestion_radius=5` | -5.2% | 0.111 | corridors (-16.7%) |
| priority | `priority_inside_aisle` | -4.4% | 0.203 | corridors (-20.9%) |
| assignment | `cost_pickup_to_delivery` | -3.5% | 0.202 | narrow (-8.7%) |
| recovery | `stall_steps` | -2.9% | 0.050 | corridors (-6.5%) |
| assignment | `cost_waiting_cap` | -2.8% | 0.116 | corridors (-8.2%) |
| score | `aisle_bonus/aisle_bonus_near` | -2.6% | 0.475 | narrow (-11.4%) |
| assignment | `cost_congestion` | -2.4% | 0.503 | corridors (-9.1%) |
| score | `crowding_penalty` | -0.3% | 0.913 | corridors (-16.2%) |
| recovery | `recovery_max_level=4` | +0.0% | 1.000 | — |
| recovery | `recovery_max_level=4` | +0.0% | 1.000 | — |
| assignment | `cost_blocking` | +0.0% | 0.744 | — |
| assignment | `cost_waiting` | +0.1% | 0.984 | corridors (-11.7%) |
| recovery | `recovery_max_level=0` | +4.2% | 0.061 | — |
| recovery | `recovery_max_level=1` | +4.2% | 0.061 | — |
| recovery | `recovery_max_level=2` | +4.2% | 0.061 | — |
| recovery | `recovery_max_level=3` | +4.2% | 0.061 | — |
| recovery | `recovery_max_level=3` | +4.2% | 0.061 | — |
| recovery | `recovery` | +4.2% | 0.061 | — |

*25 variants, 10 seeds, 400 steps, 4 maps. Effect is the change in throughput from removing the knob, so a positive number means the planner is better without it. Paired sign-flip test; git `b00ff91`.*
<!-- /generated:sensitivity -->

### Caveats worth stating

- **Four maps.** Every conclusion here is about `warehouse_bottleneck`,
  `warehouse_corridors`, `warehouse_narrow` and `warehouse_medium` at the
  robot counts and arrival rates in the dataset's `meta.scenarios`. A knob
  that is inert here may matter on a map with longer, tighter corridors.
- **Pooling hides disagreement.** The worst-map column exists because a knob
  can help on one map and hurt on another and average to zero. Read it.
- **`recovery` is a genuine trade-off, not a free win.** Turning deadlock
  recovery off raises throughput 4.2%, but jams then go unresolved (6.4 per run
  on corridors, against 0.3) and p95 service time gets *worse*. It was kept.
  Its detection rule, though, is the second most load-bearing thing in the
  planner: accepting "nobody moved" as a jam, without requiring a wait-for
  cycle or a repeated configuration, costs **54%** of throughput, because
  ordinary queueing then trips recovery constantly.
- **No multiple-comparison correction.** 23 knobs at α = 0.05 means roughly one
  false positive is expected. The load-bearing findings are far past that
  threshold; the marginal ones should be treated as suggestive.

## Reproducing

```bash
pip install -r requirements-dev.txt
python3 experiments/run_sensitivity.py --seeds 10 --jobs 4   # ~4 min
python3 experiments/run_all.py --seeds 5                     # ~30 min
python3 tools/make_docs_tables.py                            # refresh this page
python3 tools/make_figures.py
```

Every dataset in `docs/data/` carries a `meta` block with the git SHA, the
seeds, the horizon and the exact scenarios, so any row here can be traced to
the run that produced it.

## Animations

Side-by-side runs sharing a map, a seed, a robot count and a job stream, and
differing only in the planner. Red means a robot has not moved for 15 steps.

![Token Passing gridlock](gifs/01-token-passing-gridlock.gif)

Rebuild with `python3 tools/make_gifs.py`.
