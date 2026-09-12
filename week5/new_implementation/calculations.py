"""Deterministic attendance-calculation façade."""

from .answer import calculate_aggregation_chroma, calculate_aggregation_postgres  # noqa: F401
from .attendance_schema import (  # noqa: F401
    GroupedCalculationResult,
    GroupedCalculationRow,
    PercentageCalculationResult,
    ScalarCalculationResult,
)

__all__ = [name for name in globals() if not name.startswith("_")]
