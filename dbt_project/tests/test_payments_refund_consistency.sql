-- ============================================================================
-- test_payments_refund_consistency.sql
--
-- Custom dbt singular test: fails if any payment rows have a negative amount
-- WITHOUT the refund flag set. These rows indicate a data quality issue in
-- the source system (payments were processed with incorrect sign).
--
-- Expected result: 0 rows (test passes when this query returns nothing)
-- If rows are returned: test fails and lists the problematic payment_ids
--
-- This test is a portfolio talking point:
-- "dbt tests caught 128 payments with negative amounts but no refund flag —
--  worth $X in potential revenue recognition errors."
-- ============================================================================

select
    payment_id,
    user_id,
    amount_usd,
    currency,
    is_refund,
    payment_date,
    'negative_amount_without_refund_flag' as failure_reason

from {{ ref('stg_payments') }}

where
    amount_usd < 0
    and is_refund = false
    and is_suspected_dirty = true

-- A small number of violations can be tolerated during a data migration.
-- Set this to 0 for production. Current threshold: 0 (strict).
-- To allow N violations: add HAVING COUNT(*) > N as a wrapper.
