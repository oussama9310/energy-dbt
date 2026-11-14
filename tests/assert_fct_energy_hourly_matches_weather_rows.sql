-- Singular test: returns a row only on failure.
-- The load history (2012-2026) fully covers the weather period (2022-2025), so an
-- inner join must keep EVERY weather hour, exactly once. A different count means
-- the join dropped or duplicated hours.
select m.n as mart_rows, w.n as weather_rows
from (select count(*) as n from {{ ref('fct_energy_hourly') }}) m
cross join (select count(*) as n from {{ ref('stg_open_meteo__weather') }}) w
where m.n <> w.n
