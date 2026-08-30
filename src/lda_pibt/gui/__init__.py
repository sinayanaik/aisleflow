"""Browser-based GUI for the LDA-PIBT simulator (no external dependencies)."""

from .server import SimulationSession, serve

__all__ = ["SimulationSession", "serve"]
