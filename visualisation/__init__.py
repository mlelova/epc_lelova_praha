from .comparison import generate_comparison_dashboard
from .visualisation import DashboardError, generate_dashboard

__all__ = [
    "DashboardError",
    "generate_comparison_dashboard",
    "generate_dashboard",
]

# python -m visualisation.cli ./small_scale_tests/baseline_2009_network/baseline_2030_cy2009_solved.nc -o ./OUTPUT_HTML.html --default-zone DE00 --title Title
