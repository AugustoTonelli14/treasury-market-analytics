"""
BIS CSV loader — downloads public BIS statistics bulk CSV/ZIP files (FX turnover,
OTC derivatives) and reshapes them into long-format [date, value, series_id]
DataFrames matching the FRED/ECB connector output.
"""

import io
import logging
import os
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Constants
DATA_DIR = Path("data/raw")
MAX_RETRIES = 3
BACKOFF_FACTOR = 2.0
REQUEST_TIMEOUT = 30

# dataset_id -> download URL (BIS Data Portal bulk downloads, "columnar" CSV: one
# row per series, one column per time period)
DATASETS = {
    "BIS_OTC_DERIV": os.getenv(
        "BIS_OTC_DERIV_URL", "https://data.bis.org/static/bulk/WS_OTC_DERIV2_csv_col.zip"
    ),
    "BIS_FX_TURNOVER": os.getenv(
        "BIS_FX_TURNOVER_URL", "https://data.bis.org/static/bulk/WS_DER_OTC_TOV_csv_col.zip"
    ),
}

PERIOD_COLUMN_PATTERN = re.compile(
    r"^(19|20)\d{2}[-_]?(Q[1-4]|S[1-2]|H[1-2]|\d{2})?$", re.IGNORECASE
)


# Public functions (used outside this module)
def fetch_dataset(dataset_id: str, url: str | None = None) -> pd.DataFrame:
    """Download one BIS bulk dataset and return it as a long-format DataFrame."""
    target_url = url or DATASETS.get(dataset_id)
    if not target_url:
        raise ValueError(f"Unknown BIS dataset_id: {dataset_id}")

    logger.info("Requesting dataset %s from BIS", dataset_id)
    response = _request_with_retry(target_url, dataset_id)
    csv_text = _extract_csv(response.content)
    raw_df = pd.read_csv(io.StringIO(csv_text))
    return _reshape_to_long(raw_df, dataset_id)


def fetch_all_datasets(dataset_ids: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Fetch multiple BIS datasets, logging and skipping any that fail after retries."""
    ids = dataset_ids or list(DATASETS.keys())
    results: dict[str, pd.DataFrame] = {}
    for dataset_id in ids:
        try:
            results[dataset_id] = fetch_dataset(dataset_id)
        except (requests.RequestException, ValueError, zipfile.BadZipFile):
            logger.exception("Failed to fetch dataset %s", dataset_id)
    return results


def save_dataset(df: pd.DataFrame, dataset_id: str, output_dir: Path = DATA_DIR) -> Path:
    """Save a dataset DataFrame as CSV under output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{dataset_id}.csv"
    df.to_csv(output_path, index=False)
    logger.info("Saved dataset %s to %s (%d rows)", dataset_id, output_path, len(df))
    return output_path


# Private helpers (used only within this module)
def _request_with_retry(
    url: str,
    dataset_id: str,
    max_retries: int = MAX_RETRIES,
    backoff_factor: float = BACKOFF_FACTOR,
) -> requests.Response:
    """GET a URL with exponential backoff retry on transient failures."""
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
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
                dataset_id,
                exc,
                wait,
            )
            if attempt < max_retries:
                time.sleep(wait)

    logger.error("All %d attempts failed for dataset %s", max_retries, dataset_id)
    raise last_error


def _validate_response(response: requests.Response) -> None:
    """Raise ValueError if the BIS response body is empty or not a ZIP/CSV payload."""
    content = response.content
    if not content:
        raise ValueError("Empty response body from BIS")
    is_zip = content[:2] == b"PK"
    is_csv_text = b"," in content[:200] or b";" in content[:200]
    if not (is_zip or is_csv_text):
        raise ValueError("Unexpected BIS response: not a ZIP or CSV payload")


def _extract_csv(content: bytes) -> str:
    """Return raw CSV text, unzipping first if the payload is a ZIP archive."""
    if content[:2] != b"PK":
        return content.decode("utf-8-sig")

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError("No CSV file found inside BIS ZIP archive")
        return archive.read(csv_names[0]).decode("utf-8-sig")


def _reshape_to_long(df: pd.DataFrame, dataset_id: str) -> pd.DataFrame:
    """Melt a wide BIS CSV (one row per series, one column per period) into long format.

    The BIS "columnar" bulk export already carries a 'Series' column holding a
    colon-delimited code that uniquely identifies each series (all dimension
    codes joined), so it is used directly as series_id instead of being rebuilt
    from the individual dimension columns.
    """
    period_columns = [col for col in df.columns if PERIOD_COLUMN_PATTERN.match(str(col).strip())]
    if not period_columns:
        raise ValueError(f"No period-like columns found in BIS dataset {dataset_id}")
    if "Series" not in df.columns:
        raise ValueError(f"Expected a 'Series' identifier column in BIS dataset {dataset_id}")

    long_df = df.melt(
        id_vars=["Series"], value_vars=period_columns, var_name="period", value_name="value"
    ).rename(columns={"Series": "series_id"})
    long_df["date"] = long_df["period"].apply(_period_to_date)
    long_df["value"] = pd.to_numeric(long_df["value"], errors="coerce")
    return (
        long_df[["date", "value", "series_id"]]
        .dropna(subset=["date", "value"])
        .sort_values("date")
        .reset_index(drop=True)
    )


def _period_to_date(period: str) -> pd.Timestamp:
    """Convert a BIS period label (year, year-quarter, year-half) into a Timestamp."""
    label = str(period).strip().upper().replace("_", "-")
    if re.fullmatch(r"(19|20)\d{2}", label):
        return pd.Timestamp(f"{label}-01-01")
    if "Q" in label:
        year, quarter = re.split("-?Q", label)
        return pd.Period(f"{year}Q{quarter}", freq="Q").start_time
    if "S" in label or "H" in label:
        sep = "S" if "S" in label else "H"
        year, half = re.split(f"-?{sep}", label)
        month = 1 if half.strip() == "1" else 7
        return pd.Timestamp(f"{year}-{month:02d}-01")
    return pd.to_datetime(label, errors="coerce")


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for dsid, dataset_df in fetch_all_datasets().items():
        save_dataset(dataset_df, dsid)
