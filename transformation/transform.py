"""
Transformation layer — loads raw CSVs produced by the ingestion connectors,
cleans and type-casts them, and writes the result as Parquet files ready for
DuckDB modeling.
"""

import logging
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from ingestion.validators import validate_schema

load_dotenv()

logger = logging.getLogger(__name__)

# Constants
RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")


# Public functions (used outside this module)
def load_raw_series(series_id: str, input_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Load one raw series CSV and parse its date/value columns."""
    input_path = input_dir / f"{series_id}.csv"
    df = pd.read_csv(input_path)
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df


def clean_series(df: pd.DataFrame, series_id: str) -> pd.DataFrame:
    """Clean one series: drop null values, dedupe by date, sort, cast dtypes."""
    cleaned = df.dropna(subset=["date", "value"]).copy()
    cleaned = cleaned.drop_duplicates(subset=["date"], keep="last")
    cleaned = cleaned.sort_values("date").reset_index(drop=True)
    cleaned["date"] = cleaned["date"].astype("datetime64[ns]")
    cleaned["value"] = cleaned["value"].astype("float64")
    cleaned["series_id"] = series_id

    validate_schema(cleaned, series_id)
    return cleaned[["date", "value", "series_id"]]


def transform_all(input_dir: Path = RAW_DIR) -> dict[str, pd.DataFrame]:
    """Load and clean every raw series CSV found in input_dir, skipping failures."""
    results: dict[str, pd.DataFrame] = {}
    for series_id in _discover_series_ids(input_dir):
        try:
            raw = load_raw_series(series_id, input_dir)
            results[series_id] = clean_series(raw, series_id)
        except (ValueError, OSError):
            logger.exception("Failed to transform series %s", series_id)
    return results


def save_processed(df: pd.DataFrame, series_id: str, output_dir: Path = PROCESSED_DIR) -> Path:
    """Save a cleaned series DataFrame as Parquet under output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{series_id}.parquet"
    df.to_parquet(output_path, index=False)
    logger.info("Saved series %s to %s (%d rows)", series_id, output_path, len(df))
    return output_path


# Private helpers (used only within this module)
def _discover_series_ids(input_dir: Path) -> list[str]:
    """Return the series_ids for all CSV files found in input_dir."""
    if not input_dir.exists():
        return []
    return sorted(path.stem for path in input_dir.glob("*.csv"))


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for sid, series_df in transform_all().items():
        save_processed(series_df, sid)
