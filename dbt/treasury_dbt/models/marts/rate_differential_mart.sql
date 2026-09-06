with rates as (
    select * from {{ ref('stg_interest_rates') }}
),

-- One row per date: pivot the long (series, date) staging grain to wide so
-- the USD-EUR policy rate differential can be computed. FEDFUNDS (Fed Funds
-- Rate) and ECB_DEPOSIT_RATE (ECB Deposit Facility Rate) are the two
-- overnight/policy rates in stg_interest_rates and the only pair comparable
-- in tenor; EURIBOR_3M/6M/12M are interbank term rates and are deliberately
-- excluded here to avoid differencing rates of different maturities.
pivoted as (
    select
        rate_date,
        max(case when series_id = 'FEDFUNDS' then value end) as usd_policy_rate,
        max(case when series_id = 'ECB_DEPOSIT_RATE' then value end) as eur_policy_rate
    from rates
    group by rate_date
)

select
    rate_date,
    usd_policy_rate,
    eur_policy_rate,
    usd_policy_rate - eur_policy_rate as usd_eur_differential
from pivoted
order by rate_date
