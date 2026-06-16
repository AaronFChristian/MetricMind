-- ============================================================================
-- test_users_no_future_signups.sql
--
-- Custom dbt singular test: fails if any user has a signup_date in the future.
-- Causes: clock skew in the source system, data entry errors, test accounts.
--
-- Expected result: 0 rows
-- ============================================================================

select
    user_id,
    email,
    signup_date,
    current_date()          as today,
    'signup_date_in_future' as failure_reason

from {{ ref('stg_users') }}

where signup_date > current_date()
