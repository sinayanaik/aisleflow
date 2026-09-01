"""The guide's worked examples must keep agreeing with the code.

`docs/latex/` prints real numbers -- candidate scores, aisle demands,
assignment costs -- produced by `tools/worked_examples.py` from a fixed
scenario. Change a default weight or a formula and those numbers move. This
test is what stops the guide from quietly describing a system that no longer
exists.

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

@pytest.fixture(scope="module")
def blocks():
    return we.render_all()


@pytest.fixture(scope="module")
def sources():
    """Every file of the guide that may carry a marker, with its text."""
    return [(path, path.read_text(encoding="utf-8")) for path in we.guide_files()]


def test_guide_marks_every_example(blocks, sources):
    marked = {
        match.group("name")
        for _, text in sources
        for match in we.BLOCK_RE.finditer(text)
    }
    assert marked == set(blocks), (
        "the guide's worked-example markers and tools/worked_examples.py "
        "disagree about which examples exist"
    )


def test_guide_matches_the_code(blocks, sources):
    stale = []
    seen_any = False
    for path, text in sources:
        updated, seen = we.substitute(text, blocks)
        seen_any = seen_any or bool(seen)
        if updated != text:
            stale.append(path.name)
    assert seen_any, "no worked-example markers found in the guide"
    assert not stale, (
        f"{', '.join(stale)} is out of date with the code. "
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


def test_every_block_is_a_worked_example_box(blocks):
    """The guide sets these as a tcolorbox, so the wrapper must stay intact."""
    for name, block in blocks.items():
        lines = [line for line in block.splitlines() if line.strip()]
        assert lines, f"{name} rendered empty"
        assert lines[0] == r"\begin{workedexample}", name
        assert lines[-1] == r"\end{workedexample}", name
        assert lines[1].startswith(r"\textbf{Worked example.}"), name
        assert block.count(r"\begin{verbatim}") == block.count(r"\end{verbatim}"), (
            f"{name} has an unbalanced verbatim block"
        )
