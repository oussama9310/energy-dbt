"""Chart: hourly electricity consumption vs temperature, from the dbt mart.

Usage:  .venv/bin/python analysis/plot_consumption_vs_temperature.py [path/to/energy.duckdb]
Needs:  close any duckdb shell first (DuckDB allows one process on the file).
"""
import sys
from pathlib import Path

import duckdb
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

DB = sys.argv[1] if len(sys.argv) > 1 else "energy.duckdb"
OUT = Path(__file__).with_name("consumption_vs_temperature.png")

# --- 1. Load data from the mart --------------------------------------------
# read_only=True: we only read, so we cannot damage the database by accident.
con = duckdb.connect(DB, read_only=True)
df = con.sql(
    "select temperature_c, consumption_mw from main_marts.fct_energy_hourly"
).df()
con.close()
df["consumption_gw"] = df["consumption_mw"] / 1000  # GW is easier to read than MW

# --- 2. Average consumption per 1 degree bin --------------------------------
# Bins with few hours give noisy averages, so we only draw bins with >= 30 hours.
df["temp_bin"] = np.floor(df["temperature_c"])
by_bin = df.groupby("temp_bin")["consumption_gw"].agg(["mean", "count"])
by_bin = by_bin[by_bin["count"] >= 30]
x_mean = by_bin.index.to_numpy() + 0.5  # centre of each 1-degree bin

# --- 3. Style: colours by role (reference palette, light mode) --------------
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8a8984", "#e6e5e1"
ACCENT = "#eb6834"  # categorical slot 2 (orange): the one line that is the story
# Sequential single-hue ramp (blue 100 -> 700): darker = more hours.
ramp = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
cmap = LinearSegmentedColormap.from_list("blue_seq", ramp)

fig, ax = plt.subplots(figsize=(10, 6), dpi=160, facecolor=SURFACE)
ax.set_facecolor(SURFACE)

# --- 4. Density (hexbin) instead of 35k overlapping dots ---------------------
# Each hexagon counts the hours that fall in it; bins='log' keeps sparse extremes
# visible next to the very dense middle.
hb = ax.hexbin(
    df["temperature_c"], df["consumption_gw"],
    gridsize=45, mincnt=1, bins="log", cmap=cmap, linewidths=0.2, edgecolors=SURFACE,
)

# --- 5. The emphasised line: average consumption by temperature --------------
ax.plot(x_mean, by_bin["mean"], color=ACCENT, linewidth=2.5, solid_capstyle="round", zorder=3)
# Direct label (no legend needed: one line, labelled on the line).
# Placed in the empty upper-right area so it never sits on top of the data.
i = int(np.argmin(np.abs(x_mean - 27)))  # point of the line to label (around 27 °C)
ax.annotate(
    "Average consumption\nper 1 °C of temperature",
    xy=(x_mean[i], by_bin["mean"].iloc[i]), xytext=(24, 68), textcoords="data",
    color=INK, fontsize=10, fontweight="bold", ha="left", va="center",
    arrowprops=dict(arrowstyle="-", color=INK_2, linewidth=1),
)

# --- 6. Anatomy: titles, axes, grid ------------------------------------------
ax.set_title(
    "France uses more electricity when it is cold, and a little more when it is very hot",
    loc="left", fontsize=14, fontweight="bold", color=INK, pad=28,
)
ax.text(
    0, 1.02,
    f"Hourly consumption vs Paris temperature, 2022–2025 ({len(df):,} hours). "
    "Darker = more hours.",
    transform=ax.transAxes, fontsize=10, color=INK_2,
)
ax.set_xlabel("Temperature in Paris (°C)", color=INK_2, fontsize=11)
ax.set_ylabel("National consumption (GW)", color=INK_2, fontsize=11)
ax.tick_params(colors=INK_2, length=0)
ax.grid(color=GRID, linewidth=0.8)
ax.set_axisbelow(True)
for side in ("top", "right", "left", "bottom"):
    ax.spines[side].set_visible(False)

cb = fig.colorbar(hb, ax=ax, pad=0.02, fraction=0.04)
cb.set_label("Hours per cell (log scale)", color=INK_2, fontsize=10)
cb.ax.tick_params(colors=INK_2, length=0)
cb.outline.set_visible(False)

fig.text(0.01, 0.01, "Source: RTE eco2mix, Open-Meteo. Table: main_marts.fct_energy_hourly",
         color=MUTED, fontsize=8)
fig.tight_layout()
fig.savefig(OUT, facecolor=SURFACE)
print(f"saved {OUT}")
