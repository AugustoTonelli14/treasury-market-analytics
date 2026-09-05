with fx as (
    select * from {{ ref('stg_fx_rates') }}
),

with_returns as (
    select
        *,
        value / nullif(lag(value) over (
            partition by series_id order by rate_date
        ), 0) - 1 as daily_return
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
    -- stddev_samp ignores nulls, so without this guard it would silently
    -- compute a "30-day" volatility off as few as 2 daily_return values
    -- near the start of each series.
    case
        when count(daily_return) over (
            partition by series_id order by rate_date
            rows between 29 preceding and current row
        ) >= 30
        then stddev_samp(daily_return) over (
            partition by series_id order by rate_date
            rows between 29 preceding and current row
        )
    end as rolling_volatility_30d
from with_returns
