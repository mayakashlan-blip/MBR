"""Load MBR data from Omni Analytics API."""

import base64
import calendar
import copy
import json
import os
import urllib.request
from .data_schema import MBRData, StaffMember, ServiceItem


BASE_URL = "https://moxie.omniapp.co/api"
DASHBOARD_ID = "bfd963dd"
STAFF_DASHBOARD_ID = "955002e5"
SUPPLIES_DASHBOARD_ID = "54d5da36"
RETENTION_DASHBOARD_ID = "59ca3051"

# Query names → their date filter fields (per-topic)
# Omni requires TIME_FOR_INTERVAL_DURATION kind for date filtering.
# The field must belong to the same topic as the query.
QUERY_DATE_FIELDS = {
    # invoices_mart topic — use invoice_issued_date
    "KPI: Net Revenue": "dbt__moxie_invoices_mart.invoice_issued_date",
    "KPI: Paid Appointments": "dbt__moxie_invoices_mart.invoice_issued_date",
    "KPI: AOV": "dbt__moxie_invoices_mart.invoice_issued_date",
    "Client Counts": "dbt__moxie_invoices_mart.invoice_issued_date",
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


def _run_query(query_body: dict, api_key: str, retries: int = 2) -> dict:
    """Execute an Omni query and return the parsed Arrow result as a dict."""
    import pyarrow.ipc
    import time

    for attempt in range(retries + 1):
        data = json.dumps({"query": query_body}).encode()
        req = urllib.request.Request(f"{BASE_URL}/v1/query/run", data=data, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode()

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

    raise RuntimeError("No result returned from Omni query")


def _add_filters(query: dict, practice_name: str, start_date: str,
                 date_field: str = None) -> dict:
    """Add practice name and date range filters to a query.

    Uses TIME_FOR_INTERVAL_DURATION (start_date + "1 months") which is
    the only date filter kind Omni actually respects via the API.
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
            "right_side": "1 months",
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
                   api_key: str = None) -> MBRData:
    """Load MBR data from Omni Analytics API.

    Args:
        practice_name: Exact practice name as it appears in Omni.
        month: Month number (1-12).
        year: Year.
        api_key: Omni API key. Falls back to OMNI_API_KEY env var.

    Returns:
        Populated MBRData instance.
    """
    api_key = api_key or os.environ.get("OMNI_API_KEY")
    if not api_key:
        raise ValueError("No Omni API key provided. Set OMNI_API_KEY or pass --omni-key.")

    # First day of the month (TIME_FOR_INTERVAL_DURATION adds "1 months" from here)
    start_date = f"{year}-{month:02d}-01"

    # Fetch all query definitions from the dashboard
    print(f"  Connecting to Omni API...")
    dashboard = _api_get(f"/v1/documents/{DASHBOARD_ID}/queries", api_key)
    queries = {q["name"]: q["query"] for q in dashboard.get("queries", [])}
    print(f"  Found {len(queries)} queries in dashboard")

    data = MBRData(practice_name=practice_name, month=month, year=year)

    def run(name: str) -> dict:
        q = _find_query(queries, name)
        date_field = QUERY_DATE_FIELDS.get(name)
        q = _add_filters(q, practice_name, start_date, date_field)
        return _run_query(q, api_key)

    # ── Execute queries ──
    print(f"  Querying Omni for {practice_name}, {calendar.month_name[month]} {year}...")

    # KPI: Net Revenue
    r = run("KPI: Net Revenue")
    data.monthly_net_revenue = _val(r, "net_revenue_sum")
    revenue_goal = _val(r, "revenue_goal_sum")
    data.pct_net_revenue_goal = (data.monthly_net_revenue / revenue_goal
                                  if revenue_goal > 0 else 0)

    # Quarter to Date — sum net revenue from quarter start through current month
    quarter_start_month = ((month - 1) // 3) * 3 + 1  # Q1=1, Q2=4, Q3=7, Q4=10
    months_in_quarter = month - quarter_start_month + 1
    if months_in_quarter > 1:
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
            qtd_r = _run_query(qtd_q, api_key)
            data.quarter_to_date = _val(qtd_r, "net_revenue_sum")
        except Exception as e:
            print(f"  Warning: Could not load QTD: {e}")
            data.quarter_to_date = data.monthly_net_revenue
    else:
        # First month of quarter — QTD equals current month
        data.quarter_to_date = data.monthly_net_revenue

    # KPI: Paid Appointments
    r = run("KPI: Paid Appointments")
    data.total_appointments = int(_val(r, "paid_appointments"))

    # KPI: AOV
    r = run("KPI: AOV")
    data.aov = _val(r, "aov")
    aov_goal = _val(r, "aov_goal")
    data.pct_aov_goal = data.aov / aov_goal if aov_goal > 0 else 0

    # ── Previous Month MoM comparison ──
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_start = f"{prev_year}-{prev_month:02d}-01"
    print(f"  Loading prior month ({calendar.month_name[prev_month]} {prev_year}) for MoM...")

    def run_prev_safe(name: str):
        """Run a query for the previous month, returning None on failure."""
        try:
            q = _find_query(queries, name)
            date_field = QUERY_DATE_FIELDS.get(name)
            q = _add_filters(q, practice_name, prev_start, date_field)
            return _run_query(q, api_key)
        except Exception as e:
            print(f"  Warning: MoM query '{name}' failed: {e}")
            return None

    prev_rev_r = run_prev_safe("KPI: Net Revenue")
    if prev_rev_r:
        prev_revenue = _val(prev_rev_r, "net_revenue_sum")
        if prev_revenue and prev_revenue > 0:
            data.revenue_mom_pct = (data.monthly_net_revenue - prev_revenue) / prev_revenue

    prev_appt_r = run_prev_safe("KPI: Paid Appointments")
    if prev_appt_r:
        prev_appointments = int(_val(prev_appt_r, "paid_appointments"))
        if prev_appointments and prev_appointments > 0:
            data.appointments_mom_pct = (data.total_appointments - prev_appointments) / prev_appointments

    prev_aov_r = run_prev_safe("KPI: AOV")
    if prev_aov_r:
        prev_aov = _val(prev_aov_r, "aov")
        if prev_aov and prev_aov > 0:
            data.aov_mom_pct = (data.aov - prev_aov) / prev_aov

    print(f"  MoM: Rev {'N/A' if data.revenue_mom_pct is None else f'{data.revenue_mom_pct:+.1%}'}, "
          f"Appts {'N/A' if data.appointments_mom_pct is None else f'{data.appointments_mom_pct:+.1%}'}, "
          f"AOV {'N/A' if data.aov_mom_pct is None else f'{data.aov_mom_pct:+.1%}'}")

    # Utilization
    r = run("Utilization")
    util_pct = _val(r, "column_b_divided_by_column_a", default=None)
    if util_pct is not None:
        data.utilization_rate = util_pct if util_pct <= 1.0 else util_pct / 100
    else:
        total_avail = _val(r, "total_available_hours")
        total_appt = _val(r, "total_appointment_hours")
        data.utilization_rate = total_appt / total_avail if total_avail > 0 else 0

    # Client Counts
    r = run("Client Counts")
    data.new_clients = int(_val(r, "count_new_client"))
    data.existing_clients = int(_val(r, "count_existing_client"))

    # Memberships
    r = run("Active Members")
    data.memberships_active = int(_val(r, "count"))

    r = run("New Memberships")
    data.memberships_new = int(_val(r, "count"))
    data.mrr = _val(r, "mrr_sum")

    r = run("Churned Memberships")
    data.memberships_cancelled = int(_val(r, "count"))

    # Total Membership Revenue
    r = run("Total Membership Revenue")
    data.membership_sales = _val(r, "subtotal__membership_sum")

    # Gross Revenue Breakdown
    r = run("Gross Revenue Breakdown Summary")
    data.service_revenue = _val(r, "subtotal__service_sum")
    data.retail_revenue = _val(r, "subtotal__retail_product_sum")
    data.prepayment_revenue = _val(r, "subtotal__package_sum")
    data.custom_items = _val(r, "subtotal__custom_item_sum")
    # Total gross = sum of all revenue categories
    data.total_gross = (data.service_revenue + data.retail_revenue +
                        data.prepayment_revenue + data.custom_items +
                        data.membership_sales)

    # Fees as client_fees
    data.client_fees = _val(r, "fees_sum")

    # Retail to Service Ratio
    r = run("Retail to Service Revenue")
    ratio = _val(r, "calc_1", default=None)
    if ratio is not None:
        data.retail_to_service_ratio = ratio if ratio <= 1.0 else ratio / 100
    elif data.service_revenue > 0:
        data.retail_to_service_ratio = data.retail_revenue / data.service_revenue

    # Service Mix
    r = run("Gross Revenue By Official Service Type")
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
            "right_side": "1 months", "is_negative": False,
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
            "right_side": "1 months", "is_negative": False,
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
            "right_side": "1 months", "is_negative": False,
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

        # Practice-level rebooking rate: weighted average from provider data
        total_rebook_weight = sum(s.net_revenue for s in data.staff if s.rebooking_rate)
        if total_rebook_weight > 0:
            data.rebooking_rate = sum(
                s.rebooking_rate * s.net_revenue
                for s in data.staff if s.rebooking_rate
            ) / total_rebook_weight

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
                "right_side": "1 months", "is_negative": False,
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

    # ── Discounts ──
    # Derive from gross - net
    implied_adjustments = data.total_gross - data.monthly_net_revenue
    if implied_adjustments > 0 and data.discounts == 0:
        data.discounts = implied_adjustments

    print(f"  Loaded: Net Rev ${data.monthly_net_revenue:,.2f}, "
          f"{data.total_appointments} appts, "
          f"{len(data.staff)} staff, "
          f"{len(data.services)} service categories")

    return data
