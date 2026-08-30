#!/usr/bin/env python3
"""Zero-install entry point.

    python3 main.py                       # launch the GUI on a bundled map
    python3 main.py gui maps/warehouse_medium.map -n 40
    python3 main.py run maps/warehouse_small.map -n 10 -t 300
    python3 main.py inspect maps/warehouse_medium.map
    python3 main.py ablate maps/warehouse_corridors.map -n 35 --seeds 3

Works with a bare `python3` (3.10+) and nothing else installed -- the
simulator itself has zero dependencies. Equivalent to `python -m lda_pibt`
after `pip install -e .`, without the install step.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from lda_pibt.cli import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv:
        # No-arg default: open the GUI on a bundled map instead of
        # argparse's bare "command required" error.
        argv = ["gui", "maps/warehouse_medium.map"]
    sys.exit(main(argv))
