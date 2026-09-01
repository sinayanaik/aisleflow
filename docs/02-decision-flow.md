# Decision flow

*What happens each timestep, in order, and where each decision is made.*

## One timestep

All robots move simultaneously. A step is a pipeline: decide who does what,
decide where everyone wants to go, resolve the conflicts, then execute.

```mermaid
flowchart TD
    A["<b>1. New jobs arrive</b><br/><i>task.TaskGenerator</i>"] --> B
    B["<b>2. Update job state</b><br/>picked up? delivered?<br/><i>assignment.update_task_state</i>"] --> C
    C["<b>3. Match idle robots to jobs</b><br/>greedy, cheapest pair first<br/><i>assignment.TaskAssigner</i>"] --> D
    D["<b>4. Set each robot's waypoint</b><br/>and how close it is to it<br/><i>assignment.update_waypoint</i>"] --> E
    E["<b>5. Route to the waypoint</b><br/>shortest path on the grid<br/><i>graph.shortest_route</i>"] --> F
    F["<b>6. Check for jams</b><br/>and escalate if confirmed<br/><i>deadlock.DeadlockMonitor</i>"] --> G
    G["<b>7. Rank every robot</b><br/>job class + waiting time<br/><i>priority.compute_priority</i>"] --> H
    H["<b>8. Choose moves</b><br/>score, then push and backtrack<br/><i>pibt.PIBTPlanner</i>"] --> I
    I{"<b>9. Is the joint move legal?</b><br/><i>validate.validate_plan</i>"}
    I -- no --> X["raise PlanningError<br/>(never fires; this is the guard)"]
    I -- yes --> J["<b>10. Everyone moves at once</b><br/><i>robot.execute_moves</i>"]
    J --> K["<b>11. Record metrics</b><br/><i>metrics.MetricsCollector</i>"]
    K --> A
```

Steps 1–7 are advisory: they express what each robot *wants*. Step 8 is the
only one that decides what actually happens, and step 9 proves it was legal.

## Choosing one robot's move

```mermaid
flowchart TD
    A["Robot needs a move"] --> B["List candidates:<br/>4 neighbours + stay put"]
    B --> C{"Legal?<br/>on the grid, no vertex clash,<br/>no swap with my parent"}
    C -- no --> D["drop it"]
    C -- yes --> E["Score it"]
    E --> F["Sort: best score first"]
    F --> G["Try the best remaining candidate"]
    G --> H{"Is a robot sitting there?"}
    H -- "no" --> I["<b>Take it</b>"]
    H -- "yes, and it already<br/>moved elsewhere" --> I
    H -- "yes, and it has<br/>not moved yet" --> J["<b>Push:</b> ask that robot to move,<br/>lending it my rank"]
    J --> K{"Did it find a move?"}
    K -- yes --> I
    K -- no --> L["<b>Backtrack:</b> release the cell,<br/>try my next candidate"]
    L --> M{"Any candidates left?"}
    M -- yes --> G
    M -- no --> N["<b>Stay put</b><br/>(always available, so this<br/>always terminates)"]
```

The push in the middle is priority inheritance: the robot being asked
temporarily acts with the rank of whoever asked, so a whole chain can shuffle
aside for one important robot. The fallback at the bottom is why the algorithm
cannot fail — staying put is always legal, so every robot always gets a move.

**Nothing in this diagram consults a rule about which way a corridor should
flow.** Aisle direction used to appear twice here, as a candidate filter and as
a score penalty. Both are gone; see [05-results.md](05-results.md).

## Scoring one candidate cell

```mermaid
flowchart LR
    subgraph tier["Tier: worth 10 each"]
        P["progress<br/>−1, 0 or +1"]
    end
    subgraph tie["Tie-breaks: worth under 3 in total"]
        L["same lane?<br/>+2"]
        T["turned?<br/>−0.5, or −1 reversing"]
        C["crowding<br/>−0 to −1"]
    end
    P --> S(("score"))
    L --> S
    T --> S
    C --> S
```

Because progress is worth 10 and moves it by a whole unit, candidates fall into
three bands ten points apart. The tie-breaks total under 3, so **they can
reorder candidates within a band but never across one**. Progress always wins.
That property is asserted by a test, so a future term cannot quietly break it.

## Detecting and clearing a jam

```mermaid
stateDiagram-v2
    [*] --> Moving
    Moving --> Stalled: no progress for stall_steps (10)
    Stalled --> Moving: it was just a queue
    Stalled --> Jammed: AND a wait-for cycle or a repeated configuration
    note right of Stalled
        Both signals are required. "Nobody moved"
        alone is ordinary queueing; treating that
        as a jam costs 54% of throughput.
    end note
    Jammed --> L1: escalate one level per step
    L1: 1. recompute routes
    L1 --> L2
    L2: 2. raise the stuck robots' rank
    L2 --> L3
    L3: 3. allow reversing out
    L3 --> L4
    L4: 4. send to an escape cell
    L1 --> Moving: cleared
    L2 --> Moving: cleared
    L3 --> Moving: cleared
    L4 --> Moving: cleared
    L4 --> Unrecovered: still stuck
    Unrecovered --> Moving: keep applying level 4
```

The ladder used to have seven rungs. Truncating it here measured **better**
than running all seven — the strongest remedies rewrote routes faster than the
jam was clearing. Per-level counters (`recovery_lN_fires`,
`recovery_lN_resolved`) record how often each rung runs and how often the jam
clears just after, so this stays checkable rather than assumed.
