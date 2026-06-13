import json

def run(headers, user_input):
    """Get IME Water Heater orders from the CilioCIO dashboard."""
    # Status filter - default to "New"
    status = user_input.get("status", "New")

    valid_statuses = ["New", "Scheduled", "Completed", "Cancelled", "Couldn't Reach Customer"]
    if status not in valid_statuses:
        return {'status_code': 400, 'body': {'error': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'}}

    page = user_input.get("page", 1)
    if not isinstance(page, int) or page < 1:
        return {'status_code': 400, 'body': {'error': 'page must be a positive integer'}}

    try:
        data = _fetch_orders(headers, status, page)
        if data is None:
            return {'status_code': 401, 'body': {'error': 'Session expired'}}

        result = data.get("d", {})

        # Parse the double-serialized orders JSON
        orders_raw = result.get("OfflineJobs", "[]")
        try:
            orders_list = json.loads(orders_raw)
        except json.JSONDecodeError:
            orders_list = []

        # Map to clean output
        orders = []
        for o in orders_list:
            order = {
                "order_key": str(o.get("myorderkey", "")),
                "import_date": o.get("importdate"),
                "customer_name": o.get("customerfirstlast") or o.get("customerlastfirst"),
                "phone": o.get("customerphone"),
                "work_order_number": o.get("orderstorepo"),
                "store": o.get("store"),
                "status": o.get("orderstatusenum"),
                "quoted_by": o.get("lookupquestion_10213"),
            }
            if status == "Couldn't Reach Customer":
                order["appointment_key"] = "No Response"
            orders.append(order)

        # Extract description from SearchRange HTML
        search_desc = result.get("SearchRange", "")
        search_desc = search_desc.replace("<h3>", "").replace("</h3>", "").replace("Search Results ", "").strip()

        return {
            'status_code': 200,
            'body': {
                'orders': orders,
                'total_count': len(orders),
                'search_description': search_desc
            }
        }
    except Exception as e:
        return {'status_code': 500, 'body': {'error': str(e)}}

# === PRIVATE ===

from curl_cffi import requests

# Map status names to internal IDs
_STATUS_MAP = {
    "New": 10,
    "Scheduled": 30,
    "Completed": 50,
    "Cancelled": 100,
    "Couldn't Reach Customer": 3169,
}

def _fetch_orders(headers, status, page):
    """Fetch order data from the CilioCIO API."""
    base_url = BASE_URL
    status_id = _STATUS_MAP.get(status, 10)

    payload = {
        "userskey": "29420",
        "entitykey": "",
        "theSearchOrderType": 945,
        "theSearchOrderStatus": status_id,
        "theSearchView": "generalorderlist",
        "theSearchFilter": "",
        "theSearchType": "",
        "theDownloadSize": "",
        "thePageNumber": page,
        "theStartMonthRange": "",
        "theEndMonthRange": "",
        "theOrderKey": "",
        "theRecordCount": "",
        "theLinkText": status,
        "theSortFields": "",
        "theSearchFields": ""
    }

    response = requests.post(
        f"{base_url}/WebServices/OrderDetails.asmx/GetOrderData",
        json=payload,
        headers={
            **headers,
            "Content-Type": "application/json; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Referer": f"{base_url}/AccessPartners/Landing.aspx"
        },
        impersonate="chrome131",
        timeout=30
    )

    # Check for session expiration
    if response.status_code == 401 or 'index.aspx' in str(response.url):
        return None

    if response.status_code != 200:
        raise Exception(f'Failed to fetch orders: HTTP {response.status_code}')

    try:
        return response.json()
    except Exception:
        # If response is not JSON, session likely expired (login page redirect)
        return None
