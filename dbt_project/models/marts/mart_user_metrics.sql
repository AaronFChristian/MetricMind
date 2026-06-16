-- ============================================================================
-- mart_user_metrics: Daily, weekly, and monthly active users
--
-- This is the most queried mart. It powers the main KPI tiles.
--
-- Key design decisions:
--   1. "Active user" is defined ONCE in dbt_project.yml → stg_events.is_activity_event
--      This mart never redefines it. Any query that joins here gets the
--      governed definition automatically.
--   2. We build a date spine so days with zero activity still appear as rows
--      (instead of gaps) — prevents misleading "no data" in charts
--   3. Dimensions are denormalised in from stg_users for self-serve querying
--      without requiring the LLM agent to know how to JOIN
-- ============================================================================

{{
    config(
        materialized='table',
        description='Daily active users with governed activity definition. One row per user per day.'
    )
}}

with

-- All events that count as "activity"
active_events as (
    select
        user_id,
        event_date,
        event_week,
        event_month,
        count(*)                        as event_count
    from {{ ref('stg_events') }}
    where
        user_id is not null             -- exclude anonymous events
        and is_activity_event = true    -- governed activity filter
    group by 1, 2, 3, 4
),

-- User dimension — pulled once for denormalisation
users as (
    select
        user_id,
        plan,
        acquisition_channel,
        country,
        company_size,
        signup_date
    from {{ ref('stg_users') }}
    where user_id is not null
),

-- Join activity to user dimension
user_daily_activity as (
    select
        e.user_id,
        e.event_date,
        e.event_week,
        e.event_month,
        e.event_count,
        u.plan,
        u.acquisition_channel,
        u.country,
        u.company_size,
        u.signup_date,

        -- Days since signup: used for cohort analysis
        datediff('day', u.signup_date, e.event_date)    as days_since_signup,

        -- New user flag: active on or before day 7 of signup
        datediff('day', u.signup_date, e.event_date) <= 7 as is_new_user_activity

    from active_events e
    left join users u using (user_id)
),

-- Aggregate to daily metrics per dimension slice
daily_metrics as (
    select
        event_date                                          as metric_date,
        event_week,
        event_month,
        plan,
        country,
        acquisition_channel,

        -- Core active user counts
        count(distinct user_id)                             as daily_active_users,
        count(distinct
            case when is_new_user_activity then user_id end
        )                                                   as new_user_activations,
        sum(event_count)                                    as total_events,

        -- Rolling WAU and MAU computed from this same grain
        -- (The agent can query these directly without needing a self-join)
        count(distinct user_id)                             as dau  -- alias for clarity

    from user_daily_activity
    group by 1, 2, 3, 4, 5, 6
)

select
    metric_date,
    event_week,
    event_month,
    plan,
    country,
    acquisition_channel,
    daily_active_users,
    new_user_activations,
    total_events,
    dau,

    -- Metadata for freshness monitoring
    current_localtimestamp()                                     as _computed_at

from daily_metrics
order by metric_date desc
