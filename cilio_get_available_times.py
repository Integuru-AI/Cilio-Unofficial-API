import re
import json

try:
    BASE_URL
except NameError:
    BASE_URL = "https://login.ciliocio.com"

try:
    APP_URL
except NameError:
    APP_URL = BASE_URL

def run(headers, user_input):
    """Get available water heater time slots for a technician on a given date.

    Finds pre-booked WATER HEATER time blocks on the scheduler and subtracts
    all scheduled appointments to return the remaining free time intervals.
    """
    base_url = BASE_URL

    tech_name = user_input.get("tech_name")
    if not tech_name:
        return {'status_code': 400, 'body': {'error': 'tech_name is required'}}

    date_str = user_input.get("date")
    if not date_str:
        return {'status_code': 400, 'body': {'error': 'date is required (YYYY-MM-DD)'}}

    try:
        columns, events = _fetch_dayview(headers, base_url, date_str)
    except SessionExpired:
        return {'status_code': 401, 'body': {'error': 'Session expired'}}
    except Exception as e:
        return {'status_code': 500, 'body': {'error': str(e)}}

    # Match tech by name (case-insensitive partial match)
    matched = _match_tech(columns, tech_name)
    if not matched:
        available_names = [c["name"] for c in columns]
        return {'status_code': 404, 'body': {
            'error': f'Tech "{tech_name}" not found',
            'available_techs': sorted(available_names)
        }}

    tech_id = matched["id"]
    tech_display = matched["name"]

    # Filter events for this tech
    tech_events = [e for e in events if e.get("resource") == tech_id]

    # Find WATER HEATER time blocks (the full availability windows)
    wh_blocks = _find_wh_blocks(tech_events, date_str)

    if not wh_blocks:
        return {
            'status_code': 200,
            'body': {
                'tech_name': tech_display,
                'date': date_str,
                'free_blocks': [],
            }
        }

    # Find ALL scheduled appointments on this tech for the date (excluding TBZERO blocks)
    appointments = _find_all_appointments(tech_events, date_str)

    # Subtract appointments from WH blocks to get free time
    free_blocks = []
    for block in wh_blocks:
        block_free = _subtract_appointments(block, appointments)
        free_blocks.extend(block_free)

    # Sort by start time
    free_blocks.sort(key=lambda x: x["start"])

    return {
        'status_code': 200,
        'body': {
            'tech_name': tech_display,
            'date': date_str,
            'free_blocks': free_blocks,
        }
    }


# === PRIVATE ===

from curl_cffi import requests as curl_requests

class SessionExpired(Exception):
    pass


def _fetch_dayview(headers, base_url, date_str):
    """Fetch and parse the DayView scheduler page."""
    # Convert YYYY-MM-DD to YYYY-M-D (no zero-padding)
    parts = date_str.split("-")
    if len(parts) == 3:
        day_param = f"{parts[0]}-{int(parts[1])}-{int(parts[2])}"
    else:
        day_param = date_str

    resp = curl_requests.get(
        f"{base_url}/AccessMobileScheduler/DayView.aspx?day={day_param}",
        headers=headers,
        impersonate="chrome131",
        timeout=30,
    )

    if resp.status_code == 401 or "index.aspx" in str(resp.url).lower():
        # Check if we got redirected to login
        if "index.aspx" in str(resp.url).lower() and "DayView" not in str(resp.url):
            raise SessionExpired()

    html = resp.text

    if not html or len(html) < 1000:
        raise SessionExpired()

    # Parse columns: v.columns = [...]
    columns = _parse_json_block(html, "v.columns = [")
    if columns is None:
        raise Exception("Could not parse scheduler columns from page")

    # Parse events: v.events.list = [...]
    events = _parse_json_block(html, "v.events.list = [")
    if events is None:
        events = []

    return columns, events


def _parse_json_block(html, marker):
    """Parse a JSON array from HTML using bracket matching."""
    idx = html.find(marker)
    if idx < 0:
        return None

    bracket_start = html.index("[", idx)
    depth = 0
    for i in range(bracket_start, min(bracket_start + 1000000, len(html))):
        if html[i] == "[":
            depth += 1
        elif html[i] == "]":
            depth -= 1
        if depth == 0:
            return json.loads(html[bracket_start:i + 1])
    return None


def _match_tech(columns, search_name):
    """Match a tech by name. Tries exact, then case-insensitive contains."""
    search_lower = search_name.lower()

    # Exact match first
    for c in columns:
        if c["name"].lower() == search_lower:
            return c

    # Partial match
    matches = [c for c in columns if search_lower in c["name"].lower()]
    if len(matches) == 1:
        return matches[0]

    # Try matching each word
    if not matches:
        words = search_lower.split()
        matches = [c for c in columns if all(w in c["name"].lower() for w in words)]

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        # Return the shortest name match (most specific)
        return min(matches, key=lambda c: len(c["name"]))

    return None


def _find_wh_blocks(tech_events, date_str):
    """Find WATER HEATER time blocks (placeholder slots) for a specific date.
    These are identifiable by text starting with 'TBZERO' and html containing 'WATER HEATER'.
    DayView loads 2 days, so we filter by date.
    """
    blocks = []
    for e in tech_events:
        text = e.get("text", "")
        html = e.get("html", "")
        if text.startswith("TBZERO") and "WATER HEATER" in html.upper():
            start_iso = e.get("start", "")
            if not _matches_date(start_iso, date_str):
                continue
            s = _to_time(start_iso)
            t = _to_time(e.get("end", ""))
            if s and t:
                blocks.append({"start": s, "end": t})
    blocks.sort(key=lambda x: x["start"])
    return blocks


def _find_all_appointments(tech_events, date_str):
    """Find ALL scheduled appointments (non-TBZERO events) for a specific date.
    Any event that occupies time during the WH block reduces availability.
    """
    appointments = []
    for e in tech_events:
        text = e.get("text", "")
        # Skip the TBZERO placeholder blocks themselves
        if text.startswith("TBZERO"):
            continue
        start_iso = e.get("start", "")
        if not _matches_date(start_iso, date_str):
            continue
        s = _to_time(start_iso)
        t = _to_time(e.get("end", ""))
        if s and t:
            appointments.append({"start": s, "end": t})
    appointments.sort(key=lambda x: x["start"])
    return appointments


def _subtract_appointments(block, appointments):
    """Subtract appointment times from a block and return remaining free intervals.

    block: {"start": "HH:MM", "end": "HH:MM"}
    appointments: [{"start": "HH:MM", "end": "HH:MM"}, ...]

    Returns list of {"start": "HH:MM", "end": "HH:MM"} free intervals, sorted by start.
    """
    block_start = _time_to_minutes(block["start"])
    block_end = _time_to_minutes(block["end"])

    # Collect appointment intervals that overlap with this block
    occupied = []
    for appt in appointments:
        a_start = _time_to_minutes(appt["start"])
        a_end = _time_to_minutes(appt["end"])
        # Only consider if it overlaps with the block
        if a_start < block_end and a_end > block_start:
            # Clamp to block boundaries
            occupied.append((max(a_start, block_start), min(a_end, block_end)))

    if not occupied:
        return [{"start": block["start"], "end": block["end"]}]

    # Merge overlapping occupied intervals
    occupied.sort()
    merged = [occupied[0]]
    for start, end in occupied[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Compute free gaps
    free = []
    cursor = block_start
    for occ_start, occ_end in merged:
        if cursor < occ_start:
            free.append({"start": _minutes_to_time(cursor), "end": _minutes_to_time(occ_start)})
        cursor = max(cursor, occ_end)
    if cursor < block_end:
        free.append({"start": _minutes_to_time(cursor), "end": _minutes_to_time(block_end)})

    return free


def _matches_date(iso_str, date_str):
    """Check if an ISO datetime string matches a YYYY-MM-DD date."""
    if not iso_str or not date_str:
        return False
    # iso_str is like "2026-05-04T08:00:00", date_str is "2026-05-04"
    return iso_str[:10] == date_str


def _to_time(iso_str):
    """Extract HH:MM from ISO datetime string."""
    if not iso_str:
        return ""
    match = re.search(r"T(\d{2}:\d{2})", iso_str)
    return match.group(1) if match else ""


def _time_to_minutes(t):
    """Convert HH:MM to minutes since midnight."""
    if not t:
        return 0
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _minutes_to_time(m):
    """Convert minutes since midnight to HH:MM."""
    return f"{m // 60:02d}:{m % 60:02d}"
