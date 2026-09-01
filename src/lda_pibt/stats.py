"""Pure-Python significance testing and confidence intervals.

No dependency on scipy/numpy (the project has none). Used by
`experiments.run_comparison_table` to turn bare per-seed means into
mean +/- 95% CI and a p-value against a reference variant, since a handful of
seeds is too thin a basis for "supported"/"not supported" verdicts on its
own.
"""

from __future__ import annotations

import random
import statistics
from itertools import combinations, product
from math import comb
from typing import Callable, List, Sequence, Tuple

#: default two-sample test statistic: difference of means
_MeanDiff: Callable[[Sequence[float], Sequence[float]], float] = (
    lambda a, b: statistics.fmean(a) - statistics.fmean(b)
)

#: below this, an exact permutation test (enumerate every label split) is
#: cheap enough to run instead of Monte Carlo sampling.
_EXACT_ENUMERATION_LIMIT = 20_000


def bootstrap_ci(
    values: Sequence[float],
    n_resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Tuple[float, float, float]:
    """Percentile bootstrap confidence interval.

    Returns `(mean, lo, hi)` where `[lo, hi]` is the `1 - alpha` CI on the
    mean of `values`, estimated by resampling `values` with replacement
    `n_resamples` times. With fewer than 2 values, returns `(mean, mean, mean)`
    since no resampling variance can be estimated.
    """
    if len(values) < 2:
        m = float(values[0]) if values else 0.0
        return (m, m, m)

    rng = random.Random(seed)
    n = len(values)
    resample_means = []
    for _ in range(n_resamples):
        resample = [values[rng.randrange(n)] for _ in range(n)]
        resample_means.append(statistics.fmean(resample))
    resample_means.sort()

    lo_idx = int((alpha / 2) * n_resamples)
    hi_idx = min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))
    return (statistics.fmean(values), resample_means[lo_idx], resample_means[hi_idx])


def permutation_test(
    a: Sequence[float],
    b: Sequence[float],
    n_permutations: int = 10_000,
    seed: int = 0,
    statistic: Callable[[Sequence[float], Sequence[float]], float] = _MeanDiff,
) -> Tuple[float, float]:
    """Two-sided Monte Carlo (or exact, when cheap) permutation test.

    Returns `(observed_statistic, p_value)`. Under the null hypothesis that
    `a` and `b` are drawn from the same distribution, every way of relabeling
    the pooled `a + b` values into two groups of the original sizes is
    equally likely; `p_value` is the fraction of relabelings whose
    `|statistic|` is at least as extreme as the one actually observed.

    When the number of distinct label splits (`C(len(a)+len(b), len(a))`) is
    at most `_EXACT_ENUMERATION_LIMIT`, every split is enumerated exactly
    (deterministic, no `seed` effect); otherwise `n_permutations` random
    relabelings are sampled.
    """
    pooled = list(a) + list(b)
    na = len(a)
    n = len(pooled)
    if na == 0 or na == n:
        return (0.0, 1.0)

    observed = statistic(a, b)
    threshold = abs(observed)
    total_splits = comb(n, na)

    if total_splits <= _EXACT_ENUMERATION_LIMIT:
        as_extreme = 0
        indices = range(n)
        for combo in combinations(indices, na):
            combo_set = set(combo)
            sample_a = [pooled[i] for i in combo_set]
            sample_b = [pooled[i] for i in indices if i not in combo_set]
            if abs(statistic(sample_a, sample_b)) >= threshold - 1e-12:
                as_extreme += 1
        p_value = as_extreme / total_splits
        return (observed, p_value)

    rng = random.Random(seed)
    as_extreme = 0
    for _ in range(n_permutations):
        shuffled = pooled[:]
        rng.shuffle(shuffled)
        sample_a, sample_b = shuffled[:na], shuffled[na:]
        if abs(statistic(sample_a, sample_b)) >= threshold - 1e-12:
            as_extreme += 1
    p_value = as_extreme / n_permutations
    return (observed, p_value)


def _paired_differences(
    a: Sequence[float], b: Sequence[float]
) -> List[float]:
    """`a[k] - b[k]`, requiring the two arms to be matched element-wise."""
    if len(a) != len(b):
        raise ValueError(
            f"paired samples must be the same length, got {len(a)} and {len(b)}"
        )
    return [float(x) - float(y) for x, y in zip(a, b)]


def paired_bootstrap_ci(
    a: Sequence[float],
    b: Sequence[float],
    n_resamples: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> Tuple[float, float, float]:
    """Percentile bootstrap CI on the *mean paired difference* `a - b`.

    `bootstrap_ci` resamples one arm at a time and so describes the spread of
    each arm; when the two arms were run on the same seeds, the quantity of
    interest is the per-seed difference, whose variance is far smaller because
    the shared task stream cancels out. Returns `(mean_difference, lo, hi)`.
    """
    return bootstrap_ci(
        _paired_differences(a, b), n_resamples=n_resamples, alpha=alpha, seed=seed
    )


def paired_permutation_test(
    a: Sequence[float],
    b: Sequence[float],
    n_permutations: int = 10_000,
    seed: int = 0,
) -> Tuple[float, float]:
    """Two-sided paired (sign-flip) permutation test.

    Returns `(mean_difference, p_value)`. `a` and `b` must be matched
    element-wise -- in this project that means run on the same seeds, which
    `experiments.build_run` guarantees by feeding one seed to both
    `Params.seed` and the `TaskGenerator`.

    Under the null hypothesis that the treatment changes nothing, the sign of
    each per-seed difference is arbitrary, so the exchangeable unit is the sign
    rather than the group label. Enumerating the `2**n` sign patterns is exact
    when that is at most `_EXACT_ENUMERATION_LIMIT` (n <= 14), which covers
    every seed count this project runs.

    This is the test to use in place of `permutation_test` whenever the arms
    are paired: pooling and relabeling discards the pairing and, with a shared
    task stream, is markedly less powerful.
    """
    diffs = _paired_differences(a, b)
    n = len(diffs)
    if n == 0:
        return (0.0, 1.0)

    observed = statistics.fmean(diffs)
    threshold = abs(observed)
    # A run of identical values has no signal to test; every sign flip
    # reproduces the observed statistic, which would otherwise report p = 1.0
    # via the loop below anyway. Kept explicit so n == 1 is well defined.
    if all(d == 0.0 for d in diffs):
        return (observed, 1.0)

    total = 2 ** n
    if total <= _EXACT_ENUMERATION_LIMIT:
        as_extreme = 0
        for signs in product((1.0, -1.0), repeat=n):
            flipped = statistics.fmean(
                [s * d for s, d in zip(signs, diffs)]
            )
            if abs(flipped) >= threshold - 1e-12:
                as_extreme += 1
        return (observed, as_extreme / total)

    rng = random.Random(seed)
    as_extreme = 0
    for _ in range(n_permutations):
        flipped = statistics.fmean(
            [d if rng.random() < 0.5 else -d for d in diffs]
        )
        if abs(flipped) >= threshold - 1e-12:
            as_extreme += 1
    return (observed, as_extreme / n_permutations)


__all__ = [
    "bootstrap_ci",
    "permutation_test",
    "paired_bootstrap_ci",
    "paired_permutation_test",
]
