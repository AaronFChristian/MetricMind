-- ============================================================================
-- stg_payments: Clean payment records
--
-- Key decisions:
--   - Negative amounts with refund_flag=False are flagged via is_suspected_dirty
--     instead of filtered — keeps the audit trail, surfaces the issue in tests
--   - Non-USD currencies noted via is_usd flag; downstream marts filter to USD
--     unless explicitly doing multi-currency analysis
--   - net_amount_usd is the business-correct amount after refund sign correction
-- ============================================================================

with source as (
    select * from {{ ref('raw_payments') }}
),

renamed as (
    select
        payment_id,
        user_id,
        subscription_id,

        cast(amount_usd as decimal(10, 2))                  as amount_usd,

        upper(trim(currency))                               as currency,
        currency = 'USD'                                    as is_usd,

        -- Cast boolean — different sources send True/False vs 1/0 vs 'true'/'false'
        cast(refund_flag as boolean)                        as is_refund,

        cast(payment_date as date)                          as payment_date,
        date_trunc('month', cast(payment_date as date))     as payment_month,
        date_trunc('week',  cast(payment_date as date))     as payment_week,

        lower(trim(payment_method))                         as payment_method,

        -- ── Derived business columns ──────────────────────────────────────

        -- Net amount: refunds are negative; if refund_flag is True we ensure
        -- the sign is negative regardless of source
        case
            when cast(refund_flag as boolean) = true
            then -abs(cast(amount_usd as decimal(10,2)))
            else cast(amount_usd as decimal(10,2))
        end                                                 as net_amount_usd,

        -- Data quality flag: negative amount WITHOUT a refund flag = dirty row
        -- These rows will be caught by the test_payments_refund_consistency test
        cast(amount_usd as decimal(10,2)) < 0
            and cast(refund_flag as boolean) = false        as is_suspected_dirty,

        current_localtimestamp()                                 as _loaded_at,
        'raw_payments'                                      as _source

    from source
)

select * from renamed
