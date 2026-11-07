{{ config(post_hook="{{ govern(row_policy='region', pii=['customer_name', 'email', 'phone']) }}") }}

select
    customer_id,
    customer_name,
    email,
    phone,
    segment,
    country,
    region,
    signup_date
from {{ ref('stg_customers') }}
