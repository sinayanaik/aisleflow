"""Tunable parameters (spec section 33) and ablation presets (spec section 34)."""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Tuple


#: Old parameter name -> new one. Renaming Greek to plain English was the
#: point; keeping these means saved configs and `--set` flags still load.
LEGACY_NAMES: Dict[str, str] = {
    "alpha_progress": "progress_reward",
    "gamma_strong": "aisle_bonus",
    "gamma_weak": "aisle_bonus_near",
    "lambda_turn": "turn_penalty",
    "lambda_reverse": "reverse_multiplier",
    "mu_congestion": "crowding_penalty",
    "t_blocked": "stall_steps",
    "t_deadlock": "deadlock_steps",
    "w_waiting": "demand_waiting",
    "w_proximity": "demand_proximity",
    "w_route_length": "demand_route_length",
    "w_congestion": "demand_congestion",
    "assign_alpha_to_pickup": "cost_to_pickup",
    "assign_beta_pickup_to_delivery": "cost_pickup_to_delivery",
    "assign_gamma_congestion": "cost_congestion",
    "assign_delta_waiting": "cost_waiting",
    "assign_waiting_cap": "cost_waiting_cap",
    "assign_eta_direction": "cost_direction",
    "assign_zeta_blocking": "cost_blocking",
}

#: Parameters whose term was deleted outright. Loading one is a warning, not
#: an error: the config predates the simplification rather than being wrong.
REMOVED_NAMES: frozenset = frozenset({
    "beta_strong", "beta_weak", "nu_wait", "xi_bottleneck",
    "zeta_counterflow", "zeta_reservation", "progress_normalization",
    "omega_local", "omega_aisle", "omega_downstream", "downstream_horizon",
    "congestion_normalisation", "priority_emergency", "priority_loaded",
    "priority_pickup", "priority_repositioning", "priority_free",
    "blocked_weight", "urgency_weight", "w_urgency", "reservation_ttl",
    "aisle_capacity_model", "aisle_capacity_ratio", "parity_bias",
    "demand_spread", "coordinate_aisle_directions",
    "hard_direction_constraints", "reservations",
})


@dataclass
class Params:
    """Every tunable in the planner.

    Each number here survived `experiments/run_sensitivity.py`, which runs the
    planner with one knob neutralised at a time and measures what that costs.
    Knobs that changed nothing were deleted rather than defaulted to zero;
    `docs/04-parameters.md` records the measured effect of the ones that
    remain, and `LEGACY_NAMES` maps the old names onto the new ones.
    """

    # ---- candidate score: see scoring.CandidateScorer.score ----------------
    #: Reward per cell of progress towards the waypoint. Progress is -1, 0 or
    #: +1, so this sets the tier spacing every other term is measured against:
    #: a term smaller than this can only reorder moves that make equal
    #: progress. Removing it costs 89% of throughput -- it is the algorithm.
    progress_reward: float = 10.0
    #: Reward for staying in the aisle the robot is already travelling down,
    #: at full strength while in transit.
    aisle_bonus: float = 2.0
    #: The same reward once the robot is near its waypoint, where it needs to
    #: be free to turn off.
    aisle_bonus_near: float = 0.5
    #: Cost of turning a corner rather than carrying straight on.
    turn_penalty: float = 0.5
    #: Reversing costs this many times a turn.
    reverse_multiplier: float = 2.0
    #: Cost of moving into a fully crowded cell (crowding is a fraction in
    #: [0, 1], so this is the worst case).
    crowding_penalty: float = 1.0

    # ---- crowding: see congestion.CongestionModel -------------------------
    #: Manhattan radius of the "how full is it around here" measurement.
    local_congestion_radius: int = 3

    # ---- proximity: how close counts as near ------------------------------
    r_near: int = 2
    r_far: int = 8

    # ---- aisle traffic signal: see aisle_manager.AisleManager -------------
    #: Minimum green: once an aisle commits to a direction it holds it this
    #: long, so it cannot oscillate.
    minimum_aisle_lock_time: int = 20
    #: Maximum green. Hysteresis bounds how *soon* a direction may change but
    #: not how long it may persist, so an aisle facing balanced demand would
    #: hold one direction forever and starve the other side. Past this, any
    #: opposing demand forces a drain and a flip.
    maximum_aisle_lock_time: int = 40
    #: How much more demand one way than the other before the aisle flips.
    direction_switch_threshold: float = 5.0
    #: Hard ceiling on robots inside one aisle, whatever its length.
    aisle_capacity: int = 10
    #: An aisle that cannot drain within this many steps is reopened. Also
    #: caps capacity: the last robot in must still traverse the whole aisle to
    #: leave, and every robot ahead of it adds a step.
    max_drain_time: int = 30
    #: Aisles shorter than this stay bidirectional -- a one-way rule on a short
    #: link is not worth the detour and fragments the network.
    directional_aisle_min_length: int = 4
    #: Route around aisles flowing the other way instead of queueing at them.
    #: This is now the *only* way aisle direction affects a robot's path: the
    #: per-step counterflow penalty it replaced measured strongly negative.
    direction_aware_routing: bool = True
    #: How many extra cells of detour it is worth taking to avoid entering an
    #: aisle against its committed direction.
    route_direction_penalty: float = 6.0

    # ---- what an aisle's traffic is worth: aisle_manager._robot_demand -----
    demand_waiting: float = 0.5
    demand_proximity: float = 2.0
    demand_route_length: float = 0.05
    demand_congestion: float = 0.5

    # ---- priority: see priority.compute_priority --------------------------
    #: Gap between adjacent task classes. Five hand-set constants collapsed to
    #: this: only their ordering ever mattered.
    priority_class_spread: float = 100.0
    #: Bonus for a robot already inside an aisle, so narrow parts clear first.
    priority_inside_aisle: float = 50.0
    #: Rank bought per step of waiting. This is the anti-starvation guarantee:
    #: `priority.fairness_horizon` is 80 steps at these defaults. Removing it
    #: costs 16% of throughput.
    waiting_weight: float = 5.0

    # ---- deadlock detection and recovery: see deadlock.DeadlockMonitor ----
    #: Steps without progress before a robot counts as stalled.
    stall_steps: int = 10
    #: Steps before a group is treated as deadlocked rather than slow.
    deadlock_steps: int = 20
    config_history_length: int = 8
    #: Require a wait-for cycle or a repeated configuration before escalating,
    #: not merely a lack of progress. Without this, ordinary queueing trips
    #: recovery and throughput drops by 31%.
    require_deadlock_corroboration: bool = True
    #: How many of the recovery remedies may run. The levels fire strictly in
    #: order, so this is the only way to measure what each is worth; the two
    #: strongest ones measured *negative* and were removed, which is why the
    #: ladder is five long rather than seven.
    recovery_max_level: int = 5

    # ---- task assignment: see assignment.TaskAssigner.assignment_cost -----
    cost_to_pickup: float = 1.0
    cost_pickup_to_delivery: float = 0.5
    cost_congestion: float = 12.0
    cost_waiting: float = -0.5  # negative -> older tasks preferred
    #: Cap on the waiting term. `waiting_time` grows without bound in a
    #: lifelong run, so uncapped it swamps everything else after ~100 steps
    #: and the match degenerates to oldest-task-first.
    cost_waiting_cap: float = 60.0
    cost_direction: float = 1.0
    cost_blocking: float = 1.0
    assignment_candidate_limit: int = 32

    # ---- mechanism switches -----------------------------------------------
    lifelong: bool = True
    direction_control: str = "aisle"  # "none" | "robot" | "aisle"
    hysteresis: bool = True
    congestion_scoring: bool = True
    congestion_assignment: bool = True
    recovery: bool = True
    turning_cost: bool = True

    # ---- simulation -------------------------------------------------------
    max_timesteps: int = 500
    seed: int = 0
    park_when_idle: bool = True
    validate_every_step: bool = True

    # ---- baseline planners (Token Passing, RHCR) --------------------------
    #: unused by PIBT; only consumed by baselines.rhcr.RHCRPlanner
    baseline_window: int = 10
    baseline_replan_period: int = 5

    @staticmethod
    def _expand_aliases(values: Dict[str, Any]) -> Dict[str, Any]:
        """Accept the pre-simplification parameter names.

        Renames map straight onto their replacement. Names in `REMOVED_NAMES`
        are dropped with a warning rather than an error, so a saved config or
        a script written against the old planner still loads -- but silently
        honouring a weight whose term no longer exists would be worse than
        saying so.
        """
        if not any(
            k in LEGACY_NAMES or k in REMOVED_NAMES or k == "congestion_aware"
            for k in values
        ):
            return values
        values = dict(values)
        if "congestion_aware" in values:
            bundled = bool(values.pop("congestion_aware"))
            values.setdefault("congestion_scoring", bundled)
            values.setdefault("congestion_assignment", bundled)
        for old, new in LEGACY_NAMES.items():
            if old in values:
                values.setdefault(new, values.pop(old))
            values.pop(old, None)
        for gone in REMOVED_NAMES:
            if gone in values:
                values.pop(gone)
                warnings.warn(
                    f"parameter {gone!r} no longer exists: the term it weighted "
                    f"was removed after the sensitivity study measured it as "
                    f"having no effect or a negative one. See "
                    f"docs/04-parameters.md.",
                    DeprecationWarning,
                    stacklevel=3,
                )
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


#: The ladder of variants, from plain PIBT up to the full planner. Each rung
#: switches on one more mechanism, so a throughput difference between adjacent
#: rungs is attributable to that mechanism.
ABLATIONS: Dict[str, Dict[str, Any]] = {
    "pibt_baseline": dict(
        lifelong=False, direction_control="none", hysteresis=False,
        congestion_scoring=False, congestion_assignment=False,
        recovery=False, turning_cost=False, direction_aware_routing=False,
    ),
    "lifelong_pibt": dict(
        lifelong=True, direction_control="none", hysteresis=False,
        congestion_scoring=False, congestion_assignment=False,
        recovery=False, turning_cost=False, direction_aware_routing=False,
    ),
    "turning_cost_only": dict(
        lifelong=True, direction_control="none", hysteresis=False,
        congestion_scoring=False, congestion_assignment=False,
        recovery=False, turning_cost=True, direction_aware_routing=False,
    ),
    "hysteresis_pibt": dict(
        lifelong=True, direction_control="robot", hysteresis=True,
        congestion_scoring=False, congestion_assignment=False,
        recovery=False, turning_cost=True, direction_aware_routing=False,
    ),
    #: Aisles commit to a direction, and routes plan around aisles flowing the
    #: other way. This replaces the per-step counterflow penalty, which the
    #: sensitivity study measured as costing throughput rather than buying it.
    "aisle_direction_only": dict(
        lifelong=True, direction_control="aisle", hysteresis=True,
        congestion_scoring=False, congestion_assignment=False,
        recovery=False, turning_cost=True, direction_aware_routing=True,
    ),
    "congestion_only": dict(
        lifelong=True, direction_control="aisle", hysteresis=True,
        congestion_scoring=True, congestion_assignment=True,
        recovery=False, turning_cost=True, direction_aware_routing=True,
    ),
    "recovery_only": dict(
        lifelong=True, direction_control="aisle", hysteresis=True,
        congestion_scoring=False, congestion_assignment=False,
        recovery=True, turning_cost=True, direction_aware_routing=True,
    ),
    "full_lda_pibt": dict(
        lifelong=True, direction_control="aisle", hysteresis=True,
        congestion_scoring=True, congestion_assignment=True,
        recovery=True, turning_cost=True, direction_aware_routing=True,
    ),
}

#: The direction-aware routing this pass promoted to the default, switched
#: back off, so the mechanism that replaced the counterflow penalty can be
#: reported on its own.
ABLATIONS["aisle_direction_no_routing"] = dict(
    ABLATIONS["aisle_direction_only"], direction_aware_routing=False
)
ABLATIONS["no_direction_routing"] = dict(
    ABLATIONS["full_lda_pibt"], direction_aware_routing=False
)
#: Recovery without the corroboration requirement, and with the two remedies
#: this pass deleted put back -- both measured strongly, in opposite
#: directions, so both stay runnable as reportable comparisons.
ABLATIONS["recovery_uncorroborated"] = dict(
    ABLATIONS["recovery_only"], require_deadlock_corroboration=False
)
ABLATIONS["recovery_full_ladder"] = dict(
    ABLATIONS["full_lda_pibt"], recovery_max_level=7
)
ABLATIONS["aisle_direction_no_max_green"] = dict(
    ABLATIONS["aisle_direction_only"], maximum_aisle_lock_time=10**6
)

#: Params flags for the external baseline planners (Token Passing, RHCR):
#: identical to `lifelong_pibt`'s all-mechanisms-off state, since neither
#: baseline scores candidates or manages aisle direction. `recovery` is
#: deliberately not fixed here -- `experiments.BASELINE_PLANNERS` sets it per
#: variant, since whether a baseline gets the same deadlock safety net as the
#: PIBT variants is itself part of what is being compared.
BASELINE_PARAMS_PRESET: Dict[str, Any] = dict(
    lifelong=True, direction_control="none", hysteresis=False,
    congestion_scoring=False, congestion_assignment=False,
    turning_cost=False, direction_aware_routing=False,
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


FACTORIAL_DESIGNS: Dict[str, FactorialDesign] = {
    "congestion_vs_recovery": FactorialDesign(
        name="congestion_vs_recovery",
        base="aisle_direction_only",
        factor_a="congestion_only",
        factor_b="recovery_only",
        both="full_lda_pibt",
        label_a="congestion scoring + assignment",
        label_b="deadlock recovery",
    ),
}

#: Single-factor comparisons: `(treatment, control, what it isolates)`. Pairs
#: rather than 2x2 designs, because the second factor is only defined when the
#: first is on.
PAIRED_DESIGNS: Dict[str, Dict[str, str]] = {
    "direction_aware_routing": dict(
        treatment="full_lda_pibt",
        control="no_direction_routing",
        label="routes plan around aisles flowing the other way",
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
    "recovery_ladder_depth": dict(
        treatment="full_lda_pibt",
        control="recovery_full_ladder",
        label="stopping the recovery ladder at level 5",
    ),
}


@dataclass(frozen=True)
class SensitivityVariant:
    """One knob of one model, neutralised against the full planner.

    `overrides` are applied on top of `ABLATIONS["full_lda_pibt"]`, so every
    entry differs from that control by exactly one decision.  `knob` names the
    parameter in the terms the documentation uses; `family` is the model it
    belongs to, so the study reports per-model as well as per-knob.
    """

    name: str
    family: str
    knob: str
    overrides: Dict[str, Any]


#: The control every sensitivity variant is measured against.
SENSITIVITY_BASE = "full_lda_pibt"

#: A leave-one-out sweep over every live tunable in the planner.  The point is
#: to replace "these weights were chosen by hand and never questioned" with a
#: measured cost per knob, so the ones that buy nothing can be deleted and the
#: ones that survive can be documented with a number beside them.
#:
#: Neutralising means "make this term stop acting", which is not always zero:
#: `reverse_multiplier` is a multiplier whose neutral value is 1.0, capacity models
#: are a choice rather than a magnitude, and the recovery ladder is a set of
#: code paths that can only be truncated (see `recovery_max_level`).
SENSITIVITY: Tuple[SensitivityVariant, ...] = (
    # -- 1. candidate score S_i(v) (scoring.py) ----------------------------
    SensitivityVariant("score_no_progress", "score", "progress_reward", dict(progress_reward=0.0)),
    SensitivityVariant("score_no_heading", "score", "beta_strong", dict(beta_strong=0.0)),
    SensitivityVariant("score_no_aisle_continuity", "score", "aisle_bonus/aisle_bonus_near", dict(aisle_bonus=0.0, aisle_bonus_near=0.0)),
    SensitivityVariant("score_no_turn_penalty", "score", "turn_penalty", dict(turn_penalty=0.0)),
    SensitivityVariant("score_no_reverse_extra", "score", "reverse_multiplier", dict(reverse_multiplier=1.0)),
    SensitivityVariant("score_no_crowding", "score", "crowding_penalty", dict(crowding_penalty=0.0)),
    SensitivityVariant("score_no_idling", "score", "nu_wait", dict(nu_wait=0.0)),
    SensitivityVariant("score_no_chokepoint", "score", "xi_bottleneck", dict(xi_bottleneck=0.0)),
    SensitivityVariant("score_no_wrong_way", "score", "zeta_counterflow", dict(zeta_counterflow=0.0)),
    SensitivityVariant("score_no_permit_penalty", "score", "zeta_reservation", dict(zeta_reservation=0.0)),
    SensitivityVariant("score_flat_proximity_ramp", "score", "r_near/r_far", dict(r_near=0, r_far=1)),
    #: The three terms this pass was asked to remove, cut together. Terms can
    #: be jointly redundant while individually load-bearing (and the reverse),
    #: so the combination has to be run, not inferred by summing the singles.
    SensitivityVariant("score_proposed_cut", "score", "beta+zeta_counterflow+zeta_reservation", dict(beta_strong=0.0, zeta_counterflow=0.0, zeta_reservation=0.0)),

    # -- 2. congestion mixture C_i(v) (congestion.py) -----------------------
    SensitivityVariant("cong_no_local", "congestion", "omega_local", dict(omega_local=0.0)),
    SensitivityVariant("cong_no_aisle", "congestion", "omega_aisle", dict(omega_aisle=0.0)),
    SensitivityVariant("cong_no_downstream", "congestion", "omega_downstream", dict(omega_downstream=0.0)),
    SensitivityVariant("cong_radius_1", "congestion", "local_congestion_radius=1", dict(local_congestion_radius=1)),
    SensitivityVariant("cong_radius_5", "congestion", "local_congestion_radius=5", dict(local_congestion_radius=5)),
    SensitivityVariant("cong_horizon_0", "congestion", "downstream_horizon=0", dict(downstream_horizon=0)),
    SensitivityVariant("cong_horizon_10", "congestion", "downstream_horizon=10", dict(downstream_horizon=10)),
    SensitivityVariant("cong_unnormalised", "congestion", "congestion_normalisation", dict(congestion_normalisation=False)),

    # -- 3. priority p_i(t) (priority.py) -----------------------------------
    SensitivityVariant("prio_no_waiting", "priority", "waiting_weight", dict(waiting_weight=0.0)),
    SensitivityVariant("prio_no_blocked", "priority", "blocked_weight", dict(blocked_weight=0.0)),
    SensitivityVariant("prio_no_urgency", "priority", "urgency_weight", dict(urgency_weight=0.0)),
    SensitivityVariant("prio_no_inside_aisle", "priority", "priority_inside_aisle", dict(priority_inside_aisle=0.0)),
    #: Does the five-way task class ranking do anything, or is it only the
    #: waiting/blocked terms that ever decide the order?
    SensitivityVariant("prio_flat_classes", "priority", "priority_* class constants", dict(priority_emergency=0.0, priority_loaded=0.0, priority_pickup=0.0, priority_repositioning=0.0, priority_free=0.0)),

    # -- 4. aisle directional demand S_a^+/- (aisle_manager.py) -------------
    SensitivityVariant("demand_no_urgency", "aisle demand", "w_urgency", dict(w_urgency=0.0)),
    SensitivityVariant("demand_no_waiting", "aisle demand", "demand_waiting", dict(demand_waiting=0.0)),
    SensitivityVariant("demand_no_proximity", "aisle demand", "demand_proximity", dict(demand_proximity=0.0)),
    SensitivityVariant("demand_no_route_length", "aisle demand", "demand_route_length", dict(demand_route_length=0.0)),
    SensitivityVariant("demand_no_congestion", "aisle demand", "demand_congestion", dict(demand_congestion=0.0)),

    # -- 5. aisle signal timing and capacity --------------------------------
    SensitivityVariant("timing_no_min_green", "aisle timing", "minimum_aisle_lock_time", dict(minimum_aisle_lock_time=1)),
    SensitivityVariant("timing_no_max_green", "aisle timing", "maximum_aisle_lock_time", dict(maximum_aisle_lock_time=10**6)),
    SensitivityVariant("timing_no_switch_threshold", "aisle timing", "direction_switch_threshold", dict(direction_switch_threshold=0.0)),
    SensitivityVariant("capacity_model_length", "aisle timing", 'aisle_capacity_model="length"', dict(aisle_capacity_model="length")),
    SensitivityVariant("capacity_model_throughput", "aisle timing", 'aisle_capacity_model="throughput"', dict(aisle_capacity_model="throughput")),
    SensitivityVariant("timing_short_aisles_managed", "aisle timing", "directional_aisle_min_length", dict(directional_aisle_min_length=1)),
    SensitivityVariant("timing_reservation_ttl_long", "aisle timing", "reservation_ttl", dict(reservation_ttl=60)),
    SensitivityVariant("routing_direction_aware", "aisle timing", "direction_aware_routing", dict(direction_aware_routing=True)),

    # -- 6. assignment cost J(i, tau) (assignment.py) -----------------------
    SensitivityVariant("assign_no_pickup_distance", "assignment", "cost_to_pickup", dict(cost_to_pickup=0.0)),
    SensitivityVariant("assign_no_delivery_distance", "assignment", "cost_pickup_to_delivery", dict(cost_pickup_to_delivery=0.0)),
    SensitivityVariant("assign_no_congestion", "assignment", "cost_congestion", dict(cost_congestion=0.0)),
    SensitivityVariant("assign_no_waiting", "assignment", "cost_waiting", dict(cost_waiting=0.0)),
    SensitivityVariant("assign_uncapped_waiting", "assignment", "cost_waiting_cap", dict(cost_waiting_cap=1e9)),
    SensitivityVariant("assign_no_direction", "assignment", "cost_direction", dict(cost_direction=0.0)),
    SensitivityVariant("assign_no_blocking", "assignment", "cost_blocking", dict(cost_blocking=0.0)),

    # -- 7. deadlock detection and the recovery ladder (deadlock.py) --------
    #: Truncation, not leave-one-out: the levels fire strictly in order, so
    #: `recovery_max_level=k` measures what levels k+1..7 are worth.
    SensitivityVariant("recovery_max_0", "recovery", "recovery_max_level=0", dict(recovery_max_level=0)),
    SensitivityVariant("recovery_max_1", "recovery", "recovery_max_level=1", dict(recovery_max_level=1)),
    SensitivityVariant("recovery_max_2", "recovery", "recovery_max_level=2", dict(recovery_max_level=2)),
    SensitivityVariant("recovery_max_3", "recovery", "recovery_max_level=3", dict(recovery_max_level=3)),
    SensitivityVariant("recovery_max_4", "recovery", "recovery_max_level=4", dict(recovery_max_level=4)),
    SensitivityVariant("recovery_max_5", "recovery", "recovery_max_level=5", dict(recovery_max_level=5)),
    SensitivityVariant("recovery_max_6", "recovery", "recovery_max_level=6", dict(recovery_max_level=6)),
    SensitivityVariant("recovery_off", "recovery", "recovery", dict(recovery=False)),
    SensitivityVariant("recovery_uncorroborated", "recovery", "require_deadlock_corroboration", dict(require_deadlock_corroboration=False)),
    SensitivityVariant("recovery_stall_threshold_short", "recovery", "stall_steps", dict(stall_steps=5)),
)

#: Families in report order.
SENSITIVITY_FAMILIES = (
    "score",
    "congestion",
    "priority",
    "aisle demand",
    "aisle timing",
    "assignment",
    "recovery",
)


def sensitivity_params(variant: SensitivityVariant, base: Params | None = None) -> Params:
    """`Params` for one sensitivity variant: the full planner, one knob off."""
    control = ablation(SENSITIVITY_BASE, base)
    return control.merged(**variant.overrides)


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
    "SensitivityVariant",
    "SENSITIVITY",
    "SENSITIVITY_BASE",
    "SENSITIVITY_FAMILIES",
    "sensitivity_params",
]
