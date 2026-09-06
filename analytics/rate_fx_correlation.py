"""
Rate differential / FX correlation analytics — joins the USD-EUR policy
rate differential (rate_differential_mart) with USD/BRL FX data
(fx_analytics_mart) and computes the correlation between US rates and
USD/BRL, on both levels and rate-change basis.

BRL has no policy rate series ingested (see modeling/model.py's
SERIES_CATALOG), so "BRL" here is USD/BRL FX (DEXUSBR) rather than a BRL
policy rate — this directly answers the business question "Qual a
correlação entre o Fed Funds Rate e o USD/BRL?" instead of a rate-vs-rate
differential.
"""

import logging
import os
from pathlib import Path

import duckdb
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

USD_BRL_SERIES_ID = "DEXUSBR"
MIN_CHANGE_OBSERVATIONS_WARNING = 5


# Public functions (used outside this module)
def load_rate_fx(db_path: Path | None = None) -> pd.DataFrame:
    """Join rate_differential_mart with USD/BRL fx_analytics_mart rows on rate_date."""
    path = db_path or Path(os.getenv("DUCKDB_PATH", "outputs/treasury.duckdb"))
    if not path.exists():
        raise FileNotFoundError(
            f"DuckDB database not found at {path}. Run `make model` (or the full "
            "`make ingest transform model` pipeline) to build it first."
        )
    conn = duckdb.connect(str(path), read_only=True)
    try:
        rate_fx = conn.execute(
            """
            select
                r.rate_date,
                r.usd_policy_rate,
                r.usd_eur_differential,
                fx.spot_rate as usdbrl_spot_rate,
                fx.daily_return as usdbrl_daily_return
            from rate_differential_mart r
            inner join fx_analytics_mart fx
                on fx.rate_date = r.rate_date and fx.series_id = ?
            order by r.rate_date
            """,
            [USD_BRL_SERIES_ID],
        ).fetchdf()
    finally:
        conn.close()
    logger.info("Loaded %d joined rate/FX rows from %s", len(rate_fx), path)
    return rate_fx


def compute_correlations(rate_fx: pd.DataFrame) -> pd.DataFrame:
    """Pearson correlation of US rates vs USD/BRL, on levels and on rate changes.

    Level correlation between two trending series (a policy rate and a FX
    spot rate can each trend for years) is often spuriously high or low and
    doesn't reflect a real short-term relationship — differencing the rate
    first is the more meaningful comparison for that.

    rate_differential_mart has one row per date on which *any* of the
    underlying interest_rate series (FEDFUNDS, ECB_DEPOSIT_RATE,
    EURIBOR_3M/6M/12M) has an observation, with usd_policy_rate/
    usd_eur_differential null on dates where that specific series doesn't.
    FEDFUNDS in particular is a monthly series, so after joining onto
    fx_analytics_mart's daily USD/BRL grain, most rows have a null
    usd_policy_rate. A plain positional .diff() over that joined frame would
    almost always compare a real value against a null neighbor and yield
    NaN everywhere — so the change is computed over each rate's own non-null
    observation dates first, then aligned back by rate_date.
    """
    rate_series = {
        "usd_policy_rate": rate_fx["usd_policy_rate"],
        "usd_eur_differential": rate_fx["usd_eur_differential"],
    }
    rows = []
    for name, level in rate_series.items():
        rows.append(
            {
                "pair": f"{name} vs usdbrl_spot_rate",
                "basis": "level",
                "correlation": level.corr(rate_fx["usdbrl_spot_rate"]),
                "observations": _paired_observations(level, rate_fx["usdbrl_spot_rate"]),
            }
        )
        change = _change_on_observation_dates(rate_fx, name)
        change_observations = _paired_observations(change, rate_fx["usdbrl_daily_return"])
        if change_observations < MIN_CHANGE_OBSERVATIONS_WARNING:
            logger.warning(
                "Only %d paired observation(s) for %s vs usdbrl_daily_return on a "
                "change basis -- correlation may be unreliable.",
                change_observations,
                name,
            )
        rows.append(
            {
                "pair": f"{name} vs usdbrl_spot_rate",
                "basis": "change",
                "correlation": change.corr(rate_fx["usdbrl_daily_return"]),
                "observations": change_observations,
            }
        )
    return pd.DataFrame(rows)


def build_correlation_report(db_path: Path | None = None) -> pd.DataFrame:
    """Load joined rate/FX data from DuckDB and return the correlation summary."""
    rate_fx = load_rate_fx(db_path)
    correlations = compute_correlations(rate_fx)
    logger.info("Computed %d rate/FX correlation pair(s)", len(correlations))
    return correlations


# Private helpers (used only internally)
def _paired_observations(a: pd.Series, b: pd.Series) -> int:
    """Count rows where both a and b are non-null (what .corr() actually uses)."""
    return int(pd.DataFrame({"a": a, "b": b}).dropna().shape[0])


def _change_on_observation_dates(rate_fx: pd.DataFrame, column: str) -> pd.Series:
    """Diff `column` across its own non-null observations, aligned back to rate_fx's rows.

    Using a plain positional .diff() on the (sparsely populated) joined
    frame would compare each real observation against whatever neighboring
    row happens to be there, which is usually a date where a *different*
    interest_rate series was observed, not the previous reading of
    `column` itself. Restricting to `column`'s own observation dates first
    fixes that.
    """
    observed = rate_fx[["rate_date", column]].dropna().sort_values("rate_date")
    change_by_date = pd.Series(observed[column].diff().to_numpy(), index=observed["rate_date"])
    return rate_fx["rate_date"].map(change_by_date)
