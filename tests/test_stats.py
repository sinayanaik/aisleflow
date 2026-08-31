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
def test_normalised_congestion_cannot_outrank_progress():
    """The ordering slide 11 claims, made true.

    `C_local` used to be a raw robot count mixed with two ratios, so `mu * C`
    reached the scale of `alpha * Delta` and congestion silently became the
    second-largest term in the score.
    """
    from lda_pibt import Params, Robot, Warehouse
    from lda_pibt.congestion import CongestionModel, OccupancyIndex

    params = Params(congestion_normalisation=True)
    grid = "\n".join(["." * 9 for _ in range(9)])
    warehouse = Warehouse.from_string(grid, params)
    index = OccupancyIndex(warehouse, params)
    model = CongestionModel(warehouse, index, params)

    # Pack every cell: the worst congestion the model can ever report.
    robots = [
        Robot(id=i, position=v) for i, v in enumerate(warehouse.graph.vertices)
    ]
    index.rebuild(robots)
    model.begin_timestep()
    worst = max(
        model.congestion(robots[0], v) for v in warehouse.graph.vertices
    )
    assert worst <= 1.0
    assert params.mu_congestion * worst < params.alpha_progress


def test_raw_congestion_mode_still_available():
    from lda_pibt import Params, Robot, Warehouse
    from lda_pibt.congestion import CongestionModel, OccupancyIndex

    params = Params(congestion_normalisation=False)
    grid = "\n".join(["." * 9 for _ in range(9)])
    warehouse = Warehouse.from_string(grid, params)
    index = OccupancyIndex(warehouse, params)
    model = CongestionModel(warehouse, index, params)
    robots = [
        Robot(id=i, position=v) for i, v in enumerate(warehouse.graph.vertices)
    ]
    index.rebuild(robots)
    model.begin_timestep()
    centre = (4, 4)
    # Unnormalised, the local term alone exceeds a whole step of progress.
    assert model.congestion(robots[0], centre) > params.alpha_progress
