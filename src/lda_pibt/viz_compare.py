"""Side-by-side animation of two planners on the *same* scenario.

`viz.save_animation` draws one run as dots on a grid. That is enough to watch a
run, and not enough to make an argument: the claims this project makes are all
comparative -- "Token Passing gridlocks where priority inheritance does not",
"a one-way rule as a hard constraint deadlocks where the same rule as a price
does not" -- and a comparison needs both runs on screen at once, driven from
the same seed, the same map and the same task stream.

That is what this module renders. Two (or more) `Panel`s, one frame per
sampled timestep, into an animated GIF that is meant to be *readable* rather
than merely correct:

* a **timeline** across the header, so a viewer always knows how far into the
  run a frame is;
* aisles tinted by state, with a flow arrow on every committed direction, so
  FORWARD -> DRAINING -> REVERSE is visible as it happens;
* robots coloured by what they are doing, and any robot that has not moved for
  `stall_window` steps drawn *solid red* -- the single most informative mark on
  the picture, because a screenful of red dots *is* gridlock -- with a
  ``GRIDLOCKED`` badge appearing over a panel once most of its robots are red;
* a shared **delivered-over-time chart** under both grids, revealed as the run
  plays, so the divergence between the planners is a shape rather than a pair
  of numbers that a viewer has to hold in their head;
* a **narration band** along the bottom carrying one plain sentence at a time,
  cued to the timestep, saying what is happening and why it happens.

Playback is deliberately slow, with a long hold on the first frame (time to
read the setup) and a longer one on the last (time to read the outcome).

Rendering is plain Pillow, deliberately. Flat colour fills quantise to a small
palette essentially for free, and consecutive frames differ only in the robots,
the chart head and the counters, so writing the GIF with ``disposal=1`` lets
Pillow store frame *deltas* -- which is what keeps a long, large, slow
animation inside a couple of megabytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .simulator import StepSnapshot
from .types import RobotState
from .warehouse import Warehouse

RGB = Tuple[int, int, int]

# --------------------------------------------------------------------------
# palette -- flat, few colours, readable when the GIF is quantised
#
# One rule holds the whole picture together: *red means stuck*. Nothing else
# on the frame is red, so a viewer who reads no text at all still sees which
# side is failing.
# --------------------------------------------------------------------------

PAGE: RGB = (250, 250, 252)
INK: RGB = (28, 32, 40)
MUTED: RGB = (110, 118, 132)
FAINT: RGB = (168, 175, 188)
RULE: RGB = (214, 219, 228)

FLOOR: RGB = (238, 241, 246)
OBSTACLE: RGB = (74, 82, 98)
PICKUP: RGB = (206, 234, 212)
DELIVERY: RGB = (250, 226, 202)
PARKING: RGB = (224, 228, 236)

#: retained so the legend keeps its shape; nothing tints an aisle now
AISLE_TINT: Dict[str, RGB] = {
}

#: robot fill by `RobotState` -- deliberately *not* using red for any of them
ROBOT_TINT: Dict[str, RGB] = {
    RobotState.FREE.value: (140, 150, 164),
    RobotState.TO_PICKUP.value: (31, 119, 180),
    RobotState.TO_DELIVERY.value: (0, 148, 136),
    RobotState.PARKED.value: (170, 178, 190),
    RobotState.RECOVERY.value: (142, 68, 173),
}
STALLED: RGB = (214, 45, 45)
STALLED_DARK: RGB = (150, 24, 24)
GOOD: RGB = (33, 132, 92)

#: one accent per panel column, for the delivered-over-time chart. Identity,
#: not merit: scenario 05 is one this project loses, and the colours must not
#: prejudge which curve a viewer should want to see on top.
PANEL_ACCENT: Tuple[RGB, ...] = ((60, 78, 140), (190, 106, 30), (120, 60, 140))

#: fraction of a panel's robots that must be stalled before it is called out
GRIDLOCK_FRACTION = 0.55


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

    candidates = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    try:
        import matplotlib

        candidates.append(
            str(Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans-Bold.ttf")
        )
    except Exception:  # pragma: no cover - matplotlib is optional
        pass
    for path in candidates:
        if Path(path).is_file():
            try:
                from PIL import ImageFont

                return ImageFont.truetype(path, size)
            except OSError:  # pragma: no cover
                continue
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

    @property
    def completed(self) -> int:
        return self.history[-1].completed_tasks if self.history else 0

    @property
    def n_robots(self) -> int:
        return len(self.history[0].positions) if self.history else 0


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


@dataclass(frozen=True)
class Beat:
    """One sentence of narration, shown from `timestep` until the next beat.

    The GIFs exist to explain a mechanism, and a mechanism is not explained by
    dots moving quickly. A beat is what turns the picture into an argument:
    it says, at the moment it is happening, what the viewer is looking at.
    """

    timestep: int
    text: str


def beat_at(beats: Sequence[Beat], timestep: int) -> Optional[Beat]:
    """The last beat whose timestep has been reached, or None before the first."""
    current = None
    for beat in beats:
        if beat.timestep <= timestep:
            current = beat
        else:
            break
    return current


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
    #: height of the shared delivered-over-time chart, 0 to omit it
    chart: int = 96
    #: height of the narration band, 0 to omit it
    narration: int = 38

    def grid_size(self, warehouse: Warehouse) -> Tuple[int, int]:
        return warehouse.width * self.cell, warehouse.height * self.cell

    def replace(self, **changes) -> "Layout":
        fields = dict(
            cell=self.cell, pad=self.pad, header=self.header,
            panel_header=self.panel_header, footer=self.footer,
            gutter=self.gutter, chart=self.chart, narration=self.narration,
        )
        fields.update(changes)
        return Layout(**fields)


#: `cell` here is the *target*; `_fit_layout` shrinks it until the strip fits.
#: 26px cells make a robot a ~20px dot -- roughly twice the diameter these
#: animations used to have, which is most of what made them hard to follow.
DEFAULT_LAYOUT = Layout(cell=26, pad=18, header=76, panel_header=46, footer=30,
                        gutter=22)

#: header height with no title/caption drawn in it -- just tall enough for
#: the timeline bar, which stays regardless: knowing how far into the run a
#: frame is is not narration, it is the one thing a still frame cannot show
TIMELINE_ONLY_HEADER = 30

#: panel-header height with no per-panel banner drawn -- a small gap between
#: the timeline and the grid, rather than the room a title and subtitle need
COMPACT_PANEL_HEADER = 8


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
            return base.replace(cell=cell)
    return base.replace(cell=6)


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


def _draw_arrow(draw, at, first, last, cell: int) -> None:
    """A small triangle at `at`, pointing from `first` towards `last`."""
    dr = (last[0] > first[0]) - (last[0] < first[0])
    dc = (last[1] > first[1]) - (last[1] < first[1])
    cx = at[1] * cell + cell / 2
    cy = at[0] * cell + cell / 2
    size = max(2.0, cell * 0.26)
    if dc:
        tip = (cx + dc * size, cy)
        wing = ((cx - dc * size * 0.7, cy - size * 0.8),
                (cx - dc * size * 0.7, cy + size * 0.8))
    else:
        tip = (cx, cy + dr * size)
        wing = ((cx - size * 0.8, cy - dr * size * 0.7),
                (cx + size * 0.8, cy - dr * size * 0.7))
    draw.polygon([tip, wing[0], wing[1]], fill=(96, 104, 118))


def _draw_robots(draw, snapshot: StepSnapshot, stalled: frozenset, cell: int) -> None:
    """Dots for robots. A stalled robot is filled red, not merely ringed.

    An outline on a nine-pixel dot is invisible at a glance; a solid red disc
    is not, and "how much red is on this side" is exactly the quantity a
    viewer should be reading off the frame.
    """
    inset = max(1, round(cell * 0.14))
    for robot_id, (r, c) in snapshot.positions.items():
        x0, y0 = c * cell + inset, r * cell + inset
        x1, y1 = (c + 1) * cell - inset - 1, (r + 1) * cell - inset - 1
        if robot_id in stalled:
            draw.ellipse((x0, y0, x1, y1), fill=STALLED, outline=STALLED_DARK,
                         width=max(1, cell // 12))
            continue
        fill = ROBOT_TINT.get(snapshot.states.get(robot_id, ""), ROBOT_TINT["FREE"])
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


def _badge(draw, x: int, y: int, text: str, font, fill: RGB,
           text_colour: RGB = (255, 255, 255)) -> int:
    """A filled pill of text. Returns its width."""
    width = _text_width(draw, text, font) + 14
    draw.rounded_rectangle((x, y, x + width, y + 19), radius=4, fill=fill)
    draw.text((x + 7, y + 3), text, font=font, fill=text_colour)
    return width


def _draw_timeline(draw, layout: Layout, width: int, y: int, index: int,
                   frames_count: int, timestep: int, horizon: int, font) -> None:
    """A progress bar for the run, so no frame is ambiguous about *when* it is."""
    label = f"t = {timestep} / {horizon}"
    label_w = _text_width(draw, label, font)
    left = layout.pad
    right = width - layout.pad - label_w - 12
    if right <= left:  # pragma: no cover - only on absurdly narrow strips
        return
    draw.rounded_rectangle((left, y, right, y + 7), radius=3, fill=RULE)
    fraction = (index + 1) / max(1, frames_count)
    filled = int((right - left) * min(1.0, fraction))
    if filled:
        draw.rounded_rectangle((left, y, left + filled, y + 7), radius=3, fill=INK)
    draw.text((right + 12, y - 3), label, font=font, fill=INK)


#: what the shared chart plots, by name: a label and a per-panel accessor
CHART_SERIES: Dict[str, Tuple[str, str]] = {
    "delivered": ("TASKS DELIVERED", "by the end"),
}


def _series_values(panel: Panel, series: str) -> List[int]:
    return [snapshot.completed_tasks for snapshot in panel.history]


def _dashed(draw, points: Sequence[Tuple[float, float]], fill: RGB, width: int,
            on: int = 7, off: int = 5) -> None:
    """Polyline in dashes, so a curve underneath another stays visible.

    Two planners often deliver at nearly the same rate for most of a run, and a
    solid line drawn over a solid line hides the fact that they agree -- which
    is information, not clutter.
    """
    carry = 0.0
    drawing = True
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        if length == 0:
            continue
        travelled = 0.0
        while travelled < length:
            span = (on if drawing else off) - carry
            step = min(span, length - travelled)
            t0, t1 = travelled / length, (travelled + step) / length
            if drawing:
                draw.line(
                    (x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0,
                     x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1),
                    fill=fill, width=width,
                )
            travelled += step
            carry += step
            if carry >= (on if drawing else off) - 1e-9:
                carry = 0.0
                drawing = not drawing


def _draw_chart(draw, panels: Sequence[Panel], index: int, x: int, y: int,
                width: int, height: int, frames_count: int, font, bold,
                series: str = "delivered") -> None:
    """One quantity against time, one line per panel, revealed as it plays.

    Two numbers side by side are a comparison a viewer has to do arithmetic on.
    Two curves are a comparison they have already made by the time they have
    finished looking: a line that flattens has stopped, and a line that keeps
    climbing has not.
    """
    top = y + 20
    bottom = y + height - 10
    plot_h = bottom - top
    heading, suffix = CHART_SERIES.get(series, CHART_SERIES["delivered"])
    values = [_series_values(panel, series) for panel in panels]
    ceiling = max(1, max(column[-1] for column in values))

    draw.text((x, y), heading, font=bold, fill=MUTED)

    # a scale marker ("N by the end") and a per-curve key both only earn
    # their place when there is more than one curve: with one, the axis has
    # nothing to be read against and the big "delivered" number above the
    # chart already says whose line this is -- so a single-panel chart is
    # left to speak for itself rather than spoiling its own ending
    if len(panels) > 1:
        ceiling_label = f"{ceiling} {suffix}"
        draw.text((x + width - _text_width(draw, ceiling_label, font), y),
                  ceiling_label, font=font, fill=FAINT)

        # an inline key on the title row, its swatch drawn in the same stroke
        # as the curve it names. Labelling each curve at its head reads well
        # only while the curves are apart, and these curves start on top of
        # each other -- which is exactly the part of the run a viewer is
        # watching.
        key_x = x + _text_width(draw, heading, bold) + 20
        key_limit = (
            x + width - _text_width(draw, ceiling_label, font) - 16 - key_x
        ) / max(1, len(panels))
        for column, panel in enumerate(panels):
            accent = PANEL_ACCENT[column % len(PANEL_ACCENT)]
            name = _elide(draw, panel.title, font, max(40, int(key_limit) - 30))
            swatch = [(key_x, y + 7), (key_x + 16, y + 7)]
            if column % 2:
                _dashed(draw, swatch, accent, 3, on=5, off=4)
            else:
                draw.line((*swatch[0], *swatch[1]), fill=accent, width=3)
            draw.text((key_x + 22, y), name, font=font, fill=accent)
            key_x += 22 + _text_width(draw, name, font) + 18

    draw.line((x, top, x + width, top), fill=(234, 237, 243))
    draw.line((x, bottom, x + width, bottom), fill=RULE)

    def point(i: int, value: int) -> Tuple[float, float]:
        return (
            x + width * (i / max(1, frames_count - 1)),
            bottom - plot_h * (value / ceiling),
        )

    for column, panel in enumerate(panels):
        accent = PANEL_ACCENT[column % len(PANEL_ACCENT)]
        last = min(index, len(panel.history) - 1)
        step = max(1, (last + 1) // 200)
        points = [point(i, values[column][i]) for i in range(0, last + 1, step)]
        points.append(point(last, values[column][last]))
        if len(points) > 1:
            if column % 2:
                _dashed(draw, points, accent, 3)
            else:
                draw.line(points, fill=accent, width=3, joint="curve")
        hx, hy = points[-1]
        draw.ellipse((hx - 4, hy - 4, hx + 4, hy + 4), fill=accent,
                     outline=PAGE, width=1)


def _draw_narration(draw, layout: Layout, width: int, y: int, text: str,
                    font, bold) -> None:
    """The band that says, in words, what the frame is showing right now."""
    draw.rounded_rectangle((layout.pad, y, width - layout.pad, y + layout.narration - 8),
                           radius=5, fill=(240, 242, 247))
    draw.rounded_rectangle((layout.pad, y, layout.pad + 3, y + layout.narration - 8),
                           radius=2, fill=INK)
    inner = layout.pad + 14
    limit = width - layout.pad - inner - 8
    draw.text((inner, y + 5), _elide(draw, text, bold, limit), font=bold, fill=INK)


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
    beats: Sequence[Beat] = (),
    frames_count: Optional[int] = None,
    horizon: Optional[int] = None,
    chart_series: str = "delivered",
    show_recovery: bool = False,
):
    """One composed frame: header, timeline, panels, chart, narration, legend."""
    from PIL import Image, ImageDraw

    cols = len(panels)
    widest = max(p.warehouse.width for p in panels)
    tallest = max(p.warehouse.height for p in panels)
    grid_w, grid_h = widest * layout.cell, tallest * layout.cell
    stats_h = 40
    width = 2 * layout.pad + cols * grid_w + (cols - 1) * layout.gutter
    strip_w = width - 2 * layout.pad
    height = (
        layout.header + layout.panel_header + grid_h + stats_h
        + layout.chart + layout.narration + layout.footer
    )
    frames_count = frames_count or max(len(p.history) for p in panels)
    horizon = horizon or max(p.history[-1].timestep for p in panels)

    frame = Image.new("RGB", (width, height), PAGE)
    draw = ImageDraw.Draw(frame)

    title_font, sub_font = _bold(18), _font(12)
    panel_font, stat_font = _bold(14), _font(12)
    big_font = _bold(26)
    tiny_font, tiny_bold = _font(11), _bold(11)
    beat_font = _bold(13)

    if title:
        draw.text((layout.pad, 8), _elide(draw, title, title_font, strip_w),
                  font=title_font, fill=INK)
    if caption:
        caption_top = 32
        for line_no, line in enumerate(
            _wrap(draw, caption, sub_font, strip_w, lines=caption_lines)
        ):
            draw.text((layout.pad, caption_top + 14 * line_no), line, font=sub_font,
                      fill=MUTED)

    timestep = panels[0].history[min(index, len(panels[0].history) - 1)].timestep
    _draw_timeline(draw, layout, width, layout.header - 15, index, frames_count,
                   timestep, horizon, tiny_bold)

    #: the panel ahead *right now* is the one drawn in green, so the lead
    #: changing hands is visible rather than settled in advance by the ending
    leader = best_completed_at(panels, index)

    for column, panel in enumerate(panels):
        accent = PANEL_ACCENT[column % len(PANEL_ACCENT)]
        left = layout.pad + column * (grid_w + layout.gutter)
        top = layout.header
        snapshot = panel.history[min(index, len(panel.history) - 1)]
        stalled = panel.stalled[min(index, len(panel.stalled) - 1)]

        # naming a panel only means something when there is another one to
        # tell it apart from -- with one panel the grid below is the whole
        # picture, and a banner repeating what the page around it already
        # says is text nobody needed
        if cols > 1:
            draw.rectangle((left, top + 2, left + 3, top + 32), fill=accent)
            draw.text((left + 11, top), panel.title, font=panel_font, fill=INK)
            if panel.subtitle:
                draw.text((left + 11, top + 18),
                          _elide(draw, panel.subtitle, sub_font, grid_w - 11),
                          font=sub_font, fill=MUTED)

        cell_image = statics[column].copy()
        cell_draw = ImageDraw.Draw(cell_image)
        _draw_robots(cell_draw, snapshot, stalled, layout.cell)
        grid_top = top + layout.panel_header
        frame.paste(cell_image, (left, grid_top))

        # a panel most of whose robots have stopped is the headline of the
        # frame it appears in, and it should not need a caption to be read:
        # frame it in red and say so in its heading, where the badge covers no
        # part of the picture it is describing
        total = panel.n_robots
        seized = bool(total) and len(stalled) >= GRIDLOCK_FRACTION * total
        if seized:
            for ring in range(3):
                draw.rectangle(
                    (left - ring, grid_top - ring,
                     left + cell_image.width - 1 + ring,
                     grid_top + cell_image.height - 1 + ring),
                    outline=STALLED,
                )
            _badge(draw, left + grid_w - _text_width(draw, "GRIDLOCKED", tiny_bold)
                   - 14, top + 4, "GRIDLOCKED", tiny_bold, STALLED)
        else:
            draw.rectangle(
                (left, grid_top, left + cell_image.width - 1,
                 grid_top + cell_image.height - 1),
                outline=RULE,
            )

        stats_top = grid_top + grid_h + 8
        done = snapshot.completed_tasks
        colour = GOOD if done and done >= leader else INK
        draw.text((left, stats_top), f"{done}", font=big_font, fill=colour)
        offset = _text_width(draw, f"{done}", big_font) + 6
        draw.text((left + offset, stats_top + 12), "delivered", font=stat_font,
                  fill=MUTED)
        if done and done >= leader:
            behind = min(
                p.history[min(index, len(p.history) - 1)].completed_tasks
                for p in panels
            )
            if behind and done >= behind * 1.15:
                _badge(draw, left + offset + _text_width(draw, "delivered", stat_font)
                       + 12, stats_top + 9, f"{done / behind:.1f}x ahead",
                       tiny_bold, GOOD)

        right_lines = [f"{len(stalled)} of {total} stalled"]
        for line_no, line in enumerate(right_lines):
            is_stall_line = line_no == len(right_lines) - 1
            draw.text(
                (left + grid_w - _text_width(draw, line, stat_font),
                 stats_top + 2 + 15 * line_no),
                line, font=stat_font,
                fill=STALLED if (is_stall_line and stalled) else MUTED,
            )

    chart_top = layout.header + layout.panel_header + grid_h + stats_h
    if layout.chart:
        _draw_chart(draw, panels, index, layout.pad, chart_top, strip_w,
                    layout.chart, frames_count, tiny_font, tiny_bold,
                    series=chart_series)

    if layout.narration:
        beat = beat_at(beats, timestep)
        _draw_narration(draw, layout, width, chart_top + layout.chart,
                        beat.text if beat else caption, tiny_font, beat_font)

    _draw_legend(draw, layout, width, height, tiny_font, show_aisles,
                 show_recovery=show_recovery, show_tag=cols > 1)
    return frame


#: legend entries: (swatch colour, label, "dot" | "box")
_ROBOT_LEGEND = (
    (ROBOT_TINT[RobotState.TO_PICKUP.value], "heading to a pickup", "dot"),
    (ROBOT_TINT[RobotState.TO_DELIVERY.value], "carrying a task", "dot"),
    (ROBOT_TINT[RobotState.FREE.value], "idle, no task yet", "dot"),
    (STALLED, "STUCK: no move in 15 steps", "dot"),
)
_RECOVERY_LEGEND = (
    (ROBOT_TINT[RobotState.RECOVERY.value], "in recovery", "dot"),
)
_AISLE_LEGEND = (
)

#: the strapline that ends the legend, and the whole argument for these GIFs
LEGEND_TAG = "same map · same seed · same task stream"


def legend_entries(show_aisles: bool, show_recovery: bool = False):
    """Only key what the animation actually shows.

    A legend for a colour that never appears is worse than no legend: it sends
    a viewer hunting the frame for something that is not there.
    """
    entries = list(_ROBOT_LEGEND)
    if show_recovery:
        entries += list(_RECOVERY_LEGEND)
    if show_aisles:
        entries += list(_AISLE_LEGEND)
    return entries


def _legend_rows(draw, entries, font, limit: int) -> List[list]:
    """Greedily pack the key into rows no wider than `limit`."""
    rows: List[list] = [[]]
    used = 0.0
    for entry in entries:
        cost = 15 + _text_width(draw, entry[1], font) + 16
        if rows[-1] and used + cost > limit:
            rows.append([])
            used = 0.0
        rows[-1].append(entry)
        used += cost
    return rows


def legend_row_count(entries, font, limit: int, show_tag: bool = True) -> int:
    """How many rows the key needs -- settled once, before any frame is drawn."""
    from PIL import Image, ImageDraw

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    tag = (15 + _text_width(probe, LEGEND_TAG, font) + 16) if show_tag else 0
    return len(_legend_rows(probe, entries, font, limit - tag))


def _draw_legend(draw, layout: Layout, width: int, height: int, font,
                 show_aisles: bool, show_recovery: bool = False,
                 show_tag: bool = True) -> None:
    """A key along the footer, so no frame needs a caption to be read.

    `show_tag` is the "same map · same seed · same task stream" strapline: it
    only makes an argument when there is a second panel for the frame to be
    claiming parity with, so a single-panel render leaves it off rather than
    asserting a comparison that is not on screen.
    """
    entries = legend_entries(show_aisles, show_recovery)
    limit = width - 2 * layout.pad
    tag_cost = (15 + _text_width(draw, LEGEND_TAG, font) + 16) if show_tag else 0
    rows = _legend_rows(draw, entries, font, limit - tag_cost)
    y = height - layout.footer + 8
    for row in rows:
        x = layout.pad
        for colour, label, kind in row:
            if kind == "box":
                draw.rectangle((x, y + 1, x + 10, y + 11), fill=colour, outline=RULE)
            else:
                draw.ellipse((x, y + 1, x + 10, y + 11), fill=colour)
            x += 15
            draw.text((x, y), label, font=font, fill=MUTED)
            x += _text_width(draw, label, font) + 16
        y += 16
    if show_tag:
        tag_x = width - layout.pad - _text_width(draw, LEGEND_TAG, font)
        draw.text((tag_x, height - layout.footer + 8 + 16 * (len(rows) - 1)),
                  LEGEND_TAG, font=font, fill=MUTED)


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
    fps: int = 10,
    hold_last: int = 1,
    max_width: int = 1180,
    colours: int = 96,
    max_bytes: Optional[int] = 5 * 1024 * 1024,
    beats: Sequence[Beat] = (),
    chart_series: str = "delivered",
    hold_first_ms: int = 2000,
    hold_last_ms: int = 3500,
) -> Path:
    """Write the comparison GIF, and refuse to write one that is too large.

    `stride` samples every nth recorded timestep and `fps` sets the pace of the
    middle of the animation; both default slower than is usual for a GIF,
    because the point of these files is that a first-time viewer can follow
    them. The first frame is held for `hold_first_ms` (long enough to read the
    setup and the legend) and the last for `hold_last_ms` (long enough to read
    the outcome) using GIF's per-frame delays, so the hold costs two frame
    delays rather than `hold_last` duplicated frames.

    `max_bytes` is a budget, not a hint: exceeding it raises, because these
    files are committed to the repository.
    """
    from PIL import Image, ImageDraw

    if not panels or not all(p.history for p in panels):
        raise ValueError("every panel needs a recorded history")

    layout = _fit_layout(panels, max_width)
    statics = [_draw_static(p.warehouse, layout) for p in panels]
    frames_count = max(len(p.history) for p in panels)
    horizon = max(p.history[-1].timestep for p in panels)
    best = max(p.completed for p in panels)
    # only key the aisle colours when some aisle actually commits a direction:
    # on `warehouse_bottleneck` none ever does, and a legend for something the
    # picture never shows is worse than no legend
    # The aisle-direction layer this used to key colours for was removed after
    # it measured -0.3% (p = 0.95), so there is never a committed direction to
    # show. The plumbing stays so the legend keeps its shape.
    show_aisles = False
    show_recovery = any(
        state == RobotState.RECOVERY.value
        for panel in panels
        for snapshot in panel.history
        for state in snapshot.states.values()
    )

    # the caption may need a second line, and every frame must be the same
    # size, so settle its height once here rather than per frame
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    strip_w = (
        len(panels) * max(p.warehouse.width for p in panels) * layout.cell
        + (len(panels) - 1) * layout.gutter
    )
    show_tag = len(panels) > 1
    caption_lines = (
        len(_wrap(probe, caption, _font(12), strip_w, lines=2)) if caption else 0
    )
    legend_rows = legend_row_count(
        legend_entries(show_aisles, show_recovery), _font(11), strip_w,
        show_tag=show_tag,
    )
    # a title/caption, a per-panel banner and a narration band each only earn
    # their height when there is something in them to draw -- a reserved but
    # empty band is blank space, not a clean frame, so the single-panel
    # scenario (no title, no caption, no per-panel banner, no beats) comes out
    # shorter rather than carrying the room for text nobody asked for
    header = (
        layout.header + 14 * max(0, caption_lines - 1) if (title or caption)
        else TIMELINE_ONLY_HEADER
    )
    panel_header = layout.panel_header if show_tag else COMPACT_PANEL_HEADER
    layout = layout.replace(
        header=header,
        panel_header=panel_header,
        footer=layout.footer + 16 * (legend_rows - 1),
        narration=layout.narration if beats else 0,
    )

    indices = list(range(0, frames_count, max(1, stride)))
    if indices[-1] != frames_count - 1:
        indices.append(frames_count - 1)
    frames: List[Image.Image] = [
        render_frame(panels, index, layout, title, caption, statics, best,
                     show_aisles=show_aisles, caption_lines=caption_lines,
                     beats=beats, frames_count=frames_count, horizon=horizon,
                     chart_series=chart_series, show_recovery=show_recovery)
        for index in indices
    ]
    frames.extend([frames[-1]] * max(0, hold_last))

    step_ms = int(1000 / max(1, fps))
    durations = [step_ms] * len(frames)
    durations[0] = max(step_ms, hold_first_ms)
    durations[-1] = max(step_ms, hold_last_ms)

    palette_source = frames[-1].quantize(colors=colours, method=Image.MEDIANCUT)
    quantised = [f.quantize(palette=palette_source, dither=Image.NONE) for f in frames]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    quantised[0].save(
        path,
        save_all=True,
        append_images=quantised[1:],
        duration=durations,
        loop=0,
        optimize=True,
        # `disposal=1` leaves each frame in place, which lets Pillow store only
        # the rectangle that changed. Most of this picture -- floor, shelves,
        # headings, legend -- never changes, so the saving is most of the file,
        # and it is what pays for the bigger cells and the slower stride.
        disposal=1,
    )
    size = path.stat().st_size
    if max_bytes is not None and size > max_bytes:
        raise RuntimeError(
            f"{path.name} is {size / 1e6:.1f} MB, over the {max_bytes / 1e6:.0f} MB "
            f"budget -- raise `stride`, lower `colours`, or shorten the run"
        )
    return path


__all__ = [
    "Beat",
    "legend_entries",
    "Panel",
    "Layout",
    "run_panel",
    "render_frame",
    "save_comparison",
    "beat_at",
]
