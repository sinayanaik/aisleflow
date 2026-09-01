# Figures

Two figures, generated from `docs/data/` by `tools/make_figures.py`.
Neither carries a title or caption of its own -- the explanation is
here and in the pages that embed them, so there is one copy of it
rather than a copy in pixels that could drift from the prose.

```bash
python3 tools/make_figures.py
```

## Aisleflow leads every published planner on warehouse_bottleneck

![Aisleflow leads every published planner on warehouse_bottleneck](01-vs-baselines.svg)

Taller is better: tasks delivered per 1000 timesteps of simulated time, same job stream and same robot count for every planner. Whiskers are 95% bootstrap intervals over 5 seeds; aisleflow's interval clears all three baselines' with no overlap. `warehouse_bottleneck` is two halves joined by one six-cell corridor that every task must cross, in both directions, forever -- see [../06-the-maps.md](../06-the-maps.md). The full table, including RHCR, is on [../05-results.md](../05-results.md#against-the-published-planners).

## The five warehouse floors every number on the results page was measured on

![The five warehouse floors every number on the results page was measured on](05-the-maps.svg)

All drawn to one scale. Every aisle is one cell wide on all five; what differs is how long the aisles are, how many ways round there are, and whether a robot standing still can cut the floor in two. Only `bottleneck` can -- its two halves meet in one corridor. `corridors` is five 22-cell single-file runs joined at both ends; `narrow` is `medium` with the aisles twice as long and no extra way round. Per-map detail is in [../06-the-maps.md](../06-the-maps.md).

*5 seeds x 400 timesteps, Poisson arrivals, generated 2026-09-01T10:17:31Z from aisleflow @ ef0910e by experiments/run_all.py*