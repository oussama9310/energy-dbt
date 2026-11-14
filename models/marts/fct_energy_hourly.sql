-- Grain: one row per UTC hour with BOTH consumption and temperature.
-- Unique key: hour_utc.
-- Purpose: one table to study how weather relates to electricity consumption.

with load as (

    select
        hour_utc,
        consumption_mw,
        is_imputed
    from {{ ref('int_load_hourly') }}

),

weather as (

    -- int_load_hourly uses a plain UTC timestamp (hour_utc); staging weather uses
    -- timestamptz. Convert the weather side so both keys have the same type,
    -- otherwise the join could silently match on the wrong instants.
    select
        observed_at at time zone 'UTC' as hour_utc,
        temperature_c
    from {{ ref('stg_open_meteo__weather') }}

),

joined as (

    -- INNER join: keep only hours present on both sides (2022-2025). Load history
    -- before 2022 has no temperature, so it is deliberately left out of this mart.
    select
        load.hour_utc,
        load.consumption_mw,
        load.is_imputed,
        weather.temperature_c
    from load
    inner join weather using (hour_utc)

),

enriched as (

    select
        *,

        -- Calendar features in PARIS local time: people heat, cook and commute on
        -- the local clock, not on UTC. UTC -> Paris handles summer/winter time.
        extract(hour    from local_ts)  as local_hour,
        extract(isodow  from local_ts)  as local_day_of_week,   -- 1 = Monday ... 7 = Sunday
        extract(month   from local_ts)  as local_month,
        extract(isodow  from local_ts) >= 6 as is_weekend,

        -- Heating degrees: how far below 18 C it is (0 when warmer). 18 C is a
        -- common convention for the point where heating starts, an assumption you
        -- can change. Consumption should rise as this grows.
        greatest(18 - temperature_c, 0) as heating_degrees_c

    from (
        select
            *,
            timezone('Europe/Paris', hour_utc at time zone 'UTC') as local_ts
        from joined
    )

)

select
    hour_utc,
    consumption_mw,
    is_imputed,
    temperature_c,
    local_hour,
    local_day_of_week,
    local_month,
    is_weekend,
    heating_degrees_c
from enriched
