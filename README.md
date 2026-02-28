# Velo Fitness

Interactive cycling fitness dashboard built from your Strava + Garmin data.

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

**Garmin:** your regular Garmin Connect login email + password.

### 3. Extract your data
```bash
python extract.py
```
This opens a browser for Strava OAuth on first run, then downloads all your rides from both platforms. Saves everything to `rides.json`. Re-run anytime to refresh.

### 4. Launch the dashboard
```bash
python dashboard.py
```
Then open: **http://127.0.0.1:8050**

## Dashboard Features

| Chart | What it shows |
|---|---|
| Weekly Volume | Distance bars + riding time trend |
| Monthly Volume | Distance + elevation per month |
| Speed Trend | Avg speed per ride + 28-day rolling average |
| Heart Rate Trend | Avg HR per ride + rolling average |
| Power Trend | Avg watts (requires power meter) |
| Cadence Trend | Avg cadence per ride + rolling average |
| Distance vs Elevation | Scatter coloured by year |
| Cumulative Distance | Year-over-year comparison |
| Training Load | CTL (fitness), ATL (fatigue), TSB (form) |
| Annual Heatmap | Ride volume visualised by week |
| Ride Distribution | Histogram of ride distances |
| Personal Bests | Your all-time records |

## Notes

- If you use both Strava and Garmin, rides that appear on both platforms are automatically deduplicated (matched by date + approximate distance).
- Power data only appears if you have a power meter on your bike.
- Training load is calculated from Strava suffer scores where available, otherwise estimated from HR and ride duration.
- Use the **Time Range** dropdown to focus on this year, last 3 months, etc.
- Strava OAuth tokens are cached at `~/.velo_fitness/strava_tokens.json` and auto-refresh.
