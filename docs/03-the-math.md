# The maths

*Every formula the planner uses, with every symbol defined. The plain-English
version is [01-how-it-works.md](01-how-it-works.md).*

## Notation

Everything below uses these, and nothing else.

| Symbol | Plain name | Meaning | Range | Where |
|---|---|---|---|---|
| $t$ | timestep | discrete clock; all robots move at once | $0,1,2,\dots$ | `simulator.step` |
| $i$ | robot | a robot | — | `robot.Robot` |
| $x_i$ | position | the cell robot $i$ occupies | a grid cell | `robot.position` |
| $g_i$ | waypoint | where robot $i$ is heading right now | a grid cell | `robot.waypoint` |
| $v$ | candidate | a cell robot $i$ might move into | a grid cell | `pibt.candidates` |
| $N(x)$ | neighbours | the up-to-4 cells adjacent to $x$ | — | `graph.neighbors` |
| $C_i$ | candidate set | $N(x_i)\cup\{x_i\}$ — at most 5 options | — | `pibt.candidates` |
| $d(u,w)$ | route distance | steps along the shortest path, $\infty$ if unreachable | $\ge 0$ | `graph.route_distance` |
| $\Delta_i(v)$ | progress | change in distance to the waypoint | $\{-1,0,+1\}$ | `scoring.progress` |
| $S_i(v)$ | score | how good move $v$ is for robot $i$ | ℝ | `scoring.score` |
| $\kappa_i(v)$ | crowding | how busy cell $v$ is | $[0,1]$ | `congestion.crowding` |
| $\tau_i(v)$ | turn cost | 0 straight, 1 corner, 2 reverse | $\{0,1,2\}$ | `scoring.turning_cost` |
| $\ell_i(v)$ | same lane | 1 if $v$ is in the aisle $i$ is already in | $\{0,1\}$ | `scoring.score` |
| $p_i(t)$ | rank | who gets a contested cell | $\ge 0$ | `priority.compute_priority` |
| $W_i$ | waiting time | consecutive steps robot $i$ has not moved | $\ge 0$ | `robot.waiting_time` |
| $J(i,\sigma)$ | match cost | cost of giving job $\sigma$ to robot $i$ | ℝ | `assignment.assignment_cost` |

Weights are named in words, not letters — `progress_reward`, `turn_penalty`,
and so on. Their values and measured effects are in
[04-parameters.md](04-parameters.md).

## 1. Movement score

The one formula that matters.

$$
S_i(v) \;=\; \underbrace{10\,\Delta_i(v)}_{\text{progress}}
\;+\; \underbrace{b_i\,\ell_i(v)}_{\text{stay in lane}}
\;-\; \underbrace{0.5\,\tau_i(v)}_{\text{turning}}
\;-\; \underbrace{1.0\,\kappa_i(v)}_{\text{crowding}}
$$

with

$$
\Delta_i(v) \;=\; d(x_i, g_i) \;-\; d(v, g_i) \;\in\; \{-1, 0, +1\}
$$

**Why the numbers are what they are.** Adjacent cells differ in distance by
exactly one, so $\Delta_i(v)$ takes only three values and
$10\,\Delta_i(v)\in\{-10,0,+10\}$: candidates fall into three tiers ten points
apart. The other three terms are bounded by

$$
b_i + 0.5\cdot 2 + 1.0 \;=\; 2 + 1 + 1 \;=\; 4 \;<\; 10
$$

so **they can only reorder candidates within a tier, never across one**. A move
that makes progress always beats one that does not. This is asserted by
`test_the_score_has_exactly_the_terms_the_documents_claim`, so adding a
heavier term in future fails the build rather than silently changing the
algorithm.

It is also why five terms were deleted. A term worth 0.2 cannot break a tie
that the terms above it have not already broken; runs without it came out
bit-identical.

**Worked example.** Robot at $(4,4)$, waypoint $(0,4)$, so $d=4$. It arrived
from the south, and the cell to the north is in the same aisle and half
crowded.

| Candidate | $\Delta$ | $\ell$ | $\tau$ | $\kappa$ | Score |
|---|---|---|---|---|---|
| $(3,4)$ north | $+1$ | 1 | 0 | 0.5 | $10+2-0-0.5=\mathbf{11.5}$ |
| $(4,3)$ west | $0$ | 0 | 1 | 0.1 | $0+0-0.5-0.1=-0.6$ |
| $(4,5)$ east | $0$ | 0 | 1 | 0.0 | $0+0-0.5-0.0=-0.5$ |
| $(4,4)$ stay | $0$ | 0 | 0 | 0.2 | $0+0-0-0.2=-0.2$ |
| $(5,4)$ south | $-1$ | 1 | 2 | 0.0 | $-10+2-1-0=-9.0$ |

North wins by 12 points. Nothing but progress decided it — which is the normal
case. The tie-breaks matter only when two candidates share a $\Delta$.

### Lane bonus

$$
b_i \;=\;
\begin{cases}
2.0 & d(x_i,g_i) > 8 \quad\text{(transit)}\\[2pt]
1.25 & 2 < d(x_i,g_i) \le 8 \quad\text{(approach)}\\[2pt]
0.5 & d(x_i,g_i) \le 2 \quad\text{(arrival)}
\end{cases}
$$

Strong while travelling, weak on arrival: a robot crossing the floor should
commit to a lane, but one about to arrive must be free to turn off. $\ell_i(v)$
is forced to 0 when the robot is standing on a junction, where "the aisle I am
in" is not defined.

### Crowding

$$
\kappa_i(v) \;=\; \tfrac12\Bigl(\underbrace{\rho(v)}_{\text{local}} + \underbrace{\lambda(v)}_{\text{aisle}}\Bigr)
$$

$\rho(v)$ is the fraction of cells within radius 3 of $v$ holding a robot;
$\lambda(v)$ is the occupancy of $v$'s aisle over that aisle's capacity. Both
are fractions, so $\kappa\in[0,1]$ and `crowding_penalty` reads directly as
"what a completely jammed cell costs".

> Both halves must be fractions. An earlier version mixed a raw robot *count*
> with a ratio, which let crowding reach the scale of a whole step of progress
> and quietly made it the second-largest term in the score, inverting the tier
> structure above. A test now pins $\kappa\le 1$.

## 2. Rank

$$
p_i(t) \;=\; \underbrace{100\,r_i}_{\text{job class}}
\;+\; \underbrace{50\,[\,x_i \text{ in an aisle}\,]}_{\text{clear tight spots}}
\;+\; \underbrace{5\,W_i}_{\text{fairness}}
\;+\; \epsilon_i
$$

$r_i\in\{0,1,2,3,4\}$ ranks what the robot is doing: recovering (4), loaded
(3), heading to a pickup (2), repositioning (1), idle (0). $\epsilon_i$ is a
fixed per-robot tie-breaker that makes the ordering deterministic.

**The fairness guarantee.** Waiting buys rank at a fixed rate, so a robot that
has waited

$$
\frac{4 \times 100}{5} \;=\; 80 \text{ steps}
$$

outranks *anything*, whatever it is doing. No robot can be ignored
indefinitely. Measured maximum wait in practice: 2–3 steps.

The class constants used to be five separately-tunable numbers (400, 300, 200,
100, 0). Only their order ever mattered, so they are one rank times one spread.

## 3. Match cost

Greedy assignment: repeatedly take the cheapest (robot, job) pair.

$$
J(i,\sigma) \;=\; d(x_i, \text{pick}_\sigma)
\;+\; 0.5\, d(\text{pick}_\sigma, \text{drop}_\sigma)
\;+\; 12\, \bar\kappa(x_i \to \text{pick}_\sigma)
\;-\; 0.5 \min(W_\sigma, 60)
\;+\; \beta(i,\sigma)
$$

Distance to the pickup, plus half the delivery trip it commits to, plus how
crowded the way there is, minus how long the job has already waited (so old
jobs are preferred), plus a small penalty for routing through chokepoints.

The waiting term is **capped at 60**. In a lifelong run $W_\sigma$ grows
without bound; uncapped it swamps every other term within about a hundred steps
and the match degenerates into oldest-job-first, which is why crowding-aware
matching could never show an effect before the cap existed.

## 4. Legality

These are the only hard rules, and they are what makes the plan collision-free.
For all robots $i \ne j$ at every step:

$$
x_i(t{+}1) \ne x_j(t{+}1)
\qquad\text{(no two robots in one cell)}
$$

$$
\neg\bigl(x_i(t{+}1) = x_j(t) \;\wedge\; x_j(t{+}1) = x_i(t)\bigr)
\qquad\text{(no swapping through each other)}
$$

Checked after planning, every step, by `validate.validate_plan`. Every run in
every experiment reports `collision_free: true`; none of the terms deleted in
this pass had anything to do with it.

## 5. Statistics

Variants are run on matched seeds — seed $k$ produces an identical job stream
in every arm — so comparisons are **paired**. For per-seed differences
$\delta_k = a_k - b_k$:

- **Confidence interval**: percentile bootstrap on $\bar\delta$.
- **Significance**: sign-flip permutation test. Under the null the sign of each
  $\delta_k$ is arbitrary, so all $2^n$ sign patterns are enumerated exactly
  (at $n=10$ that is 1024, giving a two-sided $p$-floor of $2/1024 \approx
  0.002$).

Pooling the two arms and relabelling them — the unpaired test — throws the
matched job stream away. On a clean effect it returns $p = 0.081$ where the
paired test returns $p = 0.002$: it would have needed several times the seeds
to see the same thing.
