-- Singular test: returns rows only on failure (dbt fails a test that returns rows).
-- Fails if any hour is missing from the spine, checked in two independent ways.

-- 1) Gap check: the next hour must be exactly one hour after the current one.
select hour_utc as problem, 'gap after this hour' as reason
from (
    select hour_utc, lead(hour_utc) over (order by hour_utc) as next_hour
    from {{ ref('int_load_hourly') }}
)
where next_hour is not null
  and next_hour <> hour_utc + interval 1 hour

union all

-- 2) Count check: rows must equal the number of hours between first and last.
select min_hour as problem, 'row count differs from expected hours' as reason
from (
    select min(hour_utc) as min_hour,
           count(*)      as n,
           date_diff('hour', min(hour_utc), max(hour_utc)) + 1 as expected
    from {{ ref('int_load_hourly') }}
)
where n <> expected
