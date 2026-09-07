-- Business queries — answers the business questions from PROJECT_NOTES.md
-- that aren't already covered by a dedicated analytics/ module (yield
-- curve inversions -> yield_curve.py, FX volatility outliers ->
-- fx_volatility.py, Fed Funds/USD-BRL correlation -> rate_fx_correlation.py).
--
-- Parsed and run by analytics/run_queries.py, which splits this file on
-- the "-- name: <identifier>" markers below -- add new queries the same
-- way, one marker per query.
--
-- Business question #5 ("Como se comportam as taxas no período pré/pós
-- FOMC meetings?") is deliberately not covered here: it needs a calendar
-- of FOMC meeting dates, which isn't ingested anywhere in this project
-- (see PROJECT_NOTES.md, "Bloqueios / Decisões pendentes").

-- name: usd_eur_differential_5y
-- Business question 1: Como evoluiu o diferencial de taxas USD vs EUR nos
-- últimos 5 anos?
select
    rate_date,
    usd_policy_rate,
    eur_policy_rate,
    usd_eur_differential
from rate_differential_mart
where rate_date >= current_date - interval '5 years'
order by rate_date;

-- name: fx_volatility_eur_usd_vs_usd_brl
-- Business question 4: Qual a volatilidade histórica do EUR/USD vs
-- USD/BRL? Full time series of the 30-day rolling volatility for both
-- pairs, one row per series per date. DEXUSEU is used as the canonical
-- USD/EUR series (see stg_fx_rates: DEXUSEU and EURUSD_SPOT both
-- represent USD/EUR but aren't deduplicated).
select
    series_id,
    rate_date,
    rolling_volatility_30d
from fx_analytics_mart
where series_id in ('DEXUSEU', 'DEXUSBR')
order by series_id, rate_date;

-- name: fx_volatility_eur_usd_vs_usd_brl_summary
-- Business question 4 (summary): aggregate 30-day rolling volatility per
-- pair, for a quick side-by-side comparison instead of the full series.
select
    series_id,
    count(rolling_volatility_30d) as observations,
    avg(rolling_volatility_30d) as avg_volatility_30d,
    min(rolling_volatility_30d) as min_volatility_30d,
    max(rolling_volatility_30d) as max_volatility_30d
from fx_analytics_mart
where series_id in ('DEXUSEU', 'DEXUSBR')
group by series_id
order by series_id;
