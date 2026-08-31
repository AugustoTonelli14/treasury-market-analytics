"""
Schema validation for ingested market data — checks column presence, dtypes,
null constraints, and chronological ordering on the [date, value, series_id]
shape shared by the FRED, ECB, and BIS connectors before data moves downstream.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

# Constants
REQUIRED_COLUMNS = {"date", "value", "series_id"}


# Public functions (used outside this module)
def validate_schema(df: pd.DataFrame, series_id: str) -> bool:
    """Validate one series DataFrame, raising ValueError on the first schema violation."""
    _check_required_columns(df, series_id)
    _check_column_types(df, series_id)
    _check_no_nulls(df, series_id)
    _check_chronological_order(df, series_id)
    logger.info("Schema validation passed for series %s (%d rows)", series_id, len(df))
    return True


def validate_all(series: dict[str, pd.DataFrame]) -> dict[str, bool]:
    """Validate a batch of series DataFrames, logging and marking failures as False."""
    results: dict[str, bool] = {}
    for series_id, df in series.items():
        try:
            results[series_id] = validate_schema(df, series_id)
        except ValueError:
            logger.exception("Schema validation failed for series %s", series_id)
            results[series_id] = False
    return results


# Private helpers (used only within this module)
def _check_required_columns(df: pd.DataFrame, series_id: str) -> None:
    """Raise ValueError if any of date/value/series_id is missing."""
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Series {series_id} missing required columns: {sorted(missing)}")


def _check_column_types(df: pd.DataFrame, series_id: str) -> None:
    """Raise ValueError if 'date' isn't datetime or 'value' isn't numeric."""
    if not pd.api.types.is_datetime64_any_dtype(df["date"]):
        raise ValueError(f"Series {series_id}: 'date' column is not datetime")
    if not pd.api.types.is_numeric_dtype(df["value"]):
        raise ValueError(f"Series {series_id}: 'value' column is not numeric")


def _check_no_nulls(df: pd.DataFrame, series_id: str) -> None:
    """Raise ValueError if 'date' or 'value' contains null entries."""
    if df[["date", "value"]].isnull().any().any():
        raise ValueError(f"Series {series_id}: null values found in date/value columns")


def _check_chronological_order(df: pd.DataFrame, series_id: str) -> None:
    """Raise ValueError if 'date' is not sorted in ascending order."""
    if not df["date"].is_monotonic_increasing:
        raise ValueError(f"Series {series_id}: dates are not sorted in ascending order")
