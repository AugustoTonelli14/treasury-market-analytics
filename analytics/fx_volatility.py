"""
FX volatility analytics — reads daily returns and 30-day rolling volatility
built by the fx_analytics_mart dbt model, and derives a rolling z-score to
flag daily FX moves that are anomalous relative to each series' own recent
trailing window.
"""

import logging
import os
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ROLLING_WINDOW_DAYS = 30
Z_SCORE_OUTLIER_THRESHOLD = 3.0


# Public functions (used outside this module)
def load_fx_analytics(db_path: Path | None = None) -> pd.DataFrame:
    """Read spot rates, daily returns and rolling volatility from fx_analytics_mart."""
    path = db_path or Path(os.getenv("DUCKDB_PATH", "outputs/treasury.duckdb"))
    if not path.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {path}. Run `make model` (or the full "
            "`make ingest transform model` pipeline) to build it first."
        )
    conn = duckdb.connect(str(path), read_only=True)
    try:
        fx = conn.execute(
            """
            select fx_rate_id, rate_date, series_id, series_name, currency, source,
                   spot_rate, daily_return, rolling_volatility_30d
            from fx_analytics_mart
            order by series_id, rate_date
            """
        ).fetchdf()
    finally:
        conn.close()
    logger.info("Loaded %d FX analytics rows from %s", len(fx), path)
    return fx


def add_rolling_zscore(fx: pd.DataFrame) -> pd.DataFrame:
    """Add a per-series rolling mean of daily_return and the resulting z-score.

    z_score = (daily_return - rolling_mean_return_30d) / rolling_volatility_30d.
    Uses the same 30-observation trailing window (and warm-up requirement) as
    fx_analytics_mart's rolling_volatility_30d, so both are null/non-null
    together. Null wherever rolling_volatility_30d is null or zero — a
    missing or zero volatility means "not enough data / no dispersion yet",
    not "infinitely anomalous move".
    """
    result = fx.sort_values(["series_id", "rate_date"]).reset_index(drop=True).copy()
    result["rolling_mean_return_30d"] = result.groupby("series_id")["daily_return"].transform(
        lambda s: s.rolling(window=ROLLING_WINDOW_DAYS, min_periods=ROLLING_WINDOW_DAYS).mean()
    )
    volatility = result["rolling_volatility_30d"].where(result["rolling_volatility_30d"] != 0)
    result["z_score"] = (result["daily_return"] - result["rolling_mean_return_30d"]) / volatility
    return result


def flag_volatility_outliers(
    fx: pd.DataFrame, threshold: float = Z_SCORE_OUTLIER_THRESHOLD
) -> pd.DataFrame:
    """Return rows (already scored by add_rolling_zscore) whose |z_score| >= threshold."""
    outliers = fx[fx["z_score"].abs() >= threshold]
    return outliers.sort_values(["rate_date", "series_id"]).reset_index(drop=True)


def build_volatility_report(
    db_path: Path | None = None, threshold: float = Z_SCORE_OUTLIER_THRESHOLD
) -> pd.DataFrame:
    """Load FX analytics from DuckDB and return the days flagged as volatility outliers."""
    fx = load_fx_analytics(db_path)
    scored = add_rolling_zscore(fx)
    outliers = flag_volatility_outliers(scored, threshold)
    logger.info("Flagged %d FX volatility outlier day(s) (|z| >= %.1f)", len(outliers), threshold)
    return outliers
