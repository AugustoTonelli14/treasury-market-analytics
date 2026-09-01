"""
Modeling layer — builds a dimensional star schema (fact_market_rates,
dim_date, dim_series, dim_geography) from the cleaned Parquet series in
data/processed/ and loads it into a DuckDB database.
"""

import logging
import os
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv

from ingestion import ecb_connector, fred_connector

load_dotenv()

logger = logging.getLogger(__name__)

# Constants
PROCESSED_DIR = Path("data/processed")

# series_id -> (series_name, category, currency, country, region, currency_code)
SERIES_CATALOG: dict[str, tuple[str, str, str, str, str, str]] = {
    "DGS10": ("US 10-Year Treasury Yield", "yield", "USD", "United States", "N. America", "USD"),
    "DGS2": ("US 2-Year Treasury Yield", "yield", "USD", "United States", "N. America", "USD"),
    "DGS1MO": ("US 1-Month Treasury Yield", "yield", "USD", "United States", "N. America", "USD"),
    "DEXUSEU": ("USD/EUR Exchange Rate", "fx_rate", "EUR", "Euro Area", "Europe", "EUR"),
    "DEXUSJP": ("USD/JPY Exchange Rate", "fx_rate", "JPY", "Japan", "Asia", "JPY"),
    "DEXUSBR": ("USD/BRL Exchange Rate", "fx_rate", "BRL", "Brazil", "S. America", "BRL"),
    "FEDFUNDS": ("Fed Funds Rate", "interest_rate", "USD", "United States", "N. America", "USD"),
    "EURIBOR_3M": ("Euribor 3-Month", "interest_rate", "EUR", "Euro Area", "Europe", "EUR"),
    "EURIBOR_6M": ("Euribor 6-Month", "interest_rate", "EUR", "Euro Area", "Europe", "EUR"),
    "EURIBOR_12M": ("Euribor 12-Month", "interest_rate", "EUR", "Euro Area", "Europe", "EUR"),
    "EURUSD_SPOT": ("EUR/USD Spot Rate", "fx_rate", "USD", "United States", "North America", "USD"),
    "ECB_DEPOSIT_RATE": (
        "ECB Deposit Facility Rate",
        "interest_rate",
        "EUR",
        "Euro Area",
        "Europe",
        "EUR",
    ),
}

FRED_SERIES_IDS = set(fred_connector.SERIES_IDS)
ECB_SERIES_IDS = set(ecb_connector.SERIES.keys())

# BIS series_ids are dynamically generated dimension codes (not enumerable
# ahead of time), so anything outside the catalog above falls back to these.
DEFAULT_GEOGRAPHY = ("Global", "Global", None)


# Public functions (used outside this module)
def load_processed_series(input_dir: Path = PROCESSED_DIR) -> dict[str, pd.DataFrame]:
    """Load every cleaned series Parquet found in input_dir, keyed by series_id."""
    series: dict[str, pd.DataFrame] = {}
    if not input_dir.exists():
        return series
    for path in sorted(input_dir.glob("*.parquet")):
        series[path.stem] = pd.read_parquet(path)
    return series


def build_dim_date(series: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build dim_date from the union of all dates present across series."""
    if series:
        all_dates = pd.concat([df["date"] for df in series.values()])
    else:
        all_dates = pd.Series([], dtype="datetime64[ns]")
    full_date = pd.Series(all_dates.unique()).sort_values().reset_index(drop=True)

    dim_date = pd.DataFrame({"full_date": full_date})
    dim_date["date_key"] = dim_date["full_date"].dt.strftime("%Y%m%d").astype("int64")
    dim_date["year"] = dim_date["full_date"].dt.year
    dim_date["quarter"] = dim_date["full_date"].dt.quarter
    dim_date["month"] = dim_date["full_date"].dt.month
    dim_date["week"] = dim_date["full_date"].dt.isocalendar().week.astype("int64")
    dim_date["is_weekend"] = dim_date["full_date"].dt.dayofweek >= 5
    dim_date["is_business_day"] = ~dim_date["is_weekend"]

    columns = ["date_key", "full_date", "year", "quarter", "month", "week"]
    columns += ["is_weekend", "is_business_day"]
    return dim_date[columns]


def build_dim_series(series_ids: list[str]) -> pd.DataFrame:
    """Build dim_series with catalog metadata, defaulting unknown series to source=bis."""
    return pd.DataFrame(_series_metadata(series_id) for series_id in sorted(series_ids))


def build_dim_geography(dim_series: pd.DataFrame) -> pd.DataFrame:
    """Build dim_geography from the distinct countries referenced by dim_series."""
    geography = dim_series[["geography_key", "country", "region", "currency_code"]]
    return geography.drop_duplicates().sort_values("geography_key").reset_index(drop=True)


def build_fact_market_rates(
    series: dict[str, pd.DataFrame], dim_series: pd.DataFrame
) -> pd.DataFrame:
    """Build fact_market_rates: one row per series per date, with derived changes."""
    columns = ["date_key", "series_key", "geography_key", "value"]
    columns += ["change_1d", "change_1w", "change_1m"]
    if not series:
        return pd.DataFrame(columns=columns)

    rows = []
    for df in series.values():
        enriched = df.sort_values("date").copy()
        # ~5/21 business days approximate 1 week / 1 month for daily series.
        enriched["change_1d"] = enriched["value"].diff(1)
        enriched["change_1w"] = enriched["value"].diff(5)
        enriched["change_1m"] = enriched["value"].diff(21)
        enriched["date_key"] = enriched["date"].dt.strftime("%Y%m%d").astype("int64")
        rows.append(enriched)

    fact = pd.concat(rows, ignore_index=True)
    series_lookup = dim_series.set_index("series_id")[["series_key", "geography_key"]]
    fact = fact.join(series_lookup, on="series_id")

    return fact[columns]


def build_star_schema(input_dir: Path = PROCESSED_DIR) -> dict[str, pd.DataFrame]:
    """Load processed series and assemble the full star schema."""
    series = load_processed_series(input_dir)
    dim_date = build_dim_date(series)
    dim_series = build_dim_series(list(series.keys()))
    dim_geography = build_dim_geography(dim_series)
    fact_market_rates = build_fact_market_rates(series, dim_series)

    return {
        "dim_date": dim_date,
        "dim_series": dim_series,
        "dim_geography": dim_geography,
        "fact_market_rates": fact_market_rates,
    }


def save_to_duckdb(tables: dict[str, pd.DataFrame], db_path: Path | None = None) -> Path:
    """Write each table into the DuckDB database, replacing any existing table."""
    path = db_path or Path(os.getenv("DUCKDB_PATH", "outputs/treasury.duckdb"))
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(path))
    try:
        for table_name, df in tables.items():
            conn.register("tmp_df", df)
            conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM tmp_df")
            conn.unregister("tmp_df")
            logger.info("Loaded table %s (%d rows) into %s", table_name, len(df), path)
    finally:
        conn.close()

    return path


# Private helpers (used only within this module)
def _series_metadata(series_id: str) -> dict:
    """Look up catalog metadata for a series_id, falling back to BIS defaults."""
    if series_id in SERIES_CATALOG:
        name, category, currency, country, region, currency_code = SERIES_CATALOG[series_id]
        source = "fred" if series_id in FRED_SERIES_IDS else "ecb"
    else:
        name, category, currency = series_id, "derivatives", None
        country, region, currency_code = DEFAULT_GEOGRAPHY
        source = "bis"

    return {
        "series_key": series_id,
        "series_id": series_id,
        "series_name": name,
        "category": category,
        "currency": currency,
        "source": source,
        "geography_key": country,
        "country": country,
        "region": region,
        "currency_code": currency_code,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    star_schema = build_star_schema()
    save_to_duckdb(star_schema)
