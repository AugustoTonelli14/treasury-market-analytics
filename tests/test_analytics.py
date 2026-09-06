"""
Tests for the yield curve analytics module — inversion period detection and
loading yield_curve_mart from DuckDB. All I/O runs against pytest's tmp_path,
no real outputs/treasury.duckdb is touched.
"""

import duckdb
import pandas as pd

from analytics import yield_curve


# Test helpers
def _yield_curve_df(rows: list[tuple[str, float, bool | None]]) -> pd.DataFrame:
    dates, spreads, inverted = zip(*rows, strict=True)
    return pd.DataFrame(
        {
            "rate_date": pd.to_datetime(dates),
            "yield_1m": 5.0,
            "yield_2y": 4.0,
            "yield_10y": [None if s is None else 4.0 + s for s in spreads],
            "spread_2y_10y": spreads,
            "is_inverted": inverted,
        }
    )


# --- detect_inversions ---
def test_detect_inversions_returns_empty_when_none_inverted():
    df = _yield_curve_df(
        [
            ("2024-01-01", 0.5, False),
            ("2024-01-02", 0.4, False),
        ]
    )

    result = yield_curve.detect_inversions(df)

    assert result.empty
    assert list(result.columns) == yield_curve.INVERSION_COLUMNS


def test_detect_inversions_groups_single_contiguous_period():
    df = _yield_curve_df(
        [
            ("2024-01-01", -0.1, True),
            ("2024-01-02", -0.3, True),
            ("2024-01-03", -0.2, True),
        ]
    )

    result = yield_curve.detect_inversions(df)

    assert len(result) == 1
    period = result.iloc[0]
    assert period["start_date"] == pd.Timestamp("2024-01-01")
    assert period["end_date"] == pd.Timestamp("2024-01-03")
    assert period["duration_days"] == 3
    assert period["trading_days"] == 3
    assert period["min_spread_2y_10y"] == -0.3


def test_detect_inversions_splits_on_non_inverted_gap():
    df = _yield_curve_df(
        [
            ("2024-01-01", -0.1, True),
            ("2024-01-02", 0.1, False),
            ("2024-01-03", -0.2, True),
            ("2024-01-04", -0.1, True),
        ]
    )

    result = yield_curve.detect_inversions(df)

    assert len(result) == 2
    assert result.iloc[0]["start_date"] == pd.Timestamp("2024-01-01")
    assert result.iloc[0]["end_date"] == pd.Timestamp("2024-01-01")
    assert result.iloc[1]["start_date"] == pd.Timestamp("2024-01-03")
    assert result.iloc[1]["end_date"] == pd.Timestamp("2024-01-04")


def test_detect_inversions_treats_null_as_not_inverted():
    df = _yield_curve_df(
        [
            ("2024-01-01", None, None),
            ("2024-01-02", -0.1, True),
        ]
    )

    result = yield_curve.detect_inversions(df)

    assert len(result) == 1
    assert result.iloc[0]["start_date"] == pd.Timestamp("2024-01-02")


def test_detect_inversions_splits_on_null_inside_inverted_run():
    # A missing observation (null is_inverted) in the middle of an otherwise
    # inverted run is NOT bridged — it's treated as "not inverted" like any
    # other gap, so the run splits into two periods. See the docstring note
    # in detect_inversions: we don't assume the curve stayed inverted on a
    # day with no data.
    df = _yield_curve_df(
        [
            ("2024-01-01", -0.1, True),
            ("2024-01-02", None, None),
            ("2024-01-03", -0.2, True),
        ]
    )

    result = yield_curve.detect_inversions(df)

    assert len(result) == 2
    assert result.iloc[0]["start_date"] == result.iloc[0]["end_date"] == pd.Timestamp("2024-01-01")
    assert result.iloc[1]["start_date"] == result.iloc[1]["end_date"] == pd.Timestamp("2024-01-03")


def test_detect_inversions_counts_calendar_gap_across_weekend():
    # Friday and the following Monday are trading-day-contiguous even though
    # two calendar days (Sat/Sun) separate them.
    df = _yield_curve_df(
        [
            ("2024-01-05", -0.1, True),  # Friday
            ("2024-01-08", -0.2, True),  # Monday
        ]
    )

    result = yield_curve.detect_inversions(df)

    assert len(result) == 1
    assert result.iloc[0]["trading_days"] == 2
    assert result.iloc[0]["duration_days"] == 4


# --- load_yield_curve ---
def test_load_yield_curve_reads_mart_table(tmp_path):
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        create table yield_curve_mart (
            rate_date date,
            yield_1m double,
            yield_2y double,
            yield_10y double,
            spread_2y_10y double,
            is_inverted boolean
        )
        """
    )
    conn.execute(
        "insert into yield_curve_mart values ('2024-01-02', 5.3, 4.0, 3.9, -0.1, true)"
    )
    conn.close()

    result = yield_curve.load_yield_curve(db_path)

    assert len(result) == 1
    assert result.iloc[0]["is_inverted"]


def test_load_yield_curve_raises_for_missing_db(tmp_path):
    missing_path = tmp_path / "does-not-exist.duckdb"

    try:
        yield_curve.load_yield_curve(missing_path)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError as exc:
        assert str(missing_path) in str(exc)


# --- build_inversion_report ---
def test_build_inversion_report_end_to_end(tmp_path):
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        create table yield_curve_mart (
            rate_date date,
            yield_1m double,
            yield_2y double,
            yield_10y double,
            spread_2y_10y double,
            is_inverted boolean
        )
        """
    )
    conn.execute(
        """
        insert into yield_curve_mart values
            ('2024-01-01', 5.3, 4.0, 4.1, 0.1, false),
            ('2024-01-02', 5.3, 4.0, 3.9, -0.1, true),
            ('2024-01-03', 5.3, 4.0, 3.8, -0.2, true)
        """
    )
    conn.close()

    result = yield_curve.build_inversion_report(db_path)

    assert len(result) == 1
    assert result.iloc[0]["start_date"] == pd.Timestamp("2024-01-02")
    assert result.iloc[0]["end_date"] == pd.Timestamp("2024-01-03")
