.PHONY: install run ingest transform model dbt-run dbt-test test lint dashboard all

install:
	pip install -r requirements.txt

ingest:
	python -m ingestion.fred_connector
	python -m ingestion.ecb_connector
	python -m ingestion.bis_loader

transform:
	python -m transformation.transform

model:
	python -m modeling.model

dbt-run:
	cd dbt/treasury_dbt && dbt run

dbt-test:
	cd dbt/treasury_dbt && dbt test

run: ingest transform model dbt-run

test:
	pytest -v

lint:
	ruff check .

dashboard:
	streamlit run dashboard/app.py

all: lint test run
