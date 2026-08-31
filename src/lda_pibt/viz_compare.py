"""Side-by-side animation of two planners on the *same* scenario.

`viz.save_animation` draws one run as dots on a grid. That is enough to watch a
run, and not enough to make an argument: the claims this project makes are all
comparative -- "Token Passing gridlocks where priority inheritance does not",
"a one-way rule as a hard constraint deadlocks where the same rule as a price
does not" -- and a comparison needs both runs on screen at once, driven from
the same seed, the same map and the same task stream.

That is what this module renders. Two (or more) `Panel`s, one frame per
sampled timestep, into an animated GIF:

* aisles tinted by state, with a flow arrow on every committed direction, so
  FORWARD -> DRAINING -> REVERSE is visible as it happens;
* robots coloured by what they are doing, with any robot that has not moved
  for `stall_window` steps ringed in red -- the single most informative mark
  on the picture, because a screenful of red rings *is* gridlock;
* a per-panel readout of completed tasks and stalled robots, and a bar showing
  each panel's completed count against the best panel's, so "which side is
  winning" needs no arithmetic.

Rendering is plain Pillow, deliberately. Flat colour fills quantise to a
32-colour palette essentially for free, which is what keeps a 130-frame
two-panel GIF inside a couple of megabytes; the same frames drawn through
matplotlib's antialiased canvas are an order of magnitude larger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .simulator import StepSnapshot
from .types import AisleState, RobotState
from .warehouse import Warehouse

RGB = Tuple[int, int, int]

# --------------------------------------------------------------------------
# palette -- flat, few colours, readable when the GIF is quantised
# --------------------------------------------------------------------------

PAGE: RGB = (250, 250, 252)
INK: RGB = (32, 36, 45)
MUTED: RGB = (110, 118, 132)
RULE: RGB = (214, 219, 228)

FLOOR: RGB = (236, 240, 246)
OBSTACLE: RGB = (58, 64, 78)
PICKUP: RGB = (198, 231, 205)
DELIVERY: RGB = (250, 219, 190)
PARKING: RGB = (222, 226, 234)

#: aisle tint by `AisleState`, matching the GUI's overlay colours
AISLE_TINT: Dict[str, RGB] = {
    AisleState.FORWARD.value: (188, 217, 255),
    AisleState.REVERSE.value: (255, 214, 188),
    AisleState.DRAINING.value: (232, 208, 255),
}

#: robot fill by `RobotState`
ROBOT_TINT: Dict[str, RGB] = {
    RobotState.FREE.value: (127, 140, 155),
    RobotState.TO_PICKUP.value: (31, 119, 180),
    RobotState.TO_DELIVERY.value: (217, 95, 2),
    RobotState.PARKED.value: (160, 168, 180),
    RobotState.RECOVERY.value: (142, 68, 173),
}
STALLED: RGB = (192, 57, 43)
GOOD: RGB = (39, 139, 96)


def _font(size: int):
    """DejaVu at `size`, or Pillow's built-in bitmap font if it is missing."""
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/DejaVuSans.ttf",
    ]
    try:  # matplotlib ships DejaVu, and this package already optionally uses it
        import matplotlib

        candidates.insert(
            1, str(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf")
        )
    except Exception:  # pragma: no cover - matplotlib is optional
        pass
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:  # pragma: no cover
                continue
    return ImageFont.load_default()  # pragma: no cover


def _bold(size: int):
    from PIL import ImageFont

    path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    if Path(path).is_file():
        try:
            return ImageFont.truetype(path, size)
        except OSError:  # pragma: no cover
            pass
    return _font(size)


# --------------------------------------------------------------------------
# panels
# --------------------------------------------------------------------------


@dataclass
class Panel:
    """One planner's recorded run, plus how to label it."""

    title: str
    subtitle: str
    warehouse: Warehouse
    history: List[StepSnapshot]
    #: robot ids stalled at each recorded index, filled in by `_mark_stalls`
    stalled: List[frozenset] = field(default_factory=list)
    #: cumulative aisle direction commits up to each index, by `_count_flips`
    flips: List[int] = field(default_factory=list)

    @property
    def completed(self) -> int:
        return self.history[-1].completed_tasks if self.history else 0


def run_panel(
    map_path: str | Path,
    variant: str,
    title: str,
    subtitle: str,
    n_robots: int,
    timesteps: int,
    seed: int = 0,
    rate: float = 1.0,
    arrival: str = "poisson",
    overrides: Optional[Dict[str, object]] = None,
    stall_window: int = 15,
) -> Panel:
    """Run one configuration with history recording and wrap it in a `Panel`.

    Goes through `experiments.build_run`, the same entry point every measured
    table uses, so an animated panel and a row of the results table are the
    same run rather than two things that resemble each other.
    """
    from .experiments import build_run

    sim = build_run(
        map_path, variant, n_robots, timesteps, seed,
        rate=rate, arrival=arrival, overrides=overrides, record_history=True,
    )
    sim.run(max_timesteps=timesteps)
    panel = Panel(
        title=title, subtitle=subtitle, warehouse=sim.warehouse, history=sim.history
    )
    _mark_stalls(panel, stall_window)
    _count_flips(panel)
    return panel


def _mark_stalls(panel: Panel, window: int) -> None:
    """Flag robots whose position has not changed for `window` timesteps.

    Derived from the recorded positions rather than from the simulator, so it
    means the same thing for a PIBT variant and for an external baseline that
    keeps no such counter of its own.
    """
    history = panel.history
    panel.stalled = []
    for index, snapshot in enumerate(history):
        if index < window:
            panel.stalled.append(frozenset())
            continue
        past = history[index - window].positions
        panel.stalled.append(
            frozenset(
                rid for rid, pos in snapshot.positions.items()
                if past.get(rid) == pos
            )
        )


def _count_flips(panel: Panel) -> None:
    """Running count of aisle direction *reversals*, from the recorded states.

    A flip is an aisle committing the opposite of the direction it last
    committed, ignoring the OPEN and DRAINING states it passes through on the
    way -- the same event `AisleManager` counts as a `direction_switch`, and
    deliberately not the same as "committed a direction", since an aisle that
    releases and re-commits the *same* direction has not flipped. Reading it
    off the recorded states rather than from the simulator keeps the number
    defined for any planner and identical to what the frame shows.
    """
    total = 0
    committed: Dict[int, str] = {}
    panel.flips = []
    for snapshot in panel.history:
        for aisle_id, state in snapshot.aisle_states.items():
            if state not in (AisleState.FORWARD.value, AisleState.REVERSE.value):
                continue
            previous = committed.get(aisle_id)
            if previous is not None and previous != state:
                total += 1
            committed[aisle_id] = state
        panel.flips.append(total)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    cell: int
    pad: int
    header: int
    panel_header: int
    footer: int
    gutter: int

    def grid_size(self, warehouse: Warehouse) -> Tuple[int, int]:
        return warehouse.width * self.cell, warehouse.height * self.cell


DEFAULT_LAYOUT = Layout(cell=14, pad=14, header=46, panel_header=40, footer=26, gutter=18)


def _fit_layout(panels: Sequence[Panel], max_width: int) -> Layout:
    """Shrink the cell size until the whole strip fits in `max_width`."""
    base = DEFAULT_LAYOUT
    widest = max(p.warehouse.width for p in panels)
    for cell in range(base.cell, 5, -1):
        total = (
            2 * base.pad
            + len(panels) * widest * cell
            + (len(panels) - 1) * base.gutter
        )
        if total <= max_width:
            return Layout(cell, base.pad, base.header, base.panel_header,
                          base.footer, base.gutter)
    return Layout(6, base.pad, base.header, base.panel_header, base.footer, base.gutter)


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------


def _draw_static(warehouse: Warehouse, layout: Layout):
    """The parts of a panel that never change: floor, shelves, stations."""
    from PIL import Image, ImageDraw

    width, height = layout.grid_size(warehouse)
    image = Image.new("RGB", (width, height), OBSTACLE)
    draw = ImageDraw.Draw(image)
    cell = layout.cell

    for r in range(warehouse.height):
        for c in range(warehouse.width):
            if not warehouse.graph.contains((r, c)):
                continue
            box = (c * cell, r * cell, (c + 1) * cell - 1, (r + 1) * cell - 1)
            draw.rectangle(box, fill=FLOOR)
    for v in warehouse.pickup_vertices:
        _fill_cell(draw, v, cell, PICKUP)
    for v in warehouse.delivery_vertices:
        _fill_cell(draw, v, cell, DELIVERY)
    for v in warehouse.parking_vertices:
        _fill_cell(draw, v, cell, PARKING)
    return image


def _fill_cell(draw, vertex, cell: int, colour: RGB) -> None:
    r, c = vertex
    draw.rectangle(
        (c * cell, r * cell, (c + 1) * cell - 1, (r + 1) * cell - 1), fill=colour
    )


def _draw_aisles(draw, warehouse: Warehouse, states: Dict[int, str], cell: int) -> None:
    """Tint every committed aisle and point an arrow the way it is flowing."""
    for aisle_id, state in states.items():
        tint = AISLE_TINT.get(state)
        if tint is None:
            continue
        aisle = warehouse.aisles.get(aisle_id)
        if aisle is None:
            continue
        for vertex in aisle.vertices:
            _fill_cell(draw, vertex, cell, tint)
        if state == AisleState.DRAINING.value or len(aisle.vertices) < 3:
            continue
        first, last = aisle.vertices[0], aisle.vertices[-1]
        if state == AisleState.REVERSE.value:
            first, last = last, first
        _draw_arrow(draw, aisle.vertices[len(aisle.vertices) // 2], first, last, cell)


def _draw_arrow(draw, at, first, last, cell: int) -> None:
    """A small triangle at `at`, pointing from `first` towards `last`."""
    dr = (last[0] > first[0]) - (last[0] < first[0])
    dc = (last[1] > first[1]) - (last[1] < first[1])
    cx = at[1] * cell + cell / 2
    cy = at[0] * cell + cell / 2
    size = max(2.0, cell * 0.30)
    if dc:
        tip = (cx + dc * size, cy)
        wing = ((cx - dc * size * 0.7, cy - size * 0.8),
                (cx - dc * size * 0.7, cy + size * 0.8))
    else:
        tip = (cx, cy + dr * size)
        wing = ((cx - size * 0.8, cy - dr * size * 0.7),
                (cx + size * 0.8, cy - dr * size * 0.7))
    draw.polygon([tip, wing[0], wing[1]], fill=INK)


def _draw_robots(draw, snapshot: StepSnapshot, stalled: frozenset, cell: int) -> None:
    inset = max(1, cell // 6)
    for robot_id, (r, c) in snapshot.positions.items():
        x0, y0 = c * cell + inset, r * cell + inset
        x1, y1 = (c + 1) * cell - inset - 1, (r + 1) * cell - inset - 1
        fill = ROBOT_TINT.get(snapshot.states.get(robot_id, ""), ROBOT_TINT["FREE"])
        if robot_id in stalled:
            draw.ellipse((x0 - inset, y0 - inset, x1 + inset, y1 + inset),
                         outline=STALLED, width=max(1, cell // 8))
        draw.ellipse((x0, y0, x1, y1), fill=fill)


def _text_width(draw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def _wrap(draw, text: str, font, limit: int, lines: int = 2) -> List[str]:
    """Greedy word wrap to at most `lines` lines, the last one elided if long."""
    words = text.split()
    out: List[str] = []
    current = ""
    index = 0
    while index < len(words):
        trial = f"{current} {words[index]}".strip()
        if current and _text_width(draw, trial, font) > limit:
            out.append(current)
            current = ""
            if len(out) == lines - 1:
                break
        else:
            current = trial
            index += 1
    rest = " ".join(([current] if current else []) + words[index:]).strip()
    out.append(_elide(draw, rest, font, limit))
    return out[:lines]


def _elide(draw, text: str, font, limit: int) -> str:
    """Trim `text` to `limit` pixels. Two panels' subtitles must not collide."""
    if _text_width(draw, text, font) <= limit:
        return text
    while text and _text_width(draw, text + "...", font) > limit:
        text = text[:-1]
    return text.rstrip(" ,;") + "..."


def render_frame(
    panels: Sequence[Panel],
    index: int,
    layout: Layout,
    title: str,
    caption: str,
    statics: Sequence,
    best_completed: int,
    show_aisles: bool = True,
    caption_lines: int = 1,
):
    """One composed frame: header, every panel, footer."""
    from PIL import Image, ImageDraw

    cols = len(panels)
    widest = max(p.warehouse.width for p in panels)
    tallest = max(p.warehouse.height for p in panels)
    grid_w, grid_h = widest * layout.cell, tallest * layout.cell
    stats_h = 34
    width = 2 * layout.pad + cols * grid_w + (cols - 1) * layout.gutter
    height = (
        layout.header + layout.panel_header + grid_h + stats_h + layout.footer
    )

    frame = Image.new("RGB", (width, height), PAGE)
    draw = ImageDraw.Draw(frame)

    title_font, sub_font = _bold(15), _font(11)
    panel_font, stat_font = _bold(12), _font(11)
    big_font = _bold(17)

    clock_room = 60
    draw.text((layout.pad, 8),
              _elide(draw, title, title_font, width - 2 * layout.pad - clock_room),
              font=title_font, fill=INK)
    for line_no, line in enumerate(
        _wrap(draw, caption, sub_font, width - 2 * layout.pad, lines=caption_lines)
    ):
        draw.text((layout.pad, 26 + 13 * line_no), line, font=sub_font, fill=MUTED)

    #: the panel ahead *right now* is the one drawn in green, so the lead
    #: changing hands is visible rather than settled in advance by the ending
    leader = best_completed_at(panels, index)

    for column, panel in enumerate(panels):
        left = layout.pad + column * (grid_w + layout.gutter)
        top = layout.header
        snapshot = panel.history[min(index, len(panel.history) - 1)]
        stalled = panel.stalled[min(index, len(panel.stalled) - 1)]

        draw.text((left, top), panel.title, font=panel_font, fill=INK)
        draw.text((left, top + 15), _elide(draw, panel.subtitle, sub_font, grid_w),
                  font=sub_font, fill=MUTED)

        cell_image = statics[column].copy()
        cell_draw = ImageDraw.Draw(cell_image)
        _draw_aisles(cell_draw, panel.warehouse, snapshot.aisle_states, layout.cell)
        _draw_robots(cell_draw, snapshot, stalled, layout.cell)
        grid_top = top + layout.panel_header
        frame.paste(cell_image, (left, grid_top))
        draw.rectangle(
            (left, grid_top, left + cell_image.width - 1,
             grid_top + cell_image.height - 1),
            outline=RULE,
        )

        stats_top = grid_top + grid_h + 7
        done = snapshot.completed_tasks
        colour = GOOD if done and done >= leader else INK
        draw.text((left, stats_top), f"{done}", font=big_font, fill=colour)
        offset = _text_width(draw, f"{done}", big_font) + 5
        draw.text((left + offset, stats_top + 5), "delivered", font=stat_font,
                  fill=MUTED)
        flips = panel.flips[min(index, len(panel.flips) - 1)] if panel.flips else 0
        stall_text = f"{len(stalled)} stalled"
        if show_aisles:
            stall_text = f"{flips} direction flips   ·   {stall_text}"
        draw.text(
            (left + grid_w - _text_width(draw, stall_text, stat_font), stats_top + 5),
            stall_text, font=stat_font, fill=STALLED if stalled else MUTED,
        )

        bar_top = stats_top + 24
        draw.rectangle((left, bar_top, left + grid_w - 1, bar_top + 4), fill=RULE)
        if best_completed:
            filled = int(grid_w * done / best_completed)
            if filled:
                draw.rectangle((left, bar_top, left + filled - 1, bar_top + 4),
                               fill=colour)

    timestep = panels[0].history[min(index, len(panels[0].history) - 1)].timestep
    clock = f"t = {timestep}"
    draw.text((width - layout.pad - _text_width(draw, clock, panel_font), 10),
              clock, font=panel_font, fill=INK)

    _draw_legend(draw, layout, width, height, sub_font, show_aisles)
    return frame


#: legend entries: (swatch colour, label, "dot" | "box" | "ring")
_ROBOT_LEGEND = (
    (ROBOT_TINT[RobotState.TO_PICKUP.value], "to pickup", "dot"),
    (ROBOT_TINT[RobotState.TO_DELIVERY.value], "carrying", "dot"),
    (ROBOT_TINT[RobotState.FREE.value], "free", "dot"),
    (STALLED, "stalled 15+ steps", "ring"),
)
_AISLE_LEGEND = (
    (AISLE_TINT[AisleState.FORWARD.value], "aisle forward", "box"),
    (AISLE_TINT[AisleState.REVERSE.value], "aisle reverse", "box"),
    (AISLE_TINT[AisleState.DRAINING.value], "draining", "box"),
)


def _draw_legend(draw, layout: Layout, width: int, height: int, font,
                 show_aisles: bool) -> None:
    """A key along the footer, so no frame needs a caption to be read."""
    entries = list(_ROBOT_LEGEND) + (list(_AISLE_LEGEND) if show_aisles else [])
    y = height - layout.footer + 6
    x = layout.pad
    for colour, label, kind in entries:
        if kind == "box":
            draw.rectangle((x, y + 1, x + 9, y + 10), fill=colour, outline=RULE)
        elif kind == "ring":
            draw.ellipse((x, y + 1, x + 9, y + 10), outline=colour, width=2)
        else:
            draw.ellipse((x, y + 1, x + 9, y + 10), fill=colour)
        x += 13
        draw.text((x, y), label, font=font, fill=MUTED)
        x += _text_width(draw, label, font) + 14
    tag = "same map · same seed · same task stream"
    tag_x = width - layout.pad - _text_width(draw, tag, font)
    if tag_x > x:
        draw.text((tag_x, y), tag, font=font, fill=MUTED)


def best_completed_at(panels: Sequence[Panel], index: int) -> int:
    return max(
        p.history[min(index, len(p.history) - 1)].completed_tasks for p in panels
    )


# --------------------------------------------------------------------------
# the GIF
# --------------------------------------------------------------------------


def save_comparison(
    panels: Sequence[Panel],
    path: str | Path,
    title: str,
    caption: str,
    stride: int = 3,
    fps: int = 12,
    hold_last: int = 12,
    max_width: int = 1000,
    colours: int = 64,
    max_bytes: Optional[int] = 5 * 1024 * 1024,
) -> Path:
    """Write the comparison GIF, and refuse to write one that is too large.

    `stride` samples every nth recorded timestep; `hold_last` repeats the final
    frame so the ending -- usually the whole point -- stays on screen before
    the loop restarts. `max_bytes` is a budget, not a hint: exceeding it raises,
    because these files are committed to the repository.
    """
    from PIL import Image, ImageDraw

    if not panels or not all(p.history for p in panels):
        raise ValueError("every panel needs a recorded history")

    layout = _fit_layout(panels, max_width)
    statics = [_draw_static(p.warehouse, layout) for p in panels]
    frames_count = max(len(p.history) for p in panels)
    best = max(p.completed for p in panels)
    # only key the aisle colours when some aisle actually commits a direction:
    # on `warehouse_bottleneck` none ever does, and a legend for something the
    # picture never shows is worse than no legend
    show_aisles = any(
        state != AisleState.OPEN.value
        for panel in panels
        for snapshot in panel.history
        for state in snapshot.aisle_states.values()
    )

    # the caption may need a second line, and every frame must be the same
    # size, so settle its height once here rather than per frame
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    caption_width = (
        2 * layout.pad
        + len(panels) * max(p.warehouse.width for p in panels) * layout.cell
        + (len(panels) - 1) * layout.gutter
        - 2 * layout.pad
    )
    caption_lines = len(_wrap(probe, caption, _font(11), caption_width, lines=2))
    layout = Layout(layout.cell, layout.pad, layout.header + 13 * (caption_lines - 1),
                    layout.panel_header, layout.footer, layout.gutter)

    frames: List[Image.Image] = []
    for index in range(0, frames_count, max(1, stride)):
        frames.append(
            render_frame(panels, index, layout, title, caption, statics, best,
                         show_aisles=show_aisles, caption_lines=caption_lines)
        )
    frames.extend([frames[-1]] * max(0, hold_last))

    palette_source = frames[-1].quantize(colors=colours, method=Image.MEDIANCUT)
    quantised = [f.quantize(palette=palette_source, dither=Image.NONE) for f in frames]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    quantised[0].save(
        path,
        save_all=True,
        append_images=quantised[1:],
        duration=int(1000 / max(1, fps)),
        loop=0,
        optimize=True,
        disposal=2,
    )
    size = path.stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise RuntimeError(
            f"{path.name} is {size / 1e6:.1f} MB, over the {max_bytes / 1e6:.0f} MB "
            f"budget -- raise `stride`, lower `colours`, or shorten the run"
        )
    return path


__all__ = [
    "Panel",
    "Layout",
    "run_panel",
    "render_frame",
    "save_comparison",
]
