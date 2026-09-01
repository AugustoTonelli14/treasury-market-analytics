"""
Tests for the transformation layer — loading, cleaning, and Parquet output of
raw ingestion CSVs. All I/O runs against pytest's tmp_path, no real data
directories are touched.
"""

import pandas as pd
import pytest

from transformation import transform


# Test helpers
def _write_raw_csv(input_dir, series_id: str, rows: str) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)
    (input_dir / f"{series_id}.csv").write_text(f"date,value,series_id\n{rows}")


# --- load_raw_series ---
def test_load_raw_series_parses_date_and_value(tmp_path):
    _write_raw_csv(tmp_path, "TEST", "2024-01-01,1.5,TEST\n2024-01-02,2.5,TEST\n")

    df = transform.load_raw_series("TEST", input_dir=tmp_path)

    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert pd.api.types.is_numeric_dtype(df["value"])
    assert len(df) == 2


# --- clean_series ---
def test_clean_series_drops_duplicates_keeping_last():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-01-02"]),
            "value": [1.0, 9.0, 2.0],
            "series_id": "TEST",
        }
    )

    cleaned = transform.clean_series(df, "TEST")

    assert len(cleaned) == 2
    assert cleaned.loc[cleaned["date"] == "2024-01-01", "value"].item() == 9.0


def test_clean_series_sorts_chronologically():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-02"]),
            "value": [3.0, 1.0, 2.0],
            "series_id": "TEST",
        }
    )

    cleaned = transform.clean_series(df, "TEST")

    assert cleaned["date"].is_monotonic_increasing
    assert cleaned["value"].tolist() == [1.0, 2.0, 3.0]


def test_clean_series_drops_null_values():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02", None]),
            "value": [1.0, None, 3.0],
            "series_id": "TEST",
        }
    )

    cleaned = transform.clean_series(df, "TEST")

    assert len(cleaned) == 1
    assert cleaned["value"].iloc[0] == 1.0


def test_clean_series_raises_on_unsortable_result_columns():
    df = pd.DataFrame({"date": pd.to_datetime(["2024-01-01"]), "value": ["not-numeric"]})

    with pytest.raises((ValueError, KeyError)):
        transform.clean_series(df, "TEST")


# --- transform_all ---
def test_transform_all_skips_series_that_fail(tmp_path):
    _write_raw_csv(tmp_path, "GOOD", "2024-01-01,1.0,GOOD\n")
    (tmp_path / "BAD.csv").write_text("date,value,series_id\nnot-a-date,1.0,BAD\n")

    results = transform.transform_all(input_dir=tmp_path)

    assert "GOOD" in results
    assert "BAD" not in results


def test_transform_all_returns_empty_dict_for_missing_dir(tmp_path):
    missing_dir = tmp_path / "does-not-exist"

    results = transform.transform_all(input_dir=missing_dir)

    assert results == {}


# --- save_processed ---
def test_save_processed_writes_parquet_roundtrip(tmp_path):
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "value": [1.0, 2.0],
            "series_id": "TEST",
        }
    )

    output_path = transform.save_processed(df, "TEST", output_dir=tmp_path)

    assert output_path.exists()
    roundtrip = pd.read_parquet(output_path)
    assert roundtrip["value"].tolist() == [1.0, 2.0]
