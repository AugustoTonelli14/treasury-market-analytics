# PROJECT_NOTES.md — Treasury Market Analytics Pipeline

> Este ficheiro é o briefing permanente do projeto. 
> Atualiza a secção "Estado Atual" e "Próximo Passo" no fim de cada sessão de trabalho.

---

## 🎯 O que é este projeto

**Treasury Market Analytics Pipeline** — um pipeline de dados end-to-end focado em
mercados de FX e taxas de juro, com analytics de derivativos e dashboards executivos.

O projeto ingere dados reais de fontes institucionais (FRED, ECB, BIS), transforma-os
com dbt e DuckDB, e produz analytics sobre yield curves, spreads FX e diferenciais
de taxas de juro — o tipo de análise que se faz num Treasury & FI desk.

**Objetivo de portfolio:** demonstrar fluência em data engineering aplicado a finanças,
conectando o trabalho técnico ao contexto profissional do Itaú BBA.

---

## 🏗️ Stack Técnica

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.11+ |
| Ingestion | FRED API, ECB SDW API, BIS CSVs, yfinance |
| Armazenamento local | DuckDB |
| Transformação SQL | dbt-core + dbt-duckdb |
| Output | Parquet (PyArrow) |
| Dashboard | Streamlit |
| Qualidade | pytest, ruff |
| CI/CD | GitHub Actions |
| Automação | Makefile |

---

## 📦 Fontes de Dados

### FRED (Federal Reserve Economic Data)
- **URL:** https://fred.stlouisfed.org/
- **API Key:** definir em `.env` como `FRED_API_KEY`
- **Séries a ingerir:**
  - `DGS10` — US 10-Year Treasury Yield
  - `DGS2` — US 2-Year Treasury Yield
  - `DGS1MO` — US 1-Month Treasury Yield
  - `DEXUSEU` — USD/EUR Exchange Rate
  - `DEXUSJP` — USD/JPY Exchange Rate
  - `DEXUSBR` — USD/BRL Exchange Rate
  - `FEDFUNDS` — Federal Funds Rate

### ECB Statistical Data Warehouse
- **URL:** https://data-api.ecb.europa.eu/
- **Sem API key** — acesso público
- **Séries a ingerir:**
  - Euribor 3M, 6M, 12M
  - EUR/USD spot rate
  - ECB deposit facility rate

### BIS (Bank for International Settlements)
- **URL:** https://www.bis.org/statistics/
- **Formato:** CSVs públicos para download
- **Dados:** FX turnover, OTC derivatives statistics

### yfinance (fallback / enriquecimento)
- FX spot rates históricos
- Cross-rates

---

## 🗂️ Estrutura de Pastas

```
treasury-market-analytics/
├── CLAUDE.md                    ← Este ficheiro — atualizar diariamente
├── README.md                    ← Documentação pública do projeto
├── .env.example                 ← Template de variáveis de ambiente
├── .gitignore
├── Makefile                     ← Comandos de automação
├── pyproject.toml               ← Config ruff + pytest
├── requirements.txt
├── docker-compose.yml           ← Opcional: para serviços locais
│
├── .github/
│   └── workflows/
│       └── ci.yml               ← Lint + test + pipeline smoke
│
├── config/
│   └── settings.py              ← Configurações centrais (datas, séries, paths)
│
├── ingestion/
│   ├── fred_connector.py        ← FRED API client
│   ├── ecb_connector.py         ← ECB SDW API client
│   ├── bis_loader.py            ← BIS CSV loader
│   └── validators.py            ← Schema validation
│
├── transformation/
│   └── transform.py             ← Limpeza, type casting, Parquet output
│
├── modeling/
│   └── model.py                 ← DuckDB star schema builder
│
├── dbt/
│   └── treasury_dbt/
│       ├── dbt_project.yml
│       ├── profiles.yml
│       └── models/
│           ├── staging/
│           │   ├── stg_fx_rates.sql
│           │   ├── stg_interest_rates.sql
│           │   ├── stg_yield_curve.sql
│           │   └── schema.yml
│           └── marts/
│               ├── fx_analytics_mart.sql
│               ├── yield_curve_mart.sql
│               ├── rate_differential_mart.sql
│               └── schema.yml
│
├── analytics/
│   ├── queries.sql              ← Business queries SQL
│   └── run_queries.py
│
├── dashboard/
│   └── app.py                   ← Streamlit dashboard
│
├── tests/
│   ├── test_ingestion.py
│   ├── test_transformation.py
│   └── test_models.py
│
├── notebooks/
│   └── treasury_analysis.ipynb  ← Análise executiva final
│
├── data/
│   ├── raw/                     ← Dados brutos (gitignored)
│   ├── processed/               ← Parquet limpos (gitignored)
│   └── marts/                   ← Marts finais (gitignored)
│
├── outputs/
│   ├── treasury.duckdb          ← Star schema (gitignored)
│   └── charts/                  ← PNG charts
│
└── architecture/
    └── architecture.md          ← Diagrama Mermaid da arquitetura
```

---

## 🗃️ Star Schema — Dimensional Model

```
fact_market_rates (grain: 1 row per series per date)
├── date_key           FK → dim_date
├── series_key         FK → dim_series
├── geography_key      FK → dim_geography
├── value              FLOAT
├── change_1d          FLOAT  (derivado)
├── change_1w          FLOAT  (derivado)
└── change_1m          FLOAT  (derivado)

dim_date
├── date_key           INTEGER (YYYYMMDD)
├── full_date          DATE
├── year, quarter, month, week
├── is_weekend         BOOLEAN
└── is_business_day    BOOLEAN

dim_series
├── series_key         VARCHAR
├── series_id          VARCHAR  (ex: "DGS10")
├── series_name        VARCHAR  (ex: "US 10Y Treasury")
├── category           VARCHAR  (fx_rate | interest_rate | yield)
├── currency           VARCHAR
└── source             VARCHAR  (fred | ecb | bis)

dim_geography
├── geography_key      VARCHAR
├── country            VARCHAR
├── region             VARCHAR
└── currency_code      VARCHAR
```

---

## 📊 dbt Models

### Staging (views)
- `stg_fx_rates` — normaliza todos os FX rates (USD/EUR, USD/BRL, USD/JPY)
- `stg_interest_rates` — normaliza policy rates (Fed Funds, ECB Deposit Rate)
- `stg_yield_curve` — normaliza yields por maturidade (1M, 2Y, 10Y)

### Marts (tables)
- `fx_analytics_mart` — spot rates, daily changes, rolling volatility
- `yield_curve_mart` — yield curve por data, spread 2Y-10Y (inversão)
- `rate_differential_mart` — diferenciais de taxas entre países (base para NDF pricing)

---

## 📈 Business Questions a Responder

1. Como evoluiu o diferencial de taxas USD vs EUR nos últimos 5 anos?
2. Quando ocorreram inversões da yield curve americana? Quanto duraram?
3. Qual a correlação entre o Fed Funds Rate e o USD/BRL?
4. Qual a volatilidade histórica do EUR/USD vs USD/BRL?
5. Como se comportam as taxas no período pré/pós FOMC meetings?

---

## 📅 Plano de 4 Semanas — Commits Diários

### ✅ Semana 1 — Ingestion Layer
- [x] Dia 1: Setup repo, estrutura de pastas, README inicial, .env.example, .gitignore, Makefile base
- [ ] Dia 2: FRED API connector com retry logic e structured logging
- [ ] Dia 3: ECB API connector (euribor + EUR rates)
- [ ] Dia 4: BIS CSV loader + validators.py com schema checks
- [ ] Dia 5: pytest para ingestion (test_ingestion.py) + CI/CD base

### Semana 2 — Transformation & dbt
- [ ] Dia 1: transform.py — limpeza, type casting, Parquet output
- [ ] Dia 2: DuckDB star schema (modeling.py)
- [ ] Dia 3: dbt staging models (stg_fx_rates, stg_interest_rates, stg_yield_curve)
- [ ] Dia 4: dbt mart models (fx_analytics_mart, yield_curve_mart)
- [ ] Dia 5: dbt rate_differential_mart + schema tests + dbt test

### Semana 3 — Analytics
- [ ] Dia 1: Yield curve builder — spread 2Y-10Y, inversão detection
- [ ] Dia 2: FX volatility analytics — rolling std, z-score
- [ ] Dia 3: Rate differential analytics — USD vs EUR vs BRL
- [ ] Dia 4: Business queries SQL (analytics/queries.sql)
- [ ] Dia 5: Jupyter notebook — executive summary com charts

### Semana 4 — Dashboard & Polish
- [ ] Dia 1: Streamlit dashboard — KPI cards + charts principais
- [ ] Dia 2: GitHub Actions CI/CD completo (lint + test + smoke)
- [ ] Dia 3: README completo com architecture diagram (Mermaid)
- [ ] Dia 4: Code cleanup, ruff linting, pyproject.toml
- [ ] Dia 5: Release v1.0.0 + topics no repo

---

## 📐 Convenções de Código

### Python
- Formatter: **ruff** (configurado em pyproject.toml)
- Todas as funções têm docstring
- Type hints em todas as funções públicas
- Logging com o módulo `logging` (não print)
- Variáveis de ambiente via `python-dotenv` — nunca hardcoded
- Paths via `pathlib.Path` — nunca strings
- Funções pequenas e focadas — uma responsabilidade por função

### Commits
Seguir este formato todos os dias:
```
feat: add FRED API connector with retry logic
fix: handle null values in ECB euribor series
refactor: extract schema validation to validators.py
test: add 12 unit tests for transformation layer
docs: update README with architecture diagram
chore: add ruff config to pyproject.toml
```

### dbt
- Staging models = views, marts = tables
- Prefixo `stg_` para staging, `mart_` para marts
- Todas as colunas documentadas no schema.yml
- Tests: unique + not_null em todas as PKs

### Estrutura de ficheiros Python
```python
"""
Module docstring — o que este módulo faz.
"""
import ...

# Constants
DATA_DIR = Path("data/raw")

# Public functions (usadas fora do módulo)
def fetch_series(...) -> pd.DataFrame:
    """Docstring."""
    ...

# Private helpers (usadas apenas internamente)
def _validate_response(...) -> bool:
    ...
```

---

## 🔑 Variáveis de Ambiente (.env)

```
FRED_API_KEY=your_key_here
START_DATE=2015-01-01
END_DATE=today
DuckDB_PATH=outputs/treasury.duckdb
LOG_LEVEL=INFO
```

---

## ▶️ Comandos Makefile

```bash
make install        # pip install -r requirements.txt
make run            # pipeline completo
make ingest         # só ingestion
make transform      # só transformation
make model          # só DuckDB modeling
make dbt-run        # dbt run
make dbt-test       # dbt test
make test           # pytest
make lint           # ruff check
make dashboard      # streamlit run dashboard/app.py
make all            # lint + test + run
```

---

## 🔄 Estado Atual

**Data:** 2026-08-30

**Última coisa feita (Dia 1):**
- Criada toda a estrutura de pastas definida acima (`config/`, `ingestion/`,
  `transformation/`, `modeling/`, `dbt/treasury_dbt/models/{staging,marts}/`,
  `analytics/`, `dashboard/`, `tests/`, `notebooks/`, `data/{raw,processed,marts}/`,
  `outputs/charts/`, `architecture/`, `.github/workflows/`), com `.gitkeep` nas
  pastas de dados/outputs gitignoradas.
- `.gitignore` completo (Python, venvs, `.env`, DuckDB, `data/`, `outputs/`,
  dbt `target/`/`dbt_packages/`, pytest/ruff caches, Jupyter checkpoints).
- `.env.example` com `FRED_API_KEY`, `START_DATE`, `END_DATE`, `DUCKDB_PATH`,
  `LOG_LEVEL` e URLs base de ECB/BIS.
- `Makefile` com todos os targets pedidos (`install`, `run`, `ingest`, `transform`,
  `model`, `dbt-run`, `dbt-test`, `test`, `lint`, `dashboard`, `all`).
- `pyproject.toml` com config de ruff (E/W/F/I/UP/B/C4/SIM, line-length 100,
  target py311) e pytest (`testpaths = tests`).
- `requirements.txt` com as dependências da stack (pandas, pyarrow, fredapi,
  requests, yfinance, python-dotenv, duckdb, dbt-core, dbt-duckdb, streamlit,
  plotly, pytest, pytest-cov, ruff, jupyter).
- `README.md` profissional (overview, arquitetura, star schema, stack, fontes
  de dados, setup, comandos, estrutura de pastas).

**Próximo passo — Dia 2:**
1. FRED API connector (`ingestion/fred_connector.py`) com retry logic e
   structured logging (módulo `logging`, sem `print`)
2. Ler `FRED_API_KEY` via `python-dotenv`, paths via `pathlib.Path`
3. Guardar séries brutas em `data/raw/`
4. Commit: `feat: add FRED API connector with retry logic`

**Bloqueios / Decisões pendentes:**
- Ainda não foi criado o repositório remoto no GitHub (apenas init local até
  agora, confirmar antes do primeiro push).
- `.env.example` usa `DUCKDB_PATH` (maiúsculas) em vez de `DuckDB_PATH` como
  escrito na secção de variáveis de ambiente acima — convenção correta para
  env vars é UPPER_SNAKE_CASE; `config/settings.py` (Dia 2+) deve ler
  `DUCKDB_PATH`.

---

> **Instrução para o Claude Code:**
> Lê este ficheiro no início de cada sessão.
> Segue as convenções de código acima em tudo o que escreves.
> No fim da sessão, atualiza a secção "Estado Atual" com o que foi feito e o próximo passo.
