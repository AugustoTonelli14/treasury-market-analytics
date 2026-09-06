"""
Tests for the FX volatility analytics module — rolling z-score and outlier
detection on top of fx_analytics_mart. All I/O runs against pytest's
tmp_path, no real outputs/treasury.duckdb is touched.
"""

import duckdb
import pandas as pd
import pytest

from analytics import fx_volatility


# Test helpers
def _fx_analytics_df(
    series_id: str, n: int, returns: dict[int, float] | None = None
) -> pd.DataFrame:
    """n rows for series_id with a constant rolling_volatility_30d from row 29 onward,
    and daily_return overridden at specific 0-based row positions via `returns`
    (default 0.0 elsewhere).

    Simplified vs. production: fx_analytics_mart's real warm-up lands one row
    later (index 30, not 29), because a series' first daily_return is always
    null there (division by the previous day's value via LAG), so its 30-row
    window at index 29 only has 29 non-null values. This fixture never sets
    daily_return to null, so add_rolling_zscore's rolling(min_periods=30)
    closes its window at index 29 to match the synthetic
    rolling_volatility_30d below -- consistent by construction, not a replica
    of the dbt off-by-one.
    """
    returns = returns or {}
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    daily_return = [returns.get(i, 0.0) for i in range(n)]
    rolling_volatility_30d = pd.array(
        [None if i < 29 else 1.0 for i in range(n)], dtype="float64"
    )
    return pd.DataFrame(
        {
            "fx_rate_id": [f"{series_id}_{i}" for i in range(n)],
            "rate_date": dates,
            "series_id": series_id,
            "series_name": series_id,
            "currency": "EUR",
            "source": "fred",
            "spot_rate": 1.1,
            "daily_return": daily_return,
            "rolling_volatility_30d": rolling_volatility_30d,
        }
    )


# --- add_rolling_zscore ---
def test_add_rolling_zscore_null_during_warmup():
    fx = _fx_analytics_df("DEXUSEU", 29)

    result = fx_volatility.add_rolling_zscore(fx)

    assert result["z_score"].isna().all()


def test_add_rolling_zscore_computes_once_window_is_full():
    # 30 rows: 29 zeros then a +0.06 return on the last row. rolling mean over
    # the 30-row window = 0.06 / 30 = 0.002; volatility fixed at 1.0 by the helper.
    fx = _fx_analytics_df("DEXUSEU", 30, returns={29: 0.06})

    result = fx_volatility.add_rolling_zscore(fx)

    last = result.iloc[-1]
    assert last["rolling_mean_return_30d"] == pytest.approx(0.002)
    assert last["z_score"] == pytest.approx((0.06 - 0.002) / 1.0)


def test_add_rolling_zscore_null_when_volatility_zero():
    fx = _fx_analytics_df("DEXUSEU", 30)
    fx.loc[29, "rolling_volatility_30d"] = 0.0

    result = fx_volatility.add_rolling_zscore(fx)

    assert pd.isna(result.iloc[-1]["z_score"])


def test_add_rolling_zscore_is_independent_per_series():
    # jpy has only 10 rows -- far short of its own 30-observation warm-up. If
    # the rolling mean leaked across series (e.g. a global rolling instead of
    # a per-series groupby), a 30-row window ending on jpy's rows would reach
    # back into eur's 35 rows and produce a non-null rolling_mean_return_30d
    # for jpy; correctly isolated per series, jpy must stay entirely null.
    eur = _fx_analytics_df("DEXUSEU", 35, returns={34: 3.5})
    jpy = _fx_analytics_df("DEXUSJP", 10)
    fx = pd.concat([eur, jpy], ignore_index=True)

    result = fx_volatility.add_rolling_zscore(fx)

    jpy_means = result[result["series_id"] == "DEXUSJP"]["rolling_mean_return_30d"]
    assert jpy_means.isna().all()


# --- flag_volatility_outliers ---
def test_flag_volatility_outliers_filters_by_threshold():
    # 29 zeros then a +3.5 return: mean = 3.5/30 ~= 0.117, volatility fixed at
    # 1.0, so z_score ~= 3.38 -- above the 3.0 threshold.
    fx = _fx_analytics_df("DEXUSEU", 30, returns={29: 3.5})
    scored = fx_volatility.add_rolling_zscore(fx)

    outliers = fx_volatility.flag_volatility_outliers(scored, threshold=3.0)

    assert len(outliers) == 1
    assert outliers.iloc[0]["rate_date"] == fx.iloc[-1]["rate_date"]


def test_flag_volatility_outliers_empty_below_threshold():
    fx = _fx_analytics_df("DEXUSEU", 30, returns={29: 0.06})
    scored = fx_volatility.add_rolling_zscore(fx)

    outliers = fx_volatility.flag_volatility_outliers(scored, threshold=3.0)

    assert outliers.empty


# --- load_fx_analytics ---
def test_load_fx_analytics_reads_mart_table(tmp_path):
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        create table fx_analytics_mart (
            fx_rate_id varchar,
            rate_date date,
            series_id varchar,
            series_name varchar,
            currency varchar,
            source varchar,
            spot_rate double,
            change_1d double,
            change_1w double,
            change_1m double,
            daily_return double,
            rolling_volatility_30d double
        )
        """
    )
    conn.execute(
        """
        insert into fx_analytics_mart values
            ('DEXUSEU_1', '2024-01-02', 'DEXUSEU', 'USD/EUR', 'EUR', 'fred',
             1.1, 0.01, 0.02, 0.03, 0.01, 0.05)
        """
    )
    conn.close()

    result = fx_volatility.load_fx_analytics(db_path)

    assert len(result) == 1
    assert result.iloc[0]["series_id"] == "DEXUSEU"


def test_load_fx_analytics_raises_for_missing_db(tmp_path):
    missing_path = tmp_path / "does-not-exist.duckdb"

    try:
        fx_volatility.load_fx_analytics(missing_path)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError as exc:
        assert str(missing_path) in str(exc)


# --- build_volatility_report ---
def test_build_volatility_report_end_to_end(tmp_path):
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(
        """
        create table fx_analytics_mart (
            fx_rate_id varchar,
            rate_date date,
            series_id varchar,
            series_name varchar,
            currency varchar,
            source varchar,
            spot_rate double,
            change_1d double,
            change_1w double,
            change_1m double,
            daily_return double,
            rolling_volatility_30d double
        )
        """
    )
    rows = []
    for i in range(30):
        daily_return = 3.5 if i == 29 else 0.0
        volatility = 1.0 if i >= 29 else None
        rate_date = (pd.Timestamp("2024-01-01") + pd.Timedelta(days=i)).date().isoformat()
        rows.append(
            (
                f"DEXUSEU_{i}",
                rate_date,
                "DEXUSEU",
                "USD/EUR",
                "EUR",
                "fred",
                1.1,
                None,
                None,
                None,
                daily_return,
                volatility,
            )
        )
    conn.executemany(
        "insert into fx_analytics_mart values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows
    )
    conn.close()

    result = fx_volatility.build_volatility_report(db_path, threshold=3.0)

    assert len(result) == 1
    assert result.iloc[0]["series_id"] == "DEXUSEU"
