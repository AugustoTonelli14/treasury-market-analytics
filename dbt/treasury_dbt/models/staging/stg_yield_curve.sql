with fact as (
    select * from {{ source('star_schema', 'fact_market_rates') }}
),

series as (
    select * from {{ source('star_schema', 'dim_series') }}
    where category = 'yield'
),

dates as (
    select * from {{ source('star_schema', 'dim_date') }}
)

select
    series.series_id || '_' || strftime(dates.full_date, '%Y%m%d') as yield_curve_id,
    dates.full_date as rate_date,
    series.series_id,
    series.series_name,
    case series.series_id
        when 'DGS1MO' then '1M'
        when 'DGS2' then '2Y'
        when 'DGS10' then '10Y'
        else null
    end as maturity,
    series.currency,
    series.source,
    fact.value,
    fact.change_1d,
    fact.change_1w,
    fact.change_1m
from fact
inner join series on fact.series_key = series.series_key
inner join dates on fact.date_key = dates.date_key
