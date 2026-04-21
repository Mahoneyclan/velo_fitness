#!/usr/bin/env python3
"""
extract.py
──────────
Fetch all cycling activities from Strava and Garmin Connect,
normalise into a common schema, deduplicate, and save to rides.json.

Usage:
    1. Copy .env.example → .env and fill in your credentials.
    2. python extract.py
       (Opens a browser for Strava OAuth on first run.)

Re-run anytime to refresh data.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import base64
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode, parse_qs

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — loaded from .env or environment
# ─────────────────────────────────────────────────────────────────────────────

def _load_dotenv():
    """Minimal .env loader — no extra dependency needed."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            os.environ.setdefault(key, value)

_load_dotenv()

STRAVA_CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
GARMIN_EMAIL         = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD      = os.environ.get("GARMIN_PASSWORD", "")

OUTPUT_FILE = Path(__file__).parent / "rides.json"
TOKEN_DIR   = Path.home() / ".velo_fitness"
STRAVA_TOKEN_FILE = TOKEN_DIR / "strava_tokens.json"

# ─────────────────────────────────────────────────────────────────────────────
# Strava OAuth 2.0 with PKCE
# ─────────────────────────────────────────────────────────────────────────────

STRAVA_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL     = "https://www.strava.com/oauth/token"
STRAVA_API_BASE      = "https://www.strava.com/api/v3"
STRAVA_REDIRECT_URI  = "http://localhost:8888/callback"
STRAVA_SCOPES        = "read,activity:read_all"
CALLBACK_PORT        = 8888


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(64)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _save_strava_tokens(data: dict):
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    data["saved_at"] = datetime.now(timezone.utc).isoformat()
    with STRAVA_TOKEN_FILE.open("w") as f:
        json.dump(data, f, indent=2)
    STRAVA_TOKEN_FILE.chmod(0o600)


def _load_strava_tokens() -> Optional[dict]:
    if not STRAVA_TOKEN_FILE.exists():
        return None
    with STRAVA_TOKEN_FILE.open() as f:
        tokens = json.load(f)
    for key in ("access_token", "refresh_token", "expires_at"):
        if key not in tokens:
            return None
    return tokens


def _token_expired(expires_at: int) -> bool:
    return (expires_at - 300) <= datetime.now(timezone.utc).timestamp()


def _refresh_strava_token(refresh_token: str) -> Optional[str]:
    print("  Refreshing Strava access token...")
    try:
        r = requests.post(STRAVA_TOKEN_URL, data={
            "client_id":     STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
        })
        r.raise_for_status()
        data = r.json()
        _save_strava_tokens(data)
        print("  Token refreshed.")
        return data["access_token"]
    except Exception as e:
        print(f"  Refresh failed: {e}")
        return None


def _strava_oauth_flow() -> Optional[str]:
    """Run full OAuth PKCE flow, return access_token or None."""
    verifier  = _pkce_verifier()
    challenge = _pkce_challenge(verifier)
    state     = secrets.token_urlsafe(32)

    params = {
        "client_id":            STRAVA_CLIENT_ID,
        "redirect_uri":         STRAVA_REDIRECT_URI,
        "response_type":        "code",
        "scope":                STRAVA_SCOPES,
        "state":                state,
        "code_challenge":       challenge,
        "code_challenge_method":"S256",
        "approval_prompt":      "auto",
    }
    auth_url = f"{STRAVA_AUTHORIZE_URL}?{urlencode(params)}"

    # Capture auth code via local callback server
    result = {"code": None, "error": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = b"<html><body><h1>Success!</h1><p>You can close this tab.</p></body></html>"
                self.send_response(200)
            else:
                result["error"] = qs.get("error", ["unknown"])[0]
                body = b"<html><body><h1>Error</h1><p>Authorization failed.</p></body></html>"
                self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = HTTPServer(("localhost", CALLBACK_PORT), Handler)
    print("  Opening browser for Strava authorization...")
    webbrowser.open(auth_url)
    server.handle_request()

    if not result["code"]:
        print(f"  Authorization failed: {result['error']}")
        return None

    # Exchange code for tokens
    print("  Exchanging code for access token...")
    try:
        r = requests.post(STRAVA_TOKEN_URL, data={
            "client_id":     STRAVA_CLIENT_ID,
            "client_secret": STRAVA_CLIENT_SECRET,
            "code":          result["code"],
            "grant_type":    "authorization_code",
            "code_verifier": verifier,
        })
        r.raise_for_status()
        data = r.json()
        _save_strava_tokens(data)
        print("  Strava authentication successful.")
        return data["access_token"]
    except Exception as e:
        print(f"  Token exchange failed: {e}")
        return None


def get_strava_token() -> Optional[str]:
    """Get a valid Strava access token (cached / refreshed / new OAuth)."""
    tokens = _load_strava_tokens()
    if tokens:
        if not _token_expired(tokens["expires_at"]):
            return tokens["access_token"]
        refreshed = _refresh_strava_token(tokens["refresh_token"])
        if refreshed:
            return refreshed
    return _strava_oauth_flow()


# ─────────────────────────────────────────────────────────────────────────────
# Strava data fetching — ALL activities, paginated
# ─────────────────────────────────────────────────────────────────────────────

def fetch_strava_activities(token: str) -> List[dict]:
    """Fetch every activity from Strava (paginated, 200 per page)."""
    headers = {"Authorization": f"Bearer {token}"}
    all_activities: List[dict] = []
    page = 1
    per_page = 200

    while True:
        print(f"  Strava page {page}...")
        for attempt in range(4):
            r = requests.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                headers=headers,
                params={"per_page": per_page, "page": page},
            )
            if r.ok:
                break
            wait = 2 ** attempt * 5  # 5s, 10s, 20s, 40s
            print(f"  Strava {r.status_code} on page {page}, retrying in {wait}s...")
            time.sleep(wait)
        else:
            print(f"  Strava page {page} failed after 4 attempts — stopping pagination.")
            break
        batch = r.json()
        if not batch:
            break
        all_activities.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
        time.sleep(1)

    print(f"  Fetched {len(all_activities)} activities from Strava.")
    return all_activities


def map_strava_ride(activity: dict) -> dict:
    """Map a Strava activity to our common schema.
    Uses list-endpoint data only (no per-ride detail call needed)."""
    return {
        "id":             f"strava_{activity['id']}",
        "source":         "strava",
        "name":           activity.get("name", "Untitled"),
        "date":           activity.get("start_date_local", activity.get("start_date", "")),
        "distance_km":    round(activity.get("distance", 0) / 1000, 2),
        "elevation_m":    activity.get("total_elevation_gain", 0),
        "moving_time_s":  activity.get("moving_time", 0),
        "elapsed_time_s": activity.get("elapsed_time", 0),
        "avg_speed_kmh":  round(activity.get("average_speed", 0) * 3.6, 2),
        "max_speed_kmh":  round(activity.get("max_speed", 0) * 3.6, 2),
        "avg_hr":         activity.get("average_heartrate"),
        "max_hr":         activity.get("max_heartrate"),
        "avg_watts":      activity.get("average_watts"),
        "max_watts":      activity.get("max_watts"),
        "avg_cadence":    activity.get("average_cadence"),
        "suffer_score":   activity.get("suffer_score"),
        "calories":       None,  # only in detail endpoint
        "gear":           activity.get("gear", {}).get("name") if isinstance(activity.get("gear"), dict) else None,
        "activity_type":  activity.get("sport_type") or activity.get("type", "Ride"),
        "commute":        activity.get("commute", False),
        "indoor":         activity.get("trainer", False),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Garmin data fetching — ALL cycling activities
# ─────────────────────────────────────────────────────────────────────────────

def fetch_garmin_activities() -> List[dict]:
    """Fetch all cycling activities from Garmin Connect."""
    try:
        from garminconnect import Garmin
    except ImportError:
        print("  garminconnect not installed — skipping Garmin.")
        print("  Install with: pip install garminconnect")
        return []

    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("  GARMIN_EMAIL / GARMIN_PASSWORD not set — skipping Garmin.")
        return []

    print("  Logging in to Garmin Connect...")
    try:
        client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
        client.login()
        name = client.get_full_name()
        print(f"  Connected as {name}")
    except Exception as e:
        print(f"  Garmin login failed: {e}")
        return []

    cycling_types = {
        "cycling", "road_biking", "mountain_biking",
        "gravel_cycling", "indoor_cycling",
    }

    all_activities: List[dict] = []
    start = 0
    batch_size = 100

    while True:
        print(f"  Garmin batch from {start}...")
        try:
            batch = client.get_activities(start, batch_size)
        except Exception as e:
            print(f"  Garmin fetch error: {e}")
            break

        if not batch:
            break

        for a in batch:
            type_key = a.get("activityType", {}).get("typeKey", "").lower()
            if type_key in cycling_types:
                all_activities.append(a)

        if len(batch) < batch_size:
            break
        start += batch_size
        time.sleep(0.3)

    print(f"  Fetched {len(all_activities)} cycling activities from Garmin.")
    return all_activities


def map_garmin_ride(activity: dict) -> dict:
    """Map a Garmin activity to our common schema."""
    distance_m  = activity.get("distance", 0) or 0
    duration_s  = activity.get("duration", 0) or 0
    moving_s    = activity.get("movingDuration", duration_s) or duration_s
    elev_gain   = activity.get("elevationGain", 0) or 0
    avg_speed   = activity.get("averageSpeed", 0) or 0  # m/s
    max_speed   = activity.get("maxSpeed", 0) or 0

    return {
        "id":             f"garmin_{activity.get('activityId', 0)}",
        "source":         "garmin",
        "name":           activity.get("activityName", "Untitled"),
        "date":           activity.get("startTimeLocal", ""),
        "distance_km":    round(distance_m / 1000, 2),
        "elevation_m":    round(elev_gain, 1),
        "moving_time_s":  round(moving_s),
        "elapsed_time_s": round(duration_s),
        "avg_speed_kmh":  round(avg_speed * 3.6, 2),
        "max_speed_kmh":  round(max_speed * 3.6, 2),
        "avg_hr":         activity.get("averageHR"),
        "max_hr":         activity.get("maxHR"),
        "avg_watts":      activity.get("avgPower"),
        "max_watts":      activity.get("maxPower"),
        "avg_cadence":    activity.get("averageBikingCadenceInRevPerMinute"),
        "suffer_score":   activity.get("trainingStressScore"),
        "calories":       activity.get("calories"),
        "gear":           None,
        "activity_type":  activity.get("activityType", {}).get("typeKey", "Ride"),
        "commute":        False,
        "indoor":         False,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Deduplication
# ─────────────────────────────────────────────────────────────────────────────

def _ride_date(ride: dict) -> Optional[str]:
    """Return just the date portion (YYYY-MM-DD)."""
    raw = ride.get("date", "")
    if not raw:
        return None
    return raw[:10]


def deduplicate(rides: List[dict]) -> List[dict]:
    """
    Remove duplicate rides that appear on both Strava and Garmin.
    Match by same date + distance within 10%.
    Prefer Strava records (richer metadata: suffer_score, segments).
    """
    # Group by date
    by_date: Dict[str, List[dict]] = {}
    for r in rides:
        d = _ride_date(r)
        if d:
            by_date.setdefault(d, []).append(r)

    unique: List[dict] = []
    for date_str, group in by_date.items():
        if len(group) == 1:
            unique.append(group[0])
            continue

        # Separate by source
        strava = [r for r in group if r["source"] == "strava"]
        garmin = [r for r in group if r["source"] == "garmin"]

        if not strava or not garmin:
            # All from same source — keep all
            unique.extend(group)
            continue

        # For each Garmin ride, check if there's a matching Strava ride
        matched_garmin = set()
        for g in garmin:
            g_km = g["distance_km"]
            for s in strava:
                s_km = s["distance_km"]
                if g_km == 0 and s_km == 0:
                    matched_garmin.add(id(g))
                    break
                if g_km > 0 and s_km > 0:
                    ratio = min(g_km, s_km) / max(g_km, s_km)
                    if ratio >= 0.90:  # within 10%
                        matched_garmin.add(id(g))
                        break

        # Keep all Strava, keep unmatched Garmin
        unique.extend(strava)
        for g in garmin:
            if id(g) not in matched_garmin:
                unique.append(g)

    return sorted(unique, key=lambda r: r.get("date", ""))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print()
    print("=" * 55)
    print("  Velo Fitness — Data Extractor")
    print("=" * 55)
    print()

    all_rides: List[dict] = []

    # ── Strava ──
    if STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET:
        print("[Strava]")
        token = get_strava_token()
        if token:
            activities = fetch_strava_activities(token)
            # Filter to cycling
            cycling = [a for a in activities if a.get("type") in (
                "Ride", "VirtualRide", "EBikeRide", "GravelRide", "MountainBikeRide", "Handcycle"
            )]
            print(f"  {len(cycling)} cycling rides found.")

            for act in cycling:
                all_rides.append(map_strava_ride(act))
            print(f"  Mapped {len(cycling)} Strava rides.")
        else:
            print("  Strava authentication failed.")
    else:
        print("[Strava] No credentials configured — skipping.")
        print("  Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env")

    print()

    # ── Garmin ──
    print("[Garmin]")
    garmin_activities = fetch_garmin_activities()
    for act in garmin_activities:
        ride = map_garmin_ride(act)
        all_rides.append(ride)
    if garmin_activities:
        print(f"  Mapped {len(garmin_activities)} Garmin rides.")

    print()

    if not all_rides:
        print("No rides found from any source.")
        print("Check your credentials in .env")
        return

    # ── Deduplicate ──
    before = len(all_rides)
    all_rides = deduplicate(all_rides)
    dupes = before - len(all_rides)
    if dupes:
        print(f"Deduplication: removed {dupes} duplicate rides.")

    # ── Save ──
    with open(OUTPUT_FILE, "w") as f:
        json.dump(all_rides, f, indent=2, default=str)

    print(f"Saved {len(all_rides)} rides to {OUTPUT_FILE.name}")

    # Summary
    sources = {}
    for r in all_rides:
        sources[r["source"]] = sources.get(r["source"], 0) + 1
    for src, count in sources.items():
        print(f"  {src}: {count} rides")

    total_km = sum(r.get("distance_km", 0) or 0 for r in all_rides)
    print(f"  Total distance: {total_km:,.0f} km")

    dates = [r["date"] for r in all_rides if r.get("date")]
    if dates:
        print(f"  Date range: {min(dates)[:10]} → {max(dates)[:10]}")

    print()
    print("Next step: python dashboard.py")
    print()


if __name__ == "__main__":
    main()
