"""The comparison renderer, on two very short runs.

These are the animations the repository commits as evidence, so the renderer
needs a test -- but rendering a real one takes a minute and 2 MB, so this runs
two 25-step panels and checks the frame comes back the size the layout says it
should, with the derived per-frame series populated.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

pytest.importorskip("PIL", reason="pillow is an optional extra")

from lda_pibt import viz_compare  # noqa: E402

MAP = ROOT / "maps" / "warehouse_corridors.map"
STEPS = 25


@pytest.fixture(scope="module")
def panels():
    return [
        viz_compare.run_panel(
            MAP, variant, title, "subtitle",
            n_robots=8, timesteps=STEPS, seed=0, rate=1.0, stall_window=5,
        )
        for variant, title in (
            ("lifelong_pibt", "plain PIBT"),
            ("aisle_direction_only", "TOLL"),
        )
    ]


def test_a_panel_records_a_frame_per_timestep(panels):
    for panel in panels:
        assert len(panel.history) == STEPS
        assert len(panel.stalled) == STEPS
        assert len(panel.flips) == STEPS


def test_flip_counts_never_decrease(panels):
    """They are cumulative: a running total that fell would be a bug."""
    for panel in panels:
        assert panel.flips == sorted(panel.flips)


def test_stalls_are_derived_from_positions(panels):
    """Nothing may be flagged stalled before the window has elapsed."""
    for panel in panels:
        assert not any(panel.stalled[:5])


def test_a_frame_is_the_size_the_layout_says(panels):
    layout = viz_compare._fit_layout(panels, max_width=1000)
    statics = [viz_compare._draw_static(p.warehouse, layout) for p in panels]
    frame = viz_compare.render_frame(
        panels, 10, layout, "title", "caption", statics, best_completed=1,
    )
    widest = max(p.warehouse.width for p in panels)
    expected_width = (
        2 * layout.pad + len(panels) * widest * layout.cell
        + (len(panels) - 1) * layout.gutter
    )
    assert frame.width == expected_width
    assert frame.height > layout.header + layout.footer


def test_writing_a_gif_respects_the_budget(panels, tmp_path):
    out = viz_compare.save_comparison(
        panels, tmp_path / "test.gif", "title", "caption", stride=5, hold_last=2,
    )
    assert out.exists() and out.stat().st_size > 0

    with pytest.raises(RuntimeError, match="budget"):
        viz_compare.save_comparison(
            panels, tmp_path / "tiny.gif", "title", "caption",
            stride=5, hold_last=2, max_bytes=200,
        )
