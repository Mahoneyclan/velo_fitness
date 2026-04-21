#!/usr/bin/env python3
"""
dashboard.py
────────────
Interactive cycling fitness dashboard.

Automatically fetches fresh data from Strava/Garmin via extract.py on startup,
once per day. Subsequent runs the same day skip the fetch and load from cache.

Usage:
    python dashboard.py
    Then open  http://127.0.0.1:8050  in your browser.

Features:
    - Time range filter (3 months → all time)
    - Export HTML button — downloads a self-contained snapshot to ~/Downloads
"""

import json
from pathlib import Path

import extract
import dash
from dash import dcc, html, Input, Output, dash_table
import plotly.graph_objects as go
import plotly.express as px
import plotly.io as pio
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Load & prepare data
# ─────────────────────────────────────────────────────────────────────────────

DATA_FILE = Path(__file__).parent / "rides.json"


def load_rides() -> pd.DataFrame:
    if not DATA_FILE.exists():
        print(f"ERROR: {DATA_FILE} not found.")
        print("  Run  python extract.py  first.")
        return pd.DataFrame()

    with open(DATA_FILE) as f:
        raw = json.load(f)

    df = pd.DataFrame(raw)
    df["date"]          = pd.to_datetime(df["date"], format="mixed", utc=True).dt.tz_localize(None)
    df["week"]          = df["date"].dt.to_period("W").apply(lambda r: r.start_time)
    df["month"]         = df["date"].dt.to_period("M").apply(lambda r: r.start_time)
    df["year"]          = df["date"].dt.year
    df["moving_time_h"] = df["moving_time_s"] / 3600

    # Ensure numeric columns that may be None
    for col in ["avg_hr", "max_hr", "avg_watts", "max_watts",
                "avg_cadence", "suffer_score", "calories", "avg_speed_kmh",
                "elevation_m", "distance_km"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # ── Data quality filters ──
    # Keep indoor rides (distance_km == 0) if they have meaningful duration;
    # drop accidental starts (< 5 min) and outdoor rides shorter than 1 km
    df = df[(df["distance_km"] >= 1.0) | (df["moving_time_s"] >= 300)]
    df = df[df["moving_time_s"] >= 300]

    # Null out implausible values rather than dropping entire rides
    df.loc[df["avg_speed_kmh"] > 55, "avg_speed_kmh"] = None      # impossible avg speed
    df.loc[df["avg_watts"] > 600, "avg_watts"] = None              # impossible avg power
    df.loc[df["max_watts"] > 2500, "max_watts"] = None
    df.loc[df["avg_hr"] < 70, "avg_hr"] = None                    # sensor dropout
    df.loc[df["avg_hr"] > 220, "avg_hr"] = None
    df.loc[df["max_hr"] > 230, "max_hr"] = None
    df.loc[df["avg_cadence"] < 20, "avg_cadence"] = None           # cadence sensor not recording

    return df.sort_values("date").reset_index(drop=True)


from datetime import date
_last_run = date.fromtimestamp(DATA_FILE.stat().st_mtime) if DATA_FILE.exists() else None
if _last_run != date.today():
    extract.main()
DF = load_rides()

# ─────────────────────────────────────────────────────────────────────────────
# Design tokens
# ─────────────────────────────────────────────────────────────────────────────

DARK   = "#0d1117"
CARD   = "#161b22"
BORDER = "#30363d"
ORANGE = "#f97316"
TEAL   = "#22d3ee"
GREEN  = "#4ade80"
PURPLE = "#a78bfa"
PINK   = "#f472b6"
AMBER  = "#fbbf24"
MUTED  = "#8b949e"
TEXT   = "#e6edf3"
MONO   = "'IBM Plex Mono', 'Courier New', monospace"
DISPLAY = "'Bebas Neue', Impact, sans-serif"

BASE_LAYOUT = dict(
    paper_bgcolor=CARD,
    plot_bgcolor=CARD,
    font=dict(color=TEXT, family=MONO, size=11),
    margin=dict(l=48, r=24, t=48, b=40),
    xaxis=dict(gridcolor=BORDER, zeroline=False, tickfont=dict(size=10)),
    yaxis=dict(gridcolor=BORDER, zeroline=False, tickfont=dict(size=10)),
    legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=BORDER, font=dict(size=10)),
    hovermode="x unified",
    hoverlabel=dict(bgcolor=CARD, font_color=TEXT, bordercolor=BORDER),
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper components
# ─────────────────────────────────────────────────────────────────────────────

def fmt_time(hours: float) -> str:
    h = int(hours)
    m = int((hours - h) * 60)
    return f"{h}h {m:02d}m"


def stat_card(title: str, value: str, sub: str = "", color: str = ORANGE):
    return html.Div([
        html.P(title, style={
            "color": MUTED, "fontSize": "10px", "letterSpacing": "0.15em",
            "textTransform": "uppercase", "margin": "0 0 6px 0", "fontFamily": MONO,
        }),
        html.Div(value, style={
            "color": color, "fontSize": "1.9rem", "fontFamily": DISPLAY,
            "letterSpacing": "0.04em", "lineHeight": "1",
        }),
        html.P(sub, style={
            "color": MUTED, "fontSize": "10px", "margin": "6px 0 0 0", "fontFamily": MONO,
        }),
    ], style={
        "backgroundColor": CARD,
        "border": f"1px solid {BORDER}",
        "borderTop": f"3px solid {color}",
        "borderRadius": "6px",
        "padding": "18px 20px",
        "flex": "1",
        "minWidth": "150px",
    })


def section_head(text: str):
    return html.P(text, style={
        "color": ORANGE, "fontFamily": DISPLAY, "fontSize": "1.1rem",
        "letterSpacing": "0.12em", "margin": "0 0 10px 0",
    })


def card_wrap(*children, extra=None):
    style = {
        "backgroundColor": CARD, "border": f"1px solid {BORDER}",
        "borderRadius": "6px", "padding": "18px",
        "flex": "1", "minWidth": "380px",
    }
    if extra:
        style.update(extra)
    return html.Div(list(children), style=style)


def row(*children):
    return html.Div(list(children), style={
        "display": "flex", "gap": "14px", "flexWrap": "wrap",
        "marginBottom": "14px",
    })


def graph(fig):
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style={"height": "300px"})


# ─────────────────────────────────────────────────────────────────────────────
# Chart builders
# ─────────────────────────────────────────────────────────────────────────────

def chart_weekly_volume(df: pd.DataFrame) -> go.Figure:
    w = df.groupby("week").agg(
        distance_km=("distance_km", "sum"),
        moving_time_h=("moving_time_h", "sum"),
    ).reset_index()

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=w["week"], y=w["distance_km"],
        name="Distance (km)", marker_color=ORANGE, opacity=0.85,
    ))
    fig.add_trace(go.Scatter(
        x=w["week"], y=w["moving_time_h"],
        name="Time (h)", line=dict(color=TEAL, width=2),
        mode="lines", yaxis="y2",
    ))
    fig.update_layout(
        **BASE_LAYOUT,
        title="Weekly Distance & Riding Time",
        yaxis2=dict(overlaying="y", side="right", showgrid=False,
                    tickfont=dict(color=TEAL, size=10), title=dict(text="hours", font=dict(color=TEAL, size=10))),
    )
    return fig


def chart_monthly_volume(df: pd.DataFrame) -> go.Figure:
    m = df.groupby("month").agg(
        distance_km=("distance_km", "sum"),
        elevation_m=("elevation_m", "sum"),
        rides=("id", "count"),
    ).reset_index()

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=m["month"], y=m["distance_km"],
        name="Distance (km)", marker_color=ORANGE, opacity=0.85,
    ))
    fig.add_trace(go.Scatter(
        x=m["month"], y=m["elevation_m"],
        name="Elevation (m)", line=dict(color=GREEN, width=2),
        mode="lines+markers", marker=dict(size=4), yaxis="y2",
    ))
    fig.update_layout(
        **BASE_LAYOUT,
        title="Monthly Distance & Elevation",
        yaxis2=dict(overlaying="y", side="right", showgrid=False,
                    tickfont=dict(color=GREEN, size=10)),
    )
    return fig


def chart_speed_trend(df: pd.DataFrame) -> go.Figure:
    sdf = df.dropna(subset=["avg_speed_kmh"]).sort_values("date")
    rolling = sdf.set_index("date")["avg_speed_kmh"].rolling("28D").mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=sdf["date"], y=sdf["avg_speed_kmh"],
        mode="markers", name="Avg speed",
        marker=dict(color=TEAL, size=4, opacity=0.55),
    ))
    fig.add_trace(go.Scatter(
        x=rolling.index, y=rolling.values,
        mode="lines", name="28-day avg",
        line=dict(color=TEAL, width=2),
    ))
    fig.update_layout(**BASE_LAYOUT, title="Average Speed (km/h)")
    return fig


def chart_hr_trend(df: pd.DataFrame) -> go.Figure:
    hdf = df.dropna(subset=["avg_hr"]).sort_values("date")
    if hdf.empty:
        fig = go.Figure()
        fig.update_layout(**BASE_LAYOUT, title="Heart Rate — no HR data found")
        return fig

    rolling = hdf.set_index("date")["avg_hr"].rolling("28D").mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hdf["date"], y=hdf["avg_hr"],
        mode="markers", name="Avg HR",
        marker=dict(color=PINK, size=4, opacity=0.55),
    ))
    fig.add_trace(go.Scatter(
        x=rolling.index, y=rolling.values,
        mode="lines", name="28-day avg",
        line=dict(color=PINK, width=2),
    ))
    fig.update_layout(**BASE_LAYOUT, title="Average Heart Rate (bpm)")
    return fig


def chart_power_trend(df: pd.DataFrame) -> go.Figure:
    pdf = df.dropna(subset=["avg_watts"]).sort_values("date")
    if pdf.empty:
        fig = go.Figure()
        fig.add_annotation(text="No power data — power meter required",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           font=dict(color=MUTED, size=13), showarrow=False)
        fig.update_layout(**BASE_LAYOUT, title="Average Power (W)")
        return fig

    rolling = pdf.set_index("date")["avg_watts"].rolling("28D").mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=pdf["date"], y=pdf["avg_watts"],
        mode="markers", name="Avg power",
        marker=dict(color=PURPLE, size=4, opacity=0.55),
    ))
    fig.add_trace(go.Scatter(
        x=rolling.index, y=rolling.values,
        mode="lines", name="28-day avg",
        line=dict(color=PURPLE, width=2),
    ))
    fig.update_layout(**BASE_LAYOUT, title="Average Power (W)")
    return fig


def chart_elevation_scatter(df: pd.DataFrame) -> go.Figure:
    fig = px.scatter(
        df.sort_values("date"),
        x="distance_km", y="elevation_m",
        color="year",
        color_continuous_scale=[ORANGE, AMBER, GREEN, TEAL, PURPLE],
        hover_data=["name", "date", "avg_speed_kmh"],
        title="Distance vs Elevation",
    )
    fig.update_traces(marker=dict(size=5, opacity=0.7))
    fig.update_layout(**BASE_LAYOUT)
    return fig


def chart_training_load(df: pd.DataFrame) -> go.Figure:
    """
    Estimate Chronic Training Load (CTL ~42d), Acute Training Load (ATL ~7d),
    and Training Stress Balance (TSB = CTL - ATL).
    Uses suffer_score where available; falls back to HR x time estimate.
    """
    tdf = df.copy()
    tdf["load"] = tdf["suffer_score"].fillna(
        tdf["moving_time_h"] * tdf["avg_hr"].fillna(130) / 10
    )
    weekly = tdf.groupby("week")["load"].sum().reset_index().sort_values("week")

    loads = weekly["load"].values
    ctl_vals, atl_vals = [0.0], [0.0]
    for l in loads[1:]:
        ctl_vals.append(ctl_vals[-1] + (l - ctl_vals[-1]) / 42)
        atl_vals.append(atl_vals[-1] + (l - atl_vals[-1]) / 7)
    tsb = [c - a for c, a in zip(ctl_vals, atl_vals)]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=weekly["week"], y=weekly["load"],
        name="Weekly Load", marker_color=ORANGE, opacity=0.4,
    ))
    fig.add_trace(go.Scatter(
        x=weekly["week"], y=ctl_vals,
        name="Fitness (CTL)", line=dict(color=GREEN, width=2),
    ))
    fig.add_trace(go.Scatter(
        x=weekly["week"], y=atl_vals,
        name="Fatigue (ATL)", line=dict(color=PINK, width=2),
    ))
    fig.add_trace(go.Scatter(
        x=weekly["week"], y=tsb,
        name="Form (TSB)", line=dict(color=TEAL, width=2, dash="dot"),
        fill="tozeroy", fillcolor="rgba(34,211,238,0.05)",
    ))
    fig.update_layout(**BASE_LAYOUT, title="Training Load / Fitness / Fatigue / Form")
    return fig


def chart_heatmap(df: pd.DataFrame) -> go.Figure:
    df2 = df.copy()
    df2["week_num"] = df2["date"].dt.isocalendar().week.astype(int)
    pivot = df2.groupby(["year", "week_num"])["distance_km"].sum().reset_index()

    fig = px.density_heatmap(
        pivot, x="week_num", y=pivot["year"].astype(str),
        z="distance_km",
        color_continuous_scale=[DARK, ORANGE, AMBER],
        title="Ride Volume by Week (km)",
        nbinsx=53,
    )
    fig.update_layout(**BASE_LAYOUT, coloraxis_colorbar=dict(title="km"))
    return fig


def chart_ride_length_hist(df: pd.DataFrame) -> go.Figure:
    fig = px.histogram(
        df, x="distance_km", nbins=30,
        title="Ride Distance Distribution",
        color_discrete_sequence=[ORANGE],
    )
    fig.update_traces(marker_line_color=DARK, marker_line_width=0.5)
    fig.update_layout(**BASE_LAYOUT)
    return fig


def chart_cadence_trend(df: pd.DataFrame) -> go.Figure:
    cdf = df.dropna(subset=["avg_cadence"]).sort_values("date")
    if cdf.empty:
        fig = go.Figure()
        fig.update_layout(**BASE_LAYOUT, title="Cadence — no cadence data found")
        return fig

    rolling = cdf.set_index("date")["avg_cadence"].rolling("28D").mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=cdf["date"], y=cdf["avg_cadence"],
        mode="markers", name="Avg cadence",
        marker=dict(color=AMBER, size=4, opacity=0.55),
    ))
    fig.add_trace(go.Scatter(
        x=rolling.index, y=rolling.values,
        mode="lines", name="28-day avg",
        line=dict(color=AMBER, width=2),
    ))
    fig.update_layout(**BASE_LAYOUT, title="Average Cadence (rpm)")
    return fig


def chart_year_comparison(df: pd.DataFrame) -> go.Figure:
    """Cumulative distance by year overlaid."""
    fig = go.Figure()
    colors = [ORANGE, TEAL, GREEN, PURPLE, PINK, AMBER, "#60a5fa", "#34d399", "#fb923c", "#e879f9"]
    years = sorted(df["year"].unique())
    for i, yr in enumerate(years):
        ydf = df[df["year"] == yr].sort_values("date").copy()
        ydf["day_of_year"] = ydf["date"].dt.dayofyear
        ydf["cum_km"] = ydf["distance_km"].cumsum()
        fig.add_trace(go.Scatter(
            x=ydf["day_of_year"], y=ydf["cum_km"],
            mode="lines", name=str(yr),
            line=dict(color=colors[i % len(colors)], width=2),
        ))
    fig.update_layout(
        **BASE_LAYOUT,
        title="Cumulative Distance by Year",
        xaxis_title="Day of Year",
        yaxis_title="km",
    )
    return fig


def table_fitness_trend(df: pd.DataFrame):
    """Rolling 90-day fitness trend table with direction arrows."""
    import numpy as np

    now = df["date"].max()
    rows = []
    prev_eff = None

    # Build quarters from most recent backwards across full history
    earliest = df["date"].min()
    max_quarters = int((now - earliest).days / 90) + 2
    quarters = []
    for i in range(max_quarters):
        end = now - pd.Timedelta(days=90 * i)
        start = end - pd.Timedelta(days=90)
        chunk = df[(df["date"] >= start) & (df["date"] < end)]
        if chunk.empty:
            continue
        quarters.append((start, end, chunk))

    # Reverse so oldest is first (arrows compare to previous)
    quarters.reverse()

    for start, end, chunk in quarters:
        hr_chunk = chunk.dropna(subset=["avg_hr", "avg_speed_kmh"])
        rides = len(chunk)
        total_km = chunk["distance_km"].sum()
        avg_dist = chunk["distance_km"].mean()
        avg_speed = chunk["avg_speed_kmh"].mean()
        avg_hr = hr_chunk["avg_hr"].mean() if not hr_chunk.empty else np.nan
        efficiency = (hr_chunk["avg_speed_kmh"] / hr_chunk["avg_hr"]).mean() if not hr_chunk.empty else np.nan
        total_elev = chunk["elevation_m"].sum()

        # Direction arrows vs previous quarter
        if prev_eff is not None and not np.isnan(efficiency) and not np.isnan(prev_eff):
            delta = efficiency - prev_eff
            if delta > 0.003:
                trend = "▲"
            elif delta < -0.003:
                trend = "▼"
            else:
                trend = "―"
        else:
            trend = ""
        prev_eff = efficiency

        period_label = f"{start.strftime('%b %Y')} – {end.strftime('%b %Y')}"
        rows.append({
            "Period":         period_label,
            "Rides":          str(rides),
            "Distance":       f"{total_km:,.0f} km",
            "Avg Dist":       f"{avg_dist:.0f} km",
            "Avg Speed":      f"{avg_speed:.1f} km/h",
            "Avg HR":         f"{avg_hr:.0f}" if not np.isnan(avg_hr) else "—",
            "Efficiency":     f"{efficiency:.3f}" if not np.isnan(efficiency) else "—",
            "Trend":          trend,
            "Climbing":       f"{total_elev:,.0f} m",
        })

    if not rows:
        return html.P("Not enough data for fitness trend.", style={"color": MUTED})

    rdf = pd.DataFrame(rows)

    return dash_table.DataTable(
        data=rdf.to_dict("records"),
        columns=[{"name": c, "id": c} for c in rdf.columns],
        sort_action="native",
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": BORDER,
            "color": ORANGE,
            "fontFamily": MONO,
            "fontSize": "11px",
            "fontWeight": "500",
            "border": f"1px solid {BORDER}",
            "textAlign": "left",
        },
        style_cell={
            "backgroundColor": CARD,
            "color": TEXT,
            "fontFamily": MONO,
            "fontSize": "11px",
            "border": f"1px solid {BORDER}",
            "padding": "6px 10px",
            "textAlign": "left",
            "whiteSpace": "normal",
        },
        style_data_conditional=[
            {"if": {"filter_query": '{Trend} = "▲"', "column_id": "Trend"}, "color": GREEN},
            {"if": {"filter_query": '{Trend} = "▼"', "column_id": "Trend"}, "color": PINK},
            {"if": {"filter_query": '{Trend} = "―"', "column_id": "Trend"}, "color": MUTED},
            {"if": {"state": "active"}, "backgroundColor": BORDER, "border": f"1px solid {ORANGE}"},
        ],
    )


def table_fitness_trend_fig(df: pd.DataFrame) -> go.Figure:
    """Plotly figure version of the fitness trend table, used by the HTML exporter."""
    widget = table_fitness_trend(df)
    if not hasattr(widget, "data"):
        return go.Figure()
    rdf = pd.DataFrame(widget.data)
    cols = list(rdf.columns)
    trend_colors = [GREEN if t == "▲" else PINK if t == "▼" else MUTED for t in rdf["Trend"]]
    cell_colors = [trend_colors if c == "Trend" else [TEXT] * len(rdf) for c in cols]
    fig = go.Figure(data=[go.Table(
        columnwidth=[170, 50, 90, 75, 85, 65, 80, 50, 95],
        header=dict(values=cols, fill_color=BORDER,
                    font=dict(color=ORANGE, size=11, family=MONO), align="left", height=30),
        cells=dict(values=[rdf[c] for c in cols], fill_color=CARD,
                   font=dict(color=cell_colors, size=11, family=MONO), align="left", height=26),
    )])
    fig.update_layout(paper_bgcolor=CARD, margin=dict(l=0, r=0, t=0, b=0),
                      height=len(rdf) * 26 + 40)
    return fig


def table_personal_bests(df: pd.DataFrame):
    records = []
    categories = [
        ("Longest ride",     "distance_km",    "{:.1f} km",  "idxmax"),
        ("Most elevation",   "elevation_m",    "{:.0f} m",   "idxmax"),
        ("Fastest avg speed","avg_speed_kmh",  "{:.1f} km/h","idxmax"),
        ("Highest power",    "avg_watts",       "{:.0f} W",   "idxmax"),
        ("Longest session",  "moving_time_h",   fmt_time,     "idxmax"),
        ("Lowest avg HR",    "avg_hr",          "{:.0f} bpm", "idxmin"),
    ]

    for label, col, fmt, func in categories:
        if col not in df.columns:
            continue
        tmp = df.dropna(subset=[col])
        if tmp.empty:
            continue
        idx = getattr(tmp[col], func)()
        row_data = tmp.loc[idx]
        val = fmt(row_data[col]) if callable(fmt) else fmt.format(row_data[col])
        records.append({
            "Record": label,
            "Value":  val,
            "Ride":   str(row_data["name"])[:40],
            "Date":   str(row_data["date"].date()),
        })

    if not records:
        return html.P("No personal bests found.", style={"color": MUTED})

    rdf = pd.DataFrame(records)
    fig = go.Figure(data=[go.Table(
        columnwidth=[160, 100, 280, 90],
        header=dict(
            values=["Record", "Value", "Ride", "Date"],
            fill_color=BORDER,
            font=dict(color=ORANGE, size=11, family=MONO),
            align="left", height=30,
        ),
        cells=dict(
            values=[rdf[c] for c in rdf.columns],
            fill_color=CARD,
            font=dict(color=TEXT, size=11, family=MONO),
            align="left", height=26,
        ),
    )])
    fig.update_layout(
        paper_bgcolor=CARD,
        margin=dict(l=0, r=0, t=0, b=0),
        height=len(records) * 26 + 40,
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# Summary stats
# ─────────────────────────────────────────────────────────────────────────────

def compute_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {}
    span_years = max((df["date"].max() - df["date"].min()).days / 365.25, 0.01)
    return {
        "rides":        len(df),
        "km":           df["distance_km"].sum(),
        "hours":        df["moving_time_h"].sum(),
        "elev":         df["elevation_m"].sum(),
        "best_km":      df["distance_km"].max(),
        "avg_speed":    df["avg_speed_kmh"].mean(),
        "span_years":   span_years,
        "avg_per_ride": df["distance_km"].sum() / len(df),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Filter helper
# ─────────────────────────────────────────────────────────────────────────────

def apply_filter(time_range: str, ride_type: str = "all") -> pd.DataFrame:
    if DF.empty:
        return DF
    df = DF
    now = pd.Timestamp.now()
    cutoffs = {
        "3m":   now - pd.Timedelta(days=90),
        "6m":   now - pd.Timedelta(days=180),
        "12m":  now - pd.Timedelta(days=365),
        "2y":   now - pd.Timedelta(days=730),
        "3y":   now - pd.Timedelta(days=1095),
        "5y":   now - pd.Timedelta(days=1825),
        "year": pd.Timestamp(now.year, 1, 1),
    }
    if time_range in cutoffs:
        df = df[df["date"] >= cutoffs[time_range]]
    if ride_type == "commute":
        df = df[df["commute"] == True]
    elif ride_type == "no_commute":
        df = df[df["commute"] != True]
    elif ride_type == "indoor":
        df = df[df["indoor"] == True]
    elif ride_type == "outdoor":
        df = df[df["indoor"] != True]
    elif ride_type != "all":
        df = df[df["activity_type"] == ride_type]
    return df


# ─────────────────────────────────────────────────────────────────────────────
# App layout
# ─────────────────────────────────────────────────────────────────────────────

def build_layout():
    if DF.empty:
        return html.Div([
            html.H2("No ride data found.", style={"color": ORANGE, "padding": "40px 32px 0"}),
            html.P("Run  python extract.py  first.", style={"color": MUTED, "padding": "0 32px"}),
        ], style={"backgroundColor": DARK, "minHeight": "100vh"})

    sources = DF["source"].value_counts()
    source_str = " / ".join(f"{v} from {k.title()}" for k, v in sources.items())

    return html.Div([
        # Google Fonts
        html.Link(rel="stylesheet",
                  href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Mono:wght@400;500&display=swap"),

        # Header bar
        html.Div([
            html.Div([
                html.Div("VELO FITNESS", style={
                    "color": ORANGE, "fontFamily": DISPLAY, "fontSize": "2.4rem",
                    "letterSpacing": "0.2em", "lineHeight": "1",
                }),
                html.P(source_str, style={
                    "color": MUTED, "fontFamily": MONO, "fontSize": "11px", "margin": "6px 0 0 0",
                }),
            ]),
            html.Div([
                html.Button("Export HTML", id="export-btn", n_clicks=0, style={
                    "backgroundColor": "transparent",
                    "border": f"1px solid {BORDER}",
                    "color": TEXT,
                    "fontFamily": MONO,
                    "fontSize": "11px",
                    "padding": "6px 14px",
                    "cursor": "pointer",
                    "borderRadius": "4px",
                    "marginRight": "10px",
                    "letterSpacing": "0.08em",
                }),
                dcc.Download(id="export-download"),
                dcc.Dropdown(
                    id="type-filter",
                    options=(
                        [{"label": "All ride types",  "value": "all"},
                         {"label": "Outdoor only",    "value": "outdoor"},
                         {"label": "Indoor only",     "value": "indoor"},
                         {"label": "Commutes only",   "value": "commute"},
                         {"label": "No commutes",     "value": "no_commute"}] +
                        [{"label": t.replace("_", " ").title(), "value": t}
                         for t in sorted(DF["activity_type"].dropna().unique())]
                    ),
                    value="all",
                    clearable=False,
                    style={
                        "width": "185px",
                        "fontFamily": MONO,
                        "fontSize": "12px",
                        "marginRight": "10px",
                    },
                ),
                dcc.Dropdown(
                    id="time-filter",
                    options=[
                        {"label": "All time",        "value": "all"},
                        {"label": "This year",       "value": "year"},
                        {"label": "Last 3 months",   "value": "3m"},
                        {"label": "Last 6 months",   "value": "6m"},
                        {"label": "Last 12 months",  "value": "12m"},
                        {"label": "Last 2 years",    "value": "2y"},
                        {"label": "Last 3 years",    "value": "3y"},
                        {"label": "Last 5 years",    "value": "5y"},
                    ],
                    value="all",
                    clearable=False,
                    style={
                        "width": "185px",
                        "fontFamily": MONO,
                        "fontSize": "12px",
                    },
                ),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={
            "display": "flex", "justifyContent": "space-between", "alignItems": "flex-end",
            "padding": "22px 32px", "backgroundColor": CARD,
            "borderBottom": f"1px solid {BORDER}",
        }),

        # Dynamic content
        html.Div(id="stat-row", style={
            "display": "flex", "gap": "14px", "flexWrap": "wrap",
            "padding": "22px 32px 0",
        }),
        html.Div(id="charts", style={"padding": "16px 32px 32px"}),

    ], style={"backgroundColor": DARK, "minHeight": "100vh", "color": TEXT})


app = dash.Dash(
    __name__,
    title="Velo Fitness",
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
    suppress_callback_exceptions=True,
)
app.layout = build_layout()


# ─────────────────────────────────────────────────────────────────────────────
# Callbacks
# ─────────────────────────────────────────────────────────────────────────────

@app.callback(
    Output("stat-row", "children"),
    Output("charts", "children"),
    Input("time-filter", "value"),
    Input("type-filter", "value"),
)
def update_all(time_range: str, ride_type: str):
    df = apply_filter(time_range, ride_type)

    if df.empty:
        empty = html.P("No rides in this period.", style={"color": MUTED, "padding": "30px 0"})
        return [], empty

    s = compute_summary(df)

    # ── Stat cards ──
    cards = [
        stat_card("Total Rides",    str(s["rides"]),
                  f"over {s['span_years']:.1f} years", ORANGE),
        stat_card("Total Distance", f"{s['km']:,.0f} km",
                  f"avg {s['avg_per_ride']:.0f} km/ride", TEAL),
        stat_card("Total Time",     fmt_time(s["hours"]), "", GREEN),
        stat_card("Climbing",       f"{s['elev']/1000:,.1f} km",
                  "total elevation", PURPLE),
        stat_card("Best Ride",      f"{s['best_km']:.0f} km",
                  "longest single ride", PINK),
        stat_card("Avg Speed",      f"{s['avg_speed']:.1f} km/h",
                  "across all rides", AMBER),
    ]

    # ── Charts ──
    charts = html.Div([
        row(
            card_wrap(section_head("Weekly Distance & Riding Time"),   graph(chart_weekly_volume(df))),
            card_wrap(section_head("Monthly Distance & Elevation"),    graph(chart_monthly_volume(df))),
        ),
        row(
            card_wrap(section_head("Speed Trend"),        graph(chart_speed_trend(df))),
            card_wrap(section_head("Heart Rate Trend"),   graph(chart_hr_trend(df))),
        ),
        row(
            card_wrap(section_head("Power Trend"),             graph(chart_power_trend(df))),
            card_wrap(section_head("Cadence Trend"),            graph(chart_cadence_trend(df))),
        ),
        row(
            card_wrap(section_head("Distance vs Elevation"),   graph(chart_elevation_scatter(df))),
            card_wrap(section_head("Cumulative Distance by Year"), graph(chart_year_comparison(df))),
        ),
        row(
            card_wrap(section_head("Training Load / Fitness / Fatigue / Form"),
                      graph(chart_training_load(df)),
                      extra={"flex": "2", "minWidth": "600px"}),
            card_wrap(section_head("Ride Distance Distribution"),
                      graph(chart_ride_length_hist(df))),
        ),
        row(
            card_wrap(section_head("Annual Volume Heatmap"),
                      graph(chart_heatmap(df)),
                      extra={"flex": "2", "minWidth": "600px"}),
        ),
        card_wrap(
            section_head("Fitness Trend"),
            html.P("90-day rolling windows · Efficiency = avg speed ÷ avg HR · Higher = fitter",
                    style={"color": MUTED, "fontSize": "10px", "fontFamily": MONO, "margin": "0 0 10px 0"}),
            table_fitness_trend(df),
            extra={"marginBottom": "14px"},
        ),
        card_wrap(
            section_head("Personal Bests"),
            table_personal_bests(df),
            extra={"marginBottom": "0"},
        ),
    ])

    return cards, charts


@app.callback(
    Output("export-download", "data"),
    Input("export-btn", "n_clicks"),
    Input("time-filter", "value"),
    Input("type-filter", "value"),
    prevent_initial_call=True,
)
def export_html(n_clicks, time_range, ride_type):
    from dash import ctx
    if ctx.triggered_id != "export-btn" or not n_clicks:
        return dash.no_update

    df = apply_filter(time_range, ride_type)
    if df.empty:
        return dash.no_update

    s = compute_summary(df)
    from datetime import datetime
    exported_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    range_label = dict(all="All time", year="This year", **{k: f"Last {k}" for k in ["3m","6m","12m","2y","3y","5y"]}).get(time_range, time_range)

    plotlyjs_loaded = [False]

    def fig_html(fig, height=300):
        include_js = not plotlyjs_loaded[0]
        plotlyjs_loaded[0] = True
        fig = fig.update_layout(autosize=True, width=None, height=height)
        return pio.to_html(fig, full_html=False, include_plotlyjs=include_js,
                           default_height=f"{height}px",
                           config={"responsive": True})

    def card(title, inner, flex="1", min_width="380px"):
        return (
            f'<div style="background:{CARD};border:1px solid {BORDER};border-radius:6px;'
            f'padding:18px;flex:{flex};min-width:{min_width};box-sizing:border-box;overflow:hidden">'
            f'<p style="color:{ORANGE};font-family:Impact,sans-serif;font-size:1.1rem;'
            f'letter-spacing:0.12em;margin:0 0 10px">{title.upper()}</p>'
            f'{inner}</div>'
        )

    def row(*cards):
        return (
            f'<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:14px">'
            + "".join(cards) +
            '</div>'
        )

    # Stat cards
    stat_items = [
        ("Total Rides",    f"{s['rides']}",              f"over {s['span_years']:.1f} years",  ORANGE),
        ("Total Distance", f"{s['km']:,.0f} km",         f"avg {s['avg_per_ride']:.0f} km/ride", TEAL),
        ("Total Time",     fmt_time(s['hours']),          "",                                    GREEN),
        ("Climbing",       f"{s['elev']/1000:,.1f} km",  "total elevation",                     PURPLE),
        ("Best Ride",      f"{s['best_km']:.0f} km",     "longest single ride",                 PINK),
        ("Avg Speed",      f"{s['avg_speed']:.1f} km/h", "across all rides",                    AMBER),
    ]
    stats_html = '<div style="display:flex;flex-wrap:wrap;gap:14px;margin-bottom:14px">'
    for lbl, val, sub, color in stat_items:
        stats_html += (
            f'<div style="background:{CARD};border:1px solid {BORDER};border-top:3px solid {color};'
            f'border-radius:6px;padding:16px 20px;min-width:150px;flex:1;box-sizing:border-box">'
            f'<p style="color:{MUTED};font-size:10px;letter-spacing:0.15em;text-transform:uppercase;'
            f'margin:0 0 6px;font-family:monospace">{lbl}</p>'
            f'<div style="color:{color};font-size:1.8rem;font-family:Impact,sans-serif;'
            f'letter-spacing:0.04em;line-height:1">{val}</div>'
            f'<p style="color:{MUTED};font-size:10px;margin:6px 0 0;font-family:monospace">{sub}</p>'
            f'</div>'
        )
    stats_html += '</div>'

    charts_html = (
        row(
            card("Weekly Distance & Riding Time", fig_html(chart_weekly_volume(df))),
            card("Monthly Distance & Elevation",  fig_html(chart_monthly_volume(df))),
        ) +
        row(
            card("Speed Trend",       fig_html(chart_speed_trend(df))),
            card("Heart Rate Trend",  fig_html(chart_hr_trend(df))),
        ) +
        row(
            card("Power Trend",   fig_html(chart_power_trend(df))),
            card("Cadence Trend", fig_html(chart_cadence_trend(df))),
        ) +
        row(
            card("Distance vs Elevation",       fig_html(chart_elevation_scatter(df))),
            card("Cumulative Distance by Year",  fig_html(chart_year_comparison(df))),
        ) +
        row(
            card("Training Load / Fitness / Fatigue / Form", fig_html(chart_training_load(df), height=300), flex="2", min_width="600px"),
            card("Ride Distance Distribution",               fig_html(chart_ride_length_hist(df))),
        ) +
        row(
            card("Annual Volume Heatmap", fig_html(chart_heatmap(df)), flex="2", min_width="600px"),
        )
    )

    fitness_inner = (
        f'<p style="color:{MUTED};font-size:10px;font-family:monospace;margin:0 0 10px">'
        f'90-day rolling windows · Efficiency = avg speed ÷ avg HR · Higher = fitter</p>'
        + fig_html(table_fitness_trend_fig(df), height=200)
    )
    charts_html += card("Fitness Trend", fitness_inner) + '<div style="margin-bottom:14px"></div>'
    charts_html += card("Personal Bests", fig_html(table_personal_bests(df).figure, height=200))

    html_out = f"""<!DOCTYPE html>
<html><head>
  <meta charset="utf-8">
  <title>Velo Fitness — {range_label}</title>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=IBM+Plex+Mono:wght@400;500&display=swap">
  <style>
    * {{ box-sizing: border-box }}
    body {{ background:{DARK};color:{TEXT};font-family:'IBM Plex Mono',monospace;margin:0;padding:0 }}
    .wrap {{ max-width:1400px;margin:0 auto;padding:32px }}
  </style>
</head><body><div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:flex-end;
              border-bottom:1px solid {BORDER};padding-bottom:18px;margin-bottom:22px">
    <div>
      <div style="color:{ORANGE};font-family:'Bebas Neue',Impact,sans-serif;font-size:2.4rem;letter-spacing:0.2em;line-height:1">VELO FITNESS</div>
      <p style="color:{MUTED};font-size:11px;margin:6px 0 0">{range_label} &nbsp;·&nbsp; Exported {exported_at}</p>
    </div>
  </div>
  {stats_html}
  {charts_html}
</div></body></html>"""

    filename = f"velo_fitness_{time_range}_{datetime.now().strftime('%Y%m%d')}.html"
    return dcc.send_string(html_out, filename)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  Velo Fitness Dashboard")
    print("  http://127.0.0.1:8050")
    print("=" * 50)
    print()
    app.run(debug=False, port=8050)
