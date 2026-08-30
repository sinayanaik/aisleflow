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
from itertools import combinations
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


__all__ = ["bootstrap_ci", "permutation_test"]
