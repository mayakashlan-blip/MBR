"""Load MBR data from Omni Analytics API."""

import base64
import calendar
import copy
import json
import os
import urllib.request
from .data_schema import MBRData, StaffMember, ServiceItem, MembershipType


BASE_URL = "https://moxie.omniapp.co/api"
DASHBOARD_ID = "bfd963dd"
STAFF_DASHBOARD_ID = "955002e5"
SUPPLIES_DASHBOARD_ID = "54d5da36"
RETENTION_DASHBOARD_ID = "59ca3051"
MARKETING_DASHBOARD_ID = "0ef3afa3"

# Query names → their date filter fields (per-topic)
# Omni requires TIME_FOR_INTERVAL_DURATION kind for date filtering.
# The field must belong to the same topic as the query.
QUERY_DATE_FIELDS = {
    # transaction-based queries — use transaction_date_et to match Omni dashboard
    "KPI: Net Revenue": "dbt__moxie_invoice_transactions_mart.transaction_date_et",
    "Payments & Refunds": "dbt__moxie_invoice_transactions_mart.transaction_date_et",
    # appointment-based queries — use start_time
    "KPI: Paid Appointments": "dbt__moxie_appointments_mart.start_time",
    "KPI: AOV": "dbt__moxie_appointments_mart.start_time",
    "Client Counts": "dbt__moxie_appointments_mart.start_time",
    # invoice-based queries — use invoice_issued_date
    "Total Membership Revenue": "dbt__moxie_invoices_mart.invoice_issued_date",
    "Gross Revenue Breakdown Summary": "dbt__moxie_invoices_mart.invoice_issued_date",
    "Retail to Service Revenue": "dbt__moxie_invoices_mart.invoice_issued_date",
    "Gross Revenue By Official Service Type": "dbt__moxie_invoices_mart.invoice_issued_date",
    # utilization topic — use series_date
    "Utilization": "dbt__moxie_utilization_daily_mart.series_date",
    # memberships topic — started_at for new, ended_at for churned
    "Active Members": None,  # active = point-in-time, no date filter needed
    "New Memberships": "dbt__moxie_client_memberships_mart.started_at",
    "Churned Memberships": "dbt__moxie_client_memberships_mart.ended_at",
}


def _api_get(path: str, api_key: str):
    req = urllib.request.Request(f"{BASE_URL}{path}")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _run_query(query_body: dict, api_key: str, retries: int = 4) -> dict:
    """Execute an Omni query and return the parsed Arrow result as a dict.

    Retries on HTTP 429 (rate limit) and 5xx with exponential backoff,
    honoring the Retry-After header when Omni provides one.
    """
    import pyarrow.ipc
    import time

    last_error = None
    for attempt in range(retries + 1):
        data = json.dumps({"query": query_body}).encode()
        req = urllib.request.Request(f"{BASE_URL}/v1/query/run", data=data, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            # Retry on rate-limit (429) and transient server errors (5xx)
            if e.code == 429 or 500 <= e.code < 600:
                last_error = e
                if attempt < retries:
                    retry_after = e.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait = min(int(retry_after), 30)
                    else:
                        wait = min(2 ** attempt, 16)  # 1, 2, 4, 8, 16
                    time.sleep(wait)
                    continue
            raise

        for line in raw.strip().split("\n"):
            parsed = json.loads(line)
            if parsed.get("status") == "COMPLETE" and "result" in parsed:
                arrow_bytes = base64.b64decode(parsed["result"])
                reader = pyarrow.ipc.open_stream(arrow_bytes)
                table = reader.read_all()
                return table.to_pydict()
            if parsed.get("status") == "FAILED":
                raise RuntimeError(f"Omni query failed: {parsed.get('error_message', 'unknown')}")

        if attempt < retries:
            time.sleep(2)  # Brief pause before retry

    if last_error is not None:
        raise last_error
    raise RuntimeError("No result returned from Omni query")


def _add_filters(query: dict, practice_name: str, start_date: str,
                 date_field: str = None, duration: str = "1 months") -> dict:
    """Add practice name and date range filters to a query.

    Uses TIME_FOR_INTERVAL_DURATION (start_date + duration) which is
    the only date filter kind Omni actually respects via the API.

    duration can be "1 months", "3 months", "12 months", etc.
    """
    q = copy.deepcopy(query)
    q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
        "kind": "EQUALS",
        "type": "string",
        "values": [practice_name],
        "is_negative": False,
    }
    if date_field:
        q["filters"][date_field] = {
            "kind": "TIME_FOR_INTERVAL_DURATION",
            "type": "date",
            "ui_type": "PAST",
            "left_side": start_date,
            "right_side": duration,
            "is_negative": False,
        }
    return q


def _val(result: dict, key_substring: str, default=0):
    """Extract a single value from an Omni result dict by partial key match."""
    for k, v in result.items():
        if key_substring in k and v:
            val = v[0]
            if val is not None:
                # Arrow may return Decimal — coerce to float
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return val
    return default


def _safe_mom(current, previous, min_prev=0):
    """Compute MoM % change, capped at +/-999%. Returns None if previous too small."""
    if not previous or previous <= min_prev or not current:
        return None
    pct = (current - previous) / previous
    return max(-9.99, min(9.99, pct))


def _find_query(queries: dict, name: str) -> dict:
    """Find a query by name, with helpful error if missing."""
    if name in queries:
        return queries[name]
    # Try partial match
    for qname, qbody in queries.items():
        if name.lower() in qname.lower():
            return qbody
    raise KeyError(f"Query '{name}' not found in dashboard. Available: {list(queries.keys())}")


def load_from_omni(practice_name: str, month: int, year: int,
                   api_key: str = None, duration_months: int = 1) -> MBRData:
    """Load MBR data from Omni Analytics API.

    Args:
        practice_name: Exact practice name as it appears in Omni.
        month: Starting month number (1-12).
        year: Starting year.
        api_key: Omni API key. Falls back to OMNI_API_KEY env var.
        duration_months: How many months of data to pull (default 1).
                         Use 3 for QBR, 12 for annual review, etc.

    Returns:
        Populated MBRData instance.
    """
    api_key = api_key or os.environ.get("OMNI_API_KEY")
    if not api_key:
        raise ValueError("No Omni API key provided. Set OMNI_API_KEY or pass --omni-key.")

    # First day of the month (TIME_FOR_INTERVAL_DURATION adds duration from here)
    start_date = f"{year}-{month:02d}-01"
    duration = f"{duration_months} months"

    # Fetch all query definitions from the dashboard
    print(f"  Connecting to Omni API...")
    dashboard = _api_get(f"/v1/documents/{DASHBOARD_ID}/queries", api_key)
    queries = {q["name"]: q["query"] for q in dashboard.get("queries", [])}
    print(f"  Found {len(queries)} queries in dashboard")

    data = MBRData(practice_name=practice_name, month=month, year=year)

    # Get practice tier (provider_segment_post_launch) from Medspa Name query
    try:
        tier_q = copy.deepcopy(queries.get("Medspa Name", {}))
        if tier_q:
            tier_q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            tier_field = "dbt__moxie_medspas_mart.provider_segment_post_launch"
            if tier_field not in tier_q.get("fields", []):
                tier_q.setdefault("fields", []).append(tier_field)
            tier_q["limit"] = 5
            tier_r = _run_query(tier_q, api_key)
            tiers = tier_r.get(tier_field, [])
            if tiers and tiers[0]:
                data.tier = str(tiers[0])
                print(f"  Tier: {data.tier}")
                # Hide executive summary by default for Silver/Momentum/Growth tiers
                if data.tier in ("Silver", "Momentum", "Growth"):
                    data.show_executive_summary = False
    except Exception as e:
        print(f"  Warning: Could not load tier: {e}")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def run(name: str) -> dict:
        q = _find_query(queries, name)
        date_field = QUERY_DATE_FIELDS.get(name)
        q = _add_filters(q, practice_name, start_date, date_field, duration)
        return _run_query(q, api_key)

    def run_safe(name: str) -> dict:
        """Run a query, returning empty dict on failure."""
        try:
            return run(name)
        except Exception as e:
            print(f"  Warning: query '{name}' failed: {e}")
            return {}

    # ── Execute queries in parallel ──
    print(f"  Querying Omni for {practice_name}, {calendar.month_name[month]} {year}...")

    # Batch 1: all independent current-month queries
    batch1_names = [
        "KPI: Net Revenue", "KPI: Paid Appointments", "KPI: AOV",
        "Client Counts", "New Memberships", "Churned Memberships",
        "Total Membership Revenue", "Gross Revenue Breakdown Summary",
        "Retail to Service Revenue", "Gross Revenue By Official Service Type",
        "Utilization", "Payments & Refunds",
    ]
    batch1 = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(run_safe, name): name for name in batch1_names}
        for future in as_completed(futures):
            batch1[futures[future]] = future.result()

    # Active Members needs mrr_sum field added — run separately
    try:
        active_q = _find_query(queries, "Active Members")
        active_q = _add_filters(active_q, practice_name, start_date, QUERY_DATE_FIELDS.get("Active Members"))
        mrr_field = "dbt__moxie_client_memberships_mart.mrr_sum"
        if mrr_field not in active_q.get("fields", []):
            active_q.setdefault("fields", []).append(mrr_field)
        batch1["Active Members"] = _run_query(active_q, api_key)
    except Exception as e:
        print(f"  Warning: Active Members query failed: {e}")
        batch1["Active Members"] = {}

    # ── Process batch 1 results ──
    r = batch1.get("KPI: Net Revenue", {})
    data.monthly_net_revenue = _val(r, "net_revenue_sum")
    revenue_goal = _val(r, "revenue_goal_sum")
    data.pct_net_revenue_goal = (data.monthly_net_revenue / revenue_goal
                                  if revenue_goal > 0 else 0)

    r = batch1.get("KPI: Paid Appointments", {})
    data.total_appointments = int(_val(r, "paid_appointments"))

    r = batch1.get("KPI: AOV", {})
    data.aov = _val(r, "aov")
    aov_goal = _val(r, "aov_goal")
    data.pct_aov_goal = data.aov / aov_goal if aov_goal > 0 else 0

    # ── Previous Month MoM + QTD (parallel batch 2) ──
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_start = f"{prev_year}-{prev_month:02d}-01"
    print(f"  Loading prior month + QTD in parallel...")

    def run_prev(name: str):
        try:
            q = _find_query(queries, name)
            date_field = QUERY_DATE_FIELDS.get(name)
            q = _add_filters(q, practice_name, prev_start, date_field)
            return _run_query(q, api_key)
        except Exception:
            return None

    def run_qtd():
        quarter_start_month = ((month - 1) // 3) * 3 + 1
        months_in_quarter = month - quarter_start_month + 1
        if months_in_quarter <= 1:
            return data.monthly_net_revenue
        qtd_start = f"{year}-{quarter_start_month:02d}-01"
        qtd_q = _find_query(queries, "KPI: Net Revenue")
        qtd_date_field = QUERY_DATE_FIELDS.get("KPI: Net Revenue")
        qtd_q = copy.deepcopy(qtd_q)
        qtd_q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
            "kind": "EQUALS", "type": "string",
            "values": [practice_name], "is_negative": False,
        }
        qtd_q["filters"][qtd_date_field] = {
            "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
            "ui_type": "PAST", "left_side": qtd_start,
            "right_side": f"{months_in_quarter} months", "is_negative": False,
        }
        try:
            return _val(_run_query(qtd_q, api_key), "net_revenue_sum")
        except Exception:
            return data.monthly_net_revenue

    mom_results = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        mom_futures = {
            pool.submit(run_prev, "KPI: Net Revenue"): "prev_rev",
            pool.submit(run_prev, "KPI: Paid Appointments"): "prev_appt",
            pool.submit(run_prev, "KPI: AOV"): "prev_aov",
            pool.submit(run_prev, "Utilization"): "prev_util",
            pool.submit(run_qtd): "qtd",
        }
        for future in as_completed(mom_futures):
            mom_results[mom_futures[future]] = future.result()

    data.quarter_to_date = mom_results.get("qtd", data.monthly_net_revenue)

    prev_rev_r = mom_results.get("prev_rev")
    if prev_rev_r:
        prev_revenue = _val(prev_rev_r, "net_revenue_sum")
        data.revenue_mom_pct = _safe_mom(data.monthly_net_revenue, prev_revenue, 100)

    prev_appt_r = mom_results.get("prev_appt")
    if prev_appt_r:
        prev_appointments = int(_val(prev_appt_r, "paid_appointments"))
        data.appointments_mom_pct = _safe_mom(data.total_appointments, prev_appointments, 5)

    prev_aov_r = mom_results.get("prev_aov")
    if prev_aov_r:
        prev_aov = _val(prev_aov_r, "aov")
        data.aov_mom_pct = _safe_mom(data.aov, prev_aov, 20)

    # MoM for utilization
    prev_util_r = mom_results.get("prev_util")
    prev_util = None
    if prev_util_r:
        prev_util = _val(prev_util_r, "column_b_divided_by_column_a", default=None)
        if prev_util is not None:
            prev_util = prev_util if prev_util <= 1.0 else prev_util / 100
        else:
            pa = _val(prev_util_r, "total_available_hours")
            pt = _val(prev_util_r, "total_appointment_hours")
            prev_util = pt / pa if pa and pa > 0 else None

    print(f"  MoM: Rev {'N/A' if data.revenue_mom_pct is None else f'{data.revenue_mom_pct:+.1%}'}, "
          f"Appts {'N/A' if data.appointments_mom_pct is None else f'{data.appointments_mom_pct:+.1%}'}, "
          f"AOV {'N/A' if data.aov_mom_pct is None else f'{data.aov_mom_pct:+.1%}'}")

    # Utilization (from batch1)
    r = batch1.get("Utilization", {})
    util_pct = _val(r, "column_b_divided_by_column_a", default=None)
    if util_pct is not None:
        data.utilization_rate = util_pct if util_pct <= 1.0 else util_pct / 100
    else:
        total_avail = _val(r, "total_available_hours")
        total_appt = _val(r, "total_appointment_hours")
        data.utilization_rate = total_appt / total_avail if total_avail > 0 else 0

    # Utilization MoM
    if prev_util_r and prev_util:
        data.utilization_mom_pct = _safe_mom(data.utilization_rate, prev_util, 0.05)

    # Client Counts (from batch1)
    r = batch1.get("Client Counts", {})
    new_client_appts = int(_val(r, "count_new_client_appointments"))
    existing_client_appts = int(_val(r, "count_existing_client_appointments"))
    total_unique_clients = int(_val(r, "paid_appointment_clients"))
    total_appts_for_split = new_client_appts + existing_client_appts
    if total_appts_for_split > 0 and total_unique_clients > 0:
        new_pct = new_client_appts / total_appts_for_split
        data.new_clients = round(total_unique_clients * new_pct)
        data.existing_clients = total_unique_clients - data.new_clients
    else:
        data.new_clients = new_client_appts
        data.existing_clients = existing_client_appts

    # Memberships (Active Members from batch1 with mrr_sum)
    r = batch1.get("Active Members", {})
    data.memberships_active = int(_val(r, "count"))
    data.mrr = _val(r, "mrr_sum")

    r = batch1.get("New Memberships", {})
    data.memberships_new = int(_val(r, "count"))

    r = batch1.get("Churned Memberships", {})
    data.memberships_cancelled = int(_val(r, "count"))

    # Total Membership Revenue
    data.membership_sales = _val(batch1.get("Total Membership Revenue", {}), "subtotal__membership_sum")

    # Membership breakdown by type
    try:
        mem_name_field = "dbt__moxie_client_memberships_mart.membership_name"
        pf = {"kind": "EQUALS", "type": "string", "values": [practice_name], "is_negative": False}
        def _date_f(field):
            return {"kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                    "ui_type": "PAST", "left_side": start_date,
                    "right_side": duration, "is_negative": False}

        # Active by type
        aq = copy.deepcopy(queries["Active Members"])
        aq["fields"].append(mem_name_field)
        aq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = pf
        active_r = _run_query(aq, api_key)
        active_names = []
        active_counts = []
        for k, v in active_r.items():
            if "membership_name" in k:
                active_names = v
            elif "count" in k:
                active_counts = v

        # New by type
        nq = copy.deepcopy(queries["New Memberships"])
        nq["fields"].append(mem_name_field)
        nq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = pf
        nq["filters"]["dbt__moxie_client_memberships_mart.started_at"] = _date_f("started_at")
        new_r = _run_query(nq, api_key)
        new_names, new_counts, new_mrrs = [], [], []
        for k, v in new_r.items():
            if "membership_name" in k:
                new_names = v
            elif "mrr_sum" in k:
                new_mrrs = v
            elif "count" in k:
                new_counts = v

        # Churned by type
        cq = copy.deepcopy(queries["Churned Memberships"])
        cq["fields"].append(mem_name_field)
        cq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = pf
        cq["filters"]["dbt__moxie_client_memberships_mart.ended_at"] = _date_f("ended_at")
        churned_r = _run_query(cq, api_key)
        churned_names, churned_counts = [], []
        for k, v in churned_r.items():
            if "membership_name" in k:
                churned_names = v
            elif "count" in k:
                churned_counts = v

        # Merge into MembershipType objects
        all_names = set()
        active_lookup = {}
        for i, name in enumerate(active_names):
            if name:
                all_names.add(name)
                active_lookup[name] = int(active_counts[i]) if i < len(active_counts) and active_counts[i] else 0
        new_lookup, mrr_lookup = {}, {}
        for i, name in enumerate(new_names):
            if name:
                all_names.add(name)
                new_lookup[name] = int(new_counts[i]) if i < len(new_counts) and new_counts[i] else 0
                mrr_lookup[name] = float(new_mrrs[i]) if i < len(new_mrrs) and new_mrrs[i] else 0
        churned_lookup = {}
        for i, name in enumerate(churned_names):
            if name:
                all_names.add(name)
                churned_lookup[name] = int(churned_counts[i]) if i < len(churned_counts) and churned_counts[i] else 0

        for name in sorted(all_names):
            data.membership_types.append(MembershipType(
                name=name,
                active=active_lookup.get(name, 0),
                new=new_lookup.get(name, 0),
                churned=churned_lookup.get(name, 0),
                mrr=mrr_lookup.get(name, 0),
            ))
        # Sort by active count descending
        data.membership_types.sort(key=lambda m: m.active, reverse=True)
        print(f"  Membership types: {len(data.membership_types)} loaded")
    except Exception as e:
        print(f"  Warning: Could not load membership breakdown: {e}")

    # Gross Revenue Breakdown (from batch1)
    r = batch1.get("Gross Revenue Breakdown Summary", {})
    data.service_revenue = _val(r, "subtotal__service_sum")
    data.retail_revenue = _val(r, "subtotal__retail_product_sum")
    data.prepayment_revenue = _val(r, "subtotal__package_sum")
    data.custom_items = _val(r, "subtotal__custom_item_sum")
    data.total_gross = (data.service_revenue + data.retail_revenue +
                        data.prepayment_revenue + data.custom_items +
                        data.membership_sales)
    data.discounts = _val(r, "discounts_sum")
    data.client_fees = _val(r, "fees_sum")

    # Payments & Refunds (from batch1)
    pr = batch1.get("Payments & Refunds", {})
    data.redemptions = _val(pr, "refund_amount_sum")

    # Retail to Service Ratio (from batch1)
    r = batch1.get("Retail to Service Revenue", {})
    ratio = _val(r, "calc_1", default=None)
    if ratio is not None:
        data.retail_to_service_ratio = ratio if ratio <= 1.0 else ratio / 100
    elif data.service_revenue > 0:
        data.retail_to_service_ratio = data.retail_revenue / data.service_revenue

    # Service Mix (from batch1)
    r = batch1.get("Gross Revenue By Official Service Type", {})
    svc_names = []
    svc_revs = []
    svc_pcts = []
    for k, v in r.items():
        if "service_category" in k:
            svc_names = v
        elif "gross_revenue_sum" in k:
            svc_revs = v
        elif "calc_1" in k:
            svc_pcts = v

    for i in range(len(svc_names)):
        if svc_names[i]:
            rev = float(svc_revs[i]) if i < len(svc_revs) and svc_revs[i] else 0
            pct = float(svc_pcts[i]) if i < len(svc_pcts) and svc_pcts[i] else 0
            if pct and pct > 1:
                pct = pct  # already percentage
            elif pct:
                pct = pct * 100
            data.services.append(ServiceItem(
                name=svc_names[i],
                revenue=rev,
                pct_of_total=pct,
            ))
    data.services.sort(key=lambda s: s.revenue, reverse=True)

    # ── Staff Performance (separate dashboard) ──
    print("  Loading staff performance...")
    try:
        staff_dash = _api_get(f"/v1/documents/{STAFF_DASHBOARD_ID}/queries", api_key)
        staff_queries = {q["name"]: q["query"] for q in staff_dash.get("queries", [])}

        # Employee Sales Metrics
        sq = copy.deepcopy(staff_queries["Employee Sales Metrics"])
        sq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
            "kind": "EQUALS", "type": "string",
            "values": [practice_name], "is_negative": False,
        }
        sq["filters"]["dbt__moxie_invoices_mart.invoice_issued_date"] = {
            "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
            "ui_type": "PAST", "left_side": start_date,
            "right_side": duration, "is_negative": False,
        }
        # Add gross revenue field if not already present
        gross_field = "dbt__moxie_invoice_line_items_mart.gross_revenue_sum"
        if gross_field not in sq.get("fields", []):
            sq.setdefault("fields", []).append(gross_field)
        staff_r = _run_query(sq, api_key)

        # Rebooking Rate (per provider)
        rq = copy.deepcopy(staff_queries["Rebooking Rate"])
        rq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
            "kind": "EQUALS", "type": "string",
            "values": [practice_name], "is_negative": False,
        }
        rq["filters"]["dbt__moxie_appointments_mart.start_time"] = {
            "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
            "ui_type": "PAST", "left_side": start_date,
            "right_side": duration, "is_negative": False,
        }
        rebook_r = _run_query(rq, api_key)

        # Per-provider utilization (from main dashboard Utilization query + provider dimension)
        util_q = copy.deepcopy(queries["Utilization"])
        util_q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
            "kind": "EQUALS", "type": "string",
            "values": [practice_name], "is_negative": False,
        }
        util_q["filters"]["dbt__moxie_utilization_daily_mart.series_date"] = {
            "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
            "ui_type": "PAST", "left_side": start_date,
            "right_side": duration, "is_negative": False,
        }
        util_q["fields"].append("dbt__moxie_utilization_daily_mart.provider_name")
        util_r = _run_query(util_q, api_key)

        util_names = []
        util_rates = []
        util_appt_hours = []
        for k, v in util_r.items():
            if "provider_name" in k:
                util_names = v
            elif "column_b_divided_by_column_a" in k:
                util_rates = v
            elif "total_appointment_hours" in k:
                util_appt_hours = v
        util_lookup = {}
        hours_lookup = {}
        for i in range(len(util_names)):
            if util_names[i]:
                if util_rates[i] is not None:
                    rate = float(util_rates[i])
                    util_lookup[util_names[i]] = min(rate, 1.0)
                if i < len(util_appt_hours) and util_appt_hours[i] is not None:
                    hours_lookup[util_names[i]] = float(util_appt_hours[i])

        # Build rebooking lookup: provider_name → rate
        rebook_names = []
        rebook_rates = []
        for k, v in rebook_r.items():
            if "provider_name" in k:
                rebook_names = v
            elif "rebooking_rate" in k:
                rebook_rates = v
        rebook_lookup = {}
        for i in range(len(rebook_names)):
            if rebook_names[i]:
                rebook_lookup[rebook_names[i]] = float(rebook_rates[i]) if rebook_rates[i] else 0

        # Parse staff data
        names = []
        net_revs = []
        gross_revs = []
        aovs = []
        retail_revs = []
        svc_revs_staff = []
        for k, v in staff_r.items():
            if "attributed_provider_name" in k:
                names = v
            elif "sum_line_net_revenue" in k and "retail" not in k:
                net_revs = v
            elif "gross_revenue_sum" in k and "retail" not in k:
                gross_revs = v
            elif "avg_net_revenue_per_attributed_invoice" in k:
                aovs = v
            elif "sum_line_net_revenue_retail" in k:
                retail_revs = v
            elif "gross_revenue_retail_sum" in k:
                # Use gross retail as fallback if net retail not available
                if not retail_revs:
                    retail_revs = v

        for i in range(len(names)):
            name = names[i]
            if not name:  # skip unattributed row
                continue
            net_rev = float(net_revs[i]) if i < len(net_revs) and net_revs[i] else 0
            gross_rev = float(gross_revs[i]) if i < len(gross_revs) and gross_revs[i] else net_rev
            aov_val = float(aovs[i]) if i < len(aovs) and aovs[i] else 0
            retail = float(retail_revs[i]) if i < len(retail_revs) and retail_revs[i] else 0
            svc_rev = net_rev - retail  # service rev = total net - retail net
            rebook = rebook_lookup.get(name, 0)

            data.staff.append(StaffMember(
                name=name,
                net_revenue=net_rev,
                gross_revenue=gross_rev,
                aov=aov_val,
                utilization=util_lookup.get(name),
                rebooking_rate=rebook,
                service_revenue=max(svc_rev, 0),
                retail_revenue=retail,
                hours_worked=hours_lookup.get(name),
            ))

        data.staff.sort(key=lambda s: s.gross_revenue, reverse=True)

        # ── Per-provider MoM: run prior-month staff queries ──
        try:
            # Prior-month Employee Sales Metrics
            prev_sq = copy.deepcopy(staff_queries["Employee Sales Metrics"])
            prev_sq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            prev_sq["filters"]["dbt__moxie_invoices_mart.invoice_issued_date"] = {
                "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                "ui_type": "PAST", "left_side": prev_start,
                "right_side": "1 months", "is_negative": False,
            }
            if gross_field not in prev_sq.get("fields", []):
                prev_sq.setdefault("fields", []).append(gross_field)
            prev_staff_r = _run_query(prev_sq, api_key)

            # Parse prior-month staff data into lookups
            prev_names, prev_net_revs, prev_gross_revs, prev_aovs = [], [], [], []
            for k, v in prev_staff_r.items():
                if "attributed_provider_name" in k:
                    prev_names = v
                elif "sum_line_net_revenue" in k and "retail" not in k:
                    prev_net_revs = v
                elif "gross_revenue_sum" in k and "retail" not in k:
                    prev_gross_revs = v
                elif "avg_net_revenue_per_attributed_invoice" in k:
                    prev_aovs = v

            prev_rev_lookup = {}
            prev_aov_lookup = {}
            for i in range(len(prev_names)):
                if prev_names[i]:
                    prev_rev_lookup[prev_names[i]] = float(prev_gross_revs[i]) if i < len(prev_gross_revs) and prev_gross_revs[i] else 0
                    prev_aov_lookup[prev_names[i]] = float(prev_aovs[i]) if i < len(prev_aovs) and prev_aovs[i] else 0

            # Prior-month utilization per provider
            prev_util_q = copy.deepcopy(queries["Utilization"])
            prev_util_q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            prev_util_q["filters"]["dbt__moxie_utilization_daily_mart.series_date"] = {
                "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                "ui_type": "PAST", "left_side": prev_start,
                "right_side": "1 months", "is_negative": False,
            }
            prev_util_q["fields"].append("dbt__moxie_utilization_daily_mart.provider_name")
            prev_util_r = _run_query(prev_util_q, api_key)

            prev_util_names, prev_util_rates, prev_util_hours = [], [], []
            for k, v in prev_util_r.items():
                if "provider_name" in k:
                    prev_util_names = v
                elif "column_b_divided_by_column_a" in k:
                    prev_util_rates = v
                elif "total_appointment_hours" in k:
                    prev_util_hours = v
            prev_util_lookup = {}
            prev_hours_lookup = {}
            for i in range(len(prev_util_names)):
                if prev_util_names[i]:
                    if i < len(prev_util_rates) and prev_util_rates[i] is not None:
                        val = float(prev_util_rates[i])
                        prev_util_lookup[prev_util_names[i]] = val if val <= 1.0 else val / 100
                    if i < len(prev_util_hours) and prev_util_hours[i] is not None:
                        prev_hours_lookup[prev_util_names[i]] = float(prev_util_hours[i])

            # Prior-month rebooking per provider
            prev_rebook_lookup = {}
            try:
                prev_rq = copy.deepcopy(staff_queries["Rebooking Rate"])
                prev_rq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                    "kind": "EQUALS", "type": "string",
                    "values": [practice_name], "is_negative": False,
                }
                prev_rq["filters"]["dbt__moxie_appointments_mart.start_time"] = {
                    "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                    "ui_type": "PAST", "left_side": prev_start,
                    "right_side": "1 months", "is_negative": False,
                }
                prev_rebook_r = _run_query(prev_rq, api_key)
                prev_rebook_names_m, prev_rebook_vals_m = [], []
                for k, v in prev_rebook_r.items():
                    if "provider_name" in k:
                        prev_rebook_names_m = v
                    elif "rebooking_rate" in k:
                        prev_rebook_vals_m = v
                for i in range(len(prev_rebook_names_m)):
                    if prev_rebook_names_m[i] and i < len(prev_rebook_vals_m) and prev_rebook_vals_m[i] is not None:
                        prev_rebook_lookup[prev_rebook_names_m[i]] = float(prev_rebook_vals_m[i])
            except Exception as e:
                print(f"  Warning: Could not load prior-month rebooking: {e}")

            # Apply MoM to each staff member
            for s in data.staff:
                s.revenue_mom_pct = _safe_mom(s.gross_revenue, prev_rev_lookup.get(s.name), 500)
                s.aov_mom_pct = _safe_mom(s.aov, prev_aov_lookup.get(s.name), 50)
                s.utilization_mom_pct = _safe_mom(s.utilization, prev_util_lookup.get(s.name), 0.05)
                s.rebooking_mom_pct = _safe_mom(s.rebooking_rate, prev_rebook_lookup.get(s.name), 0.05)
                prev_hrs = prev_hours_lookup.get(s.name)
                prev_gr = prev_rev_lookup.get(s.name)
                if prev_hrs and prev_hrs > 0 and prev_gr and prev_gr > 500 and s.rev_per_hour:
                    s.rev_per_hour_mom_pct = _safe_mom(s.rev_per_hour, prev_gr / prev_hrs, 10)

            print(f"  Staff MoM: loaded for {sum(1 for s in data.staff if s.revenue_mom_pct is not None)} providers")
        except Exception as e:
            print(f"  Warning: Could not load staff MoM: {e}")

        # Practice-level rebooking rate: weighted average from provider data
        total_rebook_weight = sum(s.net_revenue for s in data.staff if s.rebooking_rate)
        if total_rebook_weight > 0:
            data.rebooking_rate = sum(
                s.rebooking_rate * s.net_revenue
                for s in data.staff if s.rebooking_rate
            ) / total_rebook_weight

        # Rebooking MoM: use prev_rebook_lookup from staff MoM block above
        try:
            if prev_rebook_lookup:
                prev_weight = sum(s.net_revenue for s in data.staff if s.name in prev_rebook_lookup and prev_rebook_lookup[s.name] > 0)
                if prev_weight > 0:
                    prev_rebook_weighted = sum(
                        prev_rebook_lookup[s.name] * s.net_revenue
                        for s in data.staff if s.name in prev_rebook_lookup and prev_rebook_lookup[s.name] > 0
                    ) / prev_weight
                    if prev_rebook_weighted > 0.05 and data.rebooking_rate > 0:
                        data.rebooking_mom_pct = _safe_mom(data.rebooking_rate, prev_rebook_weighted, 0.05)
                        if data.rebooking_mom_pct is not None:
                            print(f"  Rebooking MoM: {data.rebooking_rate:.3f} vs {prev_rebook_weighted:.3f} = {data.rebooking_mom_pct:+.1%}")
        except Exception as e:
            print(f"  Warning: Could not compute rebooking MoM: {e}")

        print(f"  Staff: {len(data.staff)} providers loaded")
    except Exception as e:
        print(f"  Warning: Could not load staff data: {e}")

    # ── Retention (separate dashboard) ──
    print("  Loading retention...")
    try:
        ret_dash = _api_get(f"/v1/documents/{RETENTION_DASHBOARD_ID}/queries", api_key)
        ret_queries = {q["name"]: q["query"] for q in ret_dash.get("queries", [])}

        rq = copy.deepcopy(list(ret_queries.values())[0])
        # This dashboard uses medspa_name_with_id filter; use CONTAINS to match
        rq["filters"]["dbt__moxie_medspas_mart.medspa_name_with_id"] = {
            "kind": "CONTAINS", "type": "string",
            "values": [practice_name], "is_negative": False,
        }
        ret_r = _run_query(rq, api_key)
        data.retention_180d = _val(ret_r, "pct_has_repeat_completed_appointments_180d")
        print(f"  Retention (180d): {data.retention_180d*100:.1f}%")

        # Retention MoM: query retention dashboard for previous month
        # Note: retention is a rolling 180d metric, but we compare the value reported for each month
        try:
            prev_rq = copy.deepcopy(list(ret_queries.values())[0])
            prev_rq["filters"]["dbt__moxie_medspas_mart.medspa_name_with_id"] = {
                "kind": "CONTAINS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            # Apply date filter for prior month to any date fields present
            for fk in list(prev_rq.get("filters", {}).keys()):
                if "date" in fk.lower() or "time" in fk.lower() or "start" in fk.lower():
                    prev_rq["filters"][fk] = {
                        "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                        "ui_type": "PAST", "left_side": prev_start,
                        "right_side": "1 months", "is_negative": False,
                    }
            prev_ret_r = _run_query(prev_rq, api_key)
            prev_retention = _val(prev_ret_r, "pct_has_repeat_completed_appointments_180d")
            data.retention_mom_pct = _safe_mom(data.retention_180d, prev_retention, 0.05)
        except Exception as e:
            print(f"  Warning: Could not load retention MoM: {e}")
    except Exception as e:
        print(f"  Warning: Could not load retention data: {e}")

    # ── Supplies Savings (separate dashboard) ──
    print("  Loading supplies savings...")
    try:
        sup_dash = _api_get(f"/v1/documents/{SUPPLIES_DASHBOARD_ID}/queries", api_key)
        sup_queries = sup_dash.get("queries", [])
        if sup_queries:
            sq = copy.deepcopy(sup_queries[0]["query"])
            sq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            sq["filters"]["dbt__shopify_orders_mart.created_at"] = {
                "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                "ui_type": "PAST", "left_side": start_date,
                "right_side": duration, "is_negative": False,
            }
            # Remove demo filter
            sq["filters"].pop("dbt__moxie_medspas_mart.is_demo_or_test_medspa", None)
            sq["filters"].pop("dbt__shopify_line_items_mart.collections", None)

            sup_r = _run_query(sq, api_key)
            savings_vals = sup_r.get("dbt__shopify_line_items_mart.gsheet_total_savings", [])
            collections = sup_r.get("dbt__shopify_line_items_mart.collections", [])

            data.supplies_total_savings = sum(float(s) for s in savings_vals if s)

            # Aggregate by brand
            by_brand = {}
            for i in range(len(collections)):
                brand = collections[i] or "Other"
                # Simplify multi-tag collections: take first recognizable brand
                for known in ["Galderma", "Allergan", "Merz", "CosmoFrance", "Evolus", "Revance"]:
                    if known in brand:
                        brand = known
                        break
                by_brand[brand] = by_brand.get(brand, 0) + (float(savings_vals[i]) if savings_vals[i] else 0)
            data.supplies_by_brand = [
                {"brand": b, "savings": s}
                for b, s in sorted(by_brand.items(), key=lambda x: -x[1])
                if s > 0
            ]

            print(f"  Supplies savings: ${data.supplies_total_savings:,.2f} across {len(data.supplies_by_brand)} brands")
    except Exception as e:
        print(f"  Warning: Could not load supplies data: {e}")

    # ── Supplies Savings from transaction data (multi-period) ──
    print("  Loading supplies transaction data...")
    try:
        from .savings_loader import load_savings_for_practice
        sav = load_savings_for_practice(practice_name, month, year)
        if sav:
            data.supplies_spend_month = sav["month"]["spend"]
            data.supplies_savings_month = sav["month"]["savings"]
            data.supplies_spend_3mo = sav["m3"]["spend"]
            data.supplies_savings_3mo = sav["m3"]["savings"]
            data.supplies_spend_ytd = sav["ytd"]["spend"]
            data.supplies_savings_ytd = sav["ytd"]["savings"]
            data.supplies_spend_all = sav["all"]["spend"]
            data.supplies_savings_all = sav["all"]["savings"]
            data.supplies_by_vendor_3mo = sav.get("by_vendor_3mo", [])
            print(f"  Supplies: month=${data.supplies_spend_month:,.0f}, "
                  f"3mo=${data.supplies_spend_3mo:,.0f}, "
                  f"YTD=${data.supplies_spend_ytd:,.0f}, "
                  f"all=${data.supplies_spend_all:,.0f}")
    except Exception as e:
        print(f"  Warning: Could not load supplies transaction data: {e}")

    # ── Marketing Performance (separate dashboard) ──
    print("  Loading marketing performance...")
    try:
        from .data_schema import MarketingData
        mkt_dash = _api_get(f"/v1/documents/{MARKETING_DASHBOARD_ID}/queries", api_key)
        mkt_queries = mkt_dash.get("queries", [])
        if mkt_queries:
            mq = copy.deepcopy(mkt_queries[0]["query"])
            # Replace PSM filter with practice filter
            mq["filters"].pop("dbt__moxie_medspas_mart.provider_success_manager_name", None)
            mq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            mq["filters"]["dbt__marketing_medspa_performance_daily_mart.series_date"] = {
                "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                "ui_type": "PAST",
                "left_side": start_date, "right_side": duration,
                "is_negative": False,
            }
            # Add revenue fields (not in the base dashboard query)
            rev_field = "dbt__marketing_medspa_performance_daily_mart.meta_new_clients_completed_appointment_revenue_sum"
            all_rev_field = "dbt__marketing_medspa_performance_daily_mart.meta_completed_appointment_revenue_sum"
            for extra_f in [rev_field, all_rev_field]:
                if extra_f not in mq.get("fields", []):
                    mq.setdefault("fields", []).append(extra_f)
            mkt_r = _run_query(mq, api_key)

            # Find the practice row (skip totals row where name is None)
            mkt_names = mkt_r.get("dbt__moxie_medspas_mart.medspa_name", [])
            mkt_idx = next((i for i, n in enumerate(mkt_names) if n and n == practice_name), None)

            if mkt_idx is not None:
                def mkt_val(field, default=0):
                    key = f"dbt__marketing_medspa_performance_daily_mart.{field}"
                    vals = mkt_r.get(key, [])
                    v = vals[mkt_idx] if mkt_idx < len(vals) else None
                    return float(v) if v is not None else default

                ad_spend = mkt_val("meta_spend_sum")
                leads = int(mkt_val("meta_leads_sum"))
                booked = int(mkt_val("meta_new_clients_booked_appointment_sum"))
                completed = int(mkt_val("meta_new_clients_completed_appointment_sum"))
                cpl = mkt_val("total_meta_cost_per_lead")

                # Revenue comes directly from Omni (net revenue from new clients)
                revenue = mkt_val("meta_new_clients_completed_appointment_revenue_sum")
                # Total revenue across all clients (new + existing) attributed to marketing
                total_rev_all = mkt_val("meta_completed_appointment_revenue_sum")

                # ROI = revenue / spend (New Client ROI)
                roi = revenue / ad_spend if ad_spend > 0 and revenue > 0 else 0

                if ad_spend > 0:
                    data.marketing = MarketingData(
                        ad_spend=ad_spend,
                        leads=leads,
                        booked=booked,
                        completed=completed,
                        revenue=round(revenue, 2),
                        total_revenue_all_clients=round(total_rev_all, 2) if total_rev_all else 0.0,
                        first_visit_roi=round(roi, 2) if roi else None,
                        lead_to_booking_rate=booked / leads if leads > 0 else None,
                        first_visit_aov=revenue / completed if completed > 0 else None,
                    )
                    print(f"  Marketing: spend=${ad_spend:,.0f}, leads={leads}, "
                          f"booked={booked}, completed={completed}, "
                          f"revenue=${revenue:,.0f}, ROI={roi:.1f}x")

                    # Campaign-level breakdown — shown when practice runs multiple campaigns
                    if ad_spend > 0:
                        try:
                            from .data_schema import CampaignData
                            cq = copy.deepcopy(mkt_queries[0]["query"])
                            cq["filters"].pop("dbt__moxie_medspas_mart.provider_success_manager_name", None)
                            cq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                                "kind": "EQUALS", "type": "string",
                                "values": [practice_name], "is_negative": False,
                            }
                            cq["filters"]["dbt__marketing_medspa_performance_daily_mart.series_date"] = {
                                "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                                "ui_type": "PAST",
                                "left_side": start_date, "right_side": duration,
                                "is_negative": False,
                            }
                            camp_field = "dbt__marketing_medspa_performance_daily_mart.campaign_category"
                            if camp_field not in cq.get("fields", []):
                                cq["fields"].append(camp_field)
                            if rev_field not in cq.get("fields", []):
                                cq["fields"].append(rev_field)
                            camp_r = _run_query(cq, api_key)

                            camp_names_col = camp_r.get(camp_field, [])
                            camp_medspa = camp_r.get("dbt__moxie_medspas_mart.medspa_name", [])
                            camp_spends = camp_r.get("dbt__marketing_medspa_performance_daily_mart.meta_spend_sum", [])
                            camp_leads = camp_r.get("dbt__marketing_medspa_performance_daily_mart.meta_leads_sum", [])
                            camp_booked = camp_r.get("dbt__marketing_medspa_performance_daily_mart.meta_new_clients_booked_appointment_sum", [])
                            camp_completed = camp_r.get("dbt__marketing_medspa_performance_daily_mart.meta_new_clients_completed_appointment_sum", [])
                            camp_revenue = camp_r.get(rev_field, [])
                            camp_totals = camp_r.get("$omni_column_total_indicator", [])

                            campaigns = []
                            for ci in range(len(camp_names_col)):
                                if ci < len(camp_totals) and camp_totals[ci] == "column_total":
                                    continue
                                cn = camp_names_col[ci] if ci < len(camp_names_col) else None
                                cm = camp_medspa[ci] if ci < len(camp_medspa) else None
                                if not cn or not cm or cm != practice_name:
                                    continue
                                cs = float(camp_spends[ci]) if ci < len(camp_spends) and camp_spends[ci] else 0
                                cl = int(camp_leads[ci]) if ci < len(camp_leads) and camp_leads[ci] else 0
                                cb = int(camp_booked[ci]) if ci < len(camp_booked) and camp_booked[ci] else 0
                                cc = int(camp_completed[ci]) if ci < len(camp_completed) and camp_completed[ci] else 0
                                cr = float(camp_revenue[ci]) if ci < len(camp_revenue) and camp_revenue[ci] else 0
                                # Only include campaigns with activity
                                if cs > 0 or cl > 0 or cc > 0:
                                    campaigns.append(CampaignData(
                                        campaign_name=cn, ad_spend=cs, leads=cl,
                                        booked=cb, completed=cc, revenue=cr,
                                    ))
                            campaigns.sort(key=lambda c: c.ad_spend, reverse=True)
                            data.marketing.campaigns = campaigns
                            print(f"  Campaigns: {len(campaigns)} active ({', '.join(c.campaign_name for c in campaigns)})")
                        except Exception as e:
                            print(f"  Warning: Could not load campaign data: {e}")

                else:
                    print("  Marketing: no ad spend for this month")
            else:
                print("  Marketing: no data found for this practice")
    except Exception as e:
        print(f"  Warning: Could not load marketing data: {e}")

    print(f"  Loaded: Net Rev ${data.monthly_net_revenue:,.2f}, "
          f"{data.total_appointments} appts, "
          f"{len(data.staff)} staff, "
          f"{len(data.services)} service categories")

    return data
