"""Unit tests for the pure-Python significance testing in `lda_pibt.stats`."""

from __future__ import annotations

import random

from lda_pibt.stats import bootstrap_ci, permutation_test


def test_bootstrap_ci_recovers_a_known_mean():
    rng = random.Random(0)
    values = [10.0 + rng.uniform(-1.0, 1.0) for _ in range(200)]
    mean, lo, hi = bootstrap_ci(values, n_resamples=2000, seed=1)
    assert lo <= mean <= hi
    assert 9.5 < mean < 10.5
    assert hi - lo < 1.0


def test_bootstrap_ci_degenerates_gracefully_for_small_samples():
    assert bootstrap_ci([]) == (0.0, 0.0, 0.0)
    assert bootstrap_ci([5.0]) == (5.0, 5.0, 5.0)


def test_permutation_test_identical_distributions_gives_high_p():
    rng = random.Random(0)
    a = [rng.uniform(0, 1) for _ in range(30)]
    b = [rng.uniform(0, 1) for _ in range(30)]
    _, p = permutation_test(a, b, n_permutations=5000, seed=1)
    assert p > 0.05


def test_permutation_test_clearly_separated_distributions_gives_low_p():
    rng = random.Random(0)
    a = [rng.uniform(0, 1) for _ in range(30)]
    b = [rng.uniform(10, 11) for _ in range(30)]
    _, p = permutation_test(a, b, n_permutations=5000, seed=1)
    assert p < 0.01


def test_permutation_test_exact_enumeration_is_deterministic():
    a = [1.0, 2.0, 3.0]
    b = [10.0, 11.0, 12.0]
    result_1 = permutation_test(a, b, seed=1)
    result_2 = permutation_test(a, b, seed=2)  # exact path ignores seed
    assert result_1 == result_2


def test_permutation_test_empty_group_returns_p_one():
    _, p = permutation_test([1.0, 2.0], [])
    assert p == 1.0


# ------------------------------------------------- congestion term calibration
def test_crowding_is_a_fraction_and_cannot_outrank_progress():
    """The tier structure the whole score rests on, made true.

    Both signals crowding averages are already fractions, so crowding is one
    too and `crowding_penalty` reads directly as "what a completely jammed
    cell costs". The local signal used to be a raw robot count mixed with a
    ratio, which let crowding reach the scale of a step of progress and
    silently made it the second-largest term in the score.
    """
    from lda_pibt import Params, Robot, Warehouse
    from lda_pibt.congestion import CongestionModel, OccupancyIndex

    params = Params()
    grid = "\n".join(["." * 9 for _ in range(9)])
    warehouse = Warehouse.from_string(grid, params)
    index = OccupancyIndex(warehouse, params)
    model = CongestionModel(warehouse, index, params)

    # Pack every cell: the worst crowding the model can ever report.
    robots = [
        Robot(id=i, position=v) for i, v in enumerate(warehouse.graph.vertices)
    ]
    index.rebuild(robots)
    worst = max(model.crowding(robots[0], v) for v in warehouse.graph.vertices)
    assert worst <= 1.0
    assert params.crowding_penalty * worst < params.progress_reward


def test_paired_test_beats_the_unpaired_one_on_matched_arms():
    """Why the sensitivity suite uses the sign-flip test.

    The suites run every variant on the same seeds, so seed k is an identical
    task stream in both arms. Pooling and relabelling throws that away: on a
    perfectly consistent effect the unpaired test cannot even reach p < 0.05,
    while the paired one reports p = 0.002.
    """
    from lda_pibt.stats import paired_permutation_test

    a = [0.10, 0.12, 0.11, 0.13, 0.12, 0.11, 0.10, 0.12, 0.13, 0.11]
    b = [x - 0.01 for x in a]
    _, paired_p = paired_permutation_test(a, b)
    _, pooled_p = permutation_test(a, b)
    assert paired_p < 0.005
    assert pooled_p > 0.05


def test_paired_test_rejects_mismatched_arms():
    from lda_pibt.stats import paired_permutation_test
    import pytest

    with pytest.raises(ValueError):
        paired_permutation_test([1.0, 2.0], [1.0])


def test_no_legacy_alias_shadows_a_live_parameter():
    """A rename table entry keyed on a *current* field silently eats overrides.

    `_expand_aliases` pops each legacy key it finds, so if a key were also a
    real field name, setting that field would drop the value on the floor and
    quietly hand back the default -- the worst failure mode a config layer
    has, because nothing raises and the run looks fine.
    """
    from lda_pibt.config import LEGACY_NAMES, REMOVED_NAMES, Params

    live = set(Params().to_dict())
    assert not (set(LEGACY_NAMES) & live), "legacy alias shadows a live field"
    assert not (REMOVED_NAMES & live), "a live field is listed as removed"
    assert set(LEGACY_NAMES.values()) <= live, "alias points at a missing field"

    # And the round trip actually works.
    assert Params.from_dict({"lambda_turn": 1.5}).turn_penalty == 1.5
    assert Params().merged(turn_penalty=1.5).turn_penalty == 1.5
