select
    order_id::integer as order_id,
    customer_id::integer as customer_id,
    order_date,
    channel,
    lower(status) as status
from {{ ref('raw_orders') }}
