# Comparison animations

Five animations, each a claim this project makes shown as a picture.
Every panel is a real run of the simulator in this repository, and the two
panels of a frame share a map, a seed, a robot count, an arrival rate and a
task stream -- they differ in the planner and nothing else.

These were rendered before the aisle-direction layer was removed, so any
one-way tinting they show describes a mechanism the planner no longer has;
the planner comparison each one makes still stands. Regenerate them with:

```bash
python3 tools/make_gifs.py            # needs pillow: pip install -e ".[viz]"
```

## How to read one

They are built to be followed on a first watch, at two timesteps per frame,
with a pause on the opening frame to read the setup and a longer one on the
last to read the outcome.

| On the frame | Means |
| --- | --- |
| Blue dot | a robot on its way to a pickup |
| Teal dot | a robot carrying a task to a delivery |
| Grey dot | a robot with no task yet |
| **Red dot** | that robot has not moved for 15 timesteps |
| **Red frame + GRIDLOCKED** | most of that panel's robots are stuck |
| Big number | tasks delivered so far, green on whichever side is ahead |
| Chart | tasks delivered over the whole run, drawn as it plays |
| Band along the bottom | what is happening in this part of the run, and why |

One colour rule carries most of the argument: **red means stuck**, and
nothing else on the frame is red. A side that fills with red has stopped
delivering, and the chart under it flattens at the same moment.

## A queue that never resolves, and one that does

![A queue that never resolves, and one that does](01-token-passing-gridlock.gif)

**warehouse_bottleneck**, 16 robots, arrival rate 0.8, 400 timesteps, seed 0. `token_passing` vs `full_lda_pibt`.

The single clearest picture in the project. Token Passing plans each robot a collision-free path through a reservation table and holds position when it cannot find one. In a one-corridor map every robot eventually queues nose-to-tail in that corridor, no robot can reserve a path through the robots ahead of it, and nothing ever moves again: watch the left panel turn entirely red and stay there while its delivered count stops. The right panel is the same instant of the same scenario under priority inheritance, where a blocked robot pushes the robot ahead of it out of the way and the queue drains. This is the failure mode PIBT was invented to remove, and it is structural: no amount of tuning removes it from Token Passing.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Same map, same 16 robots, same tasks. One corridor joins the two halves.
- **t = 40** -- Robots reach the corridor. Token Passing must reserve a whole free path before it moves one.
- **t = 90** -- Left: the corridor is full, so no free path exists to reserve — and a robot with no reservation waits.
- **t = 150** -- Left: each robot now waits on a robot that is waiting on it. Red = stuck. The delivered count has stopped.
- **t = 230** -- Right: PIBT lets a blocked robot PUSH the one ahead of it, so the same queue keeps draining.
- **t = 320** -- Nothing on the left will move again — the count is frozen at zero. The failure is structural, not a tuning problem.

</details>

```bash
python3 tools/make_gifs.py --only gridlock
```

## One-way as a constraint, one-way as a price

![One-way as a constraint, one-way as a price](02-hard-vs-soft-direction.gif)

**warehouse_corridors**, 35 robots, arrival rate 1.0, 400 timesteps, seed 0. `aisle_direction_hard` vs `aisle_direction_only`.

The core argument of the method, as a picture. Both panels commit the same aisle directions; they differ only in what a committed direction does to a move that opposes it. On the left it removes the move from the candidate set, which is what the specification literally says and what most one-way schemes do - and priority inheritance needs a robot to always have somewhere to be pushed, so deleting that option strands whole corridors. On the right the same move survives and simply costs 8, less than the 10 a step of progress is worth, so a robot drives the wrong way when that is the only way through and pays for it. Measured over five seeds this is worth between 1.9x and 3.1x throughput - the largest single effect in the repository.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Both sides commit the SAME one-way directions. They differ only in what that does to a move going the wrong way.
- **t = 50** -- Left: a counterflow move is deleted outright. Right: it survives, priced at 8 against the 10 a step of progress earns.
- **t = 120** -- Priority inheritance needs somewhere to push a blocked robot. Delete counterflow and whole corridors have nowhere.
- **t = 200** -- Left: corridors strand, and the red spreads. Right: robots drive the wrong way only when nothing else gets through.
- **t = 290** -- Right pays for each of those wrong-way steps and still delivers far more.
- **t = 350** -- One rule, two enforcements. Across five seeds at 1000 steps, pricing it beats deleting it by 1.9x-3.1x.

</details>

```bash
python3 tools/make_gifs.py --only hard-vs-soft
```

## An aisle that never flips, and one that must

![An aisle that never flips, and one that must](03-maximum-green-starvation.gif)

**warehouse_narrow**, 30 robots, arrival rate 1.2, 400 timesteps, seed 0. `aisle_direction_no_max_green` vs `aisle_direction_only`.

Hysteresis is only half a traffic signal. A dead band and a minimum lock bound how soon an aisle may change direction and say nothing about how long it may keep one - and a warehouse with pickups down one side and deliveries down the other produces near-balanced demand by construction, so the imbalance never breaks the band. On the left the aisle tints settle and stop changing: robots wanting the other direction wait, and keep waiting. On the right the same aisles reach their maximum green, turn purple as they DRAIN, and commit the opposite direction once empty. Drain-before-reverse is visible in every flip: the aisle empties before it turns, so no two robots ever meet head-on inside it. This is what makes the aisle layer starvation-free rather than merely non-flapping.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Aisle tint is the committed one-way direction; the arrows show which way it flows.
- **t = 60** -- Left has hysteresis only: an aisle keeps its direction until demand imbalance breaks the dead band.
- **t = 130** -- Pickups down one side, deliveries down the other, so demand stays near-balanced — the band never breaks.
- **t = 190** -- Right adds a maximum green: past T_max the aisle turns purple, DRAINS empty, then commits the other way.
- **t = 260** -- Draining before reversing is why no two robots ever meet head-on inside an aisle.
- **t = 330** -- Both sides deliver about the same here. The chart is the claim: left's aisles barely flip, so waiting robots keep waiting.

</details>

```bash
python3 tools/make_gifs.py --only max-green
```

## Rescuing a deadlock, and rescuing a queue

![Rescuing a deadlock, and rescuing a queue](04-recovery-corroboration.gif)

**warehouse_corridors**, 35 robots, arrival rate 1.0, 400 timesteps, seed 0. `recovery_uncorroborated` vs `recovery_only`.

In dense lifelong traffic, 'this robot has not progressed for a while' does not describe a deadlock - it describes an ordinary queue. On the left that signal alone escalates recovery, whose upper levels reverse robots, send them to escape vertices and hijack their waypoints; healthy queues get taken apart and rebuilt continuously and the delivered count barely moves. On the right the same detector must also see a wait-for cycle or a repeated configuration before it fires. Measured on this map: 0.134 tasks per step against 0.022, a six-fold difference produced entirely by refusing to act on the weakest of the three stall signals.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Both sides run the SAME seven-level recovery. They differ only in what is allowed to trigger it.
- **t = 50** -- Left fires on one signal alone: no progress for t_blocked steps.
- **t = 120** -- But in dense lifelong traffic that signal describes an ordinary queue, not a deadlock.
- **t = 200** -- Left: healthy queues are reversed, sent to escape vertices and rebuilt — continuously. The delivered count barely moves.
- **t = 280** -- Right also needs a wait-for cycle or a repeated configuration before it acts, so queues are left to drain.
- **t = 350** -- Across five seeds: 0.134 tasks per step against 0.022, from refusing to act on the weakest of three stall signals.

</details>

```bash
python3 tools/make_gifs.py --only recovery
```

## Where the aisle layer costs more than it earns

![Where the aisle layer costs more than it earns](05-open-map-honesty.gif)

**warehouse_medium**, 40 robots, arrival rate 1.5, 400 timesteps, seed 0. `full_lda_pibt` vs `lifelong_pibt`.

Every mechanism in this project buys something and costs something. On a map with many parallel routes and no scarce single-file aisle, the thing aisle management buys - orderly flow through a contended corridor - is not scarce, while the thing it costs is: a robot whose shortest route runs against a committed direction either detours or pays the counterflow penalty, and there was no congestion to justify either. The right panel simply delivers more, throughout. Over five seeds it is 502 against 313 tasks per 1000 timesteps. The honest summary of this project is that its aisle layer wins on aisle-constrained maps and loses on open ones, and this GIF is the losing half.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- The case this project LOSES, shown as plainly as the ones it wins.
- **t = 50** -- An open grid: many parallel routes, and no scarce single-file aisle to fight over.
- **t = 130** -- Left still commits aisle directions, so some robots detour or pay counterflow — with no congestion to justify either.
- **t = 220** -- Right has no aisle layer at all, and simply keeps delivering more, throughout.
- **t = 300** -- Nothing here is stuck on either side. The cost is pure overhead, not gridlock.
- **t = 350** -- Aisle management wins on aisle-constrained maps and loses on open ones. Five seeds: 313 against 502 per 1000 steps.

</details>

```bash
python3 tools/make_gifs.py --only open-map
```
