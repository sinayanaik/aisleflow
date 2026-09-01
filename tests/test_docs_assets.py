"""The committed documentation assets must stay consistent with each other.

`docs/` now carries generated artefacts that are checked in: the measured
dataset, eight figures, five animations and a dashboard. None of them is
rebuilt by the test suite -- that would take half an hour -- so what these
tests check is the wiring: that the dataset is well formed and says where it
came from, that every figure the paper and the dashboard reference exists, and
that no committed animation has quietly grown past the size budget.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
FIGURES = ROOT / "docs" / "figures"
GIFS = ROOT / "docs" / "gifs"
LATEX = ROOT / "docs" / "latex"

#: the budget `viz_compare.save_comparison` enforces when writing
GIF_BUDGET_BYTES = 5 * 1024 * 1024

SUITES = ("ablation", "baselines", "hypotheses", "paired", "factorial")


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


def test_every_figure_the_paper_includes_exists():
    referenced = set()
    for source in LATEX.rglob("*.tex"):
        referenced |= set(
            re.findall(r"\\includegraphics\[[^\]]*\]\{([^}]+)\}", source.read_text())
        )
    assert referenced, "the paper includes no figures at all"
    for name in sorted(referenced):
        assert (FIGURES / name).exists(), f"{name} is referenced but not committed"


def test_every_figure_ships_both_formats():
    """SVG renders inline on GitHub; PDF is what the paper embeds."""
    for pdf in FIGURES.glob("*.pdf"):
        assert pdf.with_suffix(".svg").exists(), f"{pdf.name} has no SVG twin"


def test_the_dashboard_is_self_contained():
    dashboard = ROOT / "docs" / "dashboard.html"
    assert dashboard.exists(), "run tools/make_figures.py --dashboard"
    html = dashboard.read_text()
    assert "__DATA__" not in html, "the dataset was never substituted in"
    external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
    assert not external, f"the dashboard fetches {external}; it must open offline"


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
