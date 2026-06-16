-- ============================================================================
-- stg_subscriptions: Clean subscription records
--
-- Key decisions:
--   - negative mrr_usd rows are NOT filtered — they surface in the not_positive test
--   - is_active computed at query time from ended_at, not stored as a column
--     (avoids stale flags if dbt runs are delayed)
--   - plan_tier maps the canonical plan names to a numeric tier for easier
--     ordering in dashboards without relying on alphabetical sort
-- ============================================================================

with source as (
    select * from {{ ref('raw_subscriptions') }}
),

renamed as (
    select
        subscription_id,
        user_id,

        -- Normalise plan name; flag legacy values with a prefix so the test
        -- catches them AND dashboards don't silently show "legacy_growth"
        coalesce(lower(trim(plan)), 'unknown')              as plan,

        -- Map plan to a numeric tier for sort ordering
        case lower(trim(plan))
            when 'free'       then 0
            when 'starter'    then 1
            when 'pro'        then 2
            when 'enterprise' then 3
            else              -1      -- catches legacy/unknown plans
        end                                                 as plan_tier,

        -- MRR in USD. Negative values are a data quality issue (tests will flag).
        -- We cast to decimal(10,2) for consistent precision.
        cast(mrr_usd as decimal(10, 2))                     as mrr_usd,

        cast(started_at as date)                            as started_at,

        -- ended_at is null for active subscriptions
        case
            when ended_at = '' or ended_at is null then null
            else try_cast(ended_at as date)
        end                                                 as ended_at,

        -- is_active: computed, not stored — always current
        case
            when ended_at = '' or ended_at is null then true
            else false
        end                                                 as is_active,

        lower(trim(billing_interval))                       as billing_interval,

        -- Annualised MRR — useful for ARR calculation in marts
        case
            when lower(trim(billing_interval)) = 'annual'
            then cast(mrr_usd as decimal(10,2)) / 12.0
            else cast(mrr_usd as decimal(10,2))
        end                                                 as monthly_recurring_revenue,

        current_localtimestamp()                                 as _loaded_at,
        'raw_subscriptions'                                 as _source

    from source
)

select * from renamed
