"""Deterministic attendance-calculation façade."""

from .answer import calculate_aggregation_chroma  # noqa: F401
from .attendance_schema import (  # noqa: F401
    GroupedCalculationResult,
    GroupedCalculationRow,
    PercentageCalculationResult,
    ScalarCalculationResult,
)
from .postgres_compiler import compile_aggregation_queries  # noqa: F401

__all__ = [name for name in globals() if not name.startswith("_")]
