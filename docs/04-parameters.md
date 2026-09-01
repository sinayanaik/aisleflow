# Parameters

Every tunable in the planner, with the **measured** cost of removing it.

The point of this page is that no number here is asserted. Each row was
produced by running the planner with that one knob neutralised, on the same
seeds as the control, across four maps —
`python3 experiments/run_sensitivity.py`. Anything that changed nothing was
deleted rather than left in at a default, which is why the list is short.

"Removing it costs" is the drop in throughput when the knob is neutralised, so
a **negative** number means the planner is *better* without it.

<!-- generated:parameters -->
| Parameter | Default | What it does | Removing it costs | Verdict |
| --- | ---: | --- | ---: | --- |
| `progress_reward` | 10.0 | Reward per cell of progress. Sets the tier spacing every other term is judged against. | +100.0% | **load-bearing** |
| `aisle_bonus` | 2.0 | Reward for staying in the aisle the robot is already in. | +2.6% | within noise |
| `aisle_bonus_near` | 0.5 | The same reward once the robot is near its waypoint. | +2.6% | within noise |
| `turn_penalty` | 0.5 | Cost of turning a corner instead of carrying straight on. | +15.1% | **load-bearing** |
| `reverse_multiplier` | 2.0 | Reversing costs this many times a turn. | +17.2% | **load-bearing** |
| `crowding_penalty` | 1.0 | Cost of moving into a completely crowded cell. | +0.3% | within noise |
| `local_congestion_radius` | 3 | Radius of the “how full is it around here” measurement. | +7.9% | earns its place |
| `priority_class_spread` | 100.0 | Rank gap between adjacent job classes. | not measured | — |
| `priority_inside_aisle` | 50.0 | Rank bonus for a robot already inside an aisle. | +4.4% | within noise |
| `waiting_weight` | 5.0 | Rank bought per step waited. The anti-starvation guarantee. | +10.0% | earns its place |
| `stall_steps` | 10 | Steps without progress before a robot counts as stalled. | +2.9% | within noise |
| `require_deadlock_corroboration` | True | Require a cycle or a repeated configuration, not just a lack of progress. | +54.1% | **load-bearing** |
| `recovery_max_level` | 5 | How many recovery remedies may run. | -4.2% | within noise |
| `cost_to_pickup` | 1.0 | Weight on distance to the pickup in the task match. | +6.4% | earns its place |
| `cost_pickup_to_delivery` | 0.5 | Weight on the delivery trip the match commits to. | +3.5% | within noise |
| `cost_congestion` | 12.0 | Weight on how crowded the way to the pickup is. | +2.4% | within noise |
| `cost_waiting` | -0.5 | Negative, so older jobs are preferred. | -0.1% | within noise |
| `cost_waiting_cap` | 60.0 | Cap on the waiting term, so it cannot swamp the match. | +2.8% | within noise |
| `cost_blocking` | 1.0 | Penalty for routing a match through chokepoints. | -0.0% | within noise |

Switches and run settings, not weights: `aisle_capacity`, `assignment_candidate_limit`, `baseline_replan_period`, `baseline_window`, `config_history_length`, `congestion_assignment`, `congestion_scoring`, `deadlock_steps`, `lifelong`, `max_drain_time`, `max_timesteps`, `park_when_idle`, `r_far`, `r_near`, `recovery`, `seed`, `turning_cost`, `validate_every_step`.
<!-- /generated:parameters -->

## How to read the verdicts

- **load-bearing** — removing it costs more than 15% of throughput. Do not
  touch these without rerunning the study.
- **earns its place** — costs 5–15%. Real, but not dramatic.
- **within noise** — under 5% pooled, and no map shows a significant paired
  loss. These survive because they are cheap and intuitive, not because the
  data demands them; a future pass could reasonably cut them.

A knob being "within noise" on these four maps is not proof it is useless
everywhere. It is proof it is useless *here*, which is the only claim the data
supports. The worst-map column in [05-results.md](05-results.md) is there
because pooled averages hide knobs that help on one map and hurt on another.

## Changing them

```bash
# one run, one override
python3 main.py --map maps/warehouse_medium.map --set turn_penalty=1.0

# a saved configuration
python3 -c "from lda_pibt.config import Params; \
            print(Params.from_json('my-params.json'))"
```

Old names still work. `LEGACY_NAMES` maps every pre-simplification name onto
its replacement — `--set lambda_turn=1.0` sets `turn_penalty` — and a name
whose term was deleted raises a `DeprecationWarning` naming this page rather
than failing silently.

> A test asserts that no legacy name collides with a live parameter. That
> collision is invisible at runtime — the override is silently dropped and you
> get the default back — and it did in fact happen once during this
> simplification.
