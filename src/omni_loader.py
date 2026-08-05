"""Load MBR data from Omni Analytics API."""

import base64
import calendar
import copy
import json
import os
import urllib.request
from .data_schema import MBRData, StaffMember, ServiceItem, MembershipType


BASE_URL = "https://moxie.omniapp.co/api"

# ── Standard Report dashboard IDs (moxie.omniapp.co/f/embedded-poc/standard-reports) ──
SALES_REPORT_ID     = "b8baa4c2"  # Medspa Sales Report
APPOINTMENTS_ID     = "d6776514"  # Medspa Appointments
STAFF_REPORT_ID     = "fed9785d"  # Medspa Staff Performance Report
TRANSACTIONS_ID     = "76abf294"  # Medspa Transactions Report
MEMBERSHIPS_ID      = "475dc8d8"  # Medspa Memberships

# ── Legacy dashboards (data not yet in Standard Reports) ──
DASHBOARD_ID           = "bfd963dd"  # tier / medspa-id lookup + GFE queries
SUPPLIES_DASHBOARD_ID  = "54d5da36"
RETENTION_DASHBOARD_ID = "59ca3051"
MARKETING_DASHBOARD_ID = "0ef3afa3"

# Query names → date filter fields used by _add_filters()
QUERY_DATE_FIELDS = {
    # ── Standard Reports ──
    # Sales Report (b8baa4c2)
    "Sales Summary":              "dbt__moxie_invoice_transactions_mart.transaction_date_et",
    "Service Revenue Summary":    "dbt__moxie_invoices_mart.invoice_issued_date",
    "Prepayment Revenue Summary": "dbt__moxie_invoices_mart.invoice_issued_date",
    "Product Revenue Summary":    "dbt__moxie_invoices_mart.invoice_issued_date",
    "Fee Revenue Summary":        "dbt__moxie_invoices_mart.invoice_issued_date",
    # Appointments (d6776514)
    "Appointment Overview":       "dbt__moxie_appointments_mart.start_time",
    "Appointment Stats":          "dbt__moxie_appointments_mart.start_time",
    # Staff Performance (fed9785d)
    "Staff Appointment Summary":  "dbt__moxie_embedded_staff_report_mart.report_date",
    "Staff Sales Summary":        "dbt__moxie_invoices_mart.first_payment_date",
    # Transactions (76abf294)
    "Payment Method Breakdown":   "dbt__moxie_transactions_mart.transaction_time",
    "Payment History":            "dbt__moxie_transactions_mart.transaction_time",
    "Refund History":             "dbt__moxie_transactions_mart.transaction_time",
    # Memberships (475dc8d8)
    "New Membership Enrollments": "dbt__moxie_client_memberships_mart.started_at",
    "Cancellations":              "dbt__moxie_client_memberships_mart.canceled_at",
    "Average Monthly Members":    "dbt__moxie_membership_churn_monthly_mart.month_start",
    "Monthly Recurring Revenue (MRR)": "dbt__moxie_membership_churn_monthly_mart.month_start",
    # ── Legacy (kept for GFE + backward compat) ──
    "KPI: Net Revenue":           "dbt__moxie_invoice_transactions_mart.transaction_date_et",
    "Payments & Refunds":         "dbt__moxie_invoice_transactions_mart.transaction_date_et",
    "KPI: Paid Appointments":     "dbt__moxie_appointments_mart.start_time",
    "KPI: AOV":                   "dbt__moxie_appointments_mart.start_time",
    "Client Counts":              "dbt__moxie_appointments_mart.start_time",
    "Total Membership Revenue":   "dbt__moxie_invoices_mart.invoice_issued_date",
    "Gross Revenue Breakdown Summary": "dbt__moxie_invoices_mart.invoice_issued_date",
    "Retail to Service Revenue":  "dbt__moxie_invoices_mart.invoice_issued_date",
    "Gross Revenue By Official Service Type": "dbt__moxie_invoices_mart.invoice_issued_date",
    "Utilization":                "dbt__moxie_utilization_daily_mart.series_date",
    "Active Members":             None,
    "New Memberships":            "dbt__moxie_client_memberships_mart.started_at",
    "Churned Memberships":        "dbt__moxie_client_memberships_mart.ended_at",
}


def _api_get(path: str, api_key: str):
    req = urllib.request.Request(f"{BASE_URL}{path}")
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _run_query(query_body: dict, api_key: str, retries: int = 7) -> dict:
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
                        wait = min(int(retry_after), 60)
                    else:
                        wait = min(4 ** attempt, 60)  # 1, 4, 16, 60, 60, 60, 60
                    print(f"  Rate limited (attempt {attempt+1}/{retries}), waiting {wait}s…")
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
                 date_field: str = None, duration: str = "1 months",
                 medspa_id: int = None) -> dict:
    """Add practice and date range filters to a query.

    Filters by medspa_name EQUALS — confirmed to work across every Omni
    mart we query. The medspa_id parameter is kept for caller signature
    compatibility but is intentionally not used as a filter: Omni's API
    rejects numeric-type filters on this field in practice, which caused
    every metric to come back as zero for practices where the medspa_id
    branch was taken. duration can be "1 months", "3 months", etc.
    """
    q = copy.deepcopy(query)
    q.setdefault("filters", {})
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


def _sum_all(result: dict, key_substring: str) -> float:
    """Sum all rows in a multi-row Omni result column by partial key match."""
    for k, v in result.items():
        if key_substring in k and v:
            total = 0.0
            for x in v:
                if x is not None:
                    try:
                        total += float(x)
                    except (TypeError, ValueError):
                        pass
            return total
    return 0.0


def _extract_col(result: dict, key_substring: str) -> list:
    """Return the full column values list by partial key match."""
    for k, v in result.items():
        if key_substring in k and v:
            return v
    return []


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

    # Load queries from Standard Report dashboards + legacy dashboard (for tier/GFE)
    print(f"  Connecting to Omni API...")
    queries = {}
    for dash_id in [SALES_REPORT_ID, APPOINTMENTS_ID, STAFF_REPORT_ID,
                    TRANSACTIONS_ID, MEMBERSHIPS_ID]:
        try:
            dash = _api_get(f"/v1/documents/{dash_id}/queries", api_key)
            for q in dash.get("queries", []):
                if q.get("name") and q.get("query"):
                    queries[q["name"]] = q["query"]
        except Exception as e:
            print(f"  Warning: could not load dashboard {dash_id}: {e}")
    # Legacy dashboard kept for tier/medspa-id lookup, GFE, and membership active-by-type
    try:
        legacy = _api_get(f"/v1/documents/{DASHBOARD_ID}/queries", api_key)
        for q in legacy.get("queries", []):
            if q.get("name") and q.get("query") and q["name"] not in queries:
                queries[q["name"]] = q["query"]
    except Exception as e:
        print(f"  Warning: could not load legacy dashboard: {e}")
    print(f"  Found {len(queries)} queries across all dashboards")

    data = MBRData(practice_name=practice_name, month=month, year=year)

    # Get practice tier (provider_segment_post_launch) and medspa_id from
    # Medspa Name query. Resolving the id once lets every downstream query
    # filter by id (unambiguous, joins consistently across all marts) instead
    # of by name (subject to '&'/'and' or city-suffix mismatches).
    medspa_id = None
    try:
        import re as _re
        def _norm_name(s):
            # Normalize & → and before stripping so "Glow & Go" == "Glow and Go"
            s = (s or "").lower().replace("&", "and")
            return _re.sub(r"[^a-z0-9]", "", s)

        tier_q = copy.deepcopy(queries.get("Medspa Name", {}))
        if tier_q:
            tier_q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "CONTAINS", "type": "string",
                "values": [practice_name.split()[0]],
                "is_negative": False,
            }
            tier_field = "dbt__moxie_medspas_mart.provider_segment_post_launch"
            name_field = "dbt__moxie_medspas_mart.medspa_name"
            id_field = "dbt__moxie_medspas_mart.medspa_id"
            for f in [tier_field, name_field, id_field]:
                if f not in tier_q.get("fields", []):
                    tier_q.setdefault("fields", []).append(f)
            tier_q["limit"] = 50
            tier_r = _run_query(tier_q, api_key)
            tier_names = tier_r.get(name_field, [])
            tiers = tier_r.get(tier_field, [])
            ids = tier_r.get(id_field, [])
            target_norm = _norm_name(practice_name)
            # 1. Exact normalized match
            tier_idx = next((i for i, n in enumerate(tier_names)
                             if n and _norm_name(n) == target_norm), None)
            # 2. Prefix match — handles "Glow & Go" in Omni vs "Glow & Go Aesthetics" entered
            if tier_idx is None:
                tier_idx = next(
                    (i for i, n in enumerate(tier_names)
                     if n and len(_norm_name(n)) >= 6 and (
                         target_norm.startswith(_norm_name(n)) or
                         _norm_name(n).startswith(target_norm)
                     )),
                    None
                )
                if tier_idx is not None:
                    print(f"  Prefix match: '{practice_name}' ~ '{tier_names[tier_idx]}'")
            if tier_idx is not None:
                # Use the canonical Omni name for all downstream EQUALS filters
                # so "&" vs "and" or other spacing differences don't cause zeros.
                canonical = tier_names[tier_idx]
                if canonical and canonical != practice_name:
                    print(f"  Name resolved: '{practice_name}' → '{canonical}'")
                    practice_name = canonical
                if tier_idx < len(tiers) and tiers[tier_idx]:
                    data.tier = str(tiers[tier_idx])
                    print(f"  Tier: {data.tier}")
                    if data.tier in ("Silver", "Momentum", "Growth"):
                        data.show_executive_summary = False
                    # Enterprise practices default to showing marketing recs.
                    # All other tiers default off; both controllable in editor.
                    if data.tier == "Enterprise":
                        data.show_marketing_recommendations = True
                if tier_idx < len(ids) and ids[tier_idx] is not None:
                    medspa_id = int(ids[tier_idx])
                    print(f"  Medspa ID: {medspa_id}")
            else:
                print(f"  Warning: Medspa Name dashboard had no row for "
                      f"'{practice_name}'. Names returned: "
                      f"{[n for n in tier_names if n][:5]}")
    except Exception as e:
        print(f"  Warning: Could not load tier/id: {e}")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def run(name: str) -> dict:
        q = _find_query(queries, name)
        date_field = QUERY_DATE_FIELDS.get(name)
        q = _add_filters(q, practice_name, start_date, date_field, duration,
                         medspa_id=medspa_id)
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

    # Batch 1: all independent current-month queries (Standard Reports)
    batch1_names = [
        "Sales Summary",                     # net revenue, gross, discounts, wallet, taxes
        "Service Revenue Summary",           # service revenue + service mix
        "Prepayment Revenue Summary",        # package/prepayment revenue
        "Product Revenue Summary",           # retail revenue
        "Fee Revenue Summary",               # client fees
        "Appointment Overview",              # paid (completed) appointments
        "Appointment Stats",                 # rebooking rate, new/existing %
        "New Membership Enrollments",        # memberships new + new revenue
        "Cancellations",                     # memberships cancelled
        "Monthly Recurring Revenue (MRR)",   # active mrr
        "Average Monthly Members",           # active membership count
        "Payment Method Breakdown",          # card / terminal split
        "Payment History",                   # transaction fees + performance fees
        "Refund History",                    # refunds
    ]
    batch1 = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_safe, name): name for name in batch1_names}
        for future in as_completed(futures):
            batch1[futures[future]] = future.result()

    # ── Process batch 1 results (Standard Reports field names) ──

    # Revenue — from Sales Summary
    r = batch1.get("Sales Summary", {})
    data.monthly_net_revenue = _val(r, "net_revenue_sum")
    data.total_gross = _val(r, "gross_revenue_sum")
    data.discounts = abs(_val(r, "discount_amount_sum"))
    data.wallet_dollar_redemptions = abs(_val(r, "total_wallet_dollars_redeemed"))
    data.wallet_item_redemptions = abs(_val(r, "total_wallet_item_discounts"))
    data.tax_collected = _val(r, "total_tax_amount_sum")
    # Goals not available in Standard Reports — remain 0

    # Revenue breakdown by type
    data.service_revenue = _sum_all(batch1.get("Service Revenue Summary", {}), "sum_line_net_revenue")
    data.prepayment_revenue = _sum_all(batch1.get("Prepayment Revenue Summary", {}), "sum_line_net_revenue")
    data.retail_revenue = _sum_all(batch1.get("Product Revenue Summary", {}), "sum_line_net_revenue")
    data.client_fees = _sum_all(batch1.get("Fee Revenue Summary", {}), "sum_line_net_revenue")

    # Appointments — from Appointment Overview
    r = batch1.get("Appointment Overview", {})
    data.total_appointments = int(_val(r, "completed_appointments"))

    # AOV = calculated (not a separate query)
    data.aov = (data.monthly_net_revenue / data.total_appointments
                if data.total_appointments > 0 else 0)

    # Rebooking + new/existing client split — from Appointment Stats
    r = batch1.get("Appointment Stats", {})
    rebooking = _val(r, "rebooking_rate", default=None)
    if rebooking is not None:
        data.rebooking_rate = rebooking if rebooking <= 1.0 else rebooking / 100
    pct_new = _val(r, "pct_completed_appointments__new_client", default=None)
    if pct_new is not None:
        if pct_new > 1:
            pct_new /= 100
        data.new_clients = round(data.total_appointments * pct_new)
        data.existing_clients = data.total_appointments - data.new_clients

    # Memberships
    r = batch1.get("Average Monthly Members", {})
    data.memberships_active = int(_val(r, "active_memberships_sum"))

    r = batch1.get("Monthly Recurring Revenue (MRR)", {})
    data.mrr = _val(r, "active_mrr")

    r = batch1.get("New Membership Enrollments", {})
    data.memberships_new = int(_val(r, "count"))
    data.membership_sales = _val(r, "membership_revenue_sum")

    r = batch1.get("Cancellations", {})
    data.memberships_cancelled = int(_val(r, "count_cancellations"))

    # Payout reconciliation — from Transaction Reports
    # Payment Method Breakdown: rows by transaction_method_category
    r = batch1.get("Payment Method Breakdown", {})
    categories = _extract_col(r, "transaction_method_category")
    amounts = _extract_col(r, "amount_sum")
    for i, cat in enumerate(categories):
        amt = float(amounts[i]) if i < len(amounts) and amounts[i] else 0
        cat_lower = (cat or "").lower()
        if "card" in cat_lower and "terminal" not in cat_lower:
            data.card_revenue += amt
        elif "terminal" in cat_lower or "reader" in cat_lower:
            data.terminal_revenue += amt

    r = batch1.get("Payment History", {})
    data.transaction_fees = _sum_all(r, "fee_sum")
    data.performance_fees = _sum_all(r, "moxie_transaction_fee_sum")

    r = batch1.get("Refund History", {})
    data.refunds = _sum_all(r, "amount_sum")
    data.redemptions = data.refunds  # backward compat

    # Retail MoM placeholder (will be updated after MoM queries)
    # Retail to service ratio
    if data.service_revenue > 0:
        data.retail_to_service_ratio = data.retail_revenue / data.service_revenue

    # ── Previous Month MoM + QTD (parallel batch 2) ──
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_start = f"{prev_year}-{prev_month:02d}-01"
    print(f"  Loading prior month + QTD in parallel...")

    def run_prev(name: str):
        try:
            q = _find_query(queries, name)
            date_field = QUERY_DATE_FIELDS.get(name)
            q = _add_filters(q, practice_name, prev_start, date_field, medspa_id=medspa_id)
            return _run_query(q, api_key)
        except Exception:
            return None

    # Pull KPI values for the two months prior to "prev" so we can render a
    # 4-bar comparison chart (m-3, m-2, m-1, current) for revenue and AOV.
    def _month_offset(m, y, k):
        idx = (y * 12 + (m - 1)) - k
        return idx % 12 + 1, idx // 12
    pm2_month, pm2_year = _month_offset(month, year, 2)
    pm3_month, pm3_year = _month_offset(month, year, 3)
    pm2_start = f"{pm2_year}-{pm2_month:02d}-01"
    pm3_start = f"{pm3_year}-{pm3_month:02d}-01"

    def run_at(name: str, start: str):
        try:
            q = _find_query(queries, name)
            date_field = QUERY_DATE_FIELDS.get(name)
            q = _add_filters(q, practice_name, start, date_field, medspa_id=medspa_id)
            return _run_query(q, api_key)
        except Exception:
            return None

    def run_qtd():
        quarter_start_month = ((month - 1) // 3) * 3 + 1
        months_in_quarter = month - quarter_start_month + 1
        if months_in_quarter <= 1:
            return data.monthly_net_revenue
        qtd_start = f"{year}-{quarter_start_month:02d}-01"
        qtd_q = _find_query(queries, "Sales Summary")
        qtd_date_field = QUERY_DATE_FIELDS.get("Sales Summary")
        qtd_q = copy.deepcopy(qtd_q)
        qtd_q.setdefault("filters", {})
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
    with ThreadPoolExecutor(max_workers=2) as pool:
        mom_futures = {
            pool.submit(run_prev, "Sales Summary"): "prev_rev",
            pool.submit(run_prev, "Appointment Overview"): "prev_appt",
            pool.submit(run_prev, "Utilization"): "prev_util",
            pool.submit(run_prev, "Product Revenue Summary"): "prev_retail",
            pool.submit(run_at, "Sales Summary", pm2_start): "rev_m2",
            pool.submit(run_at, "Sales Summary", pm3_start): "rev_m3",
            pool.submit(run_at, "Appointment Overview", pm2_start): "appt_m2",
            pool.submit(run_at, "Appointment Overview", pm3_start): "appt_m3",
            pool.submit(run_qtd): "qtd",
        }
        for future in as_completed(mom_futures):
            mom_results[mom_futures[future]] = future.result()

    data.quarter_to_date = mom_results.get("qtd", data.monthly_net_revenue)

    prev_rev_r = mom_results.get("prev_rev")
    prev_revenue = 0
    if prev_rev_r:
        prev_revenue = _val(prev_rev_r, "net_revenue_sum")
        data.revenue_mom_pct = _safe_mom(data.monthly_net_revenue, prev_revenue, 100)

    prev_appt_r = mom_results.get("prev_appt")
    prev_appointments = 0
    if prev_appt_r:
        prev_appointments = int(_val(prev_appt_r, "completed_appointments"))
        data.appointments_mom_pct = _safe_mom(data.total_appointments, prev_appointments, 5)

    # AOV MoM — calculated from prev revenue / prev appointments
    if prev_revenue > 0 and prev_appointments > 0:
        prev_aov = prev_revenue / prev_appointments
        data.aov_mom_pct = _safe_mom(data.aov, prev_aov, 20)

    # Utilization MoM (from legacy Utilization query — no Standard Report equivalent)
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

    # Retail MoM
    prev_retail_r = mom_results.get("prev_retail")
    if prev_retail_r:
        prev_retail = _sum_all(prev_retail_r, "sum_line_net_revenue")
        data.retail_revenue_mom_pct = _safe_mom(data.retail_revenue, prev_retail, 100)

    # Build 4-bar comparison history (m-3, m-2, m-1, current) for revenue & AOV.
    import calendar as _cal
    def _hist(label_month, label_year, value):
        return {
            "label": _cal.month_abbr[label_month],
            "month": label_month, "year": label_year,
            "value": float(value) if value else 0.0,
        }
    rev_m2_val = _val(mom_results.get("rev_m2") or {}, "net_revenue_sum")
    rev_m3_val = _val(mom_results.get("rev_m3") or {}, "net_revenue_sum")
    appt_m2 = int(_val(mom_results.get("appt_m2") or {}, "completed_appointments"))
    appt_m3 = int(_val(mom_results.get("appt_m3") or {}, "completed_appointments"))
    prev_rev_val = _val(prev_rev_r or {}, "net_revenue_sum") if prev_rev_r else 0
    prev_aov_val = prev_revenue / prev_appointments if prev_appointments > 0 else 0
    data.revenue_history = [
        _hist(pm3_month, pm3_year, rev_m3_val),
        _hist(pm2_month, pm2_year, rev_m2_val),
        _hist(prev_month, prev_year, prev_rev_val),
        _hist(month, year, data.monthly_net_revenue),
    ]

    aov_m2_val = rev_m2_val / appt_m2 if appt_m2 > 0 else 0
    aov_m3_val = rev_m3_val / appt_m3 if appt_m3 > 0 else 0
    data.aov_history = [
        _hist(pm3_month, pm3_year, aov_m3_val),
        _hist(pm2_month, pm2_year, aov_m2_val),
        _hist(prev_month, prev_year, prev_aov_val),
        _hist(month, year, data.aov),
    ]

    print(f"  MoM: Rev {'N/A' if data.revenue_mom_pct is None else f'{data.revenue_mom_pct:+.1%}'}, "
          f"Appts {'N/A' if data.appointments_mom_pct is None else f'{data.appointments_mom_pct:+.1%}'}, "
          f"AOV {'N/A' if data.aov_mom_pct is None else f'{data.aov_mom_pct:+.1%}'}")

    # Practice utilization — will be set from staff data; fall back to legacy Utilization query
    r = batch1.get("Utilization", {})  # may be absent from Standard Reports
    util_pct = _val(r, "column_b_divided_by_column_a", default=None)
    if util_pct is not None:
        data.utilization_rate = util_pct if util_pct <= 1.0 else util_pct / 100
    else:
        total_avail = _val(r, "total_available_hours")
        total_appt = _val(r, "total_appointment_hours")
        if total_avail > 0:
            data.utilization_rate = total_appt / total_avail

    # Utilization MoM
    if prev_util is not None:
        data.utilization_mom_pct = _safe_mom(data.utilization_rate, prev_util, 0.05)

    # Service Mix — from Service Revenue Summary (grouped by service_type)
    r = batch1.get("Service Revenue Summary", {})
    svc_types = _extract_col(r, "service_type")
    svc_order_items = _extract_col(r, "order_item")
    svc_revs = _extract_col(r, "sum_line_net_revenue")
    # Group by service_type (high-level category)
    svc_by_type: dict = {}
    for i, cat in enumerate(svc_types):
        if not cat:
            cat = svc_order_items[i] if i < len(svc_order_items) else "Other"
        rev = float(svc_revs[i]) if i < len(svc_revs) and svc_revs[i] else 0
        svc_by_type[cat] = svc_by_type.get(cat, 0) + rev
    for cat, rev in sorted(svc_by_type.items(), key=lambda x: -x[1]):
        if rev > 0:
            data.services.append(ServiceItem(name=cat, revenue=rev))
    data.services.sort(key=lambda s: s.revenue, reverse=True)
    data.compute_service_percentages()

    # Membership breakdown by type
    try:
        pf = {"kind": "EQUALS", "type": "string", "values": [practice_name], "is_negative": False}
        def _date_f(field_name):
            return {"kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                    "ui_type": "PAST", "left_side": start_date,
                    "right_side": duration, "is_negative": False}

        # Active by type — keep using legacy Active Members (no per-type field in Standard Reports)
        aq = copy.deepcopy(queries["Active Members"])
        aq.setdefault("fields", [])
        mem_name_field = "dbt__moxie_client_memberships_mart.membership_name"
        if mem_name_field not in aq["fields"]:
            aq["fields"].append(mem_name_field)
        active_mrr_field = "dbt__moxie_client_memberships_mart.mrr_sum"
        if active_mrr_field not in aq["fields"]:
            aq["fields"].append(active_mrr_field)
        aq.setdefault("filters", {})["dbt__moxie_medspas_mart.medspa_name"] = pf
        active_r = _run_query(aq, api_key)
        active_names = _extract_col(active_r, "membership_name")
        active_counts = _extract_col(active_r, "count")
        active_mrrs = _extract_col(active_r, "mrr_sum")

        # New by type — Standard Reports New Membership Enrollments + line_name dimension
        nq = copy.deepcopy(queries["New Membership Enrollments"])
        nq.setdefault("fields", [])
        line_name_field = "dbt__moxie_client_memberships_mart.line_name"
        if line_name_field not in nq["fields"]:
            nq["fields"].append(line_name_field)
        nq.setdefault("filters", {})["dbt__moxie_medspas_mart.medspa_name"] = pf
        nq["filters"]["dbt__moxie_client_memberships_mart.started_at"] = _date_f("started_at")
        new_r = _run_query(nq, api_key)
        # line_name or membership_name — try both
        new_names = _extract_col(new_r, "line_name") or _extract_col(new_r, "membership_name")
        new_counts = _extract_col(new_r, "count")

        # Churned by type — Standard Reports Cancellations + membership_name dimension
        cq = copy.deepcopy(queries["Cancellations"])
        cq.setdefault("fields", [])
        if mem_name_field not in cq["fields"]:
            cq["fields"].append(mem_name_field)
        cq.setdefault("filters", {})["dbt__moxie_medspas_mart.medspa_name"] = pf
        cq["filters"]["dbt__moxie_client_memberships_mart.canceled_at"] = _date_f("canceled_at")
        churned_r = _run_query(cq, api_key)
        churned_names = _extract_col(churned_r, "membership_name")
        churned_counts = _extract_col(churned_r, "count_cancellations") or _extract_col(churned_r, "count")

        # Merge into MembershipType objects
        all_mem_names: set = set()
        active_lookup, mrr_lookup = {}, {}
        for i, name in enumerate(active_names):
            if name:
                all_mem_names.add(name)
                active_lookup[name] = int(active_counts[i]) if i < len(active_counts) and active_counts[i] else 0
                mrr_lookup[name] = float(active_mrrs[i]) if i < len(active_mrrs) and active_mrrs[i] else 0
        new_lookup = {}
        for i, name in enumerate(new_names):
            if name:
                all_mem_names.add(name)
                new_lookup[name] = int(new_counts[i]) if i < len(new_counts) and new_counts[i] else 0
        churned_lookup = {}
        for i, name in enumerate(churned_names):
            if name:
                all_mem_names.add(name)
                churned_lookup[name] = int(churned_counts[i]) if i < len(churned_counts) and churned_counts[i] else 0

        for name in sorted(all_mem_names):
            data.membership_types.append(MembershipType(
                name=name,
                active=active_lookup.get(name, 0),
                new=new_lookup.get(name, 0),
                churned=churned_lookup.get(name, 0),
                mrr=mrr_lookup.get(name, 0),
            ))
        data.membership_types.sort(key=lambda m: m.active, reverse=True)
        print(f"  Membership types: {len(data.membership_types)} loaded")
    except Exception as e:
        print(f"  Warning: Could not load membership breakdown: {e}")

    # ── Staff Performance (Standard Reports: fed9785d) ──
    print("  Loading staff performance...")
    try:
        def _staff_filter(q, start, dur=""):
            q = copy.deepcopy(q)
            q.setdefault("filters", {})
            q["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            return q

        # Staff Appointment Summary — utilization, rebooking, aov, hours per provider
        appt_q = _staff_filter(queries["Staff Appointment Summary"])
        appt_q["filters"]["dbt__moxie_embedded_staff_report_mart.report_date"] = {
            "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
            "ui_type": "PAST", "left_side": start_date, "right_side": duration,
            "is_negative": False,
        }
        appt_r = _run_query(appt_q, api_key)

        # Staff Sales Summary — per-provider revenue by item type
        sales_q = _staff_filter(queries["Staff Sales Summary"])
        sales_q["filters"]["dbt__moxie_invoices_mart.first_payment_date"] = {
            "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
            "ui_type": "PAST", "left_side": start_date, "right_side": duration,
            "is_negative": False,
        }
        gross_field = "dbt__moxie_invoice_line_items_mart.gross_revenue_sum"
        if gross_field not in sales_q.get("fields", []):
            sales_q.setdefault("fields", []).append(gross_field)
        sales_r = _run_query(sales_q, api_key)

        # Build per-provider lookup from Staff Appointment Summary
        appt_names   = _extract_col(appt_r, "provider_name")
        appt_util    = _extract_col(appt_r, "utilization_pct")
        appt_rebook  = _extract_col(appt_r, "rebooking_rate")
        appt_hours_b = _extract_col(appt_r, "hours_booked_sum")
        appt_hours_s = _extract_col(appt_r, "hours_scheduled_sum")
        appt_aov     = _extract_col(appt_r, ".aov")  # staff-level aov

        appt_lookup: dict = {}
        for i, name in enumerate(appt_names):
            if not name:
                continue
            util = float(appt_util[i]) if i < len(appt_util) and appt_util[i] is not None else None
            if util is not None and util > 1.0:
                util = util / 100
            appt_lookup[name] = {
                "util": util,
                "rebook": float(appt_rebook[i]) if i < len(appt_rebook) and appt_rebook[i] is not None else 0,
                "hours_booked": float(appt_hours_b[i]) if i < len(appt_hours_b) and appt_hours_b[i] else None,
                "hours_sched": float(appt_hours_s[i]) if i < len(appt_hours_s) and appt_hours_s[i] else None,
                "aov": float(appt_aov[i]) if i < len(appt_aov) and appt_aov[i] else None,
            }

        # Aggregate per-provider revenue from Staff Sales Summary
        sales_names  = _extract_col(sales_r, "attributed_provider_name")
        sales_net    = _extract_col(sales_r, "sum_line_net_revenue")
        sales_gross  = _extract_col(sales_r, "gross_revenue_sum")
        sales_type   = _extract_col(sales_r, "invoice_item_type")

        sales_lookup: dict = {}
        for i, name in enumerate(sales_names):
            if not name:
                continue
            net = float(sales_net[i]) if i < len(sales_net) and sales_net[i] else 0
            gross = float(sales_gross[i]) if i < len(sales_gross) and sales_gross[i] else net
            item_type = (sales_type[i] or "").lower() if i < len(sales_type) else ""
            if name not in sales_lookup:
                sales_lookup[name] = {"net": 0, "gross": 0, "retail": 0, "service": 0}
            sales_lookup[name]["net"] += net
            sales_lookup[name]["gross"] += gross
            if "retail" in item_type or "product" in item_type:
                sales_lookup[name]["retail"] += net
            else:
                sales_lookup[name]["service"] += net

        # Build StaffMember list (union of both queries)
        all_staff_names = set(appt_lookup) | set(sales_lookup)
        for name in sorted(all_staff_names):
            appt = appt_lookup.get(name, {})
            sales = sales_lookup.get(name, {"net": 0, "gross": 0, "retail": 0, "service": 0})
            net_rev = sales["net"]
            gross_rev = sales["gross"] if sales["gross"] > 0 else net_rev
            aov_val = appt.get("aov") or (net_rev / max(1, 1))  # per-appt aov from staff query
            rebook = appt.get("rebook", 0)
            if rebook and rebook > 1.0:
                rebook = rebook / 100
            data.staff.append(StaffMember(
                name=name,
                net_revenue=net_rev,
                gross_revenue=gross_rev,
                aov=aov_val or 0,
                utilization=appt.get("util"),
                rebooking_rate=rebook or 0,
                service_revenue=max(sales["service"], 0),
                retail_revenue=sales["retail"],
                hours_worked=appt.get("hours_booked"),
            ))

        data.staff.sort(key=lambda s: s.gross_revenue, reverse=True)

        # Practice-level utilization — weighted by hours booked
        total_hours = sum(
            appt_lookup[n]["hours_booked"] or 0
            for n in all_staff_names if n in appt_lookup and appt_lookup[n].get("hours_booked")
        )
        if total_hours > 0:
            weighted_util = sum(
                (appt_lookup[n].get("util") or 0) * (appt_lookup[n]["hours_booked"] or 0)
                for n in all_staff_names
                if n in appt_lookup and appt_lookup[n].get("util") is not None and appt_lookup[n].get("hours_booked")
            ) / total_hours
            if weighted_util > 0:
                data.utilization_rate = weighted_util

        # Practice-level rebooking — weighted by net revenue
        total_rebook_weight = sum(s.net_revenue for s in data.staff if s.rebooking_rate)
        if total_rebook_weight > 0:
            data.rebooking_rate = sum(
                s.rebooking_rate * s.net_revenue
                for s in data.staff if s.rebooking_rate
            ) / total_rebook_weight

        # ── Per-provider MoM (prior month staff queries) ──
        try:
            prev_appt_q = _staff_filter(queries["Staff Appointment Summary"])
            prev_appt_q["filters"]["dbt__moxie_embedded_staff_report_mart.report_date"] = {
                "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                "ui_type": "PAST", "left_side": prev_start, "right_side": "1 months",
                "is_negative": False,
            }
            prev_appt_r = _run_query(prev_appt_q, api_key)

            prev_sales_q = _staff_filter(queries["Staff Sales Summary"])
            prev_sales_q["filters"]["dbt__moxie_invoices_mart.first_payment_date"] = {
                "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                "ui_type": "PAST", "left_side": prev_start, "right_side": "1 months",
                "is_negative": False,
            }
            if gross_field not in prev_sales_q.get("fields", []):
                prev_sales_q.setdefault("fields", []).append(gross_field)
            prev_sales_r = _run_query(prev_sales_q, api_key)

            # Build prior-month lookups
            p_appt_names   = _extract_col(prev_appt_r, "provider_name")
            p_appt_util    = _extract_col(prev_appt_r, "utilization_pct")
            p_appt_rebook  = _extract_col(prev_appt_r, "rebooking_rate")
            p_appt_hours   = _extract_col(prev_appt_r, "hours_booked_sum")
            p_appt_aov     = _extract_col(prev_appt_r, ".aov")

            prev_appt_lookup: dict = {}
            for i, name in enumerate(p_appt_names):
                if not name:
                    continue
                util = float(p_appt_util[i]) if i < len(p_appt_util) and p_appt_util[i] is not None else None
                if util is not None and util > 1.0:
                    util = util / 100
                rebook = float(p_appt_rebook[i]) if i < len(p_appt_rebook) and p_appt_rebook[i] is not None else 0
                if rebook > 1.0:
                    rebook = rebook / 100
                prev_appt_lookup[name] = {
                    "util": util,
                    "rebook": rebook,
                    "hours": float(p_appt_hours[i]) if i < len(p_appt_hours) and p_appt_hours[i] else None,
                    "aov": float(p_appt_aov[i]) if i < len(p_appt_aov) and p_appt_aov[i] else None,
                }

            p_sales_names = _extract_col(prev_sales_r, "attributed_provider_name")
            p_sales_net   = _extract_col(prev_sales_r, "sum_line_net_revenue")
            p_sales_gross = _extract_col(prev_sales_r, "gross_revenue_sum")

            prev_sales_lookup: dict = {}
            for i, name in enumerate(p_sales_names):
                if not name:
                    continue
                net = float(p_sales_net[i]) if i < len(p_sales_net) and p_sales_net[i] else 0
                gross = float(p_sales_gross[i]) if i < len(p_sales_gross) and p_sales_gross[i] else net
                if name not in prev_sales_lookup:
                    prev_sales_lookup[name] = {"net": 0, "gross": 0}
                prev_sales_lookup[name]["net"] += net
                prev_sales_lookup[name]["gross"] += gross

            # Apply MoM to each StaffMember
            for s in data.staff:
                pa = prev_appt_lookup.get(s.name, {})
                ps = prev_sales_lookup.get(s.name, {})
                prev_gr = ps.get("gross", 0)
                prev_net = ps.get("net", 0)
                prev_aov = pa.get("aov") or (prev_net / max(1, 1))
                s.revenue_mom_pct = _safe_mom(s.gross_revenue, prev_gr, 500)
                s.net_revenue_mom_pct = _safe_mom(s.net_revenue, prev_net, 500)
                s.aov_mom_pct = _safe_mom(s.aov, prev_aov, 50) if prev_aov else None
                s.utilization_mom_pct = _safe_mom(s.utilization, pa.get("util"), 0.05)
                s.rebooking_mom_pct = _safe_mom(s.rebooking_rate, pa.get("rebook"), 0.05)
                prev_hrs = pa.get("hours")
                if prev_hrs and prev_hrs > 0 and prev_gr > 500 and s.rev_per_hour:
                    s.rev_per_hour_mom_pct = _safe_mom(s.rev_per_hour, prev_gr / prev_hrs, 10)

            # Practice-level rebooking MoM
            prev_rebook_lookup = {n: d["rebook"] for n, d in prev_appt_lookup.items() if d.get("rebook")}
            prev_weight = sum(s.net_revenue for s in data.staff if s.name in prev_rebook_lookup and prev_rebook_lookup[s.name] > 0)
            if prev_weight > 0:
                prev_rebook_weighted = sum(
                    prev_rebook_lookup[s.name] * s.net_revenue
                    for s in data.staff if s.name in prev_rebook_lookup and prev_rebook_lookup[s.name] > 0
                ) / prev_weight
                if prev_rebook_weighted > 0.05 and data.rebooking_rate > 0:
                    data.rebooking_mom_pct = _safe_mom(data.rebooking_rate, prev_rebook_weighted, 0.05)

            # Practice-level utilization MoM
            p_total_hours = sum(
                d["hours"] or 0 for d in prev_appt_lookup.values() if d.get("hours")
            )
            if p_total_hours > 0:
                p_weighted_util = sum(
                    (d.get("util") or 0) * (d.get("hours") or 0)
                    for d in prev_appt_lookup.values()
                    if d.get("util") is not None and d.get("hours")
                ) / p_total_hours
                if p_weighted_util > 0 and data.utilization_rate > 0:
                    data.utilization_mom_pct = _safe_mom(data.utilization_rate, p_weighted_util, 0.05)

            print(f"  Staff MoM: loaded for {sum(1 for s in data.staff if s.revenue_mom_pct is not None)} providers")
        except Exception as e:
            print(f"  Warning: Could not load staff MoM: {e}")

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

    # ── Moxie Covered Async GFE Savings (main dashboard) ──
    print("  Loading GFE savings...")
    GFE_UNIT_PRICE = 25.0
    try:
        # Use the dedicated monthly/YTD queries that match the Omni GFE dashboard.
        # Fall back to pattern-matching if those names aren't present (older dashboards).
        MONTHLY_GFE_QUERY = "Monthly GFE Savings"
        YTD_GFE_QUERY     = "YTD GFE Savings"
        gfe_patterns = ("gfe", "good faith", "covered sync", "covered async",
                        "moxie sync", "moxie async", "mco gfe", "completed gfe")

        month_gfe_query = (MONTHLY_GFE_QUERY if MONTHLY_GFE_QUERY in queries
                           else next((n for n in queries if any(s in n.lower() for s in gfe_patterns)), None))
        ytd_gfe_query   = (YTD_GFE_QUERY if YTD_GFE_QUERY in queries
                           else month_gfe_query)

        def _gfe_pull_named(query_name, start_date_val, duration_val):
            gq = copy.deepcopy(queries[query_name])
            gq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "EQUALS", "type": "string",
                "values": [practice_name], "is_negative": False,
            }
            import re as _re
            all_field_names = list(gq.get("fields", [])) + list(gq.get("filters", {}).keys())
            unbracketed = [f for f in all_field_names
                           if "[" not in f
                           and ("date" in f.lower() or "_at" in f.lower() or "issued" in f.lower())]
            if unbracketed:
                date_field = unbracketed[0]
            else:
                bracketed = next(
                    (f for f in all_field_names
                     if "[" in f
                     and ("date" in f.lower() or "_at" in f.lower() or "issued" in f.lower())),
                    None,
                )
                date_field = _re.sub(r"\[[^\]]+\](?:__raw)?$", "", bracketed) if bracketed else None
            if date_field:
                gq["filters"][date_field] = {
                    "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                    "ui_type": "PAST",
                    "left_side": start_date_val, "right_side": duration_val,
                    "is_negative": False,
                }
            return _run_query(gq, api_key)

        # Keep _gfe_pull as alias for any legacy callers
        def _gfe_pull(start_date_val, duration_val):
            name = month_gfe_query or next((n for n in queries if any(s in n.lower() for s in gfe_patterns)), None)
            if not name:
                raise RuntimeError("No GFE query found")
            return _gfe_pull_named(name, start_date_val, duration_val)

        def _gfe_extract(result, is_ytd=False):
            if not result:
                return 0, 0.0
            n_rows = max((len(v) for v in result.values()
                          if isinstance(v, list)), default=0)
            keep = None
            for k, v in result.items():
                if ("medspa_id" in k.lower() or "medspa_name" in k.lower()) and isinstance(v, list):
                    non_null = [i for i, x in enumerate(v) if x is not None]
                    if non_null and len(non_null) < n_rows:
                        keep = set(non_null)
                        break

            candidates = []
            for k, v in result.items():
                if not v or k.startswith("$"):
                    continue
                items = [v[i] for i in sorted(keep)] if keep is not None else v
                total = 0.0
                for item in items:
                    try:
                        total += float(item)
                    except (TypeError, ValueError):
                        pass
                if total == 0.0 and all(item is None for item in items):
                    continue
                candidates.append((k.lower(), k, total))

            count_val, value_val = 0, 0.0
            for kl, k, raw in candidates:
                if "gfe" not in kl and "good_faith" not in kl:
                    continue
                try:
                    n = float(raw)
                except (TypeError, ValueError):
                    continue
                if count_val == 0 and ("count" in kl or "completed" in kl or "_sum" in kl) and "value" not in kl and "amount" not in kl and "savings" not in kl and "dollar" not in kl:
                    count_val = int(n)
                if value_val == 0.0 and ("value" in kl or "amount" in kl or "savings" in kl or "dollar" in kl or "total" in kl):
                    value_val = n

            if count_val == 0:
                for kl, k, raw in candidates:
                    try:
                        n = float(raw)
                    except (TypeError, ValueError):
                        continue
                    if ("count" in kl or "completed" in kl) and "value" not in kl and "amount" not in kl and "savings" not in kl:
                        count_val = int(n)
                        break
            if value_val == 0.0:
                for kl, k, raw in candidates:
                    try:
                        n = float(raw)
                    except (TypeError, ValueError):
                        continue
                    if "value" in kl or "amount" in kl or "savings" in kl or "dollar" in kl:
                        value_val = n
                        break

            if count_val == 0 and value_val == 0.0 and candidates:
                nums = []
                for kl, k, raw in candidates:
                    try:
                        n = float(raw)
                        nums.append((kl, k, n))
                    except (TypeError, ValueError):
                        pass
                if nums:
                    nums.sort(key=lambda t: t[2])
                    count_val = int(nums[0][2])
                    value_val = nums[-1][2] if len(nums) > 1 else nums[0][2]

            return count_val, value_val

        # Month — use dedicated "Monthly GFE Savings" query
        try:
            if month_gfe_query:
                month_r = _gfe_pull_named(month_gfe_query, start_date, duration)
                data.gfe_completed_month, data.gfe_value_month = _gfe_extract(month_r, is_ytd=False)
                if data.gfe_completed_month > 0 and data.gfe_value_month == 0.0:
                    data.gfe_value_month = data.gfe_completed_month * GFE_UNIT_PRICE
                if month_r:
                    sample = {k: (v[0] if v else None) for k, v in month_r.items() if not k.startswith("$")}
                    print(f"  GFE month raw ({month_gfe_query}): {sample}")
        except Exception as e:
            print(f"  Warning: GFE monthly query failed: {e}")

        # YTD — use dedicated "YTD GFE Savings" query
        try:
            if ytd_gfe_query:
                ytd_start = f"{year}-01-01"
                ytd_months = month
                ytd_r = _gfe_pull_named(ytd_gfe_query, ytd_start, f"{ytd_months} months")
                data.gfe_completed_ytd, data.gfe_value_ytd = _gfe_extract(ytd_r, is_ytd=True)
                if data.gfe_completed_ytd > 0 and data.gfe_value_ytd == 0.0:
                    data.gfe_value_ytd = data.gfe_completed_ytd * GFE_UNIT_PRICE
                if ytd_r:
                    sample = {k: (v[0] if v else None) for k, v in ytd_r.items() if not k.startswith("$")}
                    print(f"  GFE YTD raw ({ytd_gfe_query}): {sample}")
        except Exception as e:
            print(f"  Warning: GFE YTD query failed: {e}")

        print(f"  GFE: month {data.gfe_completed_month} @ ${data.gfe_value_month:,.0f}, "
              f"YTD {data.gfe_completed_ytd} @ ${data.gfe_value_ytd:,.0f}")
        if not month_gfe_query:
            print(f"  GFE query not found (looked for 'Monthly GFE Savings' or pattern match)")
    except Exception as e:
        print(f"  Warning: Could not load GFE savings: {e}")

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
            reb = sav.get("rebates", {})
            data.supplies_rebates_month = reb.get("month", 0.0)
            data.supplies_rebates_3mo = reb.get("m3", 0.0)
            data.supplies_rebates_ytd = reb.get("ytd", 0.0)
            data.supplies_rebates_all = reb.get("all", 0.0)
            print(f"  Supplies: month=${data.supplies_spend_month:,.0f}, "
                  f"3mo=${data.supplies_spend_3mo:,.0f}, "
                  f"YTD=${data.supplies_spend_ytd:,.0f}, "
                  f"all=${data.supplies_spend_all:,.0f}, "
                  f"rebates_3mo=${data.supplies_rebates_3mo:,.0f}")
    except Exception as e:
        print(f"  Warning: Could not load supplies transaction data: {e}")

    # ── Marketing Performance (separate dashboard) ──
    print("  Loading marketing performance...")
    # Default to an empty marketing record with the lock screen on. If the
    # dashboard lookup succeeds we'll overwrite this; if it doesn't (no row
    # for this practice, network failure, etc.) the editor still gets a
    # marketing block with a toggle so users can override the lock manually.
    from .data_schema import MarketingData
    data.marketing = MarketingData(
        ad_spend=0, leads=0, booked=0, completed=0,
        revenue=0.0, total_revenue_all_clients=0.0,
        show_marketing_lock_screen=True,
    )
    try:
        mkt_dash = _api_get(f"/v1/documents/{MARKETING_DASHBOARD_ID}/queries", api_key)
        mkt_queries = mkt_dash.get("queries", [])
        if mkt_queries:
            mq = copy.deepcopy(mkt_queries[0]["query"])
            # Replace PSM filter with practice filter — use CONTAINS so name
            # variations ('&' vs 'and', extra whitespace) still match.
            mq["filters"].pop("dbt__moxie_medspas_mart.provider_success_manager_name", None)
            mq["filters"]["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "CONTAINS", "type": "string",
                "values": [practice_name.split()[0]],
                "is_negative": False,
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

            # Find the practice row using a normalized name comparison so
            # punctuation / whitespace differences don't cause a miss.
            def _norm(s):
                import re as _re
                return _re.sub(r"[^a-z0-9]", "", (s or "").lower())
            target_norm = _norm(practice_name)
            mkt_names = mkt_r.get("dbt__moxie_medspas_mart.medspa_name", [])
            mkt_idx = next((i for i, n in enumerate(mkt_names)
                            if n and _norm(n) == target_norm), None)
            if mkt_idx is None:
                print(f"  Warning: marketing dashboard had no row for "
                      f"'{practice_name}'. Names returned: "
                      f"{[n for n in mkt_names if n][:5]}")

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

                # Build a marketing record whenever any metric is non-zero so
                # $0-spend practices that still produced leads/revenue can show
                # the section. Lock the section by default only when every
                # metric is zero — the editor can override either way.
                has_any_metric = (ad_spend > 0 or leads > 0 or booked > 0 or
                                  completed > 0 or revenue > 0 or total_rev_all > 0)

                if has_any_metric:
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
                        show_marketing_lock_screen=False,
                    )
                    print(f"  Marketing: spend=${ad_spend:,.0f}, leads={leads}, "
                          f"booked={booked}, completed={completed}, "
                          f"revenue=${revenue:,.0f}, ROI={roi:.1f}x")
                else:
                    # Everything zero — surface a marketing record so the editor
                    # can flip the lock toggle off if desired, but default to lock.
                    data.marketing = MarketingData(
                        ad_spend=0, leads=0, booked=0, completed=0,
                        revenue=0.0, total_revenue_all_clients=0.0,
                        show_marketing_lock_screen=True,
                    )
                    print("  Marketing: all metrics zero — lock screen on by default")

                # Campaign-level breakdown — only meaningful when ad spend exists
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
                print("  Marketing: no data found for this practice")
    except Exception as e:
        print(f"  Warning: Could not load marketing data: {e}")

    print(f"  Loaded: Net Rev ${data.monthly_net_revenue:,.2f}, "
          f"{data.total_appointments} appts, "
          f"{len(data.staff)} staff, "
          f"{len(data.services)} service categories")

    return data
