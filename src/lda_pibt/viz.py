"""Visualisation helpers: ASCII frames and an optional matplotlib animation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Tuple

from .types import AisleState

if TYPE_CHECKING:  # pragma: no cover
    from .simulator import Simulator

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def render_frame(sim: "Simulator", index: int) -> str:
    """ASCII picture of one recorded timestep."""
    if not sim.history:
        raise ValueError(
            "no history recorded; construct the Simulator with record_history=True"
        )
    snapshot = sim.history[index]
    canvas = [list(row) for row in sim.warehouse.grid]
    for robot_id, (r, c) in snapshot.positions.items():
        canvas[r][c] = _ALPHABET[robot_id % len(_ALPHABET)]
    header = (
        f"t={snapshot.timestep}  completed={snapshot.completed_tasks}  "
        f"directional aisles="
        f"{sum(1 for s in snapshot.aisle_states.values() if s != 'OPEN')}"
    )
    return header + "\n" + "\n".join("".join(row) for row in canvas)


def render_ascii_frames(sim: "Simulator", stride: int = 10) -> List[str]:
    return [
        render_frame(sim, i) for i in range(0, len(sim.history), max(1, stride))
    ]


_STATE_COLOURS: Dict[str, str] = {
    AisleState.OPEN.value: "#f2f2f2",
    AisleState.FORWARD.value: "#bcd9ff",
    AisleState.REVERSE.value: "#ffd6bc",
    AisleState.DRAINING.value: "#e8d0ff",
}


def save_animation(sim: "Simulator", path: str | Path, fps: int = 8) -> None:
    """Write an animation of the recorded run (needs matplotlib; gif or mp4)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.animation as animation
    import matplotlib.pyplot as plt
    import numpy as np

    if not sim.history:
        raise ValueError("no history recorded; use record_history=True")

    wh = sim.warehouse
    base = np.ones((wh.height, wh.width, 3))
    for r in range(wh.height):
        for c in range(wh.width):
            if not wh.graph.contains((r, c)):
                base[r, c] = (0.25, 0.25, 0.28)

    def hex_to_rgb(value: str) -> Tuple[float, float, float]:
        value = value.lstrip("#")
        return tuple(int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore

    fig, ax = plt.subplots(figsize=(wh.width / 4 + 2, wh.height / 4 + 2))
    ax.set_xticks([])
    ax.set_yticks([])
    image = ax.imshow(base, interpolation="nearest")
    scatter = ax.scatter([], [], s=40, c="#c0392b", zorder=3)
    title = ax.set_title("")

    def update(frame_index: int):
        snapshot = sim.history[frame_index]
        canvas = base.copy()
        for aisle_id, state in snapshot.aisle_states.items():
            colour = hex_to_rgb(_STATE_COLOURS.get(state, "#f2f2f2"))
            for r, c in wh.aisles[aisle_id].vertices:
                canvas[r, c] = colour
        image.set_data(canvas)
        coords = list(snapshot.positions.values())
        scatter.set_offsets([[c, r] for r, c in coords] or [[0, 0]])
        title.set_text(
            f"t={snapshot.timestep}   completed={snapshot.completed_tasks}"
        )
        return image, scatter, title

    anim = animation.FuncAnimation(
        fig, update, frames=len(sim.history), interval=1000 // max(1, fps), blit=False
    )
    path = Path(path)
    if path.suffix == ".gif":
        anim.save(path, writer=animation.PillowWriter(fps=fps))
    else:
        anim.save(path, fps=fps)
    plt.close(fig)


def plot_metrics(sim: "Simulator", path: str | Path) -> None:
    """Throughput and blocked-robot curves over the run."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    records = sim.metrics.records
    steps = [r.timestep for r in records]
    fig, axes = plt.subplots(3, 1, figsize=(9, 8), sharex=True)
    axes[0].plot(steps, [r.completed_tasks for r in records])
    axes[0].set_ylabel("completed tasks")
    axes[1].plot(steps, [r.moving_robots for r in records], label="moving")
    axes[1].plot(steps, [r.blocked_robots for r in records], label="blocked")
    axes[1].set_ylabel("robots")
    axes[1].legend()
    axes[2].plot(steps, [r.runtime_ms for r in records])
    axes[2].set_ylabel("ms / timestep")
    axes[2].set_xlabel("timestep")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


__all__ = ["render_frame", "render_ascii_frames", "save_animation", "plot_metrics"]
