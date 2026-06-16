-- ============================================================================
-- mart_funnel_analysis: Signup → Activation → Payment → 30-day Retention
--
-- A funnel tracks what % of users reach each stage.
-- This mart is used for:
--   1. Product funnel dashboard (where do users drop off?)
--   2. A/B test analysis (does variant A improve activation rate?)
--   3. Channel quality analysis (which channels produce highest-LTV users?)
--
-- Funnel stages:
--   Stage 1 — Signed up (all users; denominator)
--   Stage 2 — Activated (had at least 1 activity event within 7 days of signup)
--   Stage 3 — Converted (made at least 1 payment)
--   Stage 4 — Retained (active again 30 days after signup)
--
-- One row per (cohort_month, plan, country, acquisition_channel)
-- ============================================================================

{{
    config(
        materialized='table',
        description='Signup-to-retention funnel by cohort and dimension.'
    )
}}

with

-- Base: all users
users as (
    select
        user_id,
        signup_date,
        date_trunc('month', signup_date)        as cohort_month,
        plan,
        country,
        acquisition_channel
    from {{ ref('stg_users') }}
    where user_id is not null
),

-- Stage 2: Activated = at least 1 activity event within 7 days of signup
activations as (
    select distinct
        e.user_id
    from {{ ref('stg_events') }} e
    inner join users u using (user_id)
    where
        e.is_activity_event = true
        and e.event_date <= ( u.signup_date)
        and e.event_date >= u.signup_date
),

-- Stage 3: Converted = at least 1 non-dirty payment
conversions as (
    select distinct
        p.user_id
    from {{ ref('stg_payments') }} p
    where
        p.net_amount_usd > 0
        and p.is_suspected_dirty = false
        and p.is_usd = true
),

-- Stage 4: Retained at day 30 = activity event between day 28 and day 35
-- (7-day window to account for users who are "monthly" but slightly off-schedule)
retained_day30 as (
    select distinct
        e.user_id
    from {{ ref('stg_events') }} e
    inner join users u using (user_id)
    where
        e.is_activity_event = true
        and e.event_date >= ( u.signup_date)
        and e.event_date <= ( u.signup_date)
),

-- Assemble funnel flags per user
user_funnel as (
    select
        u.user_id,
        u.cohort_month,
        u.plan,
        u.country,
        u.acquisition_channel,
        u.signup_date,

        -- Stage flags (true/false per user)
        true                                                as reached_signup,
        (a.user_id is not null)                             as reached_activation,
        (c.user_id is not null)                             as reached_conversion,
        (r.user_id is not null)                             as reached_day30_retention

    from users u
    left join activations     a using (user_id)
    left join conversions     c using (user_id)
    left join retained_day30  r using (user_id)
),

-- Aggregate to cohort × dimension grain
funnel_aggregated as (
    select
        cohort_month,
        plan,
        country,
        acquisition_channel,

        -- Volume at each stage
        count(*)                                            as signups,
        sum(case when reached_activation      then 1 end)  as activations,
        sum(case when reached_conversion      then 1 end)  as conversions,
        sum(case when reached_day30_retention then 1 end)  as day30_retained,

        -- Conversion rates (relative to signups = "absolute funnel rate")
        round(sum(case when reached_activation      then 1 end) * 100.0
              / nullif(count(*), 0), 2)                    as activation_rate_pct,
        round(sum(case when reached_conversion      then 1 end) * 100.0
              / nullif(count(*), 0), 2)                    as signup_to_paid_rate_pct,
        round(sum(case when reached_day30_retention then 1 end) * 100.0
              / nullif(count(*), 0), 2)                    as day30_retention_rate_pct,

        -- Step-by-step drop-off (relative to previous stage)
        round(sum(case when reached_conversion then 1 end) * 100.0
              / nullif(sum(case when reached_activation then 1 end), 0), 2)
                                                           as activation_to_paid_rate_pct,
        round(sum(case when reached_day30_retention then 1 end) * 100.0
              / nullif(sum(case when reached_conversion then 1 end), 0), 2)
                                                           as paid_to_retained_rate_pct

    from user_funnel
    group by 1, 2, 3, 4
)

select
    *,
    current_localtimestamp()                                     as _computed_at
from funnel_aggregated
order by cohort_month desc, signups desc
