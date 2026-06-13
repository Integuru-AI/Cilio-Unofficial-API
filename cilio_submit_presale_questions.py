import json
import re

def run(headers, user_input):
    """Submit Water Heater Pre-Sale Questions, financial fields, and crew payment adjustment."""
    base_url = BASE_URL

    order_key = user_input.get("order_key")
    if not order_key:
        return {'status_code': 400, 'body': {'error': 'order_key is required'}}

    outcome = user_input.get("outcome")
    if outcome not in ("completed", "no_answer", "declined", "follow_up"):
        return {'status_code': 400, 'body': {'error': 'outcome must be "completed", "no_answer", "declined", or "follow_up"'}}

    # --- Phase 1: Process ALL data fields regardless of outcome ---
    data_result = _handle_data_fields(headers, base_url, order_key, user_input)
    if data_result.get("__session_expired__"):
        return {'status_code': 401, 'body': {'error': 'Session expired'}}
    actions = data_result["actions"]
    errors = data_result["errors"]

    # --- Phase 2: Outcome-specific actions (status + notes) ---
    if outcome == "completed":
        expired = _handle_completed_status(headers, base_url, order_key, actions, errors)
    elif outcome == "no_answer":
        expired = _handle_no_answer(headers, base_url, order_key, user_input, actions, errors)
    elif outcome == "follow_up":
        expired = _handle_follow_up(headers, base_url, order_key, user_input, actions, errors)
    else:
        expired = _handle_declined(headers, base_url, order_key, actions, errors)

    if expired:
        return {'status_code': 401, 'body': {'error': 'Session expired', 'completed_actions': actions}}

    # --- Phase 3: Save and Refresh (full-page postback) ---
    save_expired = _save_and_refresh(headers, base_url, order_key, actions, errors)
    if save_expired:
        return {'status_code': 401, 'body': {'error': 'Session expired during save and refresh', 'completed_actions': actions}}

    if errors:
        return {'status_code': 500, 'body': {'error': f'Some actions failed: {"; ".join(errors)}', 'completed_actions': actions}}

    return {'status_code': 200, 'body': {'success': True, 'actions': actions}}


# === PRIVATE ===

from curl_cffi import requests as curl_requests

_USERS_KEY = "29420"
_QUESTIONNAIRE_KEY = 1809  # Water Heater Pre-Sale Questions
_SOLD_QUESTIONNAIRE_KEY = 2761  # Quoted / Sold Water Heaters

_WH_TYPE_MAP = {
    "Gas": "16283",
    "Electric": "16284",
    "Tankless": "16285",
    "Direct-Vent": "16286",
    "N/A": "16404",
}

_GAS_TYPE_MAP = {
    "Natural Gas": "16378",
    "Liquid Propane": "16379",
}

_LOCATION_MAP = {
    "1st Floor": "26717",
    "2nd Floor": "26718",
    "Attic": "26719",
    "Basement": "26722",
    "Crawlspace": "26723",
    "Garage": "26724",
    "Outdoor Storage": "26725",
}

_WATER_TYPE_MAP = {
    "City": "16289",
    "Well": "16290",
    "Community Well": "16291",
    "N/A": "16405",
}

_INSTALL_PACKAGE_MAP = {
    "E1": "21048",
    "E2": "21049",
    "E3": "21050",
    "E4": "21051",
    "G1": "21068",
    "G2": "21069",
    "G3": "21070",
    "E1-Rural": "21071",
    "E2-Rural": "21072",
    "E3-Rural": "21073",
    "E4-Rural": "21074",
    "G1-Rural": "21075",
    "G2-Rural": "21076",
    "G3-Rural": "21077",
}

_INSTALL_PACKAGE_ITEM_MAP = {
    "E1": "56924",
    "E2": "57423",
    "E3": "57424",
    "E4": "57426",
    "G1": "57481",
    "G2": "57482",
    "G3": "57483",
}

_WH_SERVICE_ITEM_MAP = {
    "Electric": "165893",  # WH-10001 - Electric Water Heater
    "Gas": "165894",       # WH-10002 - Gas Water Heater
}

_WH_ITEM_DESCRIPTION = {
    "Electric": "Electric Water Heater",
    "Gas": "Gas Water Heater",
}

_COMMON_HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


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


def _set_question(headers, base_url, order_key, question_key, questionnaire_key, value):
    payload = {
        "UsersKey": _USERS_KEY,
        "OrderKey": str(order_key),
        "QuestionKey": question_key,
        "QuestionnaireKey": questionnaire_key,
        "QuestionValue": str(value),
    }
    return _make_request(headers, f"{base_url}/WebServices/OrderDetails.asmx/SetQuestionData", payload)


def _set_order_field(headers, base_url, order_key, element_name, value):
    payload = {
        "UsersKey": _USERS_KEY,
        "OrderKey": str(order_key),
        "ElementName": element_name,
        "ElementValue": str(value),
    }
    return _make_request(headers, f"{base_url}/WebServices/OrderDetails.asmx/SetOrderData", payload)


def _add_item(headers, base_url, order_key, item_key, quantity):
    payload = {
        "OrderKey": str(order_key),
        "UsersKey": _USERS_KEY,
        "ProductKey": str(item_key),
        "Quantity": str(quantity),
    }
    return _make_request(headers, f"{base_url}/WebServices/OrderDetails.asmx/AddItemToOrder", payload)


def _save_item_cost(headers, base_url, order_key, order_item_key, cost):
    """Set the wholesale cost (unit cost) on an order line item via SaveOrderItemToOrder."""
    payload = {
        "OrderKey": str(order_key),
        "UsersKey": _USERS_KEY,
        "Item": str(order_item_key),
        "Quantity": "",
        "WholesaleCost": f"${cost:.2f}",
        "RetailCost": "",
        "LaborCost": "",
        "EtaDate": "",
        "ItemStatus": "",
        "Comments": "",
    }
    return _make_request(headers, f"{base_url}/WebServices/OrderDetails.asmx/SaveOrderItemToOrder", payload)


def _parse_order_item_key(response_data, item_description):
    """Extract the data-item key for a specific item from AddItemToOrder response HTML.
    Returns the last match (most recently added item) to handle duplicates."""
    try:
        html = response_data.get("d", {}).get("OrderSalesItems", "")
    except (AttributeError, TypeError):
        return None
    if not html:
        return None
    last_key = None
    start = 0
    while True:
        idx = html.find(item_description, start)
        if idx < 0:
            break
        m = re.search(r"data-item='(\d+)'", html[idx:])
        if m:
            last_key = m.group(1)
        start = idx + len(item_description)
    return last_key


def _convert_date(date_str):
    """Convert YYYY-MM-DD or YYYY/MM/DD to MM/DD/YYYY for the platform."""
    if not date_str:
        return ""
    # Handle both dash and slash separators
    parts = re.split(r"[-/]", date_str)
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[1]}/{parts[2]}/{parts[0]}"
    return date_str


def _submit_presale_dialog(headers, base_url, order_key, presale_values):
    """Submit presale questions (questionnaire 1809) via addDialogWithQAContentRecord API.

    Questionnaire 1809 uses a table-style layout (rptControls) that saves data
    through the dialog content record API, NOT SetQuestionData or form postback.

    presale_values: dict of {question_key: (value, field_name)}
    Returns (ok, result) tuple.
    """
    # Step 1: GET the order page to find MyOrderQuestionnaireKey for questionnaire 1809
    page_url = f"{base_url}/accesspartners/orderview/theorder.aspx?o={order_key}"
    resp = curl_requests.get(
        page_url,
        headers=headers,
        impersonate="chrome131",
        timeout=30,
    )
    if resp.status_code == 401:
        return False, "session_expired"
    url_str = str(resp.url).lower()
    if "index.aspx" in url_str and "theorder" not in url_str:
        return False, "session_expired"
    html = resp.text
    if not html or len(html) < 1000:
        return False, "session_expired"

    # Find the questionnaire 1809 section and its MyOrderQuestionnaireKey
    q1809_match = re.search(
        r'name="[^"]*QuestionnaireRepeater\$ctl\d+\$hdnQuestionnaireKey"[^>]*value="1809"',
        html
    )
    if not q1809_match:
        return False, "Questionnaire 1809 not found on order page"

    # Get the repeater prefix (e.g. ...QuestionnaireRepeater$ctl07)
    prefix_match = re.search(
        r'name="([^"]*QuestionnaireRepeater\$ctl\d+)\$hdnQuestionnaireKey"[^>]*value="1809"',
        html
    )
    if not prefix_match:
        return False, "Could not determine questionnaire prefix"
    qnaire_prefix = prefix_match.group(1)

    # Find MyOrderQuestionnaireKey for this questionnaire
    moqk_match = re.search(
        re.escape(qnaire_prefix) + r'\$hdnMyOrderQuestionnaireKey"[^>]*value="(\d+)"',
        html
    )
    if not moqk_match:
        return False, "MyOrderQuestionnaireKey not found for questionnaire 1809"
    order_qnaire_key = moqk_match.group(1)

    # Step 2: Check for existing rows via GetDialogWithQAContent
    dialog_payload = {
        "UsersKey": _USERS_KEY,
        "OrderKey": str(order_key),
        "OrderQuestionnaireKey": order_qnaire_key,
    }
    ok, dialog_result = _make_request(
        headers,
        f"{base_url}/WebServices/OrderDetails.asmx/GetDialogWithQAContent",
        dialog_payload,
    )
    if not ok:
        return False, dialog_result

    # Check if there are existing rows (look for data-row in DialogWithQAContent)
    existing_row = ""
    dialog_content = ""
    try:
        d = dialog_result.get("d", {})
        dialog_content = d.get("DialogWithQAContent", "")
        existing_defaults = d.get("HtmlDialogWithQAContent", "{}")
    except (AttributeError, TypeError):
        existing_defaults = "{}"

    existing_rows = re.findall(r"data-row='(\d+)'", dialog_content)
    if existing_rows:
        # Edit the first existing row instead of creating a duplicate
        existing_row = existing_rows[0]

    # Step 3: Build RowData from presale_values
    # Start with existing defaults if editing
    try:
        row_data = json.loads(existing_defaults) if existing_defaults else {}
    except (json.JSONDecodeError, TypeError):
        row_data = {}

    # Apply the new values
    for qkey, (value, field_name) in presale_values.items():
        row_data[f"question_{qkey}"] = value

    # Set required metadata fields
    row_data["data-orderquestionnairekey"] = order_qnaire_key
    row_data["data-rowkey"] = existing_row  # empty = new, number = edit

    # Step 4: Save via addDialogWithQAContentRecord
    save_payload = {
        "OrderKey": str(order_key),
        "UsersKey": _USERS_KEY,
        "RowData": json.dumps(row_data),
    }
    ok, result = _make_request(
        headers,
        f"{base_url}/WebServices/OrderDetails.asmx/addDialogWithQAContentRecord",
        save_payload,
    )
    if not ok:
        return False, result

    return True, result


# =============================================================================
# SHARED: Data fields (runs for ALL outcomes)
# =============================================================================

def _handle_data_fields(headers, base_url, order_key, user_input):
    """Process all data fields regardless of outcome.
    Returns dict with 'actions', 'errors', and optional '__session_expired__'."""
    actions = []
    errors = []

    # --- Validate enum fields upfront ---
    wh_type = user_input.get("wh_type")
    if wh_type and wh_type not in _WH_TYPE_MAP:
        return {"actions": [], "errors": [f'Invalid wh_type. Must be one of: {", ".join(_WH_TYPE_MAP.keys())}']}
    gas_type = user_input.get("gas_type")
    if gas_type and gas_type not in _GAS_TYPE_MAP:
        return {"actions": [], "errors": [f'Invalid gas_type. Must be one of: {", ".join(_GAS_TYPE_MAP.keys())}']}
    location = user_input.get("location")
    if location and location not in _LOCATION_MAP:
        return {"actions": [], "errors": [f'Invalid location. Must be one of: {", ".join(_LOCATION_MAP.keys())}']}
    water_type = user_input.get("water_type")
    if water_type and water_type not in _WATER_TYPE_MAP:
        return {"actions": [], "errors": [f'Invalid water_type. Must be one of: {", ".join(_WATER_TYPE_MAP.keys())}']}
    install_package = user_input.get("install_package")
    if install_package and install_package not in _INSTALL_PACKAGE_MAP:
        return {"actions": [], "errors": [f'Invalid install_package. Must be one of: {", ".join(_INSTALL_PACKAGE_MAP.keys())}']}

    # --- Pre-sale questions (QuestionnaireKey 1809) via dialog content record API ---
    # Build the question values to set
    presale_values = {}
    presale_values[10213] = ("Sarah", "quoted_by")

    call_date = user_input.get("call_date")
    if call_date:
        presale_values[10214] = (_convert_date(call_date), "call_date")

    if wh_type:
        presale_values[10102] = (_WH_TYPE_MAP[wh_type], "wh_type")

    if gas_type:
        presale_values[10184] = (_GAS_TYPE_MAP[gas_type], "gas_type")

    if location:
        presale_values[19630] = (_LOCATION_MAP[location], "location")

    location_notes = user_input.get("location_notes")
    if location_notes:
        presale_values[10103] = (location_notes, "location_notes")

    wh_size = user_input.get("wh_size")
    if wh_size:
        presale_values[10104] = (wh_size, "wh_size")

    if water_type:
        presale_values[10105] = (_WATER_TYPE_MAP[water_type], "water_type")

    presale_notes = user_input.get("notes")
    if presale_notes:
        presale_values[14020] = (presale_notes, "notes")

    if presale_values:
        ok, result = _submit_presale_dialog(headers, base_url, order_key, presale_values)
        if not ok:
            if result == "session_expired":
                return {"actions": actions, "errors": errors, "__session_expired__": True}
            errors.append(f"presale_questions: {result}")
        else:
            for qkey, (val, field_name) in presale_values.items():
                actions.append(field_name)

    # --- Financial fields ---

    outcome = user_input.get("outcome")
    po_amount = user_input.get("purchase_order_amount")
    adjusted_po = None
    if po_amount is not None and outcome == "completed":
        adjusted_po = round(float(po_amount) * 0.85, 2)
        ok, result = _set_order_field(headers, base_url, order_key, "txtInvoiceAmount", str(adjusted_po))
        if not ok:
            if result == "session_expired":
                return {"actions": actions, "errors": errors, "__session_expired__": True}
            errors.append(f"purchase_order_amount: {result}")
        else:
            actions.append("purchase_order_amount")

    # --- Financial/line-item fields (only for completed outcome) ---
    water_heater_cost = user_input.get("water_heater_cost")

    if outcome == "completed":
        wh_item_number = user_input.get("wh_item_number")
        store_number = user_input.get("store_number")
        if wh_item_number and store_number:
            item_num_value = f"{wh_item_number}-Store {store_number}"
            ok, result = _set_order_field(headers, base_url, order_key, "txtPermitNumber", item_num_value)
            if not ok:
                if result == "session_expired":
                    return {"actions": actions, "errors": errors, "__session_expired__": True}
                errors.append(f"wh_item_number: {result}")
            else:
                actions.append("wh_item_number")

        if water_heater_cost is not None:
            ok, result = _set_question(headers, base_url, order_key, 14026, _SOLD_QUESTIONNAIRE_KEY, str(water_heater_cost))
            if not ok:
                if result == "session_expired":
                    return {"actions": actions, "errors": errors, "__session_expired__": True}
                errors.append(f"water_heater_cost: {result}")
            else:
                actions.append("water_heater_cost")

        if install_package:
            pkg_val = _INSTALL_PACKAGE_MAP[install_package]
            ok, result = _set_question(headers, base_url, order_key, 14027, _SOLD_QUESTIONNAIRE_KEY, pkg_val)
            if not ok:
                if result == "session_expired":
                    return {"actions": actions, "errors": errors, "__session_expired__": True}
                errors.append(f"install_package: {result}")
            else:
                actions.append("install_package")

            pkg_item = _INSTALL_PACKAGE_ITEM_MAP.get(install_package)
            if pkg_item:
                ok, result = _add_item(headers, base_url, order_key, pkg_item, 1)
                if not ok:
                    if result == "session_expired":
                        return {"actions": actions, "errors": errors, "__session_expired__": True}
                    errors.append(f"install_package_item: {result}")
                else:
                    actions.append("install_package_item_added")

        service_item_qty = user_input.get("service_item_qty")
        if service_item_qty and wh_type and wh_type in _WH_SERVICE_ITEM_MAP:
            wh_item_key = _WH_SERVICE_ITEM_MAP[wh_type]
            ok, result = _add_item(headers, base_url, order_key, wh_item_key, service_item_qty)
            if not ok:
                if result == "session_expired":
                    return {"actions": actions, "errors": errors, "__session_expired__": True}
                errors.append(f"wh_service_item: {result}")
            else:
                actions.append("wh_service_item_added")
                # Set unit cost on the newly added WH service item
                if water_heater_cost is not None:
                    item_desc = _WH_ITEM_DESCRIPTION.get(wh_type, "")
                    order_item_key = _parse_order_item_key(result, item_desc)
                    if order_item_key:
                        ok2, res2 = _save_item_cost(headers, base_url, order_key, order_item_key, float(water_heater_cost))
                        if not ok2:
                            if res2 == "session_expired":
                                return {"actions": actions, "errors": errors, "__session_expired__": True}
                            errors.append(f"wh_unit_cost: {res2}")
                        else:
                            actions.append(f"wh_unit_cost_set({water_heater_cost})")
                    else:
                        errors.append("wh_unit_cost: could not find order item key in response")

        # Calculate labor amount and labor hours from material_cost + labor_cost
        material_cost = user_input.get("material_cost")
        labor_cost = user_input.get("labor_cost")
        if material_cost is not None and labor_cost is not None:
            try:
                mat = float(material_cost)
                lab = float(labor_cost)
                labor_amount = round((mat + lab) * 0.85 - mat, 2)
                labor_hours = round(labor_amount / 150, 2)

                ok, result = _set_order_field(headers, base_url, order_key, "txtLaborAmount", str(labor_amount))
                if not ok:
                    if result == "session_expired":
                        return {"actions": actions, "errors": errors, "__session_expired__": True}
                    errors.append(f"labor_amount: {result}")
                else:
                    actions.append(f"labor_amount({labor_amount})")

                ok, result = _set_order_field(headers, base_url, order_key, "txtTotalLaborHours", str(labor_hours))
                if not ok:
                    if result == "session_expired":
                        return {"actions": actions, "errors": errors, "__session_expired__": True}
                    errors.append(f"total_labor_hours: {result}")
                else:
                    actions.append(f"total_labor_hours({labor_hours})")
            except (ValueError, TypeError):
                errors.append("labor calculation: material_cost and labor_cost must be numbers")

        # Crew payment adjustment (only if PO amount provided)
        if adjusted_po is not None:
            crew_result = _adjust_crew_payment(headers, base_url, order_key, adjusted_po, actions, errors)
            if crew_result:
                return {"actions": actions, "errors": errors, "__session_expired__": True}

    return {"actions": actions, "errors": errors}


# =============================================================================
# OUTCOME: COMPLETED — set status to 20 (Scheduled)
# =============================================================================

def _handle_completed_status(headers, base_url, order_key, actions, errors):
    """Set status to Scheduled (20). Returns True if session expired."""
    ok, result = _set_order_field(headers, base_url, order_key, "lstOrderStatus", "20")
    if not ok:
        if result == "session_expired":
            return True
        errors.append(f"status_update: {result}")
    else:
        actions.append("status_set_scheduled")
    return False


# =============================================================================
# OUTCOME: NO ANSWER — set status to 3169 (Couldn't Reach Customer) + note
# =============================================================================

def _handle_no_answer(headers, base_url, order_key, user_input, actions, errors):
    """Set status + add note. Returns True if session expired."""
    note_text = user_input.get("note", "")

    ok, result = _set_order_field(headers, base_url, order_key, "lstOrderStatus", "3169")
    if not ok:
        if result == "session_expired":
            return True
        errors.append(f"status_update: {result}")
    else:
        actions.append("status_set_couldnt_reach")

    if note_text:
        expired = _add_note(headers, base_url, order_key, note_text, actions, errors)
        if expired:
            return True

    return False


# =============================================================================
# OUTCOME: FOLLOW UP — set status to 3167 (Follow Up On Lead/Estimate) + note
# =============================================================================

def _handle_follow_up(headers, base_url, order_key, user_input, actions, errors):
    """Set status + add note. Returns True if session expired."""
    note_text = user_input.get("note", "")

    ok, result = _set_order_field(headers, base_url, order_key, "lstOrderStatus", "3167")
    if not ok:
        if result == "session_expired":
            return True
        errors.append(f"status_update: {result}")
    else:
        actions.append("status_set_follow_up")

    if note_text:
        expired = _add_note(headers, base_url, order_key, note_text, actions, errors)
        if expired:
            return True

    return False


# =============================================================================
# OUTCOME: DECLINED — set status to 99 (Canceled)
# =============================================================================

def _handle_declined(headers, base_url, order_key, actions, errors):
    """Set status to Canceled (99). Returns True if session expired."""
    ok, result = _set_order_field(headers, base_url, order_key, "lstOrderStatus", "99")
    if not ok:
        if result == "session_expired":
            return True
        errors.append(f"status_update: {result}")
    else:
        actions.append("status_set_cancelled")
    return False


# =============================================================================
# HELPERS
# =============================================================================

def _add_note(headers, base_url, order_key, note_text, actions, errors):
    """Add an in-house note. Returns True if session expired."""
    note_payload = {
        "OrderKey": str(order_key),
        "UsersKey": _USERS_KEY,
        "AddNoteOptions": json.dumps(["cbinhousenote|true"]),
        "AddNoteAttachments": "",
        "HTMLContent": note_text,
        "HTMLSubjectContent": "",
        "AvailableContacts": "",
        "AvailableEmails": "",
        "AlternateEmails": "",
        "AvailableAccountContacts": "",
        "Source": "",
        "SendFrom": "",
    }
    ok, result = _make_request(headers, f"{base_url}/WebServices/OrderNotes.asmx/AddNote", note_payload)
    if not ok:
        if result == "session_expired":
            return True
        errors.append(f"note: {result}")
    else:
        actions.append("note_added")
    return False


def _adjust_crew_payment(headers, base_url, order_key, po_amount, actions, errors):
    """Fetch crew payment rows, subtract PO amount from auto-calculated total, update via API.
    Returns True if session expired."""
    resp = curl_requests.get(
        f"{base_url}/accesspartners/orderview/theorder.aspx?o={order_key}",
        headers=headers,
        impersonate="chrome131",
        timeout=30,
    )
    if resp.status_code == 401:
        return True
    url_str = str(resp.url).lower()
    if "index.aspx" in url_str and "theorder" not in url_str:
        return True
    html = resp.text

    # Find payment row inputs with data-key (crew payment rows)
    payment_inputs = []
    for m in re.finditer(r'<input[^>]*?txtPaymentAmount"[^>]*?>', html):
        tag = m.group(0)
        dk = re.search(r'data-key="(\d+)"', tag)
        val = re.search(r'value="\$?([\d,.]+)"', tag)
        if dk and val:
            payment_inputs.append((dk.group(1), val.group(1)))
    if not payment_inputs:
        # No crew payment section — order may not be scheduled yet. Skip silently.
        return False

    payment_key = payment_inputs[0][0]
    auto_amount = float(payment_inputs[0][1].replace(",", ""))

    new_total = auto_amount - po_amount
    if new_total < 0:
        new_total = 0

    payload = {
        "OrderKey": str(order_key),
        "UsersKey": _USERS_KEY,
        "OrderPaymentKey": payment_key,
        "PaymentAmount": f"${new_total:.2f}",
        "Type": "PY",
    }
    ok, result = _make_request(headers, f"{base_url}/WebServices/OrderDetails.asmx/SetPaymentData", payload)
    if not ok:
        if result == "session_expired":
            return True
        errors.append(f"crew_payment: {result}")
    else:
        actions.append(f"crew_payment_adjusted({auto_amount}-{po_amount}={new_total:.2f})")

    return False


def _save_and_refresh(headers, base_url, order_key, actions, errors):
    """Perform the 'Save and Refresh' full-page postback on the order page.
    This is an ASP.NET WebForms postback that commits all pending changes.
    Returns True if session expired."""
    from urllib.parse import urlencode, quote_plus

    page_url = f"{base_url}/AccessPartners/OrderView/theOrder.aspx?o={order_key}"

    # Step 1: GET the page to obtain __VIEWSTATE, __EVENTVALIDATION, and form fields
    resp = curl_requests.get(
        page_url,
        headers=headers,
        impersonate="chrome131",
        timeout=30,
    )
    if resp.status_code == 401:
        return True
    url_str = str(resp.url).lower()
    if "index.aspx" in url_str and "theorder" not in url_str:
        return True
    html = resp.text
    if not html or len(html) < 1000:
        return True

    # Step 2: Extract all form fields from the HTML
    form_data = _extract_form_fields(html)
    if not form_data:
        errors.append("save_and_refresh: could not extract form fields")
        return False

    # Step 3: Add the button click field
    form_data["ctl00$Contentsection$btnUpdate"] = "Save and Refresh"

    # Step 4: POST the form back
    post_headers = {
        **headers,
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": base_url,
        "Referer": page_url,
    }
    # Remove JSON-specific headers if present
    post_headers.pop("Accept", None)
    post_headers.pop("X-Requested-With", None)

    resp = curl_requests.post(
        page_url,
        data=form_data,
        headers=post_headers,
        impersonate="chrome131",
        timeout=60,
        allow_redirects=True,
    )
    if resp.status_code == 401:
        return True
    url_str = str(resp.url).lower()
    if "index.aspx" in url_str and "theorder" not in url_str:
        return True

    # A successful postback returns 200 with the refreshed page
    if resp.status_code == 200:
        actions.append("save_and_refresh")
    else:
        errors.append(f"save_and_refresh: HTTP {resp.status_code}")

    return False


def _extract_form_fields(html):
    """Extract all form field values (input, select, textarea) from the ASP.NET page HTML.
    Returns a dict of field_name -> value suitable for form POST."""
    fields = {}

    # Extract hidden inputs and text inputs (value attribute)
    for m in re.finditer(
        r'<input[^>]*?name="([^"]*)"[^>]*?>',
        html, re.IGNORECASE
    ):
        tag = m.group(0)
        name = m.group(1)

        # Skip unchecked checkboxes and radio buttons
        input_type = ""
        type_match = re.search(r'type="([^"]*)"', tag, re.IGNORECASE)
        if type_match:
            input_type = type_match.group(1).lower()

        if input_type in ("checkbox", "radio"):
            if 'checked' not in tag.lower():
                continue

        # Get value
        val_match = re.search(r'value="([^"]*)"', tag, re.IGNORECASE)
        value = val_match.group(1) if val_match else ""

        # For submit buttons, skip (we add btnUpdate manually)
        if input_type == "submit":
            continue

        fields[name] = value

    # Also handle inputs where name comes after value
    for m in re.finditer(
        r'<input[^>]*?value="([^"]*)"[^>]*?name="([^"]*)"[^>]*?>',
        html, re.IGNORECASE
    ):
        tag = m.group(0)
        value = m.group(1)
        name = m.group(2)

        input_type = ""
        type_match = re.search(r'type="([^"]*)"', tag, re.IGNORECASE)
        if type_match:
            input_type = type_match.group(1).lower()

        if input_type in ("checkbox", "radio"):
            if 'checked' not in tag.lower():
                continue
        if input_type == "submit":
            continue

        if name not in fields:
            fields[name] = value

    # Extract selected option from <select> elements
    # Match select blocks and find their selected option
    for sel_match in re.finditer(
        r'<select[^>]*?name="([^"]*)"[^>]*?>(.*?)</select>',
        html, re.IGNORECASE | re.DOTALL
    ):
        name = sel_match.group(1)
        options_html = sel_match.group(2)
        # Find selected option
        selected = re.search(
            r'<option[^>]*?selected[^>]*?value="([^"]*)"',
            options_html, re.IGNORECASE
        )
        if selected:
            fields[name] = selected.group(1)
        else:
            # No explicit selected - use first option value
            first = re.search(r'<option[^>]*?value="([^"]*)"', options_html, re.IGNORECASE)
            if first:
                fields[name] = first.group(1)
            else:
                fields[name] = ""

    # Extract textarea values
    for ta_match in re.finditer(
        r'<textarea[^>]*?name="([^"]*)"[^>]*?>(.*?)</textarea>',
        html, re.IGNORECASE | re.DOTALL
    ):
        name = ta_match.group(1)
        value = ta_match.group(2)
        fields[name] = value

    return fields
