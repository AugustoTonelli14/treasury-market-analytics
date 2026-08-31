"""
ECB API connector — fetches Euribor, EUR/USD spot rate, and deposit facility rate
series from the ECB Statistical Data Warehouse (SDW) API and stores them as raw CSVs.
"""

import io
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
ECB_BASE_URL = "https://data-api.ecb.europa.eu/service/data"
DATA_DIR = Path("data/raw")
MAX_RETRIES = 3
BACKOFF_FACTOR = 2.0
REQUEST_TIMEOUT = 10

# series_id -> (flow_ref, series_key)
SERIES = {
    "EURIBOR_3M": ("FM", "M.U2.EUR.RT.MM.EURIBOR3MD_.HSTA"),  # 3-month Euribor
    "EURIBOR_6M": ("FM", "M.U2.EUR.RT.MM.EURIBOR6MD_.HSTA"),  # 6-month Euribor
    "EURIBOR_12M": ("FM", "M.U2.EUR.RT.MM.EURIBOR1YD_.HSTA"),  # 12-month Euribor
    "EURUSD_SPOT": ("EXR", "D.USD.EUR.SP00.A"),  # EUR/USD spot reference rate
    "ECB_DEPOSIT_RATE": ("FM", "D.U2.EUR.4F.KR.DFR.LEV"),  # ECB deposit facility rate
}


# Public functions (used outside this module)
def fetch_series(
    series_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Fetch one ECB SDW series and return it as a [date, value, series_id] DataFrame."""
    if series_id not in SERIES:
        raise ValueError(f"Unknown ECB series_id: {series_id}")
    flow_ref, series_key = SERIES[series_id]

    params = {
        "startPeriod": start_date or os.getenv("START_DATE", "2015-01-01"),
        "endPeriod": end_date or os.getenv("END_DATE", ""),
        "format": "csvdata",
    }
    url = f"{ECB_BASE_URL}/{flow_ref}/{series_key}"

    logger.info("Requesting series %s from ECB SDW", series_id)
    response = _request_with_retry(url, params, series_id)
    return _parse_observations(response.text, series_id)


def fetch_all_series(series_ids: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Fetch multiple ECB series, logging and skipping any that fail after retries."""
    ids = series_ids or list(SERIES.keys())
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
    series_id: str,
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
                series_id,
                exc,
                wait,
            )
            if attempt < max_retries:
                time.sleep(wait)

    logger.error("All %d attempts failed for series %s", max_retries, series_id)
    raise last_error


def _validate_response(response: requests.Response) -> None:
    """Raise ValueError if the ECB response body isn't valid observation CSV."""
    if not response.text.strip() or "OBS_VALUE" not in response.text:
        raise ValueError(f"Unexpected ECB response: {response.text[:200]}")


def _parse_observations(csv_text: str, series_id: str) -> pd.DataFrame:
    """Convert an ECB SDW CSV payload into a clean [date, value, series_id] DataFrame."""
    raw = pd.read_csv(io.StringIO(csv_text))
    df = raw[["TIME_PERIOD", "OBS_VALUE"]].rename(
        columns={"TIME_PERIOD": "date", "OBS_VALUE": "value"}
    )
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
