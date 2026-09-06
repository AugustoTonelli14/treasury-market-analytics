"""
Yield curve analytics — reads the 2Y-10Y spread and inversion flag built by
the yield_curve_mart dbt model and groups contiguous inverted observations
into inversion periods (start, end, duration, minimum spread reached).
"""

import logging
import os
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Constants
DUCKDB_PATH = Path(os.getenv("DUCKDB_PATH", "outputs/treasury.duckdb"))

INVERSION_COLUMNS = [
    "start_date",
    "end_date",
    "duration_days",
    "trading_days",
    "min_spread_2y_10y",
]


# Public functions (used outside this module)
def load_yield_curve(db_path: Path = DUCKDB_PATH) -> pd.DataFrame:
    """Read rate_date, yields, spread_2y_10y and is_inverted from yield_curve_mart."""
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        yield_curve = conn.execute(
            """
            select rate_date, yield_1m, yield_2y, yield_10y, spread_2y_10y, is_inverted
            from yield_curve_mart
            order by rate_date
            """
        ).fetchdf()
    finally:
        conn.close()
    logger.info("Loaded %d yield curve observations from %s", len(yield_curve), db_path)
    return yield_curve


def detect_inversions(yield_curve: pd.DataFrame) -> pd.DataFrame:
    """Group contiguous is_inverted rows into inversion periods with start/end/duration.

    Contiguity is measured by position in the full (business-day) date grain
    passed in, not by calendar-day gaps, so weekends/holidays between two
    trading days don't split a single inversion into separate periods.
    """
    ordered = yield_curve.sort_values("rate_date").reset_index(drop=True)
    is_inverted = ordered["is_inverted"].astype("boolean").fillna(False)
    inverted = ordered[is_inverted]
    if inverted.empty:
        return pd.DataFrame(columns=INVERSION_COLUMNS)

    period_break = inverted.index.to_series().diff().fillna(2) != 1
    period_id = period_break.cumsum()

    periods = (
        inverted.groupby(period_id)
        .agg(
            start_date=("rate_date", "min"),
            end_date=("rate_date", "max"),
            trading_days=("rate_date", "count"),
            min_spread_2y_10y=("spread_2y_10y", "min"),
        )
        .reset_index(drop=True)
    )
    periods["duration_days"] = (periods["end_date"] - periods["start_date"]).dt.days + 1
    return periods[INVERSION_COLUMNS]


def build_inversion_report(db_path: Path = DUCKDB_PATH) -> pd.DataFrame:
    """Load the yield curve from DuckDB and return its detected inversion periods."""
    yield_curve = load_yield_curve(db_path)
    inversions = detect_inversions(yield_curve)
    logger.info("Detected %d yield curve inversion period(s)", len(inversions))
    return inversions
