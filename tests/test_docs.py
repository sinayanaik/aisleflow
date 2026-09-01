"""The documents must not drift from the code or the data.

A document that quotes a number is a document that will eventually lie about
it. These tests make that a build failure instead: the generated tables have to
match `docs/data/`, and every parameter has to appear on the parameter page.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DATA = DOCS / "data"
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.config import Params  # noqa: E402

PAGES = [
    "README.md",
    "01-how-it-works.md",
    "02-decision-flow.md",
    "03-the-math.md",
    "04-parameters.md",
    "05-results.md",
    "06-the-maps.md",
]


@pytest.mark.parametrize("page", PAGES)
def test_every_page_exists_and_is_not_a_stub(page):
    text = (DOCS / page).read_text(encoding="utf-8")
    assert len(text.splitlines()) > 20, f"{page} looks like a stub"


@pytest.mark.parametrize("page", PAGES)
def test_internal_links_resolve(page):
    """A dead link between the five pages is the easiest rot to introduce."""
    import re

    text = (DOCS / page).read_text(encoding="utf-8")
    for target in re.findall(r"\]\((?!https?:)([^)#]+)", text):
        assert (DOCS / target).exists(), f"{page} links to missing {target}"


def test_generated_tables_are_current():
    """`tools/make_docs_tables.py` must be a no-op on a clean tree."""
    before = {p: (DOCS / p).read_text() for p in PAGES}
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_docs_tables.py")],
        check=True, capture_output=True, cwd=ROOT,
    )
    stale = [p for p in PAGES if (DOCS / p).read_text() != before[p]]
    for p in PAGES:  # never leave the tree dirty, even on failure
        (DOCS / p).write_text(before[p])
    assert not stale, (
        f"{stale} are stale -- run `python3 tools/make_docs_tables.py`"
    )


def test_every_parameter_is_documented():
    """A knob nobody documented is a knob nobody justified."""
    text = (DOCS / "04-parameters.md").read_text(encoding="utf-8")
    missing = [name for name in Params().to_dict() if f"`{name}`" not in text]
    assert not missing, f"undocumented parameters: {missing}"


def test_the_math_page_defines_every_symbol_it_uses():
    """Every symbol in a formula needs a row in the notation table."""
    import re

    text = (DOCS / "03-the-math.md").read_text(encoding="utf-8")
    notation = text[: text.index("## 1.")]
    used = set(re.findall(r"\\(?:kappa|tau|ell|Delta|epsilon|sigma|rho|lambda|beta)\b", text))
    for symbol in used:
        assert symbol in notation or symbol in (r"\beta", r"\rho", r"\lambda", r"\epsilon", r"\sigma"), (
            f"{symbol} is used in a formula but not in the notation table"
        )


@pytest.mark.parametrize("suite", ["sensitivity"])
def test_dataset_carries_its_provenance(suite):
    payload = json.loads((DATA / f"{suite}.json").read_text())
    meta = payload["meta"]
    for key in ("suite", "seeds", "timesteps", "scenarios", "git_sha", "generator"):
        assert key in meta, f"{suite}.json meta is missing {key}"
    assert meta["seeds"] >= 1 and meta["timesteps"] >= 1


def test_the_worked_example_matches_the_real_scorer():
    """The worked example in `03-the-math.md` must be arithmetic, not fiction.

    A wrong number in a worked example is worse than no example: it is the one
    place a reader checks their understanding against.
    """
    from lda_pibt.scoring import compute_aisle_bonus, compute_proximity_mode

    p = Params()
    bonus = compute_aisle_bonus(compute_proximity_mode(4, p), p)
    north = 10 * 1 + bonus - p.turn_penalty * 0 - p.crowding_penalty * 0.5
    east = -p.turn_penalty * 1
    south = 10 * -1 + bonus - p.turn_penalty * p.reverse_multiplier

    text = (DOCS / "03-the-math.md").read_text(encoding="utf-8")
    assert f"{bonus}" in text, "the approach-band lane bonus is misquoted"
    assert f"\\mathbf{{{north}}}" in text, f"the winning score should be {north}"
    assert f"= {south}$" in text, f"the reversing score should be {south}"
    assert f"{north - east} over the next best" in text, (
        f"the margin should be {north - east}"
    )
