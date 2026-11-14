-- Singular test: returns rows only on failure.
-- Raw weather is expected to be gap-free. If a future reload drops an hour,
-- we want to know here, not as a silent null after the join in the mart.

select observed_at as problem, 'gap after this hour' as reason
from (
    select observed_at, lead(observed_at) over (order by observed_at) as next_hour
    from {{ ref('stg_open_meteo__weather') }}
)
where next_hour is not null
  and next_hour <> observed_at + interval 1 hour
