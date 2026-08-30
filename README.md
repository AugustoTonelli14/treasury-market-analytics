# Treasury Market Analytics Pipeline

End-to-end data pipeline for FX and interest-rate market analytics — built to mirror the
kind of data engineering and quantitative analysis performed on a Treasury & Fixed Income
desk. Ingests real institutional data (FRED, ECB, BIS), transforms it with dbt + DuckDB,
and surfaces yield-curve, FX-spread, and rate-differential analytics through an interactive
dashboard.

## Overview

The pipeline pulls daily FX rates, Treasury yields, and policy rates from public
institutional APIs, models them into a dimensional (star schema) DuckDB database, applies
dbt transformations for staging and analytics marts, and exposes the results through a
Streamlit dashboard and a set of business-question SQL queries.

**Business questions answered:**

1. How has the USD vs. EUR rate differential evolved over the last 5 years?
2. When did the US yield curve invert, and for how long?
3. What is the correlation between the Fed Funds Rate and USD/BRL?
4. How does EUR/USD volatility compare to USD/BRL volatility?
5. How do rates behave around FOMC meeting dates?

## Architecture

```
FRED / ECB / BIS APIs
        │
        ▼
   ingestion/            raw pulls → data/raw/ (JSON/CSV)
        │
        ▼
   transformation/        cleaning, type casting → data/processed/ (Parquet)
        │
        ▼
   modeling/              DuckDB star schema → outputs/treasury.duckdb
        │
        ▼
   dbt/treasury_dbt/      staging views → analytics marts (tables)
        │
        ▼
   analytics/  &  dashboard/     business queries, Streamlit app, notebooks
```

See [architecture/architecture.md](architecture/architecture.md) for the full diagram.

### Data model

A dimensional star schema centered on `fact_market_rates`, with `dim_date`, `dim_series`,
and `dim_geography` dimensions — one row per market series (FX rate, policy rate, or yield)
per date, with derived 1-day/1-week/1-month changes.

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Ingestion | FRED API, ECB SDW API, BIS CSVs, yfinance |
| Local storage | DuckDB |
| SQL transformation | dbt-core + dbt-duckdb |
| Output format | Parquet (PyArrow) |
| Dashboard | Streamlit |
| Quality | pytest, ruff |
| CI/CD | GitHub Actions |
| Automation | Makefile |

## Data Sources

- **[FRED](https://fred.stlouisfed.org/)** — US Treasury yields (10Y, 2Y, 1M), Fed Funds
  Rate, USD/EUR, USD/JPY, USD/BRL exchange rates. Requires a free API key.
- **[ECB Statistical Data Warehouse](https://data-api.ecb.europa.eu/)** — Euribor (3M/6M/12M),
  EUR/USD spot rate, ECB deposit facility rate. Public, no API key required.
- **[BIS](https://www.bis.org/statistics/)** — FX turnover and OTC derivatives statistics,
  downloaded as public CSVs.
- **[yfinance](https://pypi.org/project/yfinance/)** — fallback/enrichment for historical
  FX spot rates and cross-rates.

## Getting Started

### Prerequisites

- Python 3.11+
- A free [FRED API key](https://fred.stlouisfed.org/docs/api/api_key.html)

### Setup

```bash
git clone <repo-url>
cd treasury-market-analytics
cp .env.example .env        # then fill in FRED_API_KEY
make install
```

### Running the pipeline

```bash
make ingest       # pull raw data from FRED, ECB, BIS
make transform    # clean and cast types, write Parquet
make model        # build the DuckDB star schema
make dbt-run      # run dbt staging + mart models
make dbt-test     # run dbt schema/data tests
make run          # ingest + transform + model + dbt-run, in sequence
```

### Quality checks

```bash
make lint         # ruff check
make test         # pytest
make all          # lint + test + run
```

### Dashboard

```bash
make dashboard     # streamlit run dashboard/app.py
```

## Project Structure

```
treasury-market-analytics/
├── config/            # centralized settings (dates, series, paths)
├── ingestion/          # FRED / ECB / BIS connectors + validators
├── transformation/    # cleaning, type casting, Parquet output
├── modeling/           # DuckDB star schema builder
├── dbt/treasury_dbt/  # dbt staging models and analytics marts
├── analytics/         # business-question SQL and runner
├── dashboard/          # Streamlit app
├── tests/              # pytest suite
├── notebooks/          # executive-summary analysis
├── data/                # raw / processed / marts (gitignored)
├── outputs/            # DuckDB file + chart exports
└── architecture/       # architecture diagram
```

## Status

Actively developed over a 4-week build plan — see `PROJECT_NOTES.md` for the day-by-day
roadmap and current progress.

## License

Personal portfolio project. No license granted for reuse.
