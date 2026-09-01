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
            ("lane_bonus_only", "SPAR"),
        )
    ]


def test_a_panel_records_a_frame_per_timestep(panels):
    for panel in panels:
        assert len(panel.history) == STEPS
        assert len(panel.stalled) == STEPS


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


def test_narration_shows_the_last_beat_that_has_come_due():
    """A beat stays on screen until the next one, and none before the first."""
    beats = [
        viz_compare.Beat(0, "setup"),
        viz_compare.Beat(40, "the corridor fills"),
        viz_compare.Beat(90, "nothing moves"),
    ]
    assert viz_compare.beat_at(beats, 0).text == "setup"
    assert viz_compare.beat_at(beats, 39).text == "setup"
    assert viz_compare.beat_at(beats, 40).text == "the corridor fills"
    assert viz_compare.beat_at(beats, 500).text == "nothing moves"
    assert viz_compare.beat_at([], 10) is None
    assert viz_compare.beat_at([viz_compare.Beat(5, "later")], 4) is None


def test_the_chart_can_plot_either_series(panels):
    """Either series can be plotted; both are cumulative and non-decreasing."""
    for panel in panels:
        delivered = viz_compare._series_values(panel, "delivered")
        assert len(delivered) == len(panel.history)
        assert delivered == [s.completed_tasks for s in panel.history]
        assert delivered == sorted(delivered), "tasks delivered cannot go down"
    # an unknown name must not blow up mid-render; it falls back to the default
    assert viz_compare._series_values(panels[0], "nonsense") == [
        s.completed_tasks for s in panels[0].history
    ]


def test_a_frame_renders_with_narration(panels):
    """A frame draws, and the narration beat changes what it draws.

    There used to be a second chart series here -- cumulative aisle direction
    flips -- and this test compared the two pictures. The aisle-direction layer
    was removed after it measured -0.3% (p = 0.95), so tasks delivered is the
    only series left, and the thing worth guarding is that narration reaches
    the canvas at all.
    """
    layout = viz_compare._fit_layout(panels, max_width=1000)
    statics = [viz_compare._draw_static(p.warehouse, layout) for p in panels]
    frames = [
        viz_compare.render_frame(
            panels, 10, layout, "title", "caption", statics, best_completed=1,
            beats=beats, chart_series="delivered",
        )
        for beats in ([], [viz_compare.Beat(0, "a sentence about what happens")])
    ]
    assert frames[0].size == frames[1].size
    assert frames[0].tobytes() != frames[1].tobytes(), (
        "the narration beat never reached the canvas"
    )


def test_the_first_and_last_frames_are_held_longer(panels, tmp_path):
    """A viewer needs time to read the setup, and longer to read the outcome."""
    from PIL import Image

    out = viz_compare.save_comparison(
        panels, tmp_path / "held.gif", "title", "caption", stride=5, hold_last=0,
        fps=10, hold_first_ms=2000, hold_last_ms=3500,
    )
    gif = Image.open(out)
    durations = []
    for index in range(gif.n_frames):
        gif.seek(index)
        durations.append(gif.info["duration"])
    assert durations[0] >= 2000
    assert durations[-1] >= 3500
    assert max(durations[1:-1]) < 2000
