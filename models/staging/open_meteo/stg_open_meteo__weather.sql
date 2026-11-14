-- Grain: one row per hour (Open-Meteo hourly temperature for Paris).
-- Unique key: observed_at.
-- Staging only renames and casts. No filtering or business logic here.

with source as (

    select * from {{ source('raw', 'weather') }}

),

renamed as (

    select
        -- The raw column is a timestamp WITHOUT timezone, but the file header says
        -- timezone = GMT (utc_offset_seconds = 0). `at time zone 'UTC'` tells DuckDB
        -- to read that naive value as UTC and return a timestamptz, the same type
        -- as observed_at in stg_eco2mix__load, so both sides mean the same instant.
        "time" at time zone 'UTC'   as observed_at,

        -- Unit in the name, no special characters to quote downstream.
        "temperature_2m (°C)"       as temperature_c

    from source

)

select * from renamed
