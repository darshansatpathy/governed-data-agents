select
    order_id::integer as order_id,
    line_number::integer as line_number,
    product_id::integer as product_id,
    quantity::integer as quantity,
    unit_price,
    discount_pct
from {{ ref('raw_order_items') }}
