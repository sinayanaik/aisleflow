# Comparison animations

Five animations, each a claim this project makes shown as a picture.
Every panel is a real run of the simulator in this repository, and the two
panels of a frame share a map, a seed, a robot count, an arrival rate and a
task stream -- they differ in the planner and nothing else.

The numbers quoted below are the ones in [../05-results.md](../05-results.md),
measured over five seeds; a single seeded run is one draw from that, so a
GIF shows the mechanism rather than the average.

Regenerate them all with:

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

The single clearest picture in the project. Token Passing plans each robot a collision-free path through a reservation table and holds position when it cannot find one. In a one-corridor map every robot eventually queues nose-to-tail in that corridor, no robot can reserve a path through the robots ahead of it, and nothing ever moves again: watch the left panel turn entirely red and stay there while its delivered count stops. The right panel is the same instant of the same scenario under priority inheritance, where a blocked robot pushes the robot ahead of it out of the way and the queue drains. This is the failure mode PIBT was invented to remove, and it is structural: no amount of tuning removes it from Token Passing. Measured over five seeds on this map: 147 tasks per 1000 timesteps against 8.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Same map, same 16 robots, same tasks. One corridor joins the two halves.
- **t = 40** -- Robots reach the corridor. Token Passing must reserve a whole free path before it moves one.
- **t = 90** -- Left: the corridor is full, so no free path exists to reserve — and a robot with no reservation waits.
- **t = 150** -- Left: each robot now waits on a robot that is waiting on it. Red = stuck. The delivered count has stopped.
- **t = 230** -- Right: PIBT lets a blocked robot PUSH the one ahead of it, so the same queue keeps draining.
- **t = 320** -- Nothing on the left will move again — its count has stopped climbing. The failure is structural, not a tuning problem.

</details>

```bash
python3 tools/make_gifs.py --only gridlock
```

## The cheapest term in the score, and what it buys

![The cheapest term in the score, and what it buys](02-turning-cost-on-a-tight-floor.gif)

**warehouse_corridors**, 35 robots, arrival rate 1.0, 400 timesteps, seed 0. `lifelong_pibt` vs `turning_cost_only`.

The largest single win in the repository, and it is one term. Both panels run the same lifelong PIBT on the same corridors with the same jobs; the right one additionally charges a robot for reversing direction. In a single-file corridor a robot that oscillates blocks everything behind it in both directions, and the whole queue spends its time undoing the previous step. The penalty is small by construction - it can only break ties within a tier, never outrank a step of progress - and that is the point: it settles the ties that decide whether a corridor drains or thrashes. Measured over five seeds on this map: 196 tasks per 1000 timesteps against 130, a 50% gain from the cheapest term in the score.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Same map, same 35 robots, same jobs. One term of the movement score apart.
- **t = 50** -- Both sides score a step toward the goal at 10. The right side also charges a robot for reversing.
- **t = 120** -- Left: in a single-file corridor an oscillating robot blocks everything behind it, both ways.
- **t = 200** -- Right: the turning cost breaks that tie, so corridors commit to a direction and drain.
- **t = 290** -- The penalty never outranks progress — it is far too small. It only decides ties, and the ties are what mattered.
- **t = 350** -- Five seeds on this map: 196 against 130 tasks per 1000 steps. One term, +50%.

</details>

```bash
python3 tools/make_gifs.py --only turning-cost
```

## A planner that replans, against one that pushes

![A planner that replans, against one that pushes](03-rhcr-replanning-stalls.gif)

**warehouse_corridors**, 35 robots, arrival rate 1.0, 400 timesteps, seed 0. `rhcr` vs `full_lda_pibt`.

The second published baseline, and it fails the same way the first does. RHCR plans a bounded-horizon, collision-free set of paths and replans on a rolling schedule, which works well when the windowed instance is solvable. On five single-file corridors at this density it usually is not: a robot whose windowed search finds no path holds position, holding position makes the next window harder, and the system settles into a state it cannot plan its way out of. Aisleflow never solves an instance at all - it resolves each conflict locally by lending priority to the robot in the way - so there is no search to fail. Measured over five seeds on this map: 153 tasks per 1000 timesteps against RHCR's 0.5. That is a large ratio and a weak claim, and the results page says so: the comparison that carries information is against plain lifelong PIBT, not against a baseline that starves.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Same map, same 35 robots, same jobs. Two ways of avoiding a collision.
- **t = 50** -- Left: RHCR solves a bounded window of the whole instance, then replans a few steps later.
- **t = 120** -- Left: with corridors this dense the window stops being solvable — and a robot with no plan holds position.
- **t = 200** -- Holding position makes the next window harder. Red = stuck.
- **t = 280** -- Right never solves an instance: a blocked robot lends its rank to the robot in the way and pushes through.
- **t = 350** -- Five seeds: 153 against 0.5 tasks per 1000 steps. A big ratio, but the honest comparison is plain PIBT — see figure 03.

</details>

```bash
python3 tools/make_gifs.py --only rhcr
```

## Rescuing a deadlock, and rescuing a queue

![Rescuing a deadlock, and rescuing a queue](04-recovery-corroboration.gif)

**warehouse_corridors**, 35 robots, arrival rate 1.0, 400 timesteps, seed 0. `recovery_uncorroborated` vs `recovery_only`.

In dense lifelong traffic, 'this robot has not progressed for a while' does not describe a deadlock - it describes an ordinary queue. On the left that signal alone escalates recovery, whose upper levels reverse robots, send them to escape vertices and hijack their waypoints; healthy queues get taken apart and rebuilt continuously and the delivered count barely moves. On the right the same detector must also see a wait-for cycle or a repeated configuration before it fires. Measured over five seeds on this map: 156 tasks per 1000 timesteps against 17, a nine-fold difference produced entirely by refusing to act on the weakest of the three stall signals. Pooled over all four maps the sensitivity suite puts the corroboration rule at -54% (p < 0.001), which makes it the second most load-bearing thing in the planner.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- Both sides run the SAME seven-level recovery. They differ only in what is allowed to trigger it.
- **t = 50** -- Left fires on one signal alone: no progress for stall_steps steps.
- **t = 120** -- But in dense lifelong traffic that signal describes an ordinary queue, not a deadlock.
- **t = 200** -- Left: healthy queues are reversed, sent to escape vertices and rebuilt — continuously. The delivered count barely moves.
- **t = 280** -- Right also needs a wait-for cycle or a repeated configuration before it acts, so queues are left to drain.
- **t = 350** -- Across five seeds: 156 against 17 tasks per 1000 steps, from refusing to act on the weakest of three stall signals.

</details>

```bash
python3 tools/make_gifs.py --only recovery
```

## Where the congestion machinery costs more than it earns

![Where the congestion machinery costs more than it earns](05-open-map-honesty.gif)

**warehouse_medium**, 40 robots, arrival rate 1.5, 400 timesteps, seed 0. `full_lda_pibt` vs `lifelong_pibt`.

Every mechanism in this project buys something and costs something. On a map with many parallel routes and no scarce single-file aisle, the thing this machinery buys - orderly flow through a contended corridor - is not scarce, while the thing it costs is: a robot that declines to turn, keeps to its lane and steers around a crowd is taking a longer route than it needed to, and there was no congestion to justify it. The right panel simply delivers more, throughout. Over five seeds it is 502 against 416 tasks per 1000 timesteps. The honest summary of this project is that its congestion machinery wins on aisle-constrained maps and loses on open ones, and this GIF is the losing half.

<details><summary>The narration, beat by beat</summary>

- **t = 0** -- The case this project LOSES, shown as plainly as the ones it wins.
- **t = 50** -- An open grid: many parallel routes, and no scarce single-file aisle to fight over.
- **t = 130** -- Left still pays to keep its lane and avoid the crowd — with no congestion to justify either.
- **t = 220** -- Right scores progress and nothing else, and simply keeps delivering more, throughout.
- **t = 300** -- Nothing here is stuck on either side. The cost is pure overhead, not gridlock.
- **t = 350** -- The machinery wins on aisle-constrained maps and loses on open ones. Five seeds: 416 against 502 per 1000 steps.

</details>

```bash
python3 tools/make_gifs.py --only open-map
```
