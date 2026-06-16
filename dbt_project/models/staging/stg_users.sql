-- ============================================================================
-- stg_users: Clean, typed, documented user records
--
-- What this model does:
--   1. Renames columns to consistent snake_case
--   2. Casts every column to its correct type explicitly
--   3. Applies coalesce/defaults for nullable business fields
--   4. Adds metadata columns (_loaded_at, _source)
--   5. DOES NOT filter dirty rows — we surface them in tests so the data
--      team can fix the source, not silently hide the problem
--
-- Dirty data that tests will catch:
--   - null user_id (~3% of rows)
--   - invalid email format (~2%)
--   - future signup_date (~1%)
-- ============================================================================

with source as (
    -- Reference the raw seed directly.
    -- In production this would point to a source() declaration e.g. the Stripe users table.
    select * from {{ ref('raw_users') }}
),

renamed as (
    select
        -- Primary key — nullable rows are surfaced by not_null test
        user_id,

        -- Normalise email to lowercase for consistent matching
        lower(trim(email))                                  as email,

        -- Cast date explicitly; future dates caught by a custom test
        cast(signup_date as date)                           as signup_date,

        -- Plan with a sensible fallback so dashboards don't break
        coalesce(lower(trim(plan)), 'unknown')              as plan,

        -- Acquisition channel: map legacy values forward
        case
            when lower(trim(acquisition_channel)) = 'seo' then 'organic'
            else coalesce(lower(trim(acquisition_channel)), 'unknown')
        end                                                 as acquisition_channel,

        -- ISO 3166-1 alpha-2 country code, uppercased
        upper(trim(country))                                as country,

        -- Company size bucket (used as a dimension in metric queries)
        coalesce(company_size, 'unknown')                   as company_size,

        -- ── Metadata ──────────────────────────────────────────────────────
        -- current_localtimestamp() works in both DuckDB and Snowflake
        current_localtimestamp()                                 as _loaded_at,
        'raw_users'                                         as _source

    from source
)

select * from renamed
