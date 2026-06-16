-- ============================================================================
-- mart_cohort_retention: Cohort-based retention analysis
--
-- Output: one row per (cohort_month, retention_period) combination.
-- This is the data behind the classic retention heatmap.
--
-- Retention definition:
--   A user is "retained" in period N if they had at least one governed activity
--   event in the Nth month after their signup month.
--
-- Key design note:
--   period_0 = signup month (should be ~100% — sanity check)
--   period_1 = month 1 after signup (first real retention data point)
--   period_N for N > max observed = null (not yet happened)
-- ============================================================================

{{
    config(
        materialized='table',
        description='Monthly cohort retention. One row per cohort_month × retention_period.'
    )
}}

with

-- Step 1: Assign each user to a cohort month (their signup month)
user_cohorts as (
    select
        user_id,
        date_trunc('month', signup_date)        as cohort_month,
        signup_date,
        plan,
        country,
        acquisition_channel
    from {{ ref('stg_users') }}
    where user_id is not null
),

-- Step 2: Get all months where each user was active
user_active_months as (
    select
        user_id,
        date_trunc('month', event_date)         as active_month
    from {{ ref('stg_events') }}
    where
        user_id is not null
        and is_activity_event = true
    group by 1, 2
),

-- Step 3: Cross-join to get (cohort_month, active_month) pairs
cohort_activity as (
    select
        c.user_id,
        c.cohort_month,
        c.plan,
        c.country,
        c.acquisition_channel,
        a.active_month,

        -- Retention period: 0 = signup month, 1 = next month, etc.
        -- datediff in months may vary slightly by warehouse, so we
        -- use integer arithmetic on year*12+month for portability
        ( (year(a.active_month) * 12 + month(a.active_month))
        - (year(c.cohort_month) * 12 + month(c.cohort_month)) )
                                                as retention_period

    from user_cohorts c
    inner join user_active_months a using (user_id)
    where a.active_month >= c.cohort_month      -- no activity before signup
),

-- Step 4: Cohort sizes (denominator for retention rate)
cohort_sizes as (
    select
        cohort_month,
        plan,
        country,
        count(distinct user_id)                 as cohort_size
    from user_cohorts
    group by 1, 2, 3
),

-- Step 5: Retained users per period
retained_users as (
    select
        cohort_month,
        retention_period,
        plan,
        country,
        count(distinct user_id)                 as retained_users
    from cohort_activity
    group by 1, 2, 3, 4
),

-- Step 6: Join sizes and compute rates
final as (
    select
        r.cohort_month,
        r.retention_period,
        r.plan,
        r.country,
        s.cohort_size,
        r.retained_users,

        -- Retention rate: what % of the cohort was active in this period
        round(
            r.retained_users * 100.0 / nullif(s.cohort_size, 0),
            2
        )                                       as retention_rate_pct,

        -- Absolute churn from the cohort (vs prior period, not vs period_0)
        s.cohort_size - r.retained_users        as churned_users,

        current_localtimestamp()                     as _computed_at

    from retained_users r
    inner join cohort_sizes s
        on  r.cohort_month = s.cohort_month
        and r.plan         = s.plan
        and r.country      = s.country
)

select * from final
order by cohort_month desc, retention_period asc
