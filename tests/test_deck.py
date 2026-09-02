"""The slide deck must not drift from the code, the data, or its own rules.

`docs/deck/slides.html` is 38 self-contained slides that open by double-click.
Three of them show arithmetic computed by the real planner and three quote
tables the documents generate; all six are written between marker comments by
`tools/make_deck_figures.py`, and the first test here is that running it
changes nothing. The rest are the deck's house rules made mechanical: it opens
offline, it is numbered in order, and it cannot describe a mechanism that has
been deleted from the planner.

That last one exists because the deck was deleted once for exactly that
reason. It had spent a year arguing for an aisle-direction layer that measured
-0.3% (p = 0.95).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DECK = ROOT / "docs" / "deck" / "slides.html"
sys.path.insert(0, str(ROOT / "src"))

from lda_pibt.config import REMOVED_NAMES  # noqa: E402


@pytest.fixture(scope="module")
def deck() -> str:
    assert DECK.exists(), "docs/deck/slides.html is missing"
    return DECK.read_text(encoding="utf-8")


def test_every_slide_is_numbered_in_order(deck):
    numbers = [int(n) for n in re.findall(r'<section class="slide[^"]*"[^>]*data-num="(\d+)"', deck)]
    assert numbers, "no slides found"
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"slide numbering is not sequential: {numbers}"
    )


def test_every_slide_has_a_unique_slug(deck):
    slugs = re.findall(r'data-slug="([^"]+)"', deck)
    duplicates = sorted({s for s in slugs if slugs.count(s) > 1})
    assert not duplicates, f"duplicate slugs: {duplicates}"


def test_the_generated_blocks_are_current():
    """`tools/make_deck_figures.py` must be a no-op on a clean tree.

    A slide that quotes a number by hand is a slide that will eventually lie
    about it in front of an audience.
    """
    before = DECK.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "make_deck_figures.py")],
        capture_output=True,
        text=True,
    )
    after = DECK.read_text(encoding="utf-8")
    if before != after:
        DECK.write_text(before, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert before == after, (
        "docs/deck/slides.html is stale -- run python3 tools/make_deck_figures.py"
    )


def test_every_generated_block_is_filled(deck):
    names = set(re.findall(r"<!-- generated:([a-z-]+) -->", deck))
    assert names, "the deck has no generated blocks"
    for name in sorted(names):
        body = deck.split(f"<!-- generated:{name} -->", 1)[1].split(
            f"<!-- /generated:{name} -->", 1
        )[0]
        assert body.strip(), f"the {name} block is empty"


def test_the_deck_opens_offline(deck):
    """No external font, script, stylesheet or image: it must work by
    double-click on a machine with no network, forever."""
    assert "<script" not in deck.lower(), "the deck must not run scripts"
    external = re.findall(r'(?:src|href)="(https?:|//)[^"]*"', deck)
    assert not external, f"the deck reaches out to the network: {external}"
    assert "@import" not in deck, "the deck must not import a stylesheet"
    assert "<img" not in deck.lower(), (
        "the deck must not link an image -- draw it inline as SVG"
    )


def test_no_html_element_is_nested_inside_an_svg(deck):
    """An HTML tag inside `<svg>` silently ends the SVG.

    The parser breaks out of foreign content, and the rest of the drawing is
    rendered as body text. It looks like a layout bug and it is a parsing one,
    so it is worth a test: this has happened once already.
    """
    offenders = []
    for match in re.finditer(r"<svg\b.*?</svg>", deck, re.S):
        found = set(re.findall(r"<(span|div|p|b|em|i|br|table)\b", match.group(0)))
        if found:
            line = deck[: match.start()].count("\n") + 1
            offenders.append((line, sorted(found)))
    assert not offenders, f"HTML nested inside SVG at {offenders}"


#: The one slide whose subject *is* the deletions. It has to name them to say
#: they are gone, so the two tests below read the deck without it. Everywhere
#: else, naming a deleted mechanism means the deck is describing a planner that
#: does not exist.
DELETIONS_SLIDE = "deletions"


def without_the_deletions_slide(deck: str) -> str:
    deck = re.sub(r"<aside class=\"notes\">.*?</aside>", "", deck, flags=re.S)
    return re.sub(
        rf'<section class="slide"[^>]*data-slug="{DELETIONS_SLIDE}".*?</section>',
        "",
        deck,
        flags=re.S,
    )


def test_the_deletions_slide_exists(deck):
    """The exemption below is only safe while the slide it exempts is real."""
    assert f'data-slug="{DELETIONS_SLIDE}"' in deck


def test_the_deck_does_not_describe_a_deleted_mechanism(deck):
    """Every parameter in `REMOVED_NAMES` names something the planner no longer
    has. Outside the deletions slide, none of them may appear."""
    prose = without_the_deletions_slide(deck)
    present = sorted(name for name in REMOVED_NAMES if name in prose)
    assert not present, (
        f"the deck describes mechanisms that were deleted from the planner: {present}"
    )


@pytest.mark.parametrize(
    "phrase",
    [
        "SPAR",           # the project is called aisleflow
        "one-way",        # the aisle-direction layer, deleted at b00ff91
        "maximum green",
        "entry permit",
        "head-on conflict",  # the counter exists and is never incremented
    ],
)
def test_retired_vocabulary_stays_out(deck, phrase):
    body = without_the_deletions_slide(deck.split("<body>", 1)[1])
    assert phrase.lower() not in body.lower(), (
        f"{phrase!r} describes something this planner no longer has"
    )


def test_the_deck_is_reachable_from_the_documentation():
    readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "deck/slides.html" in readme, (
        "docs/README.md does not link the deck, so nobody will find it"
    )
