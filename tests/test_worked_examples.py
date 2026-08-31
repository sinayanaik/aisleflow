"""The guide's worked examples must keep agreeing with the code.

`docs/mathematical-guide.md` prints real numbers -- candidate scores, aisle
demands, assignment costs -- produced by `tools/worked_examples.py` from a
fixed scenario. Change a default weight or a formula and those numbers move.
This test is what stops the guide from quietly describing a system that no
longer exists.

If it fails, the fix is one command:

    python3 tools/worked_examples.py --write
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import worked_examples as we  # noqa: E402

GUIDE = ROOT / "docs" / "mathematical-guide.md"


@pytest.fixture(scope="module")
def blocks():
    return we.render_all()


def test_guide_marks_every_example(blocks):
    text = GUIDE.read_text(encoding="utf-8")
    marked = {match.group("name") for match in we.BLOCK_RE.finditer(text)}
    assert marked == set(blocks), (
        "the guide's worked-example markers and tools/worked_examples.py "
        "disagree about which examples exist"
    )


def test_guide_matches_the_code(blocks):
    text = GUIDE.read_text(encoding="utf-8")
    updated, seen = we.substitute(text, blocks)
    assert seen, "no worked-example markers found in the guide"
    assert updated == text, (
        "docs/mathematical-guide.md is out of date with the code. "
        "Run: python3 tools/worked_examples.py --write"
    )


def test_score_columns_sum_to_the_printed_score():
    """The example's per-term columns are not decoration: they must add up."""
    moment = we.build_moment()
    moment.sim.planner.scorer.timestep = moment.timestep
    for row in moment.rows:
        candidate = tuple(row["vertex"])
        terms = we.score_terms(moment, candidate)
        assert sum(terms.values()) == pytest.approx(row["score"], abs=1e-4)


def test_every_block_is_a_blockquote(blocks):
    """Rendered as a callout by tools/build_docs.py, so it must stay quoted."""
    for name, block in blocks.items():
        lines = [line for line in block.splitlines() if line.strip()]
        assert lines, f"{name} rendered empty"
        assert all(line.startswith(">") for line in lines), name
        assert lines[0].startswith("> **Worked example.**"), name
