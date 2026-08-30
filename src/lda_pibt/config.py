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
    #: "step" -> Delta in {-1,0,+1}; "route" -> spec 23 normalisation
    progress_normalization: str = "step"

    # congestion mixture weights (spec 23.1)
    omega_local: float = 1.0
    omega_aisle: float = 1.0
    omega_downstream: float = 1.0
    local_congestion_radius: int = 3
    downstream_horizon: int = 5

    # ---- proximity (spec 13) ---------------------------------------------
    r_near: int = 2
    r_far: int = 8

    # ---- aisle management (spec 12, 16, 17, 18) --------------------------
    minimum_aisle_lock_time: int = 20
    direction_switch_threshold: float = 5.0
    aisle_capacity: int = 10
    #: capacity model. "length" = spec 9.2 (ratio * number of cells).
    #: "throughput" = cells a robot train can clear before the aisle must
    #: flip, i.e. ceil(ratio * length / minimum_lock_time) - a single-file
    #: corridor is gated by its exit, not by how many robots fit inside.
    aisle_capacity_model: str = "length"
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

    # ---- task assignment (spec 19) ---------------------------------------
    assign_alpha_to_pickup: float = 1.0
    assign_beta_pickup_to_delivery: float = 0.5
    assign_gamma_congestion: float = 2.0
    assign_delta_waiting: float = -0.5  # negative -> older tasks preferred
    assign_eta_direction: float = 1.0
    assign_zeta_blocking: float = 1.0
    assignment_candidate_limit: int = 32

    # ---- ablation switches (spec 34) -------------------------------------
    lifelong: bool = True
    direction_control: str = "aisle"  # "none" | "robot" | "aisle"
    hysteresis: bool = True
    congestion_aware: bool = True
    reservations: bool = True
    recovery: bool = True
    turning_cost: bool = True

    # ---- simulation -------------------------------------------------------
    max_timesteps: int = 500
    seed: int = 0
    park_when_idle: bool = True
    validate_every_step: bool = True

    def merged(self, **overrides: Any) -> "Params":
        unknown = set(overrides) - set(asdict(self))
        if unknown:
            raise KeyError(f"unknown parameter(s): {sorted(unknown)}")
        return replace(self, **overrides)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Params":
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
        congestion_aware=False,
        reservations=False,
        recovery=False,
        turning_cost=False,
    ),
    "lifelong_pibt": dict(
        lifelong=True,
        direction_control="none",
        hysteresis=False,
        congestion_aware=False,
        reservations=False,
        recovery=False,
        turning_cost=False,
    ),
    "directional_pibt": dict(
        lifelong=True,
        direction_control="robot",
        hysteresis=False,
        congestion_aware=False,
        reservations=False,
        recovery=False,
        turning_cost=True,
    ),
    "hysteresis_pibt": dict(
        lifelong=True,
        direction_control="robot",
        hysteresis=True,
        congestion_aware=False,
        reservations=False,
        recovery=False,
        turning_cost=True,
    ),
    "aisle_managed_pibt": dict(
        lifelong=True,
        direction_control="aisle",
        hysteresis=True,
        congestion_aware=False,
        reservations=True,
        recovery=False,
        turning_cost=True,
    ),
    "full_lda_pibt": dict(
        lifelong=True,
        direction_control="aisle",
        hysteresis=True,
        congestion_aware=True,
        reservations=True,
        recovery=True,
        turning_cost=True,
    ),
}


def ablation(name: str, base: Params | None = None, **overrides: Any) -> Params:
    """Return `Params` for a named ablation variant."""
    if name not in ABLATIONS:
        raise KeyError(f"unknown ablation {name!r}; choose from {sorted(ABLATIONS)}")
    params = (base or Params()).merged(**ABLATIONS[name])
    return params.merged(**overrides) if overrides else params


__all__ = ["Params", "ABLATIONS", "ablation"]
