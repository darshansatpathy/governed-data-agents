{{ config(post_hook="{{ govern(row_policy='region') }}") }}

-- One row per order line. Region is carried from the customer so that row-level security
-- applies to the fact table without a join.
select
    i.order_id,
    i.line_number,
    o.order_date,
    o.customer_id,
    i.product_id,
    c.region,
    o.channel,
    o.status,
    i.quantity,
    i.unit_price,
    round(i.quantity * i.unit_price, 2) as gross_amount,
    round(i.quantity * i.unit_price * i.discount_pct, 2) as discount_amount,
    round(i.quantity * i.unit_price, 2) - round(i.quantity * i.unit_price * i.discount_pct, 2) as net_amount
from {{ ref('stg_order_items') }} as i
join {{ ref('stg_orders') }} as o on o.order_id = i.order_id
join {{ ref('stg_customers') }} as c on c.customer_id = o.customer_id
