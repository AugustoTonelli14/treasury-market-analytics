"""
Business queries runner — parses the named SQL queries in
analytics/queries.sql and runs them against the DuckDB marts, returning
each result as a DataFrame.
"""

import logging
import os
import re
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

QUERIES_PATH = Path(__file__).parent / "queries.sql"
_NAME_MARKER = re.compile(r"^--\s*name:\s*(\S+)\s*$", re.MULTILINE)


# Public functions (used outside this module)
def load_queries(queries_path: Path = QUERIES_PATH) -> dict[str, str]:
    """Parse queries.sql into {query_name: sql_text}, split on `-- name:` markers.

    Raises ValueError on a duplicate query name (would otherwise silently
    drop the earlier query) or a marker with no SQL after it (would
    otherwise only fail later, obscurely, when DuckDB executes an empty
    string).
    """
    text = queries_path.read_text(encoding="utf-8")
    markers = list(_NAME_MARKER.finditer(text))
    queries: dict[str, str] = {}
    for i, marker in enumerate(markers):
        name = marker.group(1)
        if name in queries:
            raise ValueError(f"Duplicate query name '{name}' in {queries_path}")
        start = marker.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        body = text[start:end].strip()
        sql_without_comments = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("--")
        ).strip()
        if not sql_without_comments:
            raise ValueError(f"Query '{name}' in {queries_path} has no SQL body")
        queries[name] = body
    return queries


def run_query(
    name: str, db_path: Path | None = None, queries_path: Path = QUERIES_PATH
) -> pd.DataFrame:
    """Run one named query from queries.sql against the DuckDB marts."""
    queries = load_queries(queries_path)
    if name not in queries:
        raise KeyError(f"Unknown query '{name}'. Known queries: {sorted(queries)}")

    conn = _connect(db_path)
    try:
        result = conn.execute(queries[name]).fetchdf()
    finally:
        conn.close()
    logger.info("Query '%s' returned %d row(s)", name, len(result))
    return result


def run_all_queries(
    db_path: Path | None = None, queries_path: Path = QUERIES_PATH
) -> dict[str, pd.DataFrame]:
    """Run every named query in queries.sql and return {name: DataFrame}."""
    queries = load_queries(queries_path)
    conn = _connect(db_path)
    try:
        results = {name: conn.execute(sql).fetchdf() for name, sql in queries.items()}
    finally:
        conn.close()
    for name, df in results.items():
        logger.info("Query '%s' returned %d row(s)", name, len(df))
    return results


# Private helpers (used only internally)
def _connect(db_path: Path | None) -> duckdb.DuckDBPyConnection:
    """Resolve DUCKDB_PATH and open a read-only connection, or raise a clear error."""
    path = db_path or Path(os.getenv("DUCKDB_PATH", "outputs/treasury.duckdb"))
    if not path.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {path}. Run `make model` (or the full "
            "`make ingest transform model` pipeline) to build it first."
        )
    return duckdb.connect(str(path), read_only=True)


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for query_name, df in run_all_queries().items():
        print(f"\n=== {query_name} ({len(df)} rows) ===")
        print(df.to_string(index=False))
