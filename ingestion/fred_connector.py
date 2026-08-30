"""
FRED API connector — fetches Treasury yield, FX rate, and policy rate series
from the Federal Reserve Economic Data (FRED) API and stores them as raw CSVs.
"""

import logging
import os
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Constants
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
DATA_DIR = Path("data/raw")
MAX_RETRIES = 3
BACKOFF_FACTOR = 2.0
REQUEST_TIMEOUT = 10

SERIES_IDS = [
    "DGS10",  # US 10-Year Treasury Yield
    "DGS2",  # US 2-Year Treasury Yield
    "DGS1MO",  # US 1-Month Treasury Yield
    "DEXUSEU",  # USD/EUR Exchange Rate
    "DEXUSJP",  # USD/JPY Exchange Rate
    "DEXUSBR",  # USD/BRL Exchange Rate
    "FEDFUNDS",  # Federal Funds Rate
]


# Public functions (used outside this module)
def fetch_series(
    series_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    api_key: str | None = None,
) -> pd.DataFrame:
    """Fetch one FRED series and return it as a [date, value, series_id] DataFrame."""
    key = api_key or os.getenv("FRED_API_KEY")
    if not key:
        raise ValueError("FRED_API_KEY is not set. Configure it in .env.")

    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "observation_start": start_date or os.getenv("START_DATE", "2015-01-01"),
        "observation_end": end_date or os.getenv("END_DATE", "9999-12-31"),
    }

    logger.info("Requesting series %s from FRED", series_id)
    response = _request_with_retry(FRED_BASE_URL, params)
    return _parse_observations(response.json(), series_id)


def fetch_all_series(series_ids: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Fetch multiple FRED series, logging and skipping any that fail after retries."""
    ids = series_ids or SERIES_IDS
    results: dict[str, pd.DataFrame] = {}
    for series_id in ids:
        try:
            results[series_id] = fetch_series(series_id)
        except (requests.RequestException, ValueError):
            logger.exception("Failed to fetch series %s", series_id)
    return results


def save_series(df: pd.DataFrame, series_id: str, output_dir: Path = DATA_DIR) -> Path:
    """Save a series DataFrame as CSV under output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{series_id}.csv"
    df.to_csv(output_path, index=False)
    logger.info("Saved series %s to %s (%d rows)", series_id, output_path, len(df))
    return output_path


# Private helpers (used only within this module)
def _request_with_retry(
    url: str,
    params: dict,
    max_retries: int = MAX_RETRIES,
    backoff_factor: float = BACKOFF_FACTOR,
) -> requests.Response:
    """GET a URL with exponential backoff retry on transient failures."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            _validate_response(response)
            return response
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            wait = backoff_factor ** (attempt - 1)
            logger.warning(
                "Attempt %d/%d failed for %s (%s). Retrying in %.1fs.",
                attempt,
                max_retries,
                params.get("series_id", url),
                exc,
                wait,
            )
            if attempt < max_retries:
                time.sleep(wait)

    logger.error(
        "All %d attempts failed for series %s", max_retries, params.get("series_id", url)
    )
    raise last_error


def _validate_response(response: requests.Response) -> None:
    """Raise ValueError if the FRED response body doesn't contain observations."""
    payload = response.json()
    if "observations" not in payload:
        raise ValueError(f"Unexpected FRED response: {payload}")


def _parse_observations(payload: dict, series_id: str) -> pd.DataFrame:
    """Convert a FRED observations payload into a clean DataFrame."""
    records = payload.get("observations", [])
    df = pd.DataFrame(records)[["date", "value"]]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df["series_id"] = series_id
    return df.dropna(subset=["value"]).reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for sid, series_df in fetch_all_series().items():
        save_series(series_df, sid)
