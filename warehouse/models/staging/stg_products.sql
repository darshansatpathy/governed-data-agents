select
    product_id::integer as product_id,
    product_name,
    category,
    list_price
from {{ ref('raw_products') }}
