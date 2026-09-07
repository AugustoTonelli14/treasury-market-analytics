"""
Tests for the business queries runner — parsing analytics/queries.sql into
named queries and running them against the DuckDB marts. All I/O runs
against pytest's tmp_path, no real outputs/treasury.duckdb is touched.
"""

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from analytics import run_queries


# Test helpers
def _write_queries(tmp_path: Path, sql: str) -> Path:
    sql_path = tmp_path / "queries.sql"
    sql_path.write_text(sql, encoding="utf-8")
    return sql_path


# --- load_queries ---
def test_load_queries_parses_named_blocks(tmp_path):
    sql_path = _write_queries(
        tmp_path, "-- name: first_query\nselect 1 as x;\n\n-- name: second_query\nselect 2 as y;\n"
    )

    queries = run_queries.load_queries(sql_path)

    assert set(queries) == {"first_query", "second_query"}
    assert queries["first_query"] == "select 1 as x;"
    assert queries["second_query"] == "select 2 as y;"


def test_load_queries_raises_on_duplicate_name(tmp_path):
    sql_path = _write_queries(
        tmp_path, "-- name: dup\nselect 1;\n\n-- name: dup\nselect 2;\n"
    )

    with pytest.raises(ValueError, match="Duplicate query name 'dup'"):
        run_queries.load_queries(sql_path)


def test_load_queries_raises_on_empty_query_body(tmp_path):
    sql_path = _write_queries(tmp_path, "-- name: trailing\n")

    with pytest.raises(ValueError, match="no SQL body"):
        run_queries.load_queries(sql_path)


def test_load_queries_raises_on_comment_only_body(tmp_path):
    sql_path = _write_queries(
        tmp_path, "-- name: only_comments\n-- just an explanatory comment, no SQL\n"
    )

    with pytest.raises(ValueError, match="no SQL body"):
        run_queries.load_queries(sql_path)


def test_load_queries_ignores_leading_comments_without_markers(tmp_path):
    sql_path = _write_queries(tmp_path, "-- just a comment, no name marker\n")

    assert run_queries.load_queries(sql_path) == {}


def test_load_queries_real_file_has_expected_names():
    queries = run_queries.load_queries()

    assert "usd_eur_differential_5y" in queries
    assert "fx_volatility_eur_usd_vs_usd_brl_summary" in queries


# --- run_query ---
def test_run_query_returns_dataframe(tmp_path):
    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    sql_path = _write_queries(tmp_path, "-- name: one\nselect 1 as x, 2 as y;\n")

    result = run_queries.run_query("one", db_path, sql_path)

    assert result.to_dict("records") == [{"x": 1, "y": 2}]


def test_run_query_unknown_name_raises_key_error(tmp_path):
    sql_path = _write_queries(tmp_path, "-- name: one\nselect 1;\n")

    with pytest.raises(KeyError, match="unknown_query"):
        run_queries.run_query("unknown_query", tmp_path / "irrelevant.duckdb", sql_path)


def test_run_query_raises_for_missing_db(tmp_path):
    sql_path = _write_queries(tmp_path, "-- name: one\nselect 1;\n")
    missing_path = tmp_path / "does-not-exist.duckdb"

    with pytest.raises(FileNotFoundError, match="does-not-exist"):
        run_queries.run_query("one", missing_path, sql_path)


# --- run_all_queries ---
def test_run_all_queries_returns_all_named_results(tmp_path):
    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    sql_path = _write_queries(
        tmp_path, "-- name: one\nselect 1 as x;\n\n-- name: two\nselect 2 as y;\n"
    )

    results = run_queries.run_all_queries(db_path, sql_path)

    assert set(results) == {"one", "two"}
    assert results["one"].iloc[0]["x"] == 1
    assert results["two"].iloc[0]["y"] == 2


def test_run_all_queries_against_real_queries_sql(tmp_path):
    # Sanity-check that the actual shipped queries.sql is valid SQL against
    # the real mart schemas, not just syntactically parseable.
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
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
    conn.close()

    results = run_queries.run_all_queries(db_path)

    assert set(results) == set(run_queries.load_queries())
    for df in results.values():
        assert isinstance(df, pd.DataFrame)
