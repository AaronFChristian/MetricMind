-- ============================================================================
-- mart_revenue_metrics: Core SaaS revenue metrics
--
-- CONCEPT:
--   Answers all revenue questions: MRR, ARR, churn, CAC, LTV, LTV:CAC.
--   One row per (month x plan x country x acquisition_channel).
--
-- INPUTS:  stg_subscriptions, stg_payments, stg_users
-- OUTPUT:  main_marts.mart_revenue_metrics
--
-- DuckDB notes:
--   - INTERVAL '1 month' for date arithmetic (no dateadd)
--   - No subqueries in JOIN ON clauses (use CTEs instead)
--   - current_localtimestamp() not current_timestamp()
-- ============================================================================

{{
    config(
        materialized='table',
        description='SaaS revenue KPIs by month and dimension.'
    )
}}

with

payment_months as (
    select distinct payment_month as calendar_month
    from {{ ref('stg_payments') }}
),

active_subs_monthly as (
    select
        pm.calendar_month                               as metric_month,
        s.subscription_id,
        s.user_id,
        s.plan,
        s.monthly_recurring_revenue,
        coalesce(u.country, 'unknown')                 as country,
        coalesce(u.acquisition_channel, 'unknown')     as acquisition_channel
    from {{ ref('stg_subscriptions') }} s
    inner join payment_months pm
        on  pm.calendar_month >= s.started_at
        and (s.ended_at is null or pm.calendar_month < s.ended_at)
    left join {{ ref('stg_users') }} u on s.user_id = u.user_id
    where s.monthly_recurring_revenue > 0
),

mrr_monthly as (
    select
        metric_month,
        plan,
        country,
        acquisition_channel,
        count(distinct user_id)                         as paying_customers,
        sum(monthly_recurring_revenue)                  as mrr,
        sum(monthly_recurring_revenue) * 12             as arr,
        avg(monthly_recurring_revenue)                  as arpu
    from active_subs_monthly
    group by 1, 2, 3, 4
),

-- Users active this month (for anti-join churn calculation)
active_users_this_month as (
    select distinct metric_month, user_id, plan, country
    from active_subs_monthly
),

-- Users active last month
active_users_last_month as (
    select
        (metric_month + INTERVAL '1 month') as next_month,
        user_id,
        plan,
        country,
        monthly_recurring_revenue
    from active_subs_monthly
),

-- Churned: active last month but NOT active this month
churned_users as (
    select
        lm.next_month                                   as metric_month,
        lm.plan,
        lm.country,
        lm.user_id,
        lm.monthly_recurring_revenue
    from active_users_last_month lm
    left join active_users_this_month tm
        on  lm.next_month   = tm.metric_month
        and lm.user_id      = tm.user_id
        and lm.plan         = tm.plan
        and lm.country      = tm.country
    where tm.user_id is null   -- anti-join: no match this month = churned
),

churned_monthly as (
    select
        metric_month,
        plan,
        country,
        count(distinct user_id)                         as churned_customers,
        sum(monthly_recurring_revenue)                  as churned_mrr
    from churned_users
    group by 1, 2, 3
),

payments_monthly as (
    select
        payment_month                                   as metric_month,
        sum(case when not is_suspected_dirty
                 then net_amount_usd end)               as net_revenue,
        sum(case when is_suspected_dirty
                 then 1 else 0 end)                     as dirty_payment_rows
    from {{ ref('stg_payments') }}
    where is_usd = true
    group by 1
),

cac_by_channel as (
    select
        date_trunc('month', signup_date)               as cohort_month,
        acquisition_channel,
        count(distinct user_id)                         as new_customers,
        sum(case acquisition_channel
            when 'paid_search' then 85
            when 'referral'    then 25
            when 'email'       then 15
            when 'organic'     then 10
            else 30
        end) * 1.0
        / nullif(count(distinct user_id), 0)            as estimated_cac
    from {{ ref('stg_users') }}
    where user_id is not null
    group by 1, 2
),

final as (
    select
        m.metric_month,
        m.plan,
        m.country,
        m.acquisition_channel,
        m.paying_customers,
        round(m.mrr,  2)                                as mrr,
        round(m.arr,  2)                                as arr,
        round(m.arpu, 2)                                as arpu,

        coalesce(c.churned_customers, 0)                as churned_customers,
        round(coalesce(c.churned_mrr, 0), 2)            as churned_mrr,
        round(coalesce(c.churned_customers,0)*100.0
              / nullif(m.paying_customers,0), 2)        as churn_rate_pct,

        round(coalesce(cac.estimated_cac, 0), 2)        as estimated_cac,

        round(
            m.arpu
            / nullif(coalesce(c.churned_customers,0)*1.0
                     / nullif(m.paying_customers,0), 0)
            * {{ var('ltv_margin_assumption') }},
        2)                                              as estimated_ltv,

        round(
            (m.arpu
             / nullif(coalesce(c.churned_customers,0)*1.0
                      / nullif(m.paying_customers,0), 0)
             * {{ var('ltv_margin_assumption') }})
            / nullif(coalesce(cac.estimated_cac,0), 0),
        2)                                              as ltv_to_cac_ratio,

        round(coalesce(p.net_revenue, 0), 2)            as net_revenue,
        coalesce(p.dirty_payment_rows, 0)               as dirty_payment_rows,
        current_localtimestamp()                        as _computed_at

    from mrr_monthly m
    left join churned_monthly c
        on  m.metric_month        = c.metric_month
        and m.plan                = c.plan
        and m.country             = c.country
    left join cac_by_channel cac
        on  m.metric_month        = cac.cohort_month
        and m.acquisition_channel = cac.acquisition_channel
    left join payments_monthly p
        on  m.metric_month        = p.metric_month
)

select * from final
order by metric_month desc, mrr desc
