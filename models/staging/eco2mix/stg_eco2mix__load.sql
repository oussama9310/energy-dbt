-- Grain: one row per 15-minute slot of the eco2mix file (phantom DST rows removed).
-- Unique key: observed_at.
-- Staging only renames, casts and fixes key problems. No business filtering here.

with source as (

    select * from {{ source('raw', 'load') }}

),

renamed as (

    select
        "Date et Heure"                              as observed_at,   -- timestamptz, the instant
        "Date"                                       as local_date,
        "Heure"                                      as local_time,
        "Périmètre"                                  as perimeter,
        "Nature"                                     as data_nature,
        "Consommation (MW)"                          as consumption_mw,
        "Prévision J-1 (MW)"                         as forecast_d1_mw,
        "Prévision J (MW)"                           as forecast_d_mw,
        "Fioul (MW)"                                 as oil_mw,
        "Charbon (MW)"                               as coal_mw,
        "Gaz (MW)"                                   as gas_mw,
        "Nucléaire (MW)"                             as nuclear_mw,
        "Eolien (MW)"                                as wind_mw,
        "Solaire (MW)"                               as solar_mw,
        "Hydraulique (MW)"                           as hydro_mw,
        "Pompage (MW)"                               as pumped_storage_mw,
        "Bioénergies (MW)"                           as bioenergy_mw,
        "Ech. physiques (MW)"                        as physical_exchanges_mw,
        "Taux de CO2 (g/kWh)"                        as co2_g_per_kwh,
        "Ech. comm. Angleterre (MW)"                 as exchange_england_mw,
        "Ech. comm. Espagne (MW)"                    as exchange_spain_mw,
        "Ech. comm. Italie (MW)"                     as exchange_italy_mw,
        "Ech. comm. Suisse (MW)"                     as exchange_switzerland_mw,
        "Ech. comm. Allemagne-Belgique (MW)"         as exchange_germany_belgium_mw
    from source

),

deduplicated as (

    -- On spring-forward days the source contains rows labelled 02:xx that do not
    -- exist on the clock and carry the 03:xx timestamp, colliding with the real rows.
    -- Keep the row whose local clock time agrees with its own timestamp.
    select *
    from renamed
    where cast(timezone('Europe/Paris', observed_at) as time) = local_time

)

select * from deduplicated
