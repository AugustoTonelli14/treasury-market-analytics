"""
Tests for the rate/FX correlation analytics module — joining
rate_differential_mart with USD/BRL fx_analytics_mart rows and computing
level/change-basis correlations. All I/O runs against pytest's tmp_path, no
real outputs/treasury.duckdb is touched.
"""

import duckdb
import pandas as pd
import pytest

from analytics import rate_fx_correlation


# Test helpers
def _rate_fx_df(rows: dict[str, list]) -> pd.DataFrame:
    n = len(next(iter(rows.values())))
    return pd.DataFrame({"rate_date": pd.date_range("2024-01-01", periods=n, freq="D"), **rows})


# --- compute_correlations ---
def test_compute_correlations_perfect_positive_level_correlation():
    rate_fx = _rate_fx_df(
        {
            "usd_policy_rate": [1.0, 2.0, 3.0, 4.0, 5.0],
            "usd_eur_differential": [0.5, 1.5, 2.5, 3.5, 4.5],
            "usdbrl_spot_rate": [5.0, 5.2, 5.4, 5.6, 5.8],
            "usdbrl_daily_return": [None, 0.04, 0.038, 0.037, 0.036],
        }
    )

    result = rate_fx_correlation.compute_correlations(rate_fx)

    level_row = result[
        (result["pair"] == "usd_policy_rate vs usdbrl_spot_rate") & (result["basis"] == "level")
    ].iloc[0]
    assert level_row["correlation"] == pytest.approx(1.0)
    assert level_row["observations"] == 5


def test_compute_correlations_diff_of_linear_series_has_zero_variance():
    # usd_policy_rate increases by a constant 1.0 every row, so its diff has
    # zero variance -- correlation against anything is mathematically
    # undefined (NaN), not spuriously 1.0 or 0.0.
    rate_fx = _rate_fx_df(
        {
            "usd_policy_rate": [1.0, 2.0, 3.0, 4.0, 5.0],
            "usd_eur_differential": [0.5, 1.5, 2.5, 3.5, 4.5],
            "usdbrl_spot_rate": [5.0, 5.2, 5.1, 5.3, 5.2],
            "usdbrl_daily_return": [None, 0.02, -0.01, 0.02, -0.01],
        }
    )

    result = rate_fx_correlation.compute_correlations(rate_fx)

    change_row = result[
        (result["pair"] == "usd_policy_rate vs usdbrl_spot_rate") & (result["basis"] == "change")
    ].iloc[0]
    assert pd.isna(change_row["correlation"])
    assert change_row["observations"] == 4  # first row has no diff/return


def test_compute_correlations_level_and_change_can_diverge():
    # Both series trend up, giving a high (arguably spurious) level
    # correlation -- but their actual changes move in opposite directions,
    # giving a strongly *negative* change-basis correlation. This is the
    # non-degenerate case the module's docstring is about: level and change
    # don't just differ in magnitude, they can disagree on direction.
    rate_fx = _rate_fx_df(
        {
            "usd_policy_rate": [1.0, 1.5, 3.0, 3.2, 5.0],
            "usd_eur_differential": [1.0, 1.5, 3.0, 3.2, 5.0],
            "usdbrl_spot_rate": [5.0, 5.3, 5.35, 5.9, 5.95],
            "usdbrl_daily_return": [None, 0.06, 0.0094, 0.1028, 0.0085],
        }
    )

    result = rate_fx_correlation.compute_correlations(rate_fx)

    level_row = result[
        (result["pair"] == "usd_policy_rate vs usdbrl_spot_rate") & (result["basis"] == "level")
    ].iloc[0]
    change_row = result[
        (result["pair"] == "usd_policy_rate vs usdbrl_spot_rate") & (result["basis"] == "change")
    ].iloc[0]
    assert level_row["correlation"] == pytest.approx(0.8796861956326865)
    assert change_row["correlation"] == pytest.approx(-0.9616429893275574)


def test_compute_correlations_change_basis_uses_own_observation_dates():
    # Simulates rate_differential_mart's real grain: usd_policy_rate (a
    # monthly FEDFUNDS series) is non-null on only 2 of 10 daily rows in the
    # joined frame -- the other rows belong to dates where some other
    # interest_rate series had an observation. A naive positional .diff()
    # over this sparse frame would compare each real value against its
    # (usually null) neighbor and lose the change entirely; computing it
    # over usd_policy_rate's own non-null dates must preserve it instead.
    rate_fx = _rate_fx_df(
        {
            "usd_policy_rate": [5.25, None, None, None, None, 5.50, None, None, None, None],
            "usd_eur_differential": [None] * 10,
            "usdbrl_spot_rate": [5.0 + 0.01 * i for i in range(10)],
            "usdbrl_daily_return": [None, 0.01, 0.01, 0.01, 0.01, 0.02, 0.01, 0.01, 0.01, 0.01],
        }
    )

    result = rate_fx_correlation.compute_correlations(rate_fx)

    change_row = result[
        (result["pair"] == "usd_policy_rate vs usdbrl_spot_rate") & (result["basis"] == "change")
    ].iloc[0]
    # Exactly one real change (5.25 -> 5.50 on row index 5), paired with that
    # row's return (0.02). A single point makes the correlation undefined
    # (NaN, corr needs >=2), but observations must be 1, not 0 -- proving the
    # change survived instead of being lost to positional null neighbors.
    assert change_row["observations"] == 1
    assert pd.isna(change_row["correlation"])


def test_compute_correlations_excludes_nulls_from_observation_count():
    rate_fx = _rate_fx_df(
        {
            "usd_policy_rate": [1.0, None, 3.0, 4.0],
            "usd_eur_differential": [0.5, None, 2.5, 3.5],
            "usdbrl_spot_rate": [5.0, 5.1, 5.2, 5.3],
            "usdbrl_daily_return": [None, 0.02, 0.02, 0.02],
        }
    )

    result = rate_fx_correlation.compute_correlations(rate_fx)

    level_row = result[
        (result["pair"] == "usd_policy_rate vs usdbrl_spot_rate") & (result["basis"] == "level")
    ].iloc[0]
    assert level_row["observations"] == 3  # row index 1 has a null usd_policy_rate


def test_compute_correlations_returns_four_rows():
    rate_fx = _rate_fx_df(
        {
            "usd_policy_rate": [1.0, 2.0, 3.0],
            "usd_eur_differential": [0.5, 1.5, 2.5],
            "usdbrl_spot_rate": [5.0, 5.1, 5.2],
            "usdbrl_daily_return": [None, 0.02, 0.02],
        }
    )

    result = rate_fx_correlation.compute_correlations(rate_fx)

    assert len(result) == 4
    assert set(result["basis"]) == {"level", "change"}
    assert set(result["pair"]) == {
        "usd_policy_rate vs usdbrl_spot_rate",
        "usd_eur_differential vs usdbrl_spot_rate",
    }


# --- load_rate_fx ---
def _create_marts(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        create table rate_differential_mart (
            rate_date date,
            usd_policy_rate double,
            eur_policy_rate double,
            usd_eur_differential double
        )
        """
    )
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


def test_load_rate_fx_joins_on_date_and_filters_series(tmp_path):
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    _create_marts(conn)
    conn.execute(
        "insert into rate_differential_mart values ('2024-01-02', 5.25, 3.75, 1.5)"
    )
    conn.execute(
        """
        insert into fx_analytics_mart values
            ('DEXUSBR_1', '2024-01-02', 'DEXUSBR', 'USD/BRL', 'BRL', 'fred',
             5.0, null, null, null, 0.01, 0.02),
            ('DEXUSEU_1', '2024-01-02', 'DEXUSEU', 'USD/EUR', 'EUR', 'fred',
             1.1, null, null, null, 0.005, 0.01)
        """
    )
    conn.close()

    result = rate_fx_correlation.load_rate_fx(db_path)

    assert len(result) == 1
    assert result.iloc[0]["usdbrl_spot_rate"] == 5.0
    assert result.iloc[0]["usd_eur_differential"] == 1.5
    assert "eur_policy_rate" not in result.columns


def test_load_rate_fx_raises_for_missing_db(tmp_path):
    missing_path = tmp_path / "does-not-exist.duckdb"

    try:
        rate_fx_correlation.load_rate_fx(missing_path)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError as exc:
        assert str(missing_path) in str(exc)


# --- build_correlation_report ---
def test_build_correlation_report_end_to_end(tmp_path):
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    _create_marts(conn)
    rate_rows = [
        (f"2024-01-{i:02d}", 5.0 + i * 0.1, 3.5, 1.5 + i * 0.1) for i in range(1, 6)
    ]
    conn.executemany(
        "insert into rate_differential_mart values (?, ?, ?, ?)", rate_rows
    )
    fx_rows = [
        (
            f"DEXUSBR_{i}",
            f"2024-01-{i:02d}",
            "DEXUSBR",
            "USD/BRL",
            "BRL",
            "fred",
            5.0 + i * 0.05,
            None,
            None,
            None,
            0.01,
            0.02,
        )
        for i in range(1, 6)
    ]
    conn.executemany(
        "insert into fx_analytics_mart values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", fx_rows
    )
    conn.close()

    result = rate_fx_correlation.build_correlation_report(db_path)

    assert len(result) == 4
    assert "correlation" in result.columns
