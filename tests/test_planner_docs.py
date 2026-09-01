"""The two planner documents must keep agreeing with the code and the data.

`docs/planner/planner.html` quotes default parameter values; `Params` is where
they actually live, and a tuned weight must not leave the document describing a
planner that no longer exists.

`docs/planner/comparison.html` quotes measured throughput; every cell carries a
`data-cell="variant/map"` attribute, so the whole matrix is re-derived here from
`docs/data/` and compared.  If this fails, fix the document -- the dataset is
the record.
"""

from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from lda_pibt.config import Params

ROOT = Path(__file__).resolve().parent.parent
PLANNER = ROOT / "docs" / "planner" / "planner.html"
COMPARISON = ROOT / "docs" / "planner" / "comparison.html"
DATA = ROOT / "docs" / "data"

#: (parameter, the string the document prints for it)
QUOTED_DEFAULTS = [
    ("alpha_progress", "10"),
    ("beta_strong", "3"),
    ("gamma_strong", "2"),
    ("gamma_weak", "0.5"),
    ("lambda_turn", "0.5"),
    ("lambda_reverse", "2"),
    ("mu_congestion", "1"),
    ("nu_wait", "0.2"),
    ("xi_bottleneck", "1"),
    ("zeta_counterflow", "8"),
    ("zeta_reservation", "8"),
    ("r_near", "2"),
    ("r_far", "8"),
    ("priority_emergency", "400"),
    ("priority_loaded", "300"),
    ("priority_pickup", "200"),
    ("priority_repositioning", "100"),
    ("priority_inside_aisle", "50"),
    ("waiting_weight", "5"),
    ("blocked_weight", "10"),
    ("w_urgency", "1"),
    ("w_waiting", "0.5"),
    ("w_proximity", "2"),
    ("w_route_length", "0.05"),
    ("w_congestion", "0.5"),
    ("minimum_aisle_lock_time", "20"),
    ("maximum_aisle_lock_time", "40"),
    ("direction_switch_threshold", "5"),
    ("max_drain_time", "30"),
    ("reservation_ttl", "15"),
    ("directional_aisle_min_length", "4"),
    ("t_blocked", "10"),
    ("aisle_capacity", "10"),
    ("downstream_horizon", "5"),
    ("local_congestion_radius", "3"),
    ("route_direction_penalty", "6"),
    ("assign_gamma_congestion", "12"),
    ("assign_waiting_cap", "60"),
]


@pytest.mark.parametrize("name,printed", QUOTED_DEFAULTS)
def test_the_document_quotes_the_real_default(name, printed):
    """Every number the document states is the number the planner runs on."""
    actual = getattr(Params(), name)
    assert float(printed) == float(actual), (
        f"docs/planner/planner.html says {name} = {printed}, "
        f"but config.py says {actual}"
    )


def test_the_document_mentions_every_quoted_value():
    text = PLANNER.read_text(encoding="utf-8")
    for name, printed in QUOTED_DEFAULTS:
        assert printed in text, f"{name}'s value {printed} is not in the document"


def test_the_fairness_horizon_is_stated_correctly():
    """80 steps is P_emergency / k_w, and the document leans on it."""
    p = Params()
    horizon = (p.priority_emergency - p.priority_free) / p.waiting_weight
    assert f"{horizon:.0f} steps of waiting" in PLANNER.read_text(encoding="utf-8")


# ------------------------------------------------------------- the matrix

def _cell(value: float) -> str:
    """How the comparison document prints tasks per 1000 timesteps."""
    scaled = value * 1000.0
    if scaled < 10:
        return f"{scaled:.1f}"
    return str(Decimal(scaled).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _measured() -> dict[tuple[str, str], float]:
    """throughput by (variant, map), from both suites of the dataset."""
    out: dict[tuple[str, str], float] = {}
    ablation = json.loads((DATA / "ablation.json").read_text())
    for map_name, block in ablation["maps"].items():
        for row in block["rows"]:
            out[(row["variant"], map_name)] = row["throughput"]
    baselines = json.loads((DATA / "baselines.json").read_text())
    for map_name, block in baselines["maps"].items():
        for row in block["rows"]:
            out[(row["variant"], map_name)] = row["fields"]["throughput"]["mean"]
    return out


def test_every_matrix_cell_matches_the_dataset():
    html = COMPARISON.read_text(encoding="utf-8")
    cells = re.findall(r'data-cell="([^/]+)/([^"]+)"[^>]*>([^<]+)<', html)
    assert len(cells) >= 16, "the comparison matrix lost cells"
    measured = _measured()
    for variant, map_name, printed in cells:
        key = (variant, map_name)
        assert key in measured, f"{variant} on {map_name} is not in docs/data/"
        assert printed.strip() == _cell(measured[key]), (
            f"{variant} on {map_name}: the matrix says {printed.strip()}, "
            f"the dataset says {_cell(measured[key])}"
        )


def test_the_soft_versus_hard_range_is_what_the_data_says():
    """The headline 1.9x-3.7x, on the maps where an aisle commits a direction."""
    measured = _measured()
    ratios = []
    for map_name in ("warehouse_corridors", "warehouse_narrow", "warehouse_medium"):
        soft = measured[("aisle_direction_only", map_name)]
        hard = measured[("aisle_direction_hard", map_name)]
        ratios.append(soft / hard)
    assert f"{min(ratios):.1f}× to {max(ratios):.1f}×" in COMPARISON.read_text(
        encoding="utf-8"
    ), f"the measured range is {min(ratios):.1f}x-{max(ratios):.1f}x"


def test_both_documents_are_self_contained():
    """No external fonts, scripts or images: these print offline, forever."""
    for path in (PLANNER, COMPARISON):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text, (
            f"{path.name} references an external resource"
        )
        assert "<script" not in text, f"{path.name} carries a script"
