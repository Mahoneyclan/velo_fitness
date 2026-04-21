# Velo Fitness

Interactive cycling fitness dashboard built from your Strava and Garmin data.

![Dashboard preview](https://raw.githubusercontent.com/Mahoneyclan/velo_fitness/main/preview.png)

---

## Requirements

- Python 3.10+
- A Strava account and/or Garmin Connect account
- (Optional) A power meter for power-based charts

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

**Strava:**
1. Go to https://www.strava.com/settings/api
2. Create an application (name it anything, e.g. "My Fitness Dashboard")
3. Set "Authorization Callback Domain" to `localhost`
4. Copy the Client ID and Client Secret into `.env`

**Garmin:** just your regular Garmin Connect login email and password.

You can use either platform or both — if you use both, duplicate rides are automatically removed (matched by date + approximate distance, Strava preferred).

### 3. Run the dashboard

```bash
python dashboard.py
```

Then open **http://127.0.0.1:8050** in your browser.

On first launch it will open a browser tab for Strava OAuth authorisation. After that, tokens are cached at `~/.velo_fitness/strava_tokens.json` and refresh automatically.

Data is fetched fresh once per day. If you want to force a refresh, run `python extract.py` directly.

---

## Dashboard features

| Chart | What it shows |
|---|---|
| Weekly Volume | Distance bars + riding time line |
| Monthly Volume | Distance + elevation per month |
| Speed Trend | Avg speed per ride + 28-day rolling average |
| Heart Rate Trend | Avg HR per ride + 28-day rolling average |
| Power Trend | Avg watts — requires a power meter |
| Cadence Trend | Avg cadence + 28-day rolling average |
| Distance vs Elevation | Scatter coloured by year |
| Cumulative Distance | Year-over-year comparison, all years |
| Training Load | CTL (fitness), ATL (fatigue), TSB (form) |
| Annual Heatmap | Weekly ride volume across all years |
| Ride Distribution | Histogram of ride distances |
| Fitness Trend | 90-day rolling windows — sortable table |
| Personal Bests | Your all-time records |

### Filters

- **Time range** — All time, this year, last 3/6/12 months, last 2/3/5 years
- **Ride type** — All, Outdoor only, Indoor only, Commutes only, No commutes, or a specific sport type (Road, Mountain Bike, Gravel, Virtual, etc.)

### Export

Click **Export HTML** to download a self-contained HTML snapshot of all charts. The file opens in any browser with no internet connection required — share it by email or save it to Google Drive.

---

## Data sources & notes

- **Strava** — fetches all cycling activities via the Strava API (OAuth 2.0 with PKCE). Includes road, mountain bike, gravel, virtual, e-bike, commute, and indoor rides.
- **Garmin** — fetches via the unofficial Garmin Connect API (`garminconnect` library). Includes all cycling activity types.
- **Deduplication** — rides appearing on both platforms on the same date with similar distance are deduplicated; Strava record is kept.
- **Data quality** — rides shorter than 1 km or 5 minutes are dropped. Implausible sensor values (avg HR < 70, avg speed > 55 km/h, avg power > 600 W) are nulled rather than dropping the whole ride.
- **Training load** — calculated from Strava suffer scores where available, otherwise estimated from HR × time.
- **Power data** — only appears if you recorded with a power meter.
