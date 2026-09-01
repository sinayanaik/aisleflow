#!/usr/bin/env python3
"""Fill the generated tables in `docs/04-parameters.md` and `docs/05-results.md`.

Both documents quote numbers that come from `docs/data/`. Quoting them by hand
is how a document starts lying: the dataset gets regenerated, the prose does
not, and nothing complains. So the tables live between marker comments and are
written from the JSON, and `tests/test_docs.py` fails if they are stale.

    python3 tools/make_docs_tables.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DATA = ROOT / "docs" / "data"
DOCS = ROOT / "docs"

from lda_pibt.config import Params  # noqa: E402

BEGIN = "<!-- generated:{} -->"
END = "<!-- /generated:{} -->"


def load(name: str) -> Dict[str, Any]:
    path = DATA / f"{name}.json"
    if not path.exists():
        raise SystemExit(
            f"missing {path.relative_to(ROOT)} -- run "
            f"`python3 experiments/run_{'sensitivity' if name == 'sensitivity' else 'all'}.py`"
        )
    return json.loads(path.read_text())


def splice(doc: Path, key: str, body: str) -> bool:
    text = doc.read_text()
    begin, end = BEGIN.format(key), END.format(key)
    if begin not in text or end not in text:
        raise SystemExit(f"{doc.name}: no markers for {key!r}")
    head = text[: text.index(begin) + len(begin)]
    tail = text[text.index(end):]
    new = f"{head}\n{body.rstrip()}\n{tail}"
    if new == text:
        return False
    doc.write_text(new)
    return True


def pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


# ---------------------------------------------------------------- parameters
def parameter_table(sens: Dict[str, Any]) -> str:
    """Every live parameter, its default, and what removing it measured."""
    by_knob = {row["knob"]: row for row in sens["summary"]}
    defaults = Params().to_dict()

    #: parameter -> (the sensitivity knob that neutralises it, one-line meaning)
    MEANING = {
        "progress_reward": ("progress_reward", "Reward per cell of progress. Sets the tier spacing every other term is judged against."),
        "aisle_bonus": ("aisle_bonus/aisle_bonus_near", "Reward for staying in the aisle the robot is already in."),
        "aisle_bonus_near": ("aisle_bonus/aisle_bonus_near", "The same reward once the robot is near its waypoint."),
        "turn_penalty": ("turn_penalty", "Cost of turning a corner instead of carrying straight on."),
        "reverse_multiplier": ("reverse_multiplier", "Reversing costs this many times a turn."),
        "crowding_penalty": ("crowding_penalty", "Cost of moving into a completely crowded cell."),
        "local_congestion_radius": ("local_congestion_radius=1", "Radius of the “how full is it around here” measurement."),
        "priority_class_spread": ("priority_class_spread", "Rank gap between adjacent job classes."),
        "priority_inside_aisle": ("priority_inside_aisle", "Rank bonus for a robot already inside an aisle."),
        "waiting_weight": ("waiting_weight", "Rank bought per step waited. The anti-starvation guarantee."),
        "stall_steps": ("stall_steps", "Steps without progress before a robot counts as stalled."),
        "require_deadlock_corroboration": ("require_deadlock_corroboration", "Require a cycle or a repeated configuration, not just a lack of progress."),
        "recovery_max_level": ("recovery_max_level=3", "How many recovery remedies may run."),
        "cost_to_pickup": ("cost_to_pickup", "Weight on distance to the pickup in the task match."),
        "cost_pickup_to_delivery": ("cost_pickup_to_delivery", "Weight on the delivery trip the match commits to."),
        "cost_congestion": ("cost_congestion", "Weight on how crowded the way to the pickup is."),
        "cost_waiting": ("cost_waiting", "Negative, so older jobs are preferred."),
        "cost_waiting_cap": ("cost_waiting_cap", "Cap on the waiting term, so it cannot swamp the match."),
        "cost_blocking": ("cost_blocking", "Penalty for routing a match through chokepoints."),
    }

    lines = [
        "| Parameter | Default | What it does | Removing it costs | Verdict |",
        "| --- | ---: | --- | ---: | --- |",
    ]
    for name, (knob, meaning) in MEANING.items():
        row = by_knob.get(knob)
        if row is None:
            effect, verdict = "not measured", "—"
        else:
            effect = pct(-row["pooled_relative_delta"])
            verdict = {
                "load-bearing": "**load-bearing**",
                "cheap": "earns its place",
                "inert": "within noise",
            }[row["verdict"]]
        lines.append(
            f"| `{name}` | {defaults[name]} | {meaning} | {effect} | {verdict} |"
        )

    unlisted = sorted(set(defaults) - set(MEANING))
    lines.append("")
    lines.append(
        "Switches and run settings, not weights: "
        + ", ".join(f"`{n}`" for n in unlisted)
        + "."
    )
    return "\n".join(lines)


def sensitivity_table(sens: Dict[str, Any]) -> str:
    """Every knob, ordered worst-to-best, grouped by the model it belongs to."""
    lines = [
        "| Family | Knob neutralised | Pooled effect | p | Worst map |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for row in sens["summary"]:
        worst = (
            f"{row['worst_map'].replace('warehouse_', '')} "
            f"({pct(row['worst_relative_delta'])})"
            if row["worst_map"] else "—"
        )
        lines.append(
            f"| {row['family']} | `{row['knob']}` | {pct(row['pooled_relative_delta'])} "
            f"| {row['pooled_p_value']:.3f} | {worst} |"
        )
    meta = sens["meta"]
    lines += [
        "",
        f"*{len(sens['summary'])} variants, {meta['seeds']} seeds, "
        f"{meta['timesteps']} steps, {len(meta['scenarios'])} maps. "
        f"Effect is the change in throughput from removing the knob, so a "
        f"positive number means the planner is better without it. Paired "
        f"sign-flip test; git `{meta['git_sha']}`.*",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- results
def headline_table(baselines: Dict[str, Any]) -> str:
    """One row per planner per map: the comparison, in one place."""
    LABEL = {
        "full_lda_pibt": "**This planner**",
        "lifelong_pibt": "Plain lifelong PIBT",
        "token_passing": "Token Passing",
        "token_passing_recovery": "Token Passing + recovery",
        "rhcr": "RHCR",
    }
    maps = list(baselines["maps"])
    lines = [
        "| Planner | " + " | ".join(m.replace("warehouse_", "") for m in maps) + " |",
        "| --- | " + " | ".join("---:" for _ in maps) + " |",
    ]
    for variant, label in LABEL.items():
        cells = []
        for m in maps:
            row = next((r for r in baselines["maps"][m]["rows"]
                        if r["variant"] == variant), None)
            if row is None:
                cells.append("—")
                continue
            f = row["fields"]["throughput"]
            cells.append(f"{f['mean']:.3f}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    meta = baselines["meta"]
    lines += [
        "",
        f"*Tasks delivered per timestep; higher is better. {meta['seeds']} seeds "
        f"x {meta['timesteps']} steps, identical job streams across planners. "
        f"git `{meta['git_sha']}`.*",
    ]
    return "\n".join(lines)


def ladder_table(ablation: Dict[str, Any]) -> str:
    """Throughput per map for each rung of the ablation ladder.

    The point of showing all four maps rather than a mean is that the mean
    hides a sign flip: the mechanisms help where the floor is tight and hurt
    where it is open.
    """
    RUNGS = [
        ("lifelong_pibt", "plain lifelong PIBT"),
        ("turning_cost_only", "+ turning cost"),
        ("lane_bonus_only", "+ stay-in-lane bonus"),
        ("congestion_only", "+ crowding"),
        ("full_lda_pibt", "+ deadlock recovery (full)"),
    ]
    maps = list(ablation["maps"])
    lines = [
        "| Configuration | " + " | ".join(m.replace("warehouse_", "") for m in maps) + " |",
        "| --- | " + " | ".join("---:" for _ in maps) + " |",
    ]
    best = {}
    for m in maps:
        rows = {r["variant"]: r for r in ablation["maps"][m]["rows"]}
        best[m] = max((rows[v]["throughput"] for v, _ in RUNGS if v in rows), default=0.0)
    for variant, label in RUNGS:
        cells = []
        for m in maps:
            rows = {r["variant"]: r for r in ablation["maps"][m]["rows"]}
            if variant not in rows:
                cells.append("—")
                continue
            value = rows[variant]["throughput"]
            cells.append(f"**{value:.3f}**" if value >= best[m] else f"{value:.3f}")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    meta = ablation["meta"]
    lines += [
        "",
        f"*Tasks per timestep; **bold** is the best configuration for that map. "
        f"{meta['seeds']} seeds x {meta['timesteps']} steps, git `{meta['git_sha']}`.*",
    ]
    return "\n".join(lines)


def main() -> int:
    sens = load("sensitivity")
    changed = []
    if splice(DOCS / "04-parameters.md", "parameters", parameter_table(sens)):
        changed.append("04-parameters.md:parameters")
    if splice(DOCS / "05-results.md", "sensitivity", sensitivity_table(sens)):
        changed.append("05-results.md:sensitivity")

    ablation_path = DATA / "ablation.json"
    if ablation_path.exists():
        if splice(DOCS / "05-results.md", "ladder",
                  ladder_table(json.loads(ablation_path.read_text()))):
            changed.append("05-results.md:ladder")

    baselines_path = DATA / "baselines.json"
    if baselines_path.exists():
        if splice(DOCS / "05-results.md", "headline",
                  headline_table(json.loads(baselines_path.read_text()))):
            changed.append("05-results.md:headline")
    else:
        print("  (no baselines.json yet -- skipping the headline table)")

    print("updated: " + (", ".join(changed) if changed else "nothing (already current)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
