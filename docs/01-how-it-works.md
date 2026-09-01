# How it works

*Plain English, no symbols. For the formulas see [03-the-math.md](03-the-math.md).*

## The problem

A warehouse floor is a grid. Some cells are shelving you cannot drive through;
the rest form aisles and junctions. Dozens of robots drive around it all day,
each carrying out a job: **go to a shelf, pick something up, take it to a
packing station**. New jobs arrive continuously and never stop — that is what
"lifelong" means, and it is what makes this harder than a puzzle with a fixed
goal. There is no final state to reach, only a rate of work to sustain.

Two robots must never occupy the same cell, and two robots must never swap
places (they would drive through each other). Beyond that, everything is a
question of *throughput*: how many jobs get finished per timestep.

The hard part is not finding a path. A single robot's shortest path is easy.
The hard part is that **every robot's path is in every other robot's way**, and
a corridor one cell wide cannot let two robots pass.

## What the planner decides, every second

Time moves in discrete steps. On each one, every robot moves to a neighbouring
cell or stays put, all at once. The planner makes four decisions:

| Decision | Question it answers |
|---|---|
| **Assignment** | Which robot takes which job? |
| **Route** | Which way should this robot go? |
| **Priority** | If two robots want the same cell, who gets it? |
| **Movement** | Which neighbouring cell does each robot actually move into? |

The first three are advisory. Only the fourth is binding, and only the fourth
guarantees no collisions.

## The four ideas

### 1. Score every option, don't forbid any

A robot has at most five options each step: north, south, east, west, or stay.
The planner gives each one a score and tries them best-first.

The dominant term is **progress**: does this move take me closer to where I am
going? Worth 10 points. Since a move changes your distance by exactly −1, 0 or
+1, this sorts the options into three tiers ten points apart.

Everything else is a **tie-break within a tier**, and is deliberately small:

- **stay in your lane** (2 points) — halfway down a corridor, keep going rather
  than ducking sideways into a gap;
- **don't turn** (0.5 points, doubled for a U-turn) — corners are slow;
- **avoid the crowd** (up to 1 point) — prefer the emptier of two equally good
  cells.

That is the entire movement score. It used to have nine terms. Five of them
were smaller than a tie-break and never changed a single decision in any run
we measured — see [05-results.md](05-results.md).

> **Why nothing is forbidden.** It is tempting to make corridors one-way and
> simply delete illegal moves. Doing that breaks the planner: the collision
> algorithm below works by *pushing* a robot out of the way, which needs
> somewhere to push it. Take away that freedom and robots wedge. We measured
> the one-way rule too, and it did not just risk deadlock — it cost throughput.

### 2. Who goes first: rank, and a fairness clock

When two robots want the same cell, rank decides. Rank comes from what the
robot is doing — a robot in trouble outranks a loaded robot, which outranks one
still on its way to a pickup, which outranks an idle one — plus a bonus for
being inside a narrow aisle, so tight spots clear first.

The important part is the clock: **every step a robot spends waiting buys it
rank**. Wait long enough and you outrank anybody. At the default settings that
takes 80 steps, which is the planner's guarantee that no robot is ignored
forever. In practice robots wait about 2–3 steps.

### 3. Resolving conflicts by pushing, not queueing

This is the part that actually prevents collisions, and it is one idea:

> If I want your cell, I ask you to move. If you cannot, I try my next-best
> cell. If none of mine work, I stay put.

The robot doing the asking lends its rank to the robot being asked, so a chain
of robots can shuffle along a corridor to let an important one through — the
whole chain temporarily inherits the importance of whoever started it. Because
this recursion can always fall back on "stay put", it always terminates with a
legal set of moves. This is **PIBT** (Priority Inheritance with Backtracking).

Think of a narrow corridor with a robot at each end. Neither can pass. The
higher-ranked one asks the other to move; that robot has nowhere useful to go,
so it backs into an alcove or reverses out — and the chain resolves.

### 4. Noticing a jam, and escalating gently

Sometimes traffic still locks up: A waits for B, B waits for C, C waits for A.

Detection needs two signals, not one. "Nobody moved" on its own is *normal* —
that is just a queue. A jam also needs either a **cycle** in who-waits-for-whom,
or the same arrangement of robots repeating. Requiring that second signal
matters more than almost anything else in the planner: without it, ordinary
queueing is mistaken for deadlock and throughput drops by 54%.

Once a jam is confirmed, remedies escalate one per step, cheapest first:

1. recompute the routes — often the map has changed enough that this is all it takes;
2. raise the stuck robots' rank so the rest of the floor gives way;
3. let them reverse out of where they wedged themselves;
4. send them to a designated empty parking cell to break the pattern.

There used to be more, stronger remedies. Measuring them showed they tore up
routes faster than the jam cleared, so the ladder now stops here.

## Following one robot

Robot 7 is idle at a junction. A new job appears: fetch from shelf A, deliver
to station B.

1. **It gets the job** — it is nearest, and the way there is not crowded.
2. **It gets a route** — the shortest path to shelf A.
3. **It gets a rank** — "on its way to a pickup", middling.
4. **It scores five options.** North is +10 (closer). South is −10 (further).
   East and west are 0 (sideways). Staying is 0. North wins outright; nothing
   else was close enough to matter.
5. **North is occupied by robot 3.** Robot 7 asks robot 3 to move. Robot 3 has
   a free cell north of *it*, takes it, and robot 7 follows.
6. **Steps 7–14: a corridor.** Every step, north is +10 and the lane bonus adds
   2 for staying in the corridor. It drives straight.
7. **Step 15: the corridor exit is crowded.** Two options now tie on progress,
   both +10. The crowding term breaks the tie — it takes the emptier one.
8. **It reaches shelf A**, picks up, and its rank rises to "loaded". Its
   waypoint becomes station B and the same loop continues.

At no point did anything consult a rule about which way the corridor was
supposed to flow. That is the change this version makes, and
[05-results.md](05-results.md) is the evidence for it.
