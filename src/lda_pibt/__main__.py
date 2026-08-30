"""Entry point for ``python -m lda_pibt``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
