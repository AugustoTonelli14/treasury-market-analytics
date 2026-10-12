"""
Tests for the modeling layer — star schema construction (dim_date, dim_series,
dim_geography, fact_market_rates) and DuckDB loading. All I/O runs against
pytest's tmp_path, no real data/processed or outputs directories are touched.
"""

import duckdb
import pandas as pd

from ingestion import bis_loader
from modeling import model


# Test helpers
def _series_df(series_id: str, dates: list[str], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {"date": pd.to_datetime(dates), "value": values, "series_id": series_id}
    )


# --- load_processed_series ---
def test_load_processed_series_reads_all_parquets(tmp_path):
    _series_df("DGS10", ["2024-01-01"], [4.0]).to_parquet(tmp_path / "DGS10.parquet")
    _series_df("FEDFUNDS", ["2024-01-01"], [5.25]).to_parquet(tmp_path / "FEDFUNDS.parquet")

    series = model.load_processed_series(tmp_path)

    assert set(series.keys()) == {"DGS10", "FEDFUNDS"}


def test_load_processed_series_returns_empty_dict_for_missing_dir(tmp_path):
    assert model.load_processed_series(tmp_path / "does-not-exist") == {}


# --- build_dim_date ---
def test_build_dim_date_flags_weekend_and_business_day():
    series = {"DGS10": _series_df("DGS10", ["2024-01-06", "2024-01-08"], [4.0, 4.1])}

    dim_date = model.build_dim_date(series)

    saturday = dim_date[dim_date["full_date"] == "2024-01-06"].iloc[0]
    monday = dim_date[dim_date["full_date"] == "2024-01-08"].iloc[0]
    assert saturday["is_weekend"] and not saturday["is_business_day"]
    assert not monday["is_weekend"] and monday["is_business_day"]
    assert saturday["date_key"] == 20240106


def test_build_dim_date_dedupes_dates_across_series():
    series = {
        "A": _series_df("A", ["2024-01-01"], [1.0]),
        "B": _series_df("B", ["2024-01-01"], [2.0]),
    }

    dim_date = model.build_dim_date(series)

    assert len(dim_date) == 1


# --- build_dim_series ---
def test_build_dim_series_uses_catalog_metadata_for_known_series():
    dim_series = model.build_dim_series(["DGS10"])

    row = dim_series.iloc[0]
    assert row["source"] == "fred"
    assert row["category"] == "yield"
    assert row["geography_key"] == "United States"


def test_build_dim_series_falls_back_to_bis_defaults_for_unknown_series():
    dim_series = model.build_dim_series(["H.US.5J.A.5J"])

    row = dim_series.iloc[0]
    assert row["source"] == "bis"
    assert row["category"] == "derivatives"
    assert row["geography_key"] == "Global"


# --- build_dim_geography ---
def test_build_dim_geography_dedupes_by_country():
    dim_series = model.build_dim_series(["DGS10", "DGS2", "DEXBZUS"])

    dim_geography = model.build_dim_geography(dim_series)

    assert len(dim_geography) == 2  # United States, Brazil
    assert set(dim_geography["geography_key"]) == {"United States", "Brazil"}


def test_build_dim_geography_united_states_row_is_consistent_across_series():
    # DGS10 (FRED) and EURUSD_SPOT (ECB) both map to country="United States"
    # in SERIES_CATALOG. If their region strings ever drift apart again
    # ("N. America" vs "North America"), build_dim_geography's dedupe (which
    # compares whole rows) would silently emit two "United States" rows
    # instead of one, breaking the "1 row per country" grain.
    dim_series = model.build_dim_series(["DGS10", "EURUSD_SPOT"])

    dim_geography = model.build_dim_geography(dim_series)

    us_rows = dim_geography[dim_geography["geography_key"] == "United States"]
    assert len(us_rows) == 1


# --- build_fact_market_rates ---
def test_build_fact_market_rates_computes_changes_and_keys():
    series = {"DGS10": _series_df("DGS10", ["2024-01-01", "2024-01-02"], [4.0, 4.5])}
    dim_series = model.build_dim_series(["DGS10"])

    fact = model.build_fact_market_rates(series, dim_series)

    assert fact.loc[fact["date_key"] == 20240102, "change_1d"].item() == 0.5
    assert fact.loc[fact["date_key"] == 20240101, "series_key"].item() == "DGS10"
    assert fact.loc[fact["date_key"] == 20240101, "geography_key"].item() == "United States"


def test_build_fact_market_rates_empty_series_returns_empty_frame():
    fact = model.build_fact_market_rates({}, model.build_dim_series([]))

    assert fact.empty
    assert list(fact.columns) == [
        "date_key",
        "series_key",
        "geography_key",
        "value",
        "change_1d",
        "change_1w",
        "change_1m",
    ]


# --- build_fact_derivatives ---
def test_build_fact_derivatives_preserves_distinct_series_sharing_the_same_date():
    # Mirrors the real BIS shape: many distinct series_id values in one
    # dataset, sharing the same observation dates.
    bis_df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-07-01"]),
            "value": [100.0, 200.0, 150.0],
            "series_id": ["A:1:USD", "B:2:EUR", "A:1:USD"],
        }
    )

    fact = model.build_fact_derivatives({"BIS_OTC_DERIV": bis_df})

    assert len(fact) == 3
    assert set(fact["series_id"]) == {"A:1:USD", "B:2:EUR"}
    assert (fact["dataset"] == "BIS_OTC_DERIV").all()
    assert fact.loc[fact["series_id"] == "A:1:USD", "date_key"].tolist() == [20240101, 20240701]


def test_build_fact_derivatives_empty_dict_returns_empty_frame():
    fact = model.build_fact_derivatives({})

    assert fact.empty
    assert list(fact.columns) == ["date_key", "dataset", "series_id", "value"]


# --- build_star_schema ---
def test_build_star_schema_assembles_all_five_tables(tmp_path):
    _series_df("DGS10", ["2024-01-01"], [4.0]).to_parquet(tmp_path / "DGS10.parquet")

    tables = model.build_star_schema(tmp_path)

    assert set(tables.keys()) == {
        "dim_date",
        "dim_series",
        "dim_geography",
        "fact_market_rates",
        "fact_derivatives",
    }
    assert len(tables["fact_market_rates"]) == 1
    assert tables["fact_derivatives"].empty


def test_build_star_schema_routes_bis_datasets_to_fact_derivatives_with_zero_null_keys(tmp_path):
    """End-to-end guard for the bug this fix addresses: BIS datasets (many
    series_id values per file, sharing dates with regular market-rate
    series) must never reach fact_market_rates/dim_series, and neither fact
    table may come out with null foreign keys."""
    bis_dataset_id = next(iter(bis_loader.DATASETS))
    _series_df("DGS10", ["2024-01-01", "2024-01-02"], [4.0, 4.1]).to_parquet(
        tmp_path / "DGS10.parquet"
    )
    pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "value": [1000.0, 2000.0],
            "series_id": ["A:1:USD", "B:2:EUR"],
        }
    ).to_parquet(tmp_path / f"{bis_dataset_id}.parquet")

    tables = model.build_star_schema(tmp_path)

    assert set(tables["dim_series"]["series_id"]) == {"DGS10"}
    assert len(tables["fact_market_rates"]) == 2
    assert not tables["fact_market_rates"][["series_key", "geography_key"]].isnull().any().any()

    fact_derivatives = tables["fact_derivatives"]
    assert len(fact_derivatives) == 2
    assert set(fact_derivatives["series_id"]) == {"A:1:USD", "B:2:EUR"}
    assert not fact_derivatives.isnull().any().any()

    # fact_derivatives' date_key must resolve against the shared dim_date.
    assert set(fact_derivatives["date_key"]).issubset(set(tables["dim_date"]["date_key"]))


# --- save_to_duckdb ---
def test_save_to_duckdb_writes_queryable_tables(tmp_path):
    db_path = tmp_path / "test.duckdb"
    tables = {"dim_series": model.build_dim_series(["DGS10"])}

    result_path = model.save_to_duckdb(tables, db_path=db_path)

    assert result_path == db_path
    conn = duckdb.connect(str(db_path))
    row_count = conn.execute("SELECT COUNT(*) FROM dim_series").fetchone()[0]
    conn.close()
    assert row_count == 1
