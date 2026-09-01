#!/usr/bin/env python3
"""Render the committed figures to PNG for the slide deck.

The deck may only use assets that already exist in the repository. The GIF it
embeds is read straight out of ``docs/gifs/``; the two figures need a raster,
and that is what this writes. Nothing here draws a new chart -- it imports
``tools/make_figures.py`` and calls the same builders that wrote the committed
SVGs, so the raster and the SVG are the same figure from the same data, and a
regenerated dataset moves both together.

PNG rather than the SVG itself for two reasons: PowerPoint wants a raster
fallback alongside an SVG picture part, and this sandbox has no rasteriser
that handles matplotlib's ``font:`` shorthand faithfully. Rendering from the
source figure sidesteps both.

Usage::

    python3 presentation/render_assets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent / "assets"

#: 300 dpi against figures a few inches wide gives one to three thousand
#: pixels -- comfortably above what a 13.3in slide can show, so the figure
#: stays crisp when PowerPoint scales it and when it is projected.
DPI = 300


def main() -> int:
    sys.path.insert(0, str(ROOT / "tools"))
    import make_figures as mf

    mf.style()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, build in mf.FIGURES.items():
        fig = build()
        path = OUT_DIR / f"{name}.png"
        # the same bbox and padding `make_figures.save()` uses, so the raster
        # is cropped identically to the committed SVG
        fig.savefig(path, dpi=DPI, bbox_inches="tight", pad_inches=0.16)
        import matplotlib.pyplot as plt

        plt.close(fig)
        print(f"  wrote {path.relative_to(ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
