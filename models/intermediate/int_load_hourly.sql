-- Grain: one row per UTC hour, gap-free, from the first to the last hour with data.
-- Unique key: hour_utc.
-- Purpose: give downstream models a complete hourly series so joins to weather
-- (also hourly UTC) and time-series logic never meet missing hours.

with readings as (

    -- Step 1: move from 15-minute slots to hours.
    -- Convert to UTC first: local time has 23/25-hour days, UTC never does.
    -- Average the half-hourly readings (:00 and :30) that fall in each hour.
    -- Null slots (:15/:45) are ignored by avg() and filtered out explicitly.
    select
        date_trunc('hour', observed_at at time zone 'UTC') as hour_utc,
        avg(consumption_mw)                                as consumption_mw
    from {{ ref('stg_eco2mix__load') }}
    where consumption_mw is not null
    group by 1

),

bounds as (

    select min(hour_utc) as first_hour, max(hour_utc) as last_hour
    from readings

),

spine as (

    -- Step 2: the time spine. Every hour between the bounds, generated, not observed.
    -- Because it does not depend on the source rows, a missing hour in the source
    -- still shows up here as a row (with a null value to fill).
    select hour_utc
    from bounds, generate_series(first_hour, last_hour, interval 1 hour) as t(hour_utc)

),

joined as (

    -- Step 3: left join so that every spine hour survives, whether or not it has data.
    select
        spine.hour_utc,
        readings.consumption_mw
    from spine
    left join readings using (hour_utc)

),

neighbours as (

    -- Step 4: for each row find the nearest known value before and after it.
    -- IGNORE NULLS makes the window skip empty hours, so this also works if a
    -- gap ever spans several hours.
    select
        hour_utc,
        consumption_mw,
        last_value(consumption_mw ignore nulls) over before as prev_mw,
        last_value(case when consumption_mw is not null then hour_utc end ignore nulls)
            over before                                     as prev_hour,
        first_value(consumption_mw ignore nulls) over after as next_mw,
        first_value(case when consumption_mw is not null then hour_utc end ignore nulls)
            over after                                      as next_hour
    from joined
    window
        before as (order by hour_utc rows between unbounded preceding and current row),
        after  as (order by hour_utc rows between current row and unbounded following)

),

interpolated as (

    -- Step 5: fill gaps on the straight line between the two neighbours:
    --   prev + (next - prev) * (time since prev / time between prev and next)
    -- For a single missing hour the weight is 0.5, i.e. the midpoint.
    select
        hour_utc,
        coalesce(
            consumption_mw,
            prev_mw + (next_mw - prev_mw)
                * date_diff('second', prev_hour, hour_utc)
                / date_diff('second', prev_hour, next_hour)
        )                          as consumption_mw,
        consumption_mw is null     as is_imputed   -- Step 6: never hide that we invented a value
    from neighbours

)

select * from interpolated
