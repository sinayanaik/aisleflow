"""Aisle-manager tests (spec sections 10, 12, 16, 17, 18, 27)."""

from __future__ import annotations

import pytest

from lda_pibt import AisleManager, OccupancyIndex, Params, Robot, Warehouse
from lda_pibt.types import AisleDirection, AisleState

CORRIDOR = "\n".join(
    [
        ".........",
        ".@@@@@@@.",
        ".........",
    ]
)


def setup(**overrides):
    overrides.setdefault("directional_aisle_min_length", 1)
    params = Params(
        direction_control="aisle",
        hysteresis=True,
        reservations=True,
        **overrides,
    )
    warehouse = Warehouse.from_string(CORRIDOR, params)
    index = OccupancyIndex(warehouse, params)
    manager = AisleManager(warehouse, index, params)
    return warehouse, index, manager, params


def long_aisle(warehouse):
    return max(warehouse.aisles.values(), key=lambda a: a.length)


# ------------------------------------------------------------ state machine
def test_empty_aisle_locks_to_the_dominant_direction():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    index.rebuild([])
    result = mgr.update_aisle_direction(aisle, forward_demand=20.0, reverse_demand=0.0, timestep=0)
    assert result is AisleDirection.FORWARD
    assert aisle.state is AisleState.FORWARD


def test_balanced_demand_leaves_the_aisle_open():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    index.rebuild([])
    assert mgr.update_aisle_direction(aisle, 3.0, 3.0, 0) is AisleDirection.NONE
    assert aisle.state is AisleState.OPEN


def test_minimum_lock_time_blocks_an_immediate_flip():
    """Spec 38.4: no more than one switch per T_min timesteps."""
    wh, index, mgr, _ = setup(minimum_aisle_lock_time=20)
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    assert aisle.lock_until > 0
    mgr.update_aisle_direction(aisle, 0.0, 20.0, 1)
    assert aisle.current_direction is AisleDirection.FORWARD


def test_flip_allowed_once_the_lock_expires():
    wh, index, mgr, _ = setup(minimum_aisle_lock_time=5)
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    mgr.update_aisle_direction(aisle, 0.0, 20.0, 50)
    assert aisle.current_direction is AisleDirection.REVERSE
    assert aisle.direction_switches == 1


def test_occupied_aisle_starts_draining_instead_of_flipping():
    """Spec 17: FORWARD -> DRAINING -> (empty) -> REVERSE."""
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    occupant = Robot(id=0, position=aisle.vertices[2])
    index.rebuild([occupant])
    mgr.update_aisle_direction(aisle, 0.0, 20.0, 40)
    assert aisle.state is AisleState.DRAINING
    assert aisle.current_direction is AisleDirection.FORWARD
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 0.0, 20.0, 41)
    assert aisle.current_direction is AisleDirection.REVERSE


def test_stuck_draining_aisle_reopens():
    """Spec 16: an infeasible direction may switch immediately."""
    wh, index, mgr, params = setup(max_drain_time=5)
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    occupant = Robot(id=0, position=aisle.vertices[2])
    index.rebuild([occupant])
    mgr.update_aisle_direction(aisle, 0.0, 20.0, 40)
    assert aisle.state is AisleState.DRAINING
    for t in range(41, 60):
        mgr.update_aisle_direction(aisle, 0.0, 20.0, t)
    assert aisle.state is AisleState.OPEN


def test_unmanageable_aisles_stay_open():
    wh, index, mgr, _ = setup(directional_aisle_min_length=999)
    index.rebuild([])
    mgr.step_directions({}, 0)
    assert all(a.state is AisleState.OPEN for a in wh.aisles.values())


# ------------------------------------------------------- movement legality
def test_entering_against_the_direction_is_rejected():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    last, second_last = aisle.vertices[-1], aisle.vertices[-2]
    robot = Robot(id=0, position=last)
    robot.current_aisle = aisle.id
    outsider = Robot(id=1, position=last)
    outsider.current_aisle = None
    assert mgr.violates_aisle_direction(outsider, last, second_last, 1)


def test_robot_inside_may_always_back_out():
    """Otherwise DRAINING can never terminate."""
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    robot = Robot(id=0, position=aisle.vertices[1])
    robot.current_aisle = aisle.id
    assert not mgr.violates_aisle_direction(
        robot, aisle.vertices[1], aisle.vertices[0], 1
    )


def test_open_aisle_permits_both_directions():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    aisle.state = AisleState.OPEN
    robot = Robot(id=0, position=aisle.vertices[3])
    assert not mgr.violates_aisle_direction(
        robot, aisle.vertices[3], aisle.vertices[2], 0
    )
    assert not mgr.violates_aisle_direction(
        robot, aisle.vertices[3], aisle.vertices[4], 0
    )


def test_recovery_override_bypasses_direction():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    robot = Robot(id=0, position=aisle.vertices[-1])
    robot.ignore_direction_until = 100
    assert not mgr.violates_aisle_direction(
        robot, aisle.vertices[-1], aisle.vertices[-2], 5
    )


# ------------------------------------------------------------- reservations
def test_reservation_required_for_a_directional_aisle():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    robot = Robot(id=0, position=aisle.start_vertex)
    assert not mgr.can_enter_aisle(robot, aisle, AisleDirection.FORWARD, 1)
    mgr.requests[robot.id] = (aisle.id, AisleDirection.FORWARD)
    mgr._robot_lookup = {robot.id: robot}
    mgr.update_aisle_reservations([robot], 1)
    assert mgr.has_valid_reservation(robot, aisle, 1)
    assert mgr.can_enter_aisle(robot, aisle, AisleDirection.FORWARD, 1)


def test_reservations_expire():
    wh, index, mgr, params = setup(reservation_ttl=3)
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    robot = Robot(id=0, position=aisle.start_vertex)
    mgr.requests[robot.id] = (aisle.id, AisleDirection.FORWARD)
    mgr._robot_lookup = {robot.id: robot}
    mgr.update_aisle_reservations([robot], 0)
    assert mgr.has_valid_reservation(robot, aisle, 0)
    # The robot stops requesting this aisle, so nothing renews the grant.
    mgr.requests.clear()
    mgr.update_aisle_reservations([robot], 99)
    assert robot.id not in aisle.reservations
    assert not mgr.has_valid_reservation(robot, aisle, 99)


def test_draining_aisle_refuses_entry():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    aisle.state = AisleState.DRAINING
    robot = Robot(id=0, position=aisle.start_vertex)
    assert not mgr.can_enter_aisle(robot, aisle, AisleDirection.FORWARD, 0)


def test_direction_control_off_disables_all_constraints():
    wh, index, mgr, _ = setup()
    mgr.params = Params(direction_control="none")
    aisle = long_aisle(wh)
    aisle.state = AisleState.FORWARD
    aisle.current_direction = AisleDirection.FORWARD
    robot = Robot(id=0, position=aisle.vertices[-1])
    assert not mgr.violates_aisle_direction(
        robot, aisle.vertices[-1], aisle.vertices[-2], 0
    )


# ------------------------------------------------------- starvation freedom
def test_maximum_green_forces_a_flip_under_balanced_demand():
    """The failure hysteresis alone cannot prevent.

    With demand inside the dead band the imbalance never crosses tau, so the
    old rule held the direction for as long as anyone stayed inside the aisle.
    A warehouse with pickups on one side and deliveries on the other produces
    balanced demand by construction, so the aisle starved permanently.
    """
    wh, index, mgr, _ = setup(
        minimum_aisle_lock_time=2,
        maximum_aisle_lock_time=10,
        direction_switch_threshold=5.0,
    )
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    assert aisle.current_direction is AisleDirection.FORWARD

    occupant = Robot(id=0, position=aisle.vertices[2])
    index.rebuild([occupant])
    # Balanced demand: |imbalance| = 1.0, far inside the +-5.0 dead band.
    for t in range(1, 10):
        mgr.update_aisle_direction(aisle, 10.0, 9.0, t)
        assert aisle.state is AisleState.FORWARD, f"flipped early at t={t}"

    mgr.update_aisle_direction(aisle, 10.0, 9.0, 10)
    assert aisle.state is AisleState.DRAINING
    assert aisle.pending_direction is AisleDirection.REVERSE
    assert aisle.starvation_flips == 1

    index.rebuild([])
    assert mgr.update_aisle_direction(aisle, 10.0, 9.0, 11) is AisleDirection.REVERSE


def test_maximum_green_needs_opposing_demand():
    """An aisle nobody wants to use the other way keeps its direction."""
    wh, index, mgr, _ = setup(minimum_aisle_lock_time=2, maximum_aisle_lock_time=5)
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    index.rebuild([Robot(id=0, position=aisle.vertices[2])])
    for t in range(1, 40):
        mgr.update_aisle_direction(aisle, 20.0, 0.0, t)
    assert aisle.state is AisleState.FORWARD
    assert aisle.starvation_flips == 0


def test_drain_commits_the_pending_direction_not_the_old_one():
    """Draining to flip must not fall back through the imbalance test.

    The demand that triggered the drain is usually gone by the time the aisle
    empties, so re-running the imbalance test would simply re-commit the
    direction just drained and the flip would never happen.
    """
    wh, index, mgr, _ = setup(minimum_aisle_lock_time=2, maximum_aisle_lock_time=3)
    aisle = long_aisle(wh)
    index.rebuild([])
    mgr.update_aisle_direction(aisle, 20.0, 0.0, 0)
    index.rebuild([Robot(id=0, position=aisle.vertices[2])])
    for t in range(1, 6):
        mgr.update_aisle_direction(aisle, 20.0, 1.0, t)
    assert aisle.state is AisleState.DRAINING
    index.rebuild([])
    # Forward demand still dominates, but the drain was requested to reverse.
    assert mgr.update_aisle_direction(aisle, 20.0, 1.0, 6) is AisleDirection.REVERSE


# ----------------------------------------------- capacity admission (H3)
def test_capacity_admission_works_without_aisle_direction_control():
    """`reservations` used to be a silent no-op unless aisles chose direction.

    `AisleManager.enabled` gated the whole reservation layer, and the variant
    built to isolate reservations sets `direction_control="robot"`, so the
    isolated cell was a bit-identical copy of its own control.
    """
    wh, index, mgr, _ = setup()
    mgr.params = Params(
        direction_control="robot", reservations=True, directional_aisle_min_length=1
    )
    assert not mgr.enabled
    assert mgr.active

    aisle = long_aisle(wh)
    aisle.state = AisleState.OPEN
    aisle.capacity = 2
    robot = Robot(id=99, position=aisle.start_vertex)
    index.rebuild([Robot(id=i, position=aisle.vertices[i]) for i in range(2)])
    assert not mgr.can_enter_aisle(robot, aisle, AisleDirection.FORWARD, 0)


def test_open_aisle_needs_room_but_not_a_ticket():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    aisle.state = AisleState.OPEN
    robot = Robot(id=0, position=aisle.start_vertex)
    index.rebuild([])
    assert mgr.can_enter_aisle(robot, aisle, AisleDirection.FORWARD, 0)
    assert not mgr.has_valid_reservation(robot, aisle, 0)


# --------------------------------------------------- head-on conflict metric
def test_head_on_conflicts_counts_facing_routes_once():
    wh, index, mgr, _ = setup()
    aisle = long_aisle(wh)
    left = Robot(id=0, position=aisle.vertices[2])
    right = Robot(id=1, position=aisle.vertices[3])
    left.route = [aisle.vertices[2], aisle.vertices[3]]
    right.route = [aisle.vertices[3], aisle.vertices[2]]
    assert mgr.count_head_on_conflicts([left, right]) == 1

    # Same direction: not a head-on encounter.
    right.route = [aisle.vertices[3], aisle.vertices[4]]
    assert mgr.count_head_on_conflicts([left, right]) == 0
