select
    customer_id::integer as customer_id,
    trim(customer_name) as customer_name,
    lower(trim(email)) as email,
    phone,
    segment,
    country,
    upper(region) as region,
    signup_date
from {{ ref('raw_customers') }}
