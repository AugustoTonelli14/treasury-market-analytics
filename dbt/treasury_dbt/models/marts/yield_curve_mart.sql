with yields as (
    select * from {{ ref('stg_yield_curve') }}
),

-- One row per date: pivot the long (series, date) staging grain to wide
-- so the 2Y-10Y spread can be computed across maturities.
pivoted as (
    select
        rate_date,
        max(case when maturity = '1M' then value end) as yield_1m,
        max(case when maturity = '2Y' then value end) as yield_2y,
        max(case when maturity = '10Y' then value end) as yield_10y
    from yields
    group by rate_date
)

select
    rate_date,
    yield_1m,
    yield_2y,
    yield_10y,
    yield_10y - yield_2y as spread_2y_10y,
    (yield_10y - yield_2y) < 0 as is_inverted
from pivoted
order by rate_date
