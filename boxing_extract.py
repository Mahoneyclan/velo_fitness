#!/usr/bin/env python3
"""
boxing_extract.py
──────────────────
Fetch boxing/kickboxing activities recorded by the f3b Connect IQ app from Garmin
Connect, parse punch metrics out of the FIT file's developer fields via `fitparse`,
and write a compact JSON export for the VeloFitness iOS app's Boxing tab.

Developer-field names below were verified against a real f3b "boxing" activity FIT
export (FIT sport = 'boxing', sub_sport = 'generic'; app name field = 'F3b Boxing').
f3b's field names are short/cryptic (pRate, tPunch, mForce, etc.) — see FIELD_NAME_MAP
and RECORD_FIELD_MAP below for the mapping. If a future firmware update changes them,
re-run with --file against a fresh export (or --discover against your account) to check.

IMPORTANT — punch force unit is NOT recoverable from the FIT file. Every force dev
field's `units` string is the literal app-defined label "G,N,Kg | lbs" (all four
options concatenated), not the unit actually selected on your watch. Set
PUNCH_FORCE_UNIT in .env to whatever your f3b app is configured to display —
otherwise sessions are written with unit "unknown".

Usage:
    python boxing_extract.py --file path/to/activity.fit   # parse one local FIT/zip, print result, no login/write
    python boxing_extract.py --discover                    # inspect real activity types from your account
    python boxing_extract.py --discover --days 30          # only look at the last 30 days
    python boxing_extract.py                               # full run, writes boxing_sessions.json to iCloud
    python boxing_extract.py --days 30                      # full run, last 30 days only

Setup: reuses GARMIN_EMAIL / GARMIN_PASSWORD from .env (same as extract.py).
Optional .env overrides:
    BOXING_ACTIVITY_TYPES=boxing,kickboxing   # comma-separated Garmin activityType.typeKey values
    ICLOUD_OUTPUT_DIR=/path/to/dir            # override the auto-detected iCloud container path
    PUNCH_FORCE_UNIT=N                        # G | N | Kg | lbs — whatever your watch is set to
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional


def _load_dotenv():
    """Minimal .env loader — duplicated from extract.py rather than imported,
    to avoid coupling the two scripts."""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv()

GARMIN_EMAIL    = os.environ.get("GARMIN_EMAIL", "")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")

# Verified via a real export: FIT sport = 'boxing'. Garmin Connect's REST typeKey
# has not been directly confirmed to also read "boxing" — run --discover once
# against your account to check, and adjust here if it differs.
BOXING_ACTIVITY_TYPES = set(
    t.strip().lower()
    for t in os.environ.get(
        "BOXING_ACTIVITY_TYPES", "boxing,kickboxing,cardio_kickboxing,boxing_fitness"
    ).split(",")
    if t.strip()
)

# See module docstring — cannot be read from the FIT file itself.
PUNCH_FORCE_UNIT = os.environ.get("PUNCH_FORCE_UNIT", "unknown")

# VeloFitness app's iCloud ubiquity container — id "iCloud.mahoneyclan.VeloFitness".
# Apple mangles dots to tildes for the local Mobile Documents mirror folder name.
# Verify this path once against your Mac after the app's iCloud capability is
# provisioned (build the app once, then check ~/Library/Mobile Documents/).
_DEFAULT_ICLOUD_DIR = (
    Path.home() / "Library" / "Mobile Documents" / "iCloud~mahoneyclan~VeloFitness" / "Documents"
)
OUTPUT_DIR  = Path(os.environ.get("ICLOUD_OUTPUT_DIR", str(_DEFAULT_ICLOUD_DIR)))
OUTPUT_FILE = OUTPUT_DIR / "boxing_sessions.json"

# Session-level developer fields (verified real f3b names, lowercased) → schema key.
FIELD_NAME_MAP = {
    "prate":  "punch_rate_avg",     # units: pnch/min
    "tpunch": "total_punches",
    "tjabs":  "total_jab",
    "thooks": "total_hook",         # NB: unit label says "UpCuts" — f3b calls hooks/uppercuts the same bucket
    "tcross": "total_cross",
    "mforce": "punch_force_max_session",  # whole-activity max — units unresolvable, see PUNCH_FORCE_UNIT
    "tstps":  "total_steps",
    "%bat":   "battery_used_pct",   # observed negative (a consumption delta) — abs()'d when built
    # seen but not in our schema (unclear meaning / redundant with standard fields): wep, SpdBag, ttime, tcal
}

# Record-level (per-second) developer fields → (schema key, aggregation).
# "max" and "avg_nonzero" average only samples where something was actually happening
# that second, rather than diluting with the many zero/idle seconds in a stream.
RECORD_FIELD_MAP = {
    "force":   ("punch_force_max_record",    "max"),          # max force within that 1s window
    "vforce":  ("punch_force_avg_1s_record", "avg_nonzero"),  # avg force within that 1s window
    "ee":      ("energy_expenditure",        "avg_nonzero"),  # cal/hr rate
    "stprate": ("step_rate_avg",             "avg_nonzero"),
}


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Garmin Connect
# ─────────────────────────────────────────────────────────────────────────────

def garmin_login():
    from garminconnect import Garmin

    if not GARMIN_EMAIL or not GARMIN_PASSWORD:
        print("GARMIN_EMAIL / GARMIN_PASSWORD not set in .env — cannot continue.")
        sys.exit(1)
    print("Logging in to Garmin Connect...")
    client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    client.login()
    print(f"Connected as {client.get_full_name()}")
    return client


def _within_cutoff(activity: dict, cutoff: Optional[datetime]) -> bool:
    if not cutoff:
        return True
    start_local = activity.get("startTimeLocal", "")
    if not start_local:
        return True
    try:
        return datetime.fromisoformat(start_local[:19]) >= cutoff
    except ValueError:
        return True


def fetch_boxing_activities(client, days: Optional[int]) -> List[dict]:
    cutoff = datetime.now() - timedelta(days=days) if days else None
    matches: List[dict] = []
    start, batch_size = 0, 100

    while True:
        print(f"  Garmin batch from {start}...")
        try:
            batch = client.get_activities(start, batch_size)
        except Exception as e:
            print(f"  Garmin fetch error: {e}")
            break
        if not batch:
            break

        hit_cutoff = False
        for a in batch:
            if not _within_cutoff(a, cutoff):
                hit_cutoff = True
                continue
            type_key = (a.get("activityType", {}) or {}).get("typeKey", "").lower()
            if type_key in BOXING_ACTIVITY_TYPES:
                matches.append(a)

        if hit_cutoff or len(batch) < batch_size:
            break
        start += batch_size

    print(f"  {len(matches)} boxing activities matched (types: {sorted(BOXING_ACTIVITY_TYPES)}).")
    return matches


def discover_activity_types(client, days: int):
    """Print every distinct activityType.typeKey seen, to confirm BOXING_ACTIVITY_TYPES."""
    cutoff = datetime.now() - timedelta(days=days)
    seen: Dict[str, int] = {}
    start, batch_size = 0, 100

    while True:
        try:
            batch = client.get_activities(start, batch_size)
        except Exception as e:
            print(f"  Garmin fetch error: {e}")
            break
        if not batch:
            break

        hit_cutoff = False
        for a in batch:
            if not _within_cutoff(a, cutoff):
                hit_cutoff = True
                continue
            type_key = (a.get("activityType", {}) or {}).get("typeKey", "unknown")
            seen[type_key] = seen.get(type_key, 0) + 1

        if hit_cutoff or len(batch) < batch_size:
            break
        start += batch_size

    print("\nActivity types seen:")
    for k, v in sorted(seen.items(), key=lambda kv: -kv[1]):
        flag = "  <-- currently matched as boxing" if k in BOXING_ACTIVITY_TYPES else ""
        print(f"  {k}: {v}{flag}")


def download_fit(client, activity_id: int) -> bytes:
    from garminconnect import Garmin

    raw = client.download_activity(activity_id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
    return _unzip_fit(raw)


def _unzip_fit(raw: bytes) -> bytes:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            fit_names = [n for n in z.namelist() if n.lower().endswith(".fit")]
            if not fit_names:
                raise ValueError("No .fit file inside downloaded zip")
            return z.read(fit_names[0])
    except zipfile.BadZipFile:
        return raw  # some garminconnect versions return the raw FIT directly


# ─────────────────────────────────────────────────────────────────────────────
# FIT parsing
# ─────────────────────────────────────────────────────────────────────────────

def discover_fields(fit_bytes: bytes, limit_records: int = 3):
    """Print every field name/value/units seen in session + first N record messages,
    plus the union of all distinct record field names across the whole activity."""
    import fitparse

    fitfile = fitparse.FitFile(io.BytesIO(fit_bytes))

    print("  -- session message fields --")
    for msg in fitfile.get_messages("session"):
        for field in msg.fields:
            print(f"    {field.name!r:35s} = {field.value!r} ({field.units})")

    print(f"  -- record message fields (first {limit_records} records) --")
    seen_record_fields = set()
    count = 0
    for msg in fitfile.get_messages("record"):
        if count < limit_records:
            for field in msg.fields:
                print(f"    {field.name!r:35s} = {field.value!r} ({field.units})")
            print("    ---")
        for field in msg.fields:
            seen_record_fields.add((field.name, field.units))
        count += 1

    print(f"  -- all distinct record field names across {count} records --")
    for name, units in sorted(seen_record_fields, key=lambda x: str(x[0])):
        print(f"    {name!r} ({units})")


def parse_boxing_fit(fit_bytes: bytes) -> dict:
    """Extract our schema's fields from one boxing FIT file. Missing fields stay None."""
    import fitparse

    fitfile = fitparse.FitFile(io.BytesIO(fit_bytes))
    out: dict = {k: None for k in set(FIELD_NAME_MAP.values())}
    out.update({k: None for k, _ in RECORD_FIELD_MAP.values()})
    out.update({"_avg_hr": None, "_max_hr": None, "_calories": None, "_duration_s": None})

    # Session-level totals (developer fields) + standard fields
    for msg in fitfile.get_messages("session"):
        by_name = {_normalize(f.name): f for f in msg.fields}
        for raw_name, schema_key in FIELD_NAME_MAP.items():
            f = by_name.get(raw_name)
            if f is not None and f.value is not None:
                out[schema_key] = f.value
        out["_avg_hr"]     = msg.get_value("avg_heart_rate")
        out["_max_hr"]     = msg.get_value("max_heart_rate")
        out["_calories"]   = msg.get_value("total_calories")
        out["_duration_s"] = msg.get_value("total_elapsed_time")

    # Record-level (per-second) streams — aggregate ourselves rather than trusting
    # a session-level summary field, since several of these have no session-level
    # equivalent at all (force is the exception — mForce mirrors max(Force)).
    streams: Dict[str, List[float]] = {key: [] for key, _ in RECORD_FIELD_MAP.values()}
    for msg in fitfile.get_messages("record"):
        by_name = {_normalize(f.name): f for f in msg.fields}
        for raw_name, (schema_key, _agg) in RECORD_FIELD_MAP.items():
            f = by_name.get(raw_name)
            if f is not None and f.value is not None:
                streams[schema_key].append(float(f.value))

    for raw_name, (schema_key, agg) in RECORD_FIELD_MAP.items():
        values = streams[schema_key]
        if not values:
            continue
        if agg == "max":
            out[schema_key] = out[schema_key] or max(values)
        elif agg == "avg_nonzero":
            nonzero = [v for v in values if v != 0]
            if nonzero:
                out[schema_key] = out[schema_key] or (sum(nonzero) / len(nonzero))

    if out.get("battery_used_pct") is not None:
        out["battery_used_pct"] = abs(out["battery_used_pct"])  # observed as a negative delta

    return out


def build_session_record(activity: dict, parsed: dict) -> dict:
    has_force = parsed.get("punch_force_max_session") is not None or parsed.get("punch_force_max_record") is not None
    return {
        "id":                 f"garmin_{activity.get('activityId')}",
        "date":               activity.get("startTimeLocal", ""),
        "duration_s":         int(parsed.get("_duration_s") or activity.get("duration") or 0),
        "avg_hr":             parsed.get("_avg_hr") or activity.get("averageHR"),
        "max_hr":             parsed.get("_max_hr") or activity.get("maxHR"),
        "calories":           parsed.get("_calories") or activity.get("calories"),
        "punch_rate_avg":     parsed.get("punch_rate_avg"),
        "total_punches":      parsed.get("total_punches"),
        "total_jab":          parsed.get("total_jab"),
        "total_hook":         parsed.get("total_hook"),
        "total_cross":        parsed.get("total_cross"),
        "punch_force_max":    parsed.get("punch_force_max_session") or parsed.get("punch_force_max_record"),
        "punch_force_avg_1s": parsed.get("punch_force_avg_1s_record"),
        "punch_force_unit":   PUNCH_FORCE_UNIT if has_force else None,
        "step_rate_avg":      parsed.get("step_rate_avg"),
        "energy_expenditure": parsed.get("energy_expenditure"),
        "total_steps":        parsed.get("total_steps"),
        "battery_used_pct":   parsed.get("battery_used_pct"),
        "source":             "garmin",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", type=str, default=None,
                     help="Parse one local .fit or .zip export and print the result — no Garmin login, no JSON write")
    ap.add_argument("--discover", action="store_true",
                     help="Print raw activity types + dev-field names/units from your account, no JSON write")
    ap.add_argument("--days", type=int, default=None,
                     help="Only look at activities from the last N days (discover defaults to 90)")
    args = ap.parse_args()

    print()
    print("=" * 55)
    print("  Velo Fitness — Boxing Extractor")
    print("=" * 55)
    print()

    if args.file:
        path = Path(args.file)
        raw = path.read_bytes()
        fit_bytes = _unzip_fit(raw) if path.suffix.lower() == ".zip" else raw
        parsed = parse_boxing_fit(fit_bytes)
        fake_activity = {"activityId": path.stem, "startTimeLocal": ""}
        record = build_session_record(fake_activity, parsed)
        print(json.dumps(record, indent=2, default=str))
        return

    client = garmin_login()

    if args.discover:
        print("\n[Discover] activity types seen:")
        discover_activity_types(client, days=args.days or 90)

        activities = fetch_boxing_activities(client, days=args.days or 90)
        for a in activities[:3]:
            print(f"\n[Discover] activity {a.get('activityId')} — {a.get('activityName')}")
            fit_bytes = download_fit(client, a["activityId"])
            discover_fields(fit_bytes)
        if not activities:
            print("\nNo activities matched BOXING_ACTIVITY_TYPES — check the list above and "
                  "set BOXING_ACTIVITY_TYPES in .env to the correct typeKey(s), then re-run --discover.")
        return

    activities = fetch_boxing_activities(client, days=args.days)
    if not activities:
        print("No boxing activities found. Run with --discover to check activity type keys.")
        return

    sessions = []
    for a in activities:
        print(f"  Parsing activity {a.get('activityId')} — {a.get('activityName')}...")
        try:
            fit_bytes = download_fit(client, a["activityId"])
            parsed = parse_boxing_fit(fit_bytes)
            sessions.append(build_session_record(a, parsed))
        except Exception as e:
            print(f"    Failed: {e}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(sessions, f, indent=2, default=str)

    print(f"\nSaved {len(sessions)} boxing sessions to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
