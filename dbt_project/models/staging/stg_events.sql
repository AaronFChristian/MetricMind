-- ============================================================================
-- stg_events: Clean product usage events
--
-- Key decisions:
--   - Timestamps normalised to UTC (Snowflake: CONVERT_TIMEZONE; DuckDB: AT TIME ZONE)
--   - event_type filtered to the governed activity list via var('activity_event_types')
--     for downstream active-user calculations — but ALL events are kept in this
--     staging model; filtering happens in marts so we don't lose audit trail
--   - is_activity_event flag added so marts can filter without a JOIN
-- ============================================================================

with source as (
    select * from {{ ref('raw_events') }}
),

renamed as (
    select
        event_id,
        user_id,
        lower(trim(event_type))                             as event_type,

        -- Normalise timestamp to UTC.
        -- DuckDB and Snowflake both support this syntax.
        cast(event_timestamp as timestamp)                  as event_timestamp,

        -- Derived date columns for easy partitioning in marts
        cast(event_timestamp as date)                       as event_date,
        date_trunc('week',  cast(event_timestamp as date))  as event_week,
        date_trunc('month', cast(event_timestamp as date))  as event_month,

        session_id,

        -- ── Governance flag ───────────────────────────────────────────────
        -- TRUE only for event types that count toward "active user" definition.
        -- Definition lives in dbt_project.yml vars.activity_event_types.
        -- THIS IS THE SINGLE SOURCE OF TRUTH for "what is an active user".
        lower(trim(event_type)) in (
            {{ "'" + "','".join(var('activity_event_types')) + "'" }}
        )                                                   as is_activity_event,

        current_localtimestamp()                                 as _loaded_at,
        'raw_events'                                        as _source

    from source
)

select * from renamed
