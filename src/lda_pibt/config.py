"""Tunable parameters (spec section 33) and ablation presets (spec section 34)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict


@dataclass
class Params:
    # ---- candidate scoring (spec 23, 24, 33) -------------------------------
    alpha_progress: float = 10.0
    beta_strong: float = 3.0
    beta_weak: float = 1.0
    gamma_strong: float = 2.0
    gamma_weak: float = 0.5
    lambda_turn: float = 0.5
    lambda_reverse: float = 2.0  # eta_reverse multiplier on the turn penalty
    mu_congestion: float = 1.0
    nu_wait: float = 0.2
    xi_bottleneck: float = 1.0
    #: Penalty for a move that opposes the aisle's committed direction, and for
    #: entering a directional aisle without a valid ticket.  These replace the
    #: hard rejections that used to delete such moves from the candidate set.
    #: The design principle is that the high level *ranks* candidates and never
    #: decides safety, so both must sit strictly between the other soft terms
    #: and `alpha_progress`: a robot takes a counterflow move only when it is
    #: the only way to make progress, or when nothing else is left.
    zeta_counterflow: float = 8.0
    zeta_reservation: float = 8.0
    #: "step" -> Delta in {-1,0,+1}; "route" -> spec 23 normalisation
    progress_normalization: str = "step"

    # congestion mixture weights (spec 23.1)
    omega_local: float = 1.0
    omega_aisle: float = 1.0
    omega_downstream: float = 1.0
    local_congestion_radius: int = 3
    downstream_horizon: int = 5
    #: Report local density as a ratio in [0, 1] rather than a raw robot count,
    #: and normalise the omega mixture to sum to 1.  Without this C_local is a
    #: count of up to ~13 mixed with two ratios, so mu*C reaches the same scale
    #: as alpha*Delta and the intended ordering alpha >> beta > mu breaks down.
    congestion_normalisation: bool = True

    # ---- proximity (spec 13) ---------------------------------------------
    r_near: int = 2
    r_far: int = 8

    # ---- aisle management (spec 12, 16, 17, 18) --------------------------
    minimum_aisle_lock_time: int = 20
    #: Maximum green.  Hysteresis gives an aisle a *minimum* time in one
    #: direction; without a maximum, an aisle facing symmetric demand (the
    #: normal case when pickups are on one side and deliveries on the other)
    #: keeps a direction half its traffic cannot use, forever.  A direction
    #: held this long with any opposing demand must drain and flip.
    maximum_aisle_lock_time: int = 40
    direction_switch_threshold: float = 5.0
    aisle_capacity: int = 10
    #: capacity model. "length" = spec 9.2 (ratio * number of cells).
    #: "throughput" = cells a robot train can clear before the aisle must
    #: flip, i.e. ceil(ratio * length / minimum_lock_time) - a single-file
    #: corridor is gated by its exit, not by how many robots fit inside.
    #: "drain" (default) sits between the two: as many robots as fit, but
    #: never more than can clear the aisle inside `max_drain_time`.
    aisle_capacity_model: str = "drain"
    aisle_capacity_ratio: float = 1.0
    reservation_ttl: int = 15
    #: an aisle that cannot drain within this many steps is reopened
    max_drain_time: int = 30
    #: aisles shorter than this stay bidirectional (short links are not
    #: worth a one-way rule and making them one-way fragments the network)
    directional_aisle_min_length: int = 4
    #: route around aisles flowing the other way instead of queueing
    direction_aware_routing: bool = False
    route_direction_penalty: float = 6.0
    #: extension (0.0 = spec behaviour): persistent per-aisle bias that makes
    #: neighbouring parallel aisles prefer opposite directions, so they do not
    #: all flip together
    parity_bias: float = 0.0
    #: Assign directions as a set rather than one aisle at a time: commit in
    #: descending |imbalance| and roll back any commit that would break strong
    #: connectivity of the directed residual graph.  Measured effect on the
    #: bundled maps is nil (a single one-way aisle never disconnects a ladder),
    #: so this is off by default and kept as a reportable ablation cell.
    coordinate_aisle_directions: bool = False
    #: Aggregate directional demand over every aisle a robot's route touches,
    #: decayed by distance to the entry, instead of charging the robot to its
    #: next aisle alone.  Spec 12 describes this; it measures net negative on
    #: the bundled maps, so it ships off by default and is reported.
    demand_spread: bool = False
    #: Apply aisle direction and reservations as hard candidate rejections
    #: (pre-2026 behaviour) instead of score penalties.  Deleting legal moves
    #: breaks PIBT's progress argument, so this is off; it stays runnable
    #: because the soft-vs-hard comparison is a headline result.
    hard_direction_constraints: bool = False

    # directional demand weights (spec 12)
    w_urgency: float = 1.0
    w_waiting: float = 0.5
    w_proximity: float = 2.0
    w_route_length: float = 0.05
    w_congestion: float = 0.5

    # ---- priority (spec 21) ----------------------------------------------
    priority_emergency: float = 400.0
    priority_loaded: float = 300.0
    priority_pickup: float = 200.0
    priority_repositioning: float = 100.0
    priority_free: float = 0.0
    priority_inside_aisle: float = 50.0
    waiting_weight: float = 5.0
    blocked_weight: float = 10.0
    urgency_weight: float = 1.0

    # ---- deadlock (spec 28, 29, 33) --------------------------------------
    t_blocked: int = 10
    t_deadlock: int = 20
    config_history_length: int = 8
    #: Spec 28 names three stall signals: no progress, a repeated joint
    #: configuration, and a cycle in the wait-for graph.  With this on, no
    #: progress is only a *precondition* - a group must also show a cycle or a
    #: repeated configuration before recovery escalates.  Without it, ordinary
    #: queueing in dense traffic trips recovery, and levels 5-7 (temporary
    #: reverse, escape vertices, waypoint hijack) then destroy throughput.
    require_deadlock_corroboration: bool = True

    # ---- task assignment (spec 19) ---------------------------------------
    assign_alpha_to_pickup: float = 1.0
    assign_beta_pickup_to_delivery: float = 0.5
    #: `route_congestion` is now a per-cell density in roughly [0, 2], so this
    #: has to be on the order of the distance term for congestion to influence
    #: the match at all.  At the old value of 2.0 the whole congestion term was
    #: worth ~4 cost units against distances of 10-40.
    assign_gamma_congestion: float = 12.0
    assign_delta_waiting: float = -0.5  # negative -> older tasks preferred
    #: Cap on the waiting term.  `waiting_time` grows without bound in a
    #: lifelong run, so uncapped it swamps distance, congestion and everything
    #: else after ~100 steps and the match degenerates to oldest-task-first.
    assign_waiting_cap: float = 60.0
    assign_eta_direction: float = 1.0
    assign_zeta_blocking: float = 1.0
    assignment_candidate_limit: int = 32

    # ---- ablation switches (spec 34) -------------------------------------
    lifelong: bool = True
    direction_control: str = "aisle"  # "none" | "robot" | "aisle"
    hysteresis: bool = True
    #: `congestion_aware` used to switch three mechanisms at once: the movement
    #: score's mu*C penalty, the assignment cost's congestion term, and the
    #: assignment blocking term.  H4 is a claim about *assignment* only, so the
    #: two halves are separate flags now.  `congestion_aware` survives as a
    #: read/write alias that sets and reads both (see the properties below), so
    #: existing presets, CLI flags and saved configs keep working.
    congestion_scoring: bool = True
    congestion_assignment: bool = True
    reservations: bool = True
    recovery: bool = True
    turning_cost: bool = True

    # ---- simulation -------------------------------------------------------
    max_timesteps: int = 500
    seed: int = 0
    park_when_idle: bool = True
    validate_every_step: bool = True

    # ---- baseline planners (Token Passing, RHCR) --------------------------
    #: unused by PIBT/SPAR-PIBT; only consumed by baselines.rhcr.RHCRPlanner
    baseline_window: int = 10
    baseline_replan_period: int = 5

    # -- compatibility alias for the flag that used to bundle three mechanisms
    @property
    def congestion_aware(self) -> bool:
        """True only when both congestion mechanisms are on (see the fields)."""
        return self.congestion_scoring and self.congestion_assignment

    @staticmethod
    def _expand_aliases(values: Dict[str, Any]) -> Dict[str, Any]:
        """Rewrite `congestion_aware` into the two flags it used to bundle."""
        if "congestion_aware" not in values:
            return values
        values = dict(values)
        bundled = bool(values.pop("congestion_aware"))
        values.setdefault("congestion_scoring", bundled)
        values.setdefault("congestion_assignment", bundled)
        return values

    def merged(self, **overrides: Any) -> "Params":
        overrides = self._expand_aliases(overrides)
        unknown = set(overrides) - set(asdict(self))
        if unknown:
            raise KeyError(f"unknown parameter(s): {sorted(unknown)}")
        return replace(self, **overrides)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Params":
        data = cls._expand_aliases(data)
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - known
        if unknown:
            raise KeyError(f"unknown parameter(s): {sorted(unknown)}")
        return cls(**data)

    @classmethod
    def from_json(cls, path: str | Path) -> "Params":
        return cls.from_dict(json.loads(Path(path).read_text()))


#: Ablation variants of the table in spec section 34.
ABLATIONS: Dict[str, Dict[str, Any]] = {
    "pibt_baseline": dict(
        lifelong=False,
        direction_control="none",
        hysteresis=False,
        congestion_scoring=False,
        congestion_assignment=False,
        reservations=False,
        recovery=False,
        turning_cost=False,
    ),
    "lifelong_pibt": dict(
        lifelong=True,
        direction_control="none",
        hysteresis=False,
        congestion_scoring=False,
        congestion_assignment=False,
        reservations=False,
        recovery=False,
        turning_cost=False,
    ),
    "directional_pibt": dict(
        lifelong=True,
        direction_control="robot",
        hysteresis=False,
        congestion_scoring=False,
        congestion_assignment=False,
        reservations=False,
        recovery=False,
        turning_cost=True,
    ),
    "hysteresis_pibt": dict(
        lifelong=True,
        direction_control="robot",
        hysteresis=True,
        congestion_scoring=False,
        congestion_assignment=False,
        reservations=False,
        recovery=False,
        turning_cost=True,
    ),
    "aisle_managed_pibt": dict(
        lifelong=True,
        direction_control="aisle",
        hysteresis=True,
        congestion_scoring=False,
        congestion_assignment=False,
        reservations=True,
        recovery=False,
        turning_cost=True,
    ),
    "full_lda_pibt": dict(
        lifelong=True,
        direction_control="aisle",
        hysteresis=True,
        congestion_scoring=True,
        congestion_assignment=True,
        reservations=True,
        recovery=True,
        turning_cost=True,
    ),
}

#: Params flags for the external baseline planners (Token Passing, RHCR) --
#: identical to `lifelong_pibt`'s "every PIBT/LDA-specific mechanism off"
#: state, since neither baseline scores candidates or manages aisle
#: direction. `recovery` is deliberately not fixed here: the baseline
#: variant registry (`experiments.BASELINE_PLANNERS`) sets it per-variant
#: (e.g. `token_passing` vs `token_passing_recovery`), since whether a
#: baseline gets the same deadlock-recovery safety net as the PIBT variants
#: is itself part of what is being compared.
BASELINE_PARAMS_PRESET: Dict[str, Any] = dict(
    lifelong=True,
    direction_control="none",
    hysteresis=False,
    congestion_scoring=False,
    congestion_assignment=False,
    reservations=False,
    turning_cost=False,
)


#: Single-flag isolation variants. The headline ladder above changes two
#: flags at once on three of its steps (lifelong_pibt -> directional_pibt,
#: hysteresis_pibt -> aisle_managed_pibt, aisle_managed_pibt -> full_lda_pibt),
#: so a throughput delta on those steps cannot be attributed to either flag
#: alone. Each variant here flips exactly one of the two flags relative to
#: the same base, so it forms a clean 2x2 factorial with its sibling and the
#: two ladder rungs that bracket it (see FACTORIAL_DESIGNS).
ABLATIONS.update(
    {
        "turning_cost_only": dict(
            lifelong=True,
            direction_control="none",
            hysteresis=False,
            congestion_scoring=False,
            congestion_assignment=False,
            reservations=False,
            recovery=False,
            turning_cost=True,
        ),
        "direction_control_only": dict(
            lifelong=True,
            direction_control="robot",
            hysteresis=False,
            congestion_scoring=False,
            congestion_assignment=False,
            reservations=False,
            recovery=False,
            turning_cost=False,
        ),
        "aisle_direction_only": dict(
            lifelong=True,
            direction_control="aisle",
            hysteresis=True,
            congestion_scoring=False,
            congestion_assignment=False,
            reservations=False,
            recovery=False,
            turning_cost=True,
        ),
        "reservations_only": dict(
            lifelong=True,
            direction_control="robot",
            hysteresis=True,
            congestion_scoring=False,
            congestion_assignment=False,
            reservations=True,
            recovery=False,
            turning_cost=True,
        ),
        "congestion_only": dict(
            lifelong=True,
            direction_control="aisle",
            hysteresis=True,
            congestion_scoring=True,
            congestion_assignment=True,
            reservations=True,
            recovery=False,
            turning_cost=True,
        ),
        "recovery_only": dict(
            lifelong=True,
            direction_control="aisle",
            hysteresis=True,
            congestion_scoring=False,
            congestion_assignment=False,
            reservations=True,
            recovery=True,
            turning_cost=True,
        ),
    }
)


#: Variants that isolate the mechanisms this pass repaired.  Each one differs
#: from a variant above by exactly one of the new flags, so the pair is a clean
#: single-factor comparison.
ABLATIONS.update(
    {
        # -- D0: direction as a hard rejection vs. a score penalty ----------
        "aisle_direction_hard": dict(
            ABLATIONS["aisle_direction_only"], hard_direction_constraints=True
        ),
        "aisle_managed_hard": dict(
            ABLATIONS["aisle_managed_pibt"], hard_direction_constraints=True
        ),
        # -- D2: with and without a bounded maximum green -------------------
        "aisle_direction_no_max_green": dict(
            ABLATIONS["aisle_direction_only"], maximum_aisle_lock_time=10**6
        ),
        # -- D8: the two halves of the old `congestion_aware` flag ----------
        "congestion_scoring_only": dict(
            ABLATIONS["aisle_managed_pibt"], congestion_scoring=True
        ),
        "congestion_assignment_only": dict(
            ABLATIONS["aisle_managed_pibt"], congestion_assignment=True
        ),
        # -- D10: recovery on corroborated signals vs. on no-progress alone --
        "recovery_uncorroborated": dict(
            ABLATIONS["recovery_only"], require_deadlock_corroboration=False
        ),
        # -- the robot-level direction term, switched off entirely. Its decay
        # (r_near/r_far) turns out to be inert -- beta only ever breaks ties
        # among candidates with equal progress, and the decay lowers it in
        # exactly the regime where progress already decides -- so the weight,
        # not its schedule, is the thing worth ablating.
        "no_direction_term": dict(ABLATIONS["aisle_direction_only"], beta_strong=0.0),
    }
)


@dataclass(frozen=True)
class FactorialDesign:
    """A 2x2 factorial that de-confounds one bundled step of the ladder."""

    name: str
    base: str  #: neither flag
    factor_a: str  #: flag A alone
    factor_b: str  #: flag B alone
    both: str  #: both flags (the ladder's next rung)
    label_a: str
    label_b: str


#: The three factorials that resolve the ladder's confounded steps.
FACTORIAL_DESIGNS: Dict[str, FactorialDesign] = {
    "direction_vs_turning_cost": FactorialDesign(
        name="direction_vs_turning_cost",
        base="lifelong_pibt",
        factor_a="direction_control_only",
        factor_b="turning_cost_only",
        both="directional_pibt",
        label_a="direction_control=robot",
        label_b="turning_cost",
    ),
    # This design was unreadable until `reservations` was decoupled from
    # `direction_control`: `reservations_only` sets direction_control="robot",
    # and the reservation layer used to be gated on direction_control=="aisle",
    # so factor B was a bit-identical copy of the base and its main effect was
    # necessarily zero.  With entry admission now working under any direction
    # mode, all four corners are distinct and the 2x2 is meaningful.
    "aisle_direction_vs_reservations": FactorialDesign(
        name="aisle_direction_vs_reservations",
        base="hysteresis_pibt",
        factor_a="aisle_direction_only",
        factor_b="reservations_only",
        both="aisle_managed_pibt",
        label_a="direction_control=aisle",
        label_b="reservations",
    ),
    "congestion_vs_recovery": FactorialDesign(
        name="congestion_vs_recovery",
        base="aisle_managed_pibt",
        factor_a="congestion_only",
        factor_b="recovery_only",
        both="full_lda_pibt",
        label_a="congestion_scoring+assignment",
        label_b="recovery",
    ),
    # `congestion_aware` itself bundled two mechanisms, so the design above
    # cannot say which half of it acts.  This splits them.
    "congestion_scoring_vs_assignment": FactorialDesign(
        name="congestion_scoring_vs_assignment",
        base="aisle_managed_pibt",
        factor_a="congestion_scoring_only",
        factor_b="congestion_assignment_only",
        both="congestion_only",
        label_a="congestion_scoring (movement)",
        label_b="congestion_assignment (matching)",
    ),
}

#: Single-factor comparisons: `(treatment, control, what it isolates)`.  These
#: are pairs rather than 2x2 designs because the second factor is only defined
#: when the first is on (there is no "hard constraint" cell without aisle
#: direction, and no "corroboration" cell without recovery).
PAIRED_DESIGNS: Dict[str, Dict[str, str]] = {
    "soft_vs_hard_direction": dict(
        treatment="aisle_direction_only",
        control="aisle_direction_hard",
        label="direction ranks candidates (vs. rejects them)",
    ),
    "soft_vs_hard_direction_managed": dict(
        treatment="aisle_managed_pibt",
        control="aisle_managed_hard",
        label="direction + reservations rank (vs. reject)",
    ),
    "max_green": dict(
        treatment="aisle_direction_only",
        control="aisle_direction_no_max_green",
        label="bounded maximum green (starvation freedom)",
    ),
    "recovery_corroboration": dict(
        treatment="recovery_only",
        control="recovery_uncorroborated",
        label="recovery needs a cycle or a repeated configuration",
    ),
}


def ablation(name: str, base: Params | None = None, **overrides: Any) -> Params:
    """Return `Params` for a named ablation variant."""
    if name not in ABLATIONS:
        raise KeyError(f"unknown ablation {name!r}; choose from {sorted(ABLATIONS)}")
    params = (base or Params()).merged(**ABLATIONS[name])
    return params.merged(**overrides) if overrides else params


__all__ = [
    "Params",
    "ABLATIONS",
    "BASELINE_PARAMS_PRESET",
    "FactorialDesign",
    "FACTORIAL_DESIGNS",
    "PAIRED_DESIGNS",
    "ablation",
]
