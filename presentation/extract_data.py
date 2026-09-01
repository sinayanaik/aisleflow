#!/usr/bin/env python3
"""Collect every number the slide deck quotes into ``deck_data.json``.

Two sources, and the split matters:

*Generated tables* -- the featured comparison, the ablation ladder, the
sensitivity study and the map structure -- are lifted verbatim out of the
``<!-- generated:NAME -->`` blocks in ``docs/``. Those blocks are written by
``tools/make_docs_tables.py`` from ``docs/data/`` and a test fails if they
drift from it, so parsing them rather than recomputing means the deck quotes
exactly what the documents quote, guarded by the same test.

*Unplotted measurements* -- planner runtime per timestep, and throughput
against fleet size -- have no generated table or figure anywhere, so they are
computed here straight from ``docs/data/baselines.json`` and
``docs/data/density.json``. Nothing is simulated and nothing is rounded until
the slide prints it.

Every headline figure the deck asserts in prose is re-derived here and checked
against the parsed tables, so a regenerated dataset that moves a number breaks
this script rather than leaving a stale claim on a slide.

Usage::

    python3 presentation/extract_data.py            # writes deck_data.json
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "docs" / "data"
OUT = Path(__file__).resolve().parent / "deck_data.json"

#: the four floors the results are about, tightest first -- the same order
#: `tools/make_figures.py` uses, so the deck's tables read in the same
#: direction as the committed figures
MAP_ORDER = [
    "warehouse_bottleneck",
    "warehouse_corridors",
    "warehouse_narrow",
    "warehouse_medium",
]

#: display names, matching `tools/make_figures.VARIANT_LABEL` so a planner is
#: called the same thing on a slide as on the figure beside it
VARIANT_LABEL = {
    "lifelong_pibt": "Aisleflow (plain lifelong PIBT)",
    "full_lda_pibt": "Aisleflow (full congestion config)",
    "token_passing": "Token Passing",
    "token_passing_task_swaps": "TP + task swaps",
    "rhcr": "RHCR",
}

#: the two configurations that ship. Everything else in the ablation ladder is
#: a rung on the way between them.
AISLEFLOW_VARIANTS = ("lifelong_pibt", "full_lda_pibt")
BASELINE_VARIANTS = ("rhcr", "token_passing", "token_passing_task_swaps")


class DataError(RuntimeError):
    """A number the deck depends on is missing or has moved."""


def load(name: str) -> Dict[str, Any]:
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        raise DataError(
            f"{path.relative_to(ROOT)} is missing -- run "
            f"`python3 experiments/run_all.py --only {name}` first"
        )
    return json.loads(path.read_text())


# --------------------------------------------------------------------------
# the generated doc blocks
# --------------------------------------------------------------------------


def generated_block(doc: Path, name: str) -> Dict[str, Any]:
    """A `<!-- generated:NAME -->` markdown table, split into header and body.

    The `| --- |` separator row is dropped, and so is the italic provenance
    line inside the block -- `block_caption` picks that up separately, because
    a slide needs it in a smaller size than a paragraph does.
    """
    text = doc.read_text()
    match = re.search(
        rf"<!-- generated:{name} -->\n(.*?)\n<!-- /generated:{name} -->",
        text,
        re.S,
    )
    if match is None:
        raise DataError(f"no `generated:{name}` block in {doc.relative_to(ROOT)}")

    rows = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} for c in cells):
            continue
        rows.append(cells)
    if len(rows) < 2:
        raise DataError(f"`generated:{name}` block in {doc.name} has no table body")
    return {"header": rows[0], "rows": rows[1:]}


def block_caption(doc: Path, name: str) -> str:
    """The italic provenance line the generated block carries."""
    text = doc.read_text()
    match = re.search(
        rf"<!-- generated:{name} -->\n(.*?)\n<!-- /generated:{name} -->", text, re.S
    )
    for line in match.group(1).splitlines():
        line = line.strip()
        if line.startswith("*") and line.endswith("*"):
            return line.strip("*").strip()
    return ""


# --------------------------------------------------------------------------
# unplotted measurement 1: what the planner costs to run
# --------------------------------------------------------------------------


def runtime_table(baselines: Dict[str, Any]) -> Dict[str, Any]:
    """Mean planner runtime per timestep, per planner per floor.

    Recorded by every run in the baseline suite and never plotted or tabulated
    anywhere in `docs/`. It is the deck's clearest commercial number, so it is
    computed here rather than asserted.
    """
    rows = []
    for map_name in MAP_ORDER:
        entry = baselines["maps"][map_name]
        per_variant = {
            row["variant"]: row["fields"]["mean_runtime_ms_per_step"]["mean"]
            for row in entry["rows"]
        }
        # the conservative ratio: aisleflow's *dearest* configuration against
        # the baseline, so the advantage on the slide is the one that holds
        # even when the floor needs the full congestion machinery
        dearest_aisleflow = max(per_variant[v] for v in AISLEFLOW_VARIANTS)
        rows.append(
            {
                "map": map_name,
                "robots": entry["robots"],
                "ms_per_step": {k: round(v, 2) for k, v in per_variant.items()},
                "vs_rhcr": round(per_variant["rhcr"] / dearest_aisleflow),
                "vs_token_passing": round(
                    per_variant["token_passing"] / dearest_aisleflow
                ),
            }
        )

    ratios_rhcr = [r["vs_rhcr"] for r in rows]
    ratios_tp = [r["vs_token_passing"] for r in rows]
    return {
        "rows": rows,
        "min_vs_rhcr": int(min(ratios_rhcr)),
        "max_vs_rhcr": int(max(ratios_rhcr)),
        "min_vs_tp": int(min(ratios_tp)),
        "max_vs_tp": int(max(ratios_tp)),
    }


# --------------------------------------------------------------------------
# unplotted measurement 2: what the next robot is worth
# --------------------------------------------------------------------------


def density_table(density: Dict[str, Any]) -> Dict[str, Any]:
    """Throughput against fleet size, for the best aisleflow config per floor.

    `tools/make_figures.py` says this sweep used to have a figure and lost it
    when the results page narrowed to one claim on one floor. The measurement
    is still in `docs/data/density.json`, and it is the deck's fleet-sizing
    slide.
    """
    out = []
    for map_name, entry in density["maps"].items():
        counts = entry["robot_counts"]
        by_variant: Dict[str, Dict[int, float]] = {}
        for row in entry["rows"]:
            by_variant.setdefault(row["variant"], {})[row["n_robots"]] = (
                1000.0 * row["throughput"]
            )
        # "best config for this floor" is the deck's own recommendation, so the
        # curve shown is the one a reader would actually deploy there
        best = max(AISLEFLOW_VARIANTS, key=lambda v: max(by_variant[v].values()))
        series = [round(by_variant[best][n]) for n in counts]
        peak = max(range(len(counts)), key=lambda i: series[i])
        out.append(
            {
                "map": map_name,
                "rate": entry["rate"],
                "robot_counts": counts,
                "variant": best,
                "variant_label": VARIANT_LABEL[best],
                "per_1000": series,
                "peak_robots": counts[peak],
                "peak_value": series[peak],
                # the whole argument of the slide: what the last robots bought
                "delta_20_to_40": series[counts.index(40)] - series[counts.index(20)],
            }
        )
    return {"rows": out, "seeds": density["meta"]["seeds"]}


# --------------------------------------------------------------------------
# the four-floor comparison
# --------------------------------------------------------------------------


def per_1000(field: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mean": round(1000.0 * field["mean"]),
        "lo": round(1000.0 * field["ci_lo"]),
        "hi": round(1000.0 * field["ci_hi"]),
    }


def four_floors(baselines: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Best aisleflow configuration against every baseline, on all four maps.

    The featured figure is one floor, chosen because the case is clearest
    there. This is the complete picture the results page asks a reader to
    weigh it against, including the two floors where the lead is inside the
    intervals.
    """
    rows = []
    for map_name in MAP_ORDER:
        entry = baselines["maps"][map_name]
        fields = {row["variant"]: row["fields"] for row in entry["rows"]}

        best = max(AISLEFLOW_VARIANTS, key=lambda v: fields[v]["throughput"]["mean"])
        aisleflow = per_1000(fields[best]["throughput"])
        rival = max(
            BASELINE_VARIANTS, key=lambda v: fields[v]["throughput"]["mean"]
        )
        strongest = per_1000(fields[rival]["throughput"])

        # "decisive" is the no-overlap test the featured figure's caption uses,
        # applied uniformly rather than claimed for the one floor it holds on
        decisive = aisleflow["lo"] > strongest["hi"]
        rows.append(
            {
                "map": map_name,
                "robots": entry["robots"],
                "rate": entry["rate"],
                "best_variant": best,
                "best_variant_label": VARIANT_LABEL[best],
                "aisleflow": aisleflow,
                "strongest_baseline": rival,
                "strongest_baseline_label": VARIANT_LABEL[rival],
                "baselines": {
                    v: per_1000(fields[v]["throughput"]) for v in BASELINE_VARIANTS
                },
                "lead_pct": round(
                    100.0 * (aisleflow["mean"] / strongest["mean"] - 1.0)
                )
                if strongest["mean"]
                else None,
                "decisive": decisive,
                "verdict": "decisive — intervals do not overlap"
                if decisive
                else "ahead, but inside the intervals",
                "collision_free": all(row["collision_free"] for row in entry["rows"]),
            }
        )
    return rows


# --------------------------------------------------------------------------
# consistency checks
# --------------------------------------------------------------------------


def check(condition: bool, message: str) -> None:
    if not condition:
        raise DataError(message)


def cross_check(payload: Dict[str, Any]) -> None:
    """Re-derive from `docs/data/` every figure the deck states in prose.

    Each of these is a sentence on a slide. If the dataset is regenerated and
    a number moves, this raises instead of letting the slide keep the old one.
    """
    featured = payload["featured"]["rows"]
    check(featured[0][0].strip("*") == "Aisleflow", "featured table no longer leads with aisleflow")
    check(featured[0][1] == "147", f"featured aisleflow throughput moved: {featured[0][1]}")

    bottleneck = next(r for r in payload["four_floors"] if r["map"] == "warehouse_bottleneck")
    check(
        bottleneck["aisleflow"]["mean"] == int(featured[0][1]),
        "the featured table and baselines.json disagree on the headline number",
    )
    check(bottleneck["decisive"], "the headline no-overlap claim no longer holds")

    corridors = next(r for r in payload["density"]["rows"] if r["map"] == "warehouse_corridors")
    check(
        abs(corridors["delta_20_to_40"]) <= 5,
        f"corridors 20-vs-40 robots is no longer a wash: {corridors['delta_20_to_40']}",
    )
    medium = next(r for r in payload["density"]["rows"] if r["map"] == "warehouse_medium")
    check(
        medium["delta_20_to_40"] > 100,
        f"medium no longer rewards a bigger fleet: {medium['delta_20_to_40']}",
    )

    runtime = payload["runtime"]
    check(
        runtime["min_vs_rhcr"] >= 10,
        f"the compute advantage over RHCR has collapsed: {runtime['min_vs_rhcr']}x",
    )

    ladder = payload["ladder"]["rows"]
    check(len(ladder) >= 5, "the ablation ladder lost rows")
    # the deck's most-quoted honest finding: the bottom rung wins no column
    bottom = ladder[-1][1:]
    check(
        not any(cell.startswith("**") for cell in bottom),
        "the full configuration now wins a column -- slide 12's claim is stale",
    )


def main() -> int:
    baselines = load("baselines")
    density = load("density")
    results = ROOT / "docs" / "05-results.md"
    maps_doc = ROOT / "docs" / "06-the-maps.md"

    payload = {
        "meta": {
            "git_sha": baselines["meta"]["git_sha"],
            "seeds": baselines["meta"]["seeds"],
            "timesteps": baselines["meta"]["timesteps"],
            "generated_utc": baselines["meta"]["generated_utc"],
            "scenarios": baselines["meta"]["scenarios"],
        },
        "featured": {**generated_block(results, "featured"),
                     "caption": block_caption(results, "featured")},
        "ladder": {**generated_block(results, "ladder"),
                   "caption": block_caption(results, "ladder")},
        "sensitivity": {**generated_block(results, "sensitivity"),
                        "caption": block_caption(results, "sensitivity")},
        "maps": {**generated_block(maps_doc, "maps"),
                 "caption": block_caption(maps_doc, "maps")},
        "four_floors": four_floors(baselines),
        "runtime": runtime_table(baselines),
        "density": density_table(density),
    }

    cross_check(payload)
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"  wrote {OUT.relative_to(ROOT)}")
    for row in payload["four_floors"]:
        print(
            f"    {row['map']:<22} {row['aisleflow']['mean']:>4}/1k "
            f"vs {row['strongest_baseline']:<6} {row['strongest_baseline_label']:<14} "
            f"+{row['lead_pct']}%  {row['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
