"""The committed documentation assets must stay consistent with each other.

`docs/` carries generated artefacts that are checked in: the measured dataset,
five figures and five animations. None of them is rebuilt by the test suite --
that would take half an hour -- so what these tests check is the wiring: that
the dataset is well formed and says where it came from, that the figure
directory holds exactly the figures the generator writes, that every one of
them is actually shown to a reader somewhere, and that no committed animation
has quietly grown past the size budget.

That third check exists because it failed silently for a long time: nine
figures were generated, committed and regenerated across several passes
without a single document ever embedding one of them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
DOCS = ROOT / "docs"
FIGURES = ROOT / "docs" / "figures"
GIFS = ROOT / "docs" / "gifs"

#: the budget `viz_compare.save_comparison` enforces when writing
GIF_BUDGET_BYTES = 5 * 1024 * 1024

SUITES = ("ablation", "baselines", "hypotheses", "paired", "factorial", "sensitivity",)


@pytest.mark.parametrize("suite", SUITES)
def test_dataset_is_well_formed(suite):
    path = DATA / f"{suite}.json"
    assert path.exists(), f"{path.name} is missing: run experiments/run_all.py"
    payload = json.loads(path.read_text())
    meta = payload["meta"]
    for key in ("suite", "seeds", "timesteps", "scenarios", "git_sha",
                "generated_utc", "generator"):
        assert key in meta, f"{path.name} has no {key} in its provenance header"
    assert meta["suite"] == suite
    assert meta["seeds"] >= 1 and meta["timesteps"] >= 1
    assert meta["scenarios"], "no scenarios recorded"


def figure_names():
    """The figure keys `tools/make_figures.py` knows how to write."""
    sys.path.insert(0, str(ROOT / "tools"))
    from make_figures import FIGURES as REGISTRY  # noqa: E402

    return list(REGISTRY)


def test_figures_are_svg_only():
    """SVG renders inline on GitHub and keeps its labels as selectable text.

    The PDF twins each figure used to ship were for a LaTeX build that no
    longer exists; nothing reads them, and a second format is a second thing
    to forget to regenerate.
    """
    stray = sorted(p.name for p in FIGURES.iterdir() if p.suffix != ".svg")
    assert not stray, f"docs/figures/ should hold SVG only, found: {stray}"


def test_every_generated_figure_is_committed():
    committed = {p.stem for p in FIGURES.glob("*.svg")}
    expected = set(figure_names())
    assert committed == expected, (
        "docs/figures/ is out of step with tools/make_figures.py -- "
        f"missing {sorted(expected - committed)}, "
        f"orphaned {sorted(committed - expected)}; run `python3 tools/make_figures.py`"
    )


def test_every_figure_is_referenced_by_a_page():
    """A figure nobody embedded is a figure nobody reads."""
    pages = [ROOT / "README.md"] + sorted(DOCS.glob("*.md"))
    prose = "\n".join(page.read_text(encoding="utf-8") for page in pages)
    unreferenced = [
        svg.name for svg in sorted(FIGURES.glob("*.svg")) if svg.name not in prose
    ]
    assert not unreferenced, (
        f"generated but shown to nobody: {unreferenced} -- "
        "embed them in docs/05-results.md or stop generating them"
    )


def test_committed_animations_stay_within_budget():
    gifs = sorted(GIFS.glob("*.gif"))
    assert gifs, "no animations committed: run tools/make_gifs.py"
    oversized = {
        gif.name: gif.stat().st_size for gif in gifs
        if gif.stat().st_size > GIF_BUDGET_BYTES
    }
    assert not oversized, f"over the 5 MB budget: {oversized}"


def test_the_gif_readme_covers_every_animation():
    readme = (GIFS / "README.md").read_text()
    for gif in sorted(GIFS.glob("*.gif")):
        assert gif.name in readme, f"{gif.name} is committed but undocumented"
