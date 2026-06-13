import json
import re

BASE_URL = "https://login.ciliocio.com"
APP_URL = "https://login.ciliocio.com/index.aspx"

def run(headers, user_input):
    """Schedule a technician appointment with built-in availability check."""
    base_url = BASE_URL

    order_key = user_input.get("order_key")
    if not order_key:
        return {'status_code': 400, 'body': {'error': 'order_key is required'}}

    tech_name = user_input.get("tech_name")
    if not tech_name:
        return {'status_code': 400, 'body': {'error': 'tech_name is required'}}

    schedule_date = user_input.get("schedule_date")
    if not schedule_date:
        return {'status_code': 400, 'body': {'error': 'schedule_date is required (YYYY-MM-DD)'}}

    start_time = user_input.get("start_time")
    if not start_time:
        return {'status_code': 400, 'body': {'error': 'start_time is required (HH:MM 24h)'}}

    wh_type = user_input.get("wh_type")
    duration = int(user_input.get("duration", 180))

    # --- Step 1: Check availability via Water Heater time blocks ---
    try:
        columns, events = _fetch_dayview(headers, base_url, schedule_date)
    except SessionExpired:
        return {'status_code': 401, 'body': {'error': 'Session expired'}}
    except Exception as e:
        return {'status_code': 500, 'body': {'error': f'Failed to fetch schedule: {str(e)}'}}

    matched = _match_tech(columns, tech_name)
    if not matched:
        available_names = sorted([c["name"] for c in columns])
        return {'status_code': 404, 'body': {
            'error': f'Tech "{tech_name}" not found on scheduler',
            'available_techs': available_names,
        }}

    tech_id_dayview = matched["id"]
    tech_display = matched["name"]

    # Get all events for this tech
    tech_events = [e for e in events if e.get("resource") == tech_id_dayview]

    # Find WATER HEATER time blocks (placeholder slots) for the requested date
    wh_blocks = _find_wh_blocks(tech_events, schedule_date)
    if not wh_blocks:
        return {'status_code': 409, 'body': {
            'error': f'No water heater time block found for {tech_display} on {schedule_date}',
            'tech_name': tech_display,
            'date': schedule_date,
            'wh_blocks': [],
        }}

    # Find existing WH appointments (actual booked jobs) for the requested date
    wh_appointments = _find_wh_appointments(tech_events, schedule_date)

    # Compute free slots: take WH blocks, subtract overlapping appointments
    req_start = _time_to_minutes(start_time)
    req_end = req_start + duration
    free_slots = _compute_free_slots(wh_blocks, wh_appointments)

    # Check if the requested time fits entirely within any free slot
    fits = False
    for slot in free_slots:
        ss = _time_to_minutes(slot["start"])
        se = _time_to_minutes(slot["end"])
        if req_start >= ss and req_end <= se:
            fits = True
            break

    if not fits:
        return {'status_code': 409, 'body': {
            'error': f'Cannot schedule {start_time}-{_minutes_to_time(req_end)} for {tech_display}',
            'tech_name': tech_display,
            'date': schedule_date,
            'free_slots': free_slots,
        }}

    # --- Step 2: Set labor category (if wh_type provided) ---
    actions = []
    errors = []

    if wh_type and wh_type in _LABOR_CATEGORY_MAP:
        ok, result = _set_order_field(headers, base_url, order_key, "lstLaborCategory", _LABOR_CATEGORY_MAP[wh_type])
        if not ok:
            if result == "session_expired":
                return {'status_code': 401, 'body': {'error': 'Session expired'}}
            errors.append(f"labor_category: {result}")
        else:
            actions.append("labor_category_set")

    # --- Step 3: Resolve tech ID for scheduling API ---
    tech_id, err = _resolve_tech_id(headers, base_url, order_key, tech_name)
    if not tech_id:
        if err and "session_expired" in err:
            return {'status_code': 401, 'body': {'error': 'Session expired'}}
        return {'status_code': 404, 'body': {'error': err}}

    # --- Step 4: Book the appointment ---
    sched_payload = {
        "OrderKey": str(order_key),
        "UsersKey": _USERS_KEY,
        "SchedUsers": str(tech_id),
        "ScheduleDate": _convert_date(schedule_date),
        "StartTimeHour": _convert_time_24_to_12(start_time),
        "TaskTimeEstimation": str(duration),
        "HistoryEnum": 0,
        "TaskType": _VISIT_TYPE_KEY,
    }
    ok, result = _make_request(headers, f"{base_url}/WebServices/OrderDetails.asmx/ScheduleAppointment", sched_payload)
    if not ok:
        if result == "session_expired":
            return {'status_code': 401, 'body': {'error': 'Session expired'}}
        return {'status_code': 500, 'body': {'error': f'Scheduling failed: {result}'}}

    actions.append("appointment_scheduled")

    # --- Step 5: Set status to Scheduled ---
    ok, result = _set_order_field(headers, base_url, order_key, "lstOrderStatus", "20")
    if not ok:
        if result == "session_expired":
            return {'status_code': 401, 'body': {'error': 'Session expired', 'completed_actions': actions}}
        errors.append(f"status_update: {result}")
    else:
        actions.append("status_set_scheduled")

    if errors:
        return {'status_code': 500, 'body': {'error': f'Some actions failed: {"; ".join(errors)}', 'completed_actions': actions}}

    return {'status_code': 200, 'body': {
        'success': True,
        'actions': actions,
        'tech_name': tech_display,
        'date': schedule_date,
        'start_time': start_time,
        'end_time': _minutes_to_time(req_end),
    }}


# === PRIVATE ===

from curl_cffi import requests as curl_requests

_USERS_KEY = "29420"
_ENTITY_KEY = "1814"
_VISIT_TYPE_KEY = "6930"

_LABOR_CATEGORY_MAP = {
    "Gas": "456",
    "Electric": "457",
    "Tankless": "6252",
}

_COMMON_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}

class SessionExpired(Exception):
    pass


def _make_request(headers, url, payload):
    resp = curl_requests.post(
        url,
        json=payload,
        headers={**headers, **_COMMON_HEADERS},
        impersonate="chrome131",
        timeout=30,
    )
    if resp.status_code == 401 or "index.aspx" in str(resp.url):
        return False, "session_expired"
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    try:
        data = resp.json()
    except Exception:
        return False, "session_expired"
    return True, data


def _set_order_field(headers, base_url, order_key, element_name, value):
    payload = {
        "UsersKey": _USERS_KEY,
        "OrderKey": str(order_key),
        "ElementName": element_name,
        "ElementValue": str(value),
    }
    return _make_request(headers, f"{base_url}/WebServices/OrderDetails.asmx/SetOrderData", payload)


def _convert_date(date_str):
    if not date_str:
        return ""
    parts = date_str.split("-")
    if len(parts) == 3:
        return f"{parts[1]}/{parts[2]}/{parts[0]}"
    return date_str


def _convert_time_24_to_12(time_24):
    if not time_24:
        return ""
    parts = time_24.split(":")
    hour = int(parts[0])
    minute = parts[1]
    if hour == 0:
        return f"12:{minute} AM"
    elif hour < 12:
        return f"{hour}:{minute} AM"
    elif hour == 12:
        return f"12:{minute} PM"
    else:
        return f"{hour - 12}:{minute} PM"


def _resolve_tech_id(headers, base_url, order_key, tech_name):
    payload = {
        "OrderKey": str(order_key),
        "UsersKey": _USERS_KEY,
        "EntityKey": _ENTITY_KEY,
    }
    ok, data = _make_request(
        headers,
        f"{base_url}/WebServices/OrderDetails.asmx/GetSchedulingResourcesByJob",
        payload,
    )
    if not ok:
        return None, f"Failed to fetch tech list: {data}"

    users = data.get("d", {}).get("UsersToSchedule", [])
    if not users:
        return None, "No technicians found for this order"

    search = tech_name.lower()
    for u in users:
        if u["Text"].lower() == search:
            return u["Value"], None
    matches = [u for u in users if search in u["Text"].lower()]
    if len(matches) == 1:
        return matches[0]["Value"], None
    if not matches:
        words = search.split()
        matches = [u for u in users if all(w in u["Text"].lower() for w in words)]
    if len(matches) == 1:
        return matches[0]["Value"], None
    if len(matches) > 1:
        return min(matches, key=lambda u: len(u["Text"]))["Value"], None
    return None, f'Tech "{tech_name}" not found'


# --- DayView parsing (availability check) ---

def _fetch_dayview(headers, base_url, date_str):
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
        if "index.aspx" in str(resp.url).lower() and "DayView" not in str(resp.url):
            raise SessionExpired()

    html = resp.text
    if not html or len(html) < 1000:
        raise SessionExpired()

    columns = _parse_json_block(html, "v.columns = [")
    if columns is None:
        raise Exception("Could not parse scheduler columns from page")

    events = _parse_json_block(html, "v.events.list = [")
    if events is None:
        events = []

    return columns, events


def _parse_json_block(html, marker):
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
    search_lower = search_name.lower()
    for c in columns:
        if c["name"].lower() == search_lower:
            return c
    matches = [c for c in columns if search_lower in c["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        words = search_lower.split()
        matches = [c for c in columns if all(w in c["name"].lower() for w in words)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return min(matches, key=lambda c: len(c["name"]))
    return None


def _to_time(iso_str):
    if not iso_str:
        return ""
    match = re.search(r"T(\d{2}:\d{2})", iso_str)
    return match.group(1) if match else ""


def _time_to_minutes(t):
    if not t:
        return 0
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _minutes_to_time(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def _matches_date(iso_str, date_str):
    """Check if an ISO datetime string matches a YYYY-MM-DD date."""
    if not iso_str or not date_str:
        return False
    return iso_str[:10] == date_str


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


def _find_wh_appointments(tech_events, date_str):
    """Find actual Water Heater appointments (booked jobs, not time blocks) for a specific date.
    These have 'Water Heater' in their areas HTML and text is NOT a TBZERO block.
    """
    appointments = []
    for e in tech_events:
        text = e.get("text", "")
        if text.startswith("TBZERO"):
            continue
        start_iso = e.get("start", "")
        if not _matches_date(start_iso, date_str):
            continue
        # Check areas for Water Heater description
        is_wh = False
        desc = ""
        for area in e.get("areas", []):
            area_html = area.get("html", "")
            if "Water Heater" in area_html:
                is_wh = True
                desc = re.sub(r"<br\s*/?>", " | ", area_html)
                desc = re.sub(r"<[^>]+>", "", desc).strip()
                break
        if is_wh:
            s = _to_time(start_iso)
            t = _to_time(e.get("end", ""))
            tag = e.get("tag", [])
            if s and t:
                appointments.append({"start": s, "end": t, "description": desc, "order_key": tag[0] if tag else ""})
    appointments.sort(key=lambda x: x["start"])
    return appointments


def _compute_free_slots(wh_blocks, wh_appointments):
    """Compute free time slots by subtracting appointments from WH blocks.

    For each WH block, remove any overlapping appointment time ranges.
    The remaining gaps are the free slots where new appointments can be booked.
    """
    free_slots = []
    for block in wh_blocks:
        bs = _time_to_minutes(block["start"])
        be = _time_to_minutes(block["end"])

        # Collect all appointments that overlap this block
        overlaps = []
        for appt in wh_appointments:
            as_ = _time_to_minutes(appt["start"])
            ae = _time_to_minutes(appt["end"])
            if as_ < be and ae > bs:
                # Clip appointment to block boundaries
                overlaps.append((max(as_, bs), min(ae, be)))

        if not overlaps:
            # Entire block is free
            free_slots.append({"start": block["start"], "end": block["end"]})
            continue

        # Sort overlaps by start time and merge any that overlap each other
        overlaps.sort()
        merged = [overlaps[0]]
        for start, end in overlaps[1:]:
            if start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        # Compute gaps between merged appointments within the block
        cursor = bs
        for ostart, oend in merged:
            if cursor < ostart:
                free_slots.append({"start": _minutes_to_time(cursor), "end": _minutes_to_time(ostart)})
            cursor = max(cursor, oend)
        if cursor < be:
            free_slots.append({"start": _minutes_to_time(cursor), "end": _minutes_to_time(be)})

    return free_slots
