with fx as (
    select * from {{ ref('stg_fx_rates') }}
),

with_returns as (
    select
        *,
        value / lag(value) over (
            partition by series_id order by rate_date
        ) - 1 as daily_return
    from fx
)

select
    fx_rate_id,
    rate_date,
    series_id,
    series_name,
    currency,
    source,
    value as spot_rate,
    change_1d,
    change_1w,
    change_1m,
    daily_return,
    -- 30 trading days is the common window for short-term FX volatility.
    stddev_samp(daily_return) over (
        partition by series_id order by rate_date
        rows between 29 preceding and current row
    ) as rolling_volatility_30d
from with_returns
