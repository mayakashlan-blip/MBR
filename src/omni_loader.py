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

# ── Single source of truth: [New Embedded] Monthly Business Review ──
# The team iterates on this dashboard and its document id can change when
# it's republished (6b24fa95 → 7c568f71 on 2026-08-12). _resolve_mbr_dash()
# falls back to a lookup by exact name whenever the id 404s.
NEW_MBR_ID             = "7c568f71"
NEW_MBR_NAME           = "[New Embedded] Monthly Business Review"
# Kept for data the consolidated dashboard doesn't carry
DASHBOARD_ID           = "bfd963dd"  # tier / medspa-id lookup
SUPPLIES_DASHBOARD_ID  = "54d5da36"
MARKETING_DASHBOARD_ID = "0ef3afa3"  # campaign-level breakdown only
EMBEDDED_MBR_ID        = NEW_MBR_ID  # back-compat alias (parity checker)

_T = "dbt__moxie_invoice_transactions_mart"
_A = "dbt__moxie_appointments_mart"

# Query names → date filter fields used by _add_filters(). Using the SAME
# field as a query's baked date template overwrites it; a different field
# would AND against it and zero out historical months.
QUERY_DATE_FIELDS = {
    # ── Consolidated MBR dashboard (6b24fa95) ──
    "Sales Summary":              f"{_T}.transaction_date_et",
    "Total Sales by Category":    "dbt__moxie_invoices_mart.invoice_issued_date",
    "Total Sales by Service":     f"{_T}.transaction_date_et",
    "KPI Goal: Net Revenue":      f"{_T}.transaction_date_et",
    "KPI Goal: AOV":              f"{_T}.transaction_date_et",
    "KPI Goal: Paid Appointments": f"{_A}.start_time_local",
    "KPI Goal: Net Revenue — Last 4 Months": f"{_T}.transaction_date_et",
    "KPI Goal: AOV — Last 4 Months":         f"{_T}.transaction_date_et",
    "KPI Goal: Paid Appointments — Last 4 Months": f"{_A}.start_time",
    "KPI: Rebooking Rate":        f"{_A}.start_time_local",
    "KPI: Retention Rate":        f"{_A}.start_time_local",
    "KPI: Utilization":           "dbt__moxie_utilization_daily_mart.series_date",
    "Client Mix":                 f"{_A}.start_time_local",
    "Client Counts":              f"{_A}.start_time_local",
    "Net Revenue, QTD":           f"{_T}.transaction_date_et",
    "Payments & Refunds":         f"{_T}.transaction_date_et",
    "Membership Overview":        "dbt__moxie_client_membership_churn_monthly_mart.month_start",
    "Membership Breakdown":       "dbt__moxie_client_membership_churn_monthly_mart.month_start",
    "Staff Performance":          "dbt__moxie_embedded_staff_report_mart.report_date",
    "Monthly Marketing Performance": "dbt__marketing_medspa_performance_daily_mart.series_date",
    "YTD Marketing Performance":  "dbt__marketing_medspa_performance_daily_mart.series_date",
    "Monthly GFE Savings":        "dbt__moxie_gfe_review_submissions_mart.review_finished_at",
    "YTD GFE Savings":            "dbt__moxie_gfe_review_submissions_mart.review_finished_at",
}

# Multi-month windows: query name → months of history to request. The window
# ends at the report month; rows come back per-month for MoM + trend charts.
QUERY_WINDOW_MONTHS = {
    "KPI Goal: Net Revenue — Last 4 Months": 4,
    "KPI Goal: AOV — Last 4 Months": 4,
    "KPI Goal: Paid Appointments — Last 4 Months": 4,
    "KPI Goal: Paid Appointments": 2,
    "KPI: Rebooking Rate": 2,
    "KPI: Retention Rate": 2,
    "KPI: Utilization": 2,
}


def _api_get(path: str, api_key: str, retries: int = 5):
    """GET an Omni API path with 429/5xx retry — a failed dashboard fetch
    otherwise cascades into a report full of 'query not found' warnings."""
    import time
    for attempt in range(retries + 1):
        req = urllib.request.Request(f"{BASE_URL}{path}")
        req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if (e.code == 429 or 500 <= e.code < 600) and attempt < retries:
                retry_after = e.headers.get("Retry-After")
                wait = min(int(retry_after), 30) if retry_after and retry_after.isdigit() \
                    else min(2 * 2 ** attempt, 30)
                print(f"  API GET rate limited (attempt {attempt+1}/{retries}), waiting {wait}s…")
                time.sleep(wait)
                continue
            raise


# Dashboard query definitions barely change — cache them so each report
# generation doesn't spend ~9 rate-limited API calls re-fetching them.
_DASH_CACHE: dict = {}
_DASH_TTL_SECONDS = 600


def _get_dashboard_queries(dash_id: str, api_key: str) -> dict:
    import time
    now = time.time()
    hit = _DASH_CACHE.get(dash_id)
    if hit and now - hit[0] < _DASH_TTL_SECONDS:
        return hit[1]
    payload = _api_get(f"/v1/documents/{dash_id}/queries", api_key)
    _DASH_CACHE[dash_id] = (now, payload)
    return payload


def _resolve_mbr_dashboard(api_key: str) -> dict:
    """Fetch the consolidated MBR dashboard's queries, surviving id changes.

    Republishing the dashboard can mint a new document id (it happened
    mid-migration: 6b24fa95 → 7c568f71). Fast path is the known id; on a
    404 we page the documents API and match NEW_MBR_NAME exactly.
    """
    try:
        return _get_dashboard_queries(NEW_MBR_ID, api_key)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    print(f"  MBR dashboard id {NEW_MBR_ID} not found — resolving by name…")
    import urllib.parse
    cursor = None
    for _ in range(20):
        path = "/v1/documents?pageSize=100"
        if cursor:
            path += f"&cursor={urllib.parse.quote(cursor)}"
        page = _api_get(path, api_key) or {}
        for rec in page.get("records", []):
            if rec.get("name") == NEW_MBR_NAME and not rec.get("deleted"):
                new_id = rec.get("identifier")
                print(f"  Resolved '{NEW_MBR_NAME}' → {new_id}")
                return _get_dashboard_queries(new_id, api_key)
        info = page.get("pageInfo", {})
        if not info.get("hasNextPage"):
            break
        cursor = info.get("nextCursor")
    raise RuntimeError(
        f"Could not find dashboard named '{NEW_MBR_NAME}' in Omni — "
        f"was it renamed or deleted?")


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


def _ensure_filters(q: dict) -> dict:
    """Ensure q['filters'] is a mutable dict (handles null/list from Omni). Returns q['filters']."""
    if not isinstance(q.get("filters"), dict):
        q["filters"] = {}
    return q["filters"]


def _practice_filter(practice_name: str, medspa_id: int = None):
    """Return (field, filter_dict) scoping a query to one practice.

    Prefers a number-typed medspa_id EQUALS filter — duplicate names exist
    in Omni (e.g. two 'Coastal Glo' records, ids 1194/1195) and a name
    filter silently merges them. Falls back to medspa_name when the id
    could not be resolved. (An earlier id attempt failed because it sent
    string-typed values; Omni requires "type": "number" for this field.)
    """
    if medspa_id is not None:
        return ("dbt__moxie_medspas_mart.medspa_id", {
            "kind": "EQUALS", "type": "number", "is_inclusive": False,
            "values": [int(medspa_id)], "is_negative": False,
        })
    return ("dbt__moxie_medspas_mart.medspa_name", {
        "kind": "EQUALS", "type": "string",
        "values": [practice_name], "is_negative": False,
    })


def _add_filters(query: dict, practice_name: str, start_date: str,
                 date_field: str = None, duration: str = "1 months",
                 medspa_id: int = None) -> dict:
    """Add practice and date range filters to a query.

    duration can be "1 months", "3 months", etc.
    """
    q = copy.deepcopy(query)
    _ensure_filters(q)
    pf_field, pf_dict = _practice_filter(practice_name, medspa_id)
    # Drop stale name-based templates so they never AND against our id filter.
    # Dashboard queries ship with test values baked in (e.g. medspa_name_with_id
    # = "The Ivy Wellness (1538)" on the AOV tile).
    q["filters"].pop("dbt__moxie_medspas_mart.medspa_name", None)
    q["filters"].pop("dbt__moxie_medspas_mart.medspa_name_with_id", None)
    q["filters"][pf_field] = pf_dict
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

    # Load queries from the consolidated MBR dashboard — the single source of
    # truth for every report metric, including the tier/medspa-id lookup
    # ("Medspa Name"). Supplies + marketing-campaign extras still come from
    # their own dashboards further down.
    print(f"  Connecting to Omni API...")
    queries = {}
    try:
        dash = _resolve_mbr_dashboard(api_key)
        for q in dash.get("queries", []):
            if q.get("name") and q.get("query"):
                queries[q["name"]] = q["query"]
    except Exception as e:
        print(f"  Warning: could not load MBR dashboard: {e}")
    # GFE queries ship with a hardcoded reviewer list baked in from testing —
    # clear it so every practice's reviewers count.
    for _gname in ("Monthly GFE Savings", "YTD GFE Savings",
                   "Completed GFEs By Reviewer [New Flow]"):
        _gq = queries.get(_gname)
        if _gq:
            _gf = (_gq.get("filters") or {}).get(
                "dbt__moxie_providers_mart.provider_name")
            if isinstance(_gf, dict):
                _gf["values"] = []
    # The dashboard's Total Sales by Service chart keeps a top-10 row limit
    # for display; the report wants every service category (percentages are
    # computed against practice Total Sales, so nothing may be truncated).
    _svc_q = queries.get("Total Sales by Service")
    if _svc_q and _svc_q.get("limit"):
        _svc_q["limit"] = 500
    # The Monthly Marketing Performance tile lacks the "booked appointments"
    # step of the funnel — ride it along on the same mart.
    _mkq = queries.get("Monthly Marketing Performance")
    _MKT_BOOKED = ("dbt__marketing_medspa_performance_daily_mart"
                   ".meta_new_clients_booked_appointment_sum")
    if _mkq and isinstance(_mkq.get("fields"), list) and _MKT_BOOKED not in _mkq["fields"]:
        _mkq["fields"].append(_MKT_BOOKED)
    print(f"  Found {len(queries)} queries on the MBR dashboard")
    if not queries:
        # Without query definitions every metric would silently load as $0.
        # Fail loudly instead of generating (and saving) an empty report.
        raise RuntimeError(
            "Could not load any dashboard queries from Omni (likely rate "
            "limited). Wait a minute and regenerate — do not trust a $0 report."
        )

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
            _ensure_filters(tier_q)["dbt__moxie_medspas_mart.medspa_name"] = {
                "kind": "CONTAINS", "type": "string",
                "values": [practice_name.split()[0]],
                "is_negative": False,
            }
            tier_field = "dbt__moxie_medspas_mart.provider_segment_post_launch"
            name_field = "dbt__moxie_medspas_mart.medspa_name"
            id_field = "dbt__moxie_medspas_mart.medspa_id"
            if not isinstance(tier_q.get("fields"), list):
                tier_q["fields"] = []
            for f in [tier_field, name_field, id_field]:
                if f not in tier_q["fields"]:
                    tier_q["fields"].append(f)
            tier_q["limit"] = 50
            tier_r = _run_query(tier_q, api_key)
            tier_names = tier_r.get(name_field, [])
            tiers = tier_r.get(tier_field, [])
            ids = tier_r.get(id_field, [])
            target_norm = _norm_name(practice_name)

            def _pick(indices):
                """Duplicate records can share one name (e.g. two 'Coastal Glo'
                rows) — prefer the one with a tier set; it's the live record.
                If still ambiguous (e.g. two untiered 'The Beauty Bar' rows),
                probe trailing-12-month revenue and pick the active record."""
                if not indices:
                    return None
                if len(indices) == 1:
                    return indices[0]
                dupes = [(int(ids[i]) if i < len(ids) and ids[i] is not None else None,
                          tiers[i] if i < len(tiers) else None) for i in indices]
                with_tier = [i for i in indices if i < len(tiers) and tiers[i]]
                pool = with_tier or indices
                if len(pool) == 1:
                    print(f"  Duplicate records for '{practice_name}': {dupes} "
                          f"— using tiered record")
                    return pool[0]
                best, best_rev = pool[0], -1.0
                for i in pool:
                    mid = int(ids[i]) if i < len(ids) and ids[i] is not None else None
                    if mid is None:
                        continue
                    try:
                        probe = _find_query(queries, "Sales Summary")
                        probe = _add_filters(
                            probe, practice_name, f"{year - 1}-{month:02d}-01",
                            QUERY_DATE_FIELDS["Sales Summary"], "13 months",
                            medspa_id=mid)
                        rev = _val(_run_query(probe, api_key), "net_revenue_sum")
                    except Exception:
                        rev = 0.0
                    if rev > best_rev:
                        best, best_rev = i, rev
                print(f"  Duplicate records for '{practice_name}': {dupes} — "
                      f"picked id {ids[best]} (${best_rev:,.0f} trailing-12mo revenue)")
                return best

            # 1. Exact normalized match
            tier_idx = _pick([i for i, n in enumerate(tier_names)
                              if n and _norm_name(n) == target_norm])
            # 2. Prefix match — handles "Glow & Go" in Omni vs "Glow & Go Aesthetics" entered
            if tier_idx is None:
                tier_idx = _pick(
                    [i for i, n in enumerate(tier_names)
                     if n and len(_norm_name(n)) >= 6 and (
                         target_norm.startswith(_norm_name(n)) or
                         _norm_name(n).startswith(target_norm)
                     )]
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
    data.medspa_id = medspa_id

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _month_start_offset(k_back: int) -> str:
        idx = year * 12 + (month - 1) - k_back
        return f"{idx // 12}-{idx % 12 + 1:02d}-01"

    def run(name: str) -> dict:
        q = _find_query(queries, name)
        date_field = QUERY_DATE_FIELDS.get(name)
        k = QUERY_WINDOW_MONTHS.get(name)
        if k and k > 1:
            # Multi-month window ending at the report month (per-month rows
            # for MoM + trend charts, mirroring the dashboard's tiles)
            q = _add_filters(q, practice_name, _month_start_offset(k - 1),
                             date_field, f"{k} months", medspa_id=medspa_id)
        else:
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

    # Extend Sales Summary with the per-category subtotal measures. These live
    # on the same transactions mart as the Total Sales headline, so
    # services + gift cards + packages + memberships + retail + custom + fees
    # sums to total_invoice_revenue_sum exactly (confirmed against Suite).
    # Tips ride along too — the consolidated dashboard's Sales Summary tile
    # doesn't carry provider_owner_tip_amount_sum natively.
    _SUBTOTAL_FIELDS = [
        "dbt__moxie_invoice_transactions_mart.subtotal__service_sum",
        "dbt__moxie_invoice_transactions_mart.subtotal__package_sum",
        "dbt__moxie_invoice_transactions_mart.subtotal__membership_sum",
        "dbt__moxie_invoice_transactions_mart.subtotal__retail_product_sum",
        "dbt__moxie_invoice_transactions_mart.subtotal__gift_card_sum",
        "dbt__moxie_invoice_transactions_mart.subtotal__custom_item_sum",
        "dbt__moxie_invoice_transactions_mart.fee_amount_sum",
        "dbt__moxie_invoice_transactions_mart.provider_owner_tip_amount_sum",
        # Net revenue goal joins from the goals mart within the same topic —
        # exactly how the dashboard's "KPI Goal: Net Revenue" tile does it.
        "dbt__moxie_medspa_goals_monthly_mart.revenue_goal_sum",
        # Canonical AOV (Sales-Report basis; also in the monthly summary mart)
        "dbt__moxie_invoice_transactions_mart.aov",
    ]
    if "Sales Summary" in queries:
        _sq = queries["Sales Summary"]
        if not isinstance(_sq.get("fields"), list):
            _sq["fields"] = []
        for _f in _SUBTOTAL_FIELDS:
            if _f not in _sq["fields"]:
                _sq["fields"].append(_f)

    # Batch 1: all independent queries from the consolidated MBR dashboard.
    # The windowed KPI queries (QUERY_WINDOW_MONTHS) return one row per month,
    # covering current value, prior-month MoM, and 4-bar trend charts in a
    # single run each — no separate per-month re-queries.
    batch1_names = [
        "Sales Summary",                     # totals + subtotals + goal + aov + tips
        "Total Sales by Service",            # service mix (line-items gross revenue)
        "Client Mix",                        # new/existing appointment counts
        "Client Counts",                     # distinct paying clients
        "Membership Overview",               # active/new/churned/MRR totals
        "Membership Breakdown",              # same measures per membership name
        "Payments & Refunds",                # payment + refund totals
        "KPI Goal: Net Revenue — Last 4 Months",       # revenue trend + goals
        "KPI Goal: AOV — Last 4 Months",               # aov trend + goals
        "KPI Goal: Paid Appointments — Last 4 Months", # appointment trend + goals
        "KPI Goal: Paid Appointments",       # paid appts current + prior (tile basis)
        "KPI: Rebooking Rate",               # current + prior month
        "KPI: Retention Rate",               # current + prior month
        "KPI: Utilization",                  # current + prior month
        "Monthly Marketing Performance",     # marketing funnel (single row)
    ]
    batch1 = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_safe, name): name for name in batch1_names}
        for future in as_completed(futures):
            batch1[futures[future]] = future.result()

    # ── Month-keyed extraction for month-dimension tiles ──
    # Windowed queries return one row per month and sort order varies by
    # tile, so rows are always keyed by their 'YYYY-MM' label, never by
    # position.
    def _month_map(res: dict, value_substring: str) -> dict:
        res = res or {}
        months = next((v for k, v in res.items()
                       if k.endswith("[month]") and isinstance(v, list)), None)
        vals = next((v for k, v in res.items()
                     if value_substring in k and "[" not in k
                     and not k.startswith("$") and isinstance(v, list)), None)
        out = {}
        if months and vals:
            for i, m in enumerate(months):
                if m is not None and i < len(vals) and vals[i] is not None:
                    try:
                        out[str(m)] = float(vals[i])
                    except (TypeError, ValueError):
                        pass
        return out

    def _mkey(y: int, m: int) -> str:
        return f"{y}-{m:02d}"

    cur_key = _mkey(year, month)
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    prev_start = f"{prev_year}-{prev_month:02d}-01"
    prev_key = _mkey(prev_year, prev_month)

    # ── Process batch 1 results ──

    # Revenue totals — from ungrouped Sales Summary (wallet/tax are invoice-level).
    # The dashboard's field names differ slightly from the old Standard Report
    # (discount_amount_sum_0, total_wallet_dollars_redeemed__negative_) but
    # _val's substring match handles both.
    r = batch1.get("Sales Summary", {})
    data.monthly_net_revenue = _val(r, "net_revenue_sum")
    data.total_sales = _val(r, "total_invoice_revenue_sum")
    data.total_gross = _val(r, "gross_revenue_sum")
    data.discounts = abs(_val(r, "discount_amount_sum"))
    data.wallet_dollar_redemptions = abs(_val(r, "total_wallet_dollars_redeemed"))
    data.wallet_item_redemptions = abs(_val(r, "total_wallet_item_discounts"))
    data.tax_collected = _val(r, "total_tax_amount_sum")
    data.tips = _val(r, "provider_owner_tip_amount_sum")

    # Revenue breakdown — per-category subtotal measures from the same Sales
    # Summary result (transactions mart, same date basis as Total Sales).
    data.service_revenue = _val(r, "subtotal__service_sum")
    data.gift_card_revenue = _val(r, "subtotal__gift_card_sum")
    data.prepayment_revenue = _val(r, "subtotal__package_sum")
    data.membership_sales = _val(r, "subtotal__membership_sum")
    data.retail_revenue = _val(r, "subtotal__retail_product_sum")
    data.custom_items = _val(r, "subtotal__custom_item_sum")
    data.client_fees = _val(r, "fee_amount_sum")
    _bar_total = (data.service_revenue + data.gift_card_revenue +
                  data.prepayment_revenue + data.membership_sales +
                  data.retail_revenue + data.custom_items + data.client_fees)
    print(f"  Sales categories sum ${_bar_total:,.2f} vs Total Sales ${data.total_sales:,.2f}")

    # ── Appointments + goals — dashboard's "KPI Goal: Paid Appointments" ──
    # The 2-month windowed tile (start_time_local basis, matching what
    # practices see in Suite) covers both the headline and the MoM value.
    def _val_suffix(res: dict, suffix: str, default=0):
        for k, v in res.items():
            if k.endswith(suffix) and v and v[0] is not None:
                try:
                    return float(v[0])
                except (TypeError, ValueError):
                    return v[0]
        return default

    paid_map = _month_map(batch1.get("KPI Goal: Paid Appointments"),
                          "paid_appointments")
    paid4_map = _month_map(batch1.get("KPI Goal: Paid Appointments — Last 4 Months"),
                           "paid_appointments")
    data.total_appointments = int(paid_map.get(cur_key)
                                  or paid4_map.get(cur_key, 0))
    paid_prev = paid_map.get(prev_key) or paid4_map.get(prev_key)

    appt_goal_map = _month_map(batch1.get("KPI Goal: Paid Appointments"),
                               "appointment_goal_sum")
    data.appt_goal = appt_goal_map.get(cur_key, 0)

    # AOV goal — from the AOV tile's appointment-goals-mart join
    aov_goal_map = _month_map(batch1.get("KPI Goal: AOV — Last 4 Months"),
                              "aov_goal_average")
    data.aov_goal = aov_goal_map.get(cur_key, 0)

    # AOV — transactions-mart measure (the tile's own basis). Rides along on
    # the Sales Summary result.
    data.aov = _val_suffix(batch1.get("Sales Summary", {}),
                           "invoice_transactions_mart.aov")
    if data.aov == 0:
        data.aov = (data.monthly_net_revenue / data.total_appointments
                    if data.total_appointments > 0 else 0)

    # ── Monthly goals ──
    # Net revenue goal rides along on the Sales Summary result via the
    # goals-mart join (same as Suite's KPI tile). AOV goal comes from the
    # appointment-goals mart in the paid-appointments query above.
    data.revenue_goal = _val(batch1.get("Sales Summary", {}), "revenue_goal_sum")

    # Fallback: re-base onto dbt__moxie_medspa_monthly_summary_mart (its own
    # topic; month grain field series_month) if either goal is still missing.
    try:
        goal_base = queries.get("Sales Summary") or next(iter(queries.values()), None)
        if goal_base and (data.revenue_goal <= 0 or data.aov_goal <= 0):
            _GOALS_MART = "dbt__moxie_medspa_monthly_summary_mart"
            gq = copy.deepcopy(goal_base)
            gq["table"] = _GOALS_MART
            gq["join_paths_from_topic_name"] = _GOALS_MART
            gq["fields"] = [f"{_GOALS_MART}.revenue_goal", f"{_GOALS_MART}.aov_goal"]
            gq["pivots"] = []
            gq["sorts"] = []
            gq["row_totals"] = {}
            gq["column_totals"] = {}
            _pf_field, _pf = _practice_filter(practice_name, medspa_id)
            gq["filters"] = {
                _pf_field: _pf,
                f"{_GOALS_MART}.series_month": {
                    "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                    "ui_type": "PAST", "left_side": start_date,
                    "right_side": duration, "is_negative": False,
                },
            }
            goal_r = _run_query(gq, api_key)
            if data.revenue_goal <= 0:
                data.revenue_goal = _val(goal_r, "revenue_goal")
            if data.aov_goal <= 0:
                data.aov_goal = _val(goal_r, "aov_goal")
    except Exception as e:
        print(f"  Warning: could not load goals: {e}")
    if data.revenue_goal > 0:
        data.pct_net_revenue_goal = data.monthly_net_revenue / data.revenue_goal
    if data.aov_goal > 0:
        data.pct_aov_goal = data.aov / data.aov_goal
    print(f"  Goals: revenue ${data.revenue_goal:,.0f} "
          f"({data.pct_net_revenue_goal * 100:.1f}%), "
          f"AOV ${data.aov_goal:,.0f} ({data.pct_aov_goal * 100:.1f}%), "
          f"appts {data.total_appointments} of {data.appt_goal:,.0f}")

    # Rebooking — the dashboard's own KPI tile (2-month window → MoM too)
    rebook_map = _month_map(batch1.get("KPI: Rebooking Rate"), "rebooking_rate")
    _rb = rebook_map.get(cur_key)
    if _rb is not None:
        data.rebooking_rate = _rb if _rb <= 1.0 else _rb / 100
    _rb_prev = rebook_map.get(prev_key)
    if _rb_prev is not None and data.rebooking_rate > 0:
        if _rb_prev > 1.0:
            _rb_prev /= 100
        if _rb_prev > 0.05:
            data.rebooking_mom_pct = _safe_mom(data.rebooking_rate, _rb_prev, 0.05)

    # Retention — the dashboard's KPI tile (rolling 180d measure, per month)
    ret_map = _month_map(batch1.get("KPI: Retention Rate"),
                         "pct_has_repeat_completed_appointments_180d")
    data.retention_180d = ret_map.get(cur_key, 0.0)
    _ret_prev = ret_map.get(prev_key)
    if _ret_prev and data.retention_180d:
        data.retention_mom_pct = _safe_mom(data.retention_180d, _ret_prev, 0.05)
    if data.retention_180d:
        print(f"  Retention (180d): {data.retention_180d*100:.1f}%")

    # Practice utilization — the dashboard's KPI tile (2-month window → MoM).
    # The tile is the source of truth; no staff-hours-weighted blend (same
    # decision as rebooking — blends drift from Omni's own number).
    util_map = _month_map(batch1.get("KPI: Utilization"),
                          "column_b_divided_by_column_a")
    _u = util_map.get(cur_key)
    if _u is not None:
        data.utilization_rate = _u if _u <= 1.0 else _u / 100
    _u_prev = util_map.get(prev_key)
    if _u_prev is not None and data.utilization_rate > 0:
        if _u_prev > 1.0:
            _u_prev /= 100
        data.utilization_mom_pct = _safe_mom(data.utilization_rate, _u_prev, 0.05)

    # Client mix — new/existing are appointment counts (client-mix bar);
    # paid_appointment_clients is DISTINCT paying clients, so Revenue per
    # Client doesn't collapse into AOV.
    r = batch1.get("Client Mix", {})
    _new = int(_val(r, "count_new_client_appointments"))
    _existing = int(_val(r, "count_existing_client_appointments"))
    if _new or _existing:
        data.new_clients = _new
        data.existing_clients = _existing
    r = batch1.get("Client Counts", {})
    _distinct = int(_val(r, "paid_appointment_clients"))
    if _distinct > 0:
        data.paid_clients = _distinct

    # Memberships — one Membership Overview tile carries all four measures
    r = batch1.get("Membership Overview", {})
    data.memberships_active = int(_val(r, "active_memberships_sum"))
    data.memberships_new = int(_val(r, "new_memberships_sum"))
    data.memberships_cancelled = int(_val(r, "churned_memberships_sum"))
    data.mrr = _val(r, "active_mrr")
    # membership_sales comes from subtotal__membership_sum (authoritative, same
    # basis as Total Sales) — no enrollment-revenue fallback, it would break
    # the category bars' reconciliation.

    # Refunds — from the Payments & Refunds tile
    r = batch1.get("Payments & Refunds", {})
    data.refunds = abs(_val(r, "refund_amount_sum"))
    data.redemptions = data.refunds  # backward compat

    # Retail MoM placeholder (will be updated after MoM queries)
    # Retail to service ratio
    if data.service_revenue > 0:
        data.retail_to_service_ratio = data.retail_revenue / data.service_revenue

    # ── Previous Month MoM + QTD ──
    # Revenue / AOV / appointment MoM and trend history all come from the
    # dashboard's windowed "— Last 4 Months" tiles (already in batch1).
    # Only two extra runs remain: prior-month Sales Summary (retail MoM +
    # prev-AOV fallback) and QTD.
    print(f"  Loading prior month + QTD in parallel...")

    def run_prev(name: str):
        try:
            q = _find_query(queries, name)
            date_field = QUERY_DATE_FIELDS.get(name)
            q = _add_filters(q, practice_name, prev_start, date_field, medspa_id=medspa_id)
            return _run_query(q, api_key)
        except Exception:
            return None

    def _month_offset(m, y, k):
        idx = (y * 12 + (m - 1)) - k
        return idx % 12 + 1, idx // 12

    def run_qtd():
        quarter_start_month = ((month - 1) // 3) * 3 + 1
        months_in_quarter = month - quarter_start_month + 1
        if months_in_quarter <= 1:
            return data.monthly_net_revenue
        qtd_start = f"{year}-{quarter_start_month:02d}-01"
        qtd_q = _find_query(queries, "Sales Summary")
        qtd_date_field = QUERY_DATE_FIELDS.get("Sales Summary")
        qtd_q = copy.deepcopy(qtd_q)
        _ensure_filters(qtd_q)
        _pf_field, _pf = _practice_filter(practice_name, medspa_id)
        qtd_q["filters"][_pf_field] = _pf
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
            pool.submit(run_qtd): "qtd",
        }
        for future in as_completed(mom_futures):
            mom_results[mom_futures[future]] = future.result()

    data.quarter_to_date = mom_results.get("qtd", data.monthly_net_revenue)
    prev_rev_r = mom_results.get("prev_rev")

    # Revenue MoM — from the Net Revenue Last-4-Months tile
    rev_map = _month_map(batch1.get("KPI Goal: Net Revenue — Last 4 Months"),
                         "net_revenue_sum")
    prev_revenue = rev_map.get(prev_key, 0)
    if not prev_revenue and prev_rev_r:
        prev_revenue = _val(prev_rev_r, "net_revenue_sum")
    if prev_revenue:
        data.revenue_mom_pct = _safe_mom(data.monthly_net_revenue, prev_revenue, 100)

    # Appointments MoM — paid appointments, same tile basis as the headline
    if paid_prev:
        data.appointments_mom_pct = _safe_mom(
            data.total_appointments, int(paid_prev), 5)

    # AOV MoM — from the AOV Last-4-Months tile (transactions-mart measure)
    aov_map = _month_map(batch1.get("KPI Goal: AOV — Last 4 Months"),
                         "invoice_transactions_mart.aov")
    prev_aov = (aov_map.get(prev_key)
                or _val_suffix(prev_rev_r or {}, "invoice_transactions_mart.aov"))
    if prev_aov and data.aov > 0:
        data.aov_mom_pct = _safe_mom(data.aov, prev_aov, 20)

    # Retail MoM — prior-month Sales Summary carries the same subtotal fields
    if prev_rev_r:
        prev_retail = _val(prev_rev_r, "subtotal__retail_product_sum")
        data.retail_revenue_mom_pct = _safe_mom(data.retail_revenue, prev_retail, 100)

    # Build 4-bar comparison history (m-3, m-2, m-1, current) for revenue,
    # AOV — straight from the Last-4-Months tiles, keyed by month label.
    import calendar as _cal
    def _hist(label_month, label_year, value):
        return {
            "label": _cal.month_abbr[label_month],
            "month": label_month, "year": label_year,
            "value": float(value) if value else 0.0,
        }
    _hist_months = [_month_offset(month, year, k) for k in (3, 2, 1, 0)]
    data.revenue_history = [
        _hist(m, y, rev_map.get(_mkey(y, m),
                                data.monthly_net_revenue if (m, y) == (month, year) else 0))
        for m, y in _hist_months
    ]
    data.aov_history = [
        _hist(m, y, aov_map.get(_mkey(y, m),
                                data.aov if (m, y) == (month, year) else 0))
        for m, y in _hist_months
    ]

    print(f"  MoM: Rev {'N/A' if data.revenue_mom_pct is None else f'{data.revenue_mom_pct:+.1%}'}, "
          f"Appts {'N/A' if data.appointments_mom_pct is None else f'{data.appointments_mom_pct:+.1%}'}, "
          f"AOV {'N/A' if data.aov_mom_pct is None else f'{data.aov_mom_pct:+.1%}'}")

    # Service Mix — the dashboard's "Total Sales by Service" tile:
    # line-items gross revenue by service_category (additive — invoice-level
    # measures grouped by line-item dimensions would overlap across rows).
    r = batch1.get("Total Sales by Service", {})
    svc_cats = _extract_col(r, "service_category")
    svc_cat_revs = _extract_col(r, "gross_revenue_sum")
    svc_by_type: dict = {}
    for i, cat in enumerate(svc_cats):
        rev = float(svc_cat_revs[i]) if i < len(svc_cat_revs) and svc_cat_revs[i] else 0
        cat = cat or "Other"
        svc_by_type[cat] = svc_by_type.get(cat, 0) + rev

    for cat, rev in sorted(svc_by_type.items(), key=lambda x: -x[1]):
        if rev > 0:
            data.services.append(ServiceItem(name=cat, revenue=rev))
    data.services.sort(key=lambda s: s.revenue, reverse=True)
    data.compute_service_percentages()

    # Membership breakdown by type — one Membership Breakdown tile carries
    # name + active + new + churned + MRR per membership (plus a grouped
    # column-total row that must be skipped).
    try:
        r = batch1.get("Membership Breakdown", {})
        mem_names = _extract_col(r, "membership_name")
        mem_actives = _extract_col(r, "active_memberships_sum")
        mem_news = _extract_col(r, "new_memberships_sum")
        mem_churneds = _extract_col(r, "churned_memberships_sum")
        mem_mrrs = _extract_col(r, "active_mrr")
        total_markers = r.get("$omni_column_total_indicator", [])

        def _cell(col, i, cast):
            return cast(col[i]) if i < len(col) and col[i] is not None else 0

        for i, name in enumerate(mem_names):
            if not name:
                continue
            if i < len(total_markers) and total_markers[i] == "column_total":
                continue
            data.membership_types.append(MembershipType(
                name=name,
                active=_cell(mem_actives, i, int),
                new=_cell(mem_news, i, int),
                churned=_cell(mem_churneds, i, int),
                mrr=_cell(mem_mrrs, i, float),
            ))
        data.membership_types.sort(key=lambda m: m.active, reverse=True)
        print(f"  Membership types: {len(data.membership_types)} loaded")
    except Exception as e:
        print(f"  Warning: Could not load membership breakdown: {e}")

    # ── Staff Performance — the dashboard's own per-provider tile ──
    # One query carries name, Total Sales, Net Revenue, AOV, utilization,
    # rebooking, and hours booked (role=provider, GFE reviewers excluded by
    # the tile's own is_gfe_reviewer filter). Run once for the report month
    # and once for the prior month (the tile has no month dimension, so MoM
    # needs a second run).
    print("  Loading staff performance...")
    try:
        def _staff_run(start, dur):
            q = _find_query(queries, "Staff Performance")
            q = _add_filters(q, practice_name, start,
                             QUERY_DATE_FIELDS.get("Staff Performance"), dur,
                             medspa_id=medspa_id)
            return _run_query(q, api_key)

        def _staff_lookup(res):
            names = _extract_col(res, "provider_name")
            totals = _extract_col(res, "total_sales_sum")
            nets = _extract_col(res, "total_net_revenue_sum")
            aovs = _extract_col(res, ".aov")
            utils = _extract_col(res, "utilization_pct")
            rebooks = _extract_col(res, "rebooking_rate")
            hours = _extract_col(res, "hours_booked_sum")
            out = {}
            for i, name in enumerate(names):
                if not name:
                    continue
                def _g(col):
                    return (float(col[i]) if i < len(col)
                            and col[i] is not None else None)
                util = _g(utils)
                if util is not None and util > 1.0:
                    util /= 100
                rebook = _g(rebooks) or 0
                if rebook > 1.0:
                    rebook /= 100
                out[name] = {
                    "total": _g(totals) or 0.0,
                    "net": _g(nets) or 0.0,
                    "aov": _g(aovs),
                    "util": util,
                    "rebook": rebook,
                    "hours": _g(hours),
                }
            return out

        cur_staff = _staff_lookup(_staff_run(start_date, duration))

        # The report's staff table is a sales table: only providers with
        # revenue this month (the tile also returns zero-revenue staff).
        for name in sorted(cur_staff):
            s = cur_staff[name]
            if s["total"] <= 0 and s["net"] <= 0:
                continue
            data.staff.append(StaffMember(
                name=name,
                net_revenue=s["net"],
                gross_revenue=s["total"] if s["total"] > 0 else s["net"],  # rendered as "Total Sales"
                aov=s["aov"] or 0,
                utilization=s["util"],
                rebooking_rate=s["rebook"],
                service_revenue=s["net"],
                retail_revenue=0,
                hours_worked=s["hours"],
            ))
        data.staff.sort(key=lambda s: s.gross_revenue, reverse=True)

        # Practice-level utilization/rebooking stay on the dashboard's own
        # KPI tiles (parsed above) — staff-weighted blends drift from Omni's
        # numbers (e.g. 26.3% blended vs Omni's 25% rebooking).

        # ── Per-provider MoM (prior-month Staff Performance run) ──
        try:
            prev_staff = _staff_lookup(_staff_run(prev_start, "1 months"))
            for s in data.staff:
                p = prev_staff.get(s.name)
                if not p:
                    continue
                s.revenue_mom_pct = _safe_mom(s.gross_revenue, p["total"], 500)
                s.net_revenue_mom_pct = _safe_mom(s.net_revenue, p["net"], 500)
                s.aov_mom_pct = _safe_mom(s.aov, p["aov"], 50) if p["aov"] else None
                s.utilization_mom_pct = _safe_mom(s.utilization, p["util"], 0.05)
                s.rebooking_mom_pct = _safe_mom(s.rebooking_rate, p["rebook"], 0.05)
                if p["hours"] and p["total"] > 500 and s.rev_per_hour:
                    s.rev_per_hour_mom_pct = _safe_mom(
                        s.rev_per_hour, p["total"] / p["hours"], 10)
            print(f"  Staff MoM: loaded for "
                  f"{sum(1 for s in data.staff if s.revenue_mom_pct is not None)} providers")
        except Exception as e:
            print(f"  Warning: Could not load staff MoM: {e}")

        print(f"  Staff: {len(data.staff)} providers loaded")
    except Exception as e:
        print(f"  Warning: Could not load staff data: {e}")

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
            _pf_field, _pf = _practice_filter(practice_name, medspa_id)
            _ensure_filters(gq)[_pf_field] = _pf
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
        sup_dash = _get_dashboard_queries(SUPPLIES_DASHBOARD_ID, api_key)
        sup_queries = sup_dash.get("queries", [])
        if sup_queries:
            sq = copy.deepcopy(sup_queries[0]["query"])
            _pf_field, _pf = _practice_filter(practice_name, medspa_id)
            _ensure_filters(sq)[_pf_field] = _pf
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

    # ── Marketing Performance ──
    # Headline funnel metrics come from the consolidated dashboard's
    # "Monthly Marketing Performance" tile (single aggregate row for the
    # practice, already in batch1). The campaign-level table still comes
    # from the marketing dashboard — the consolidated dashboard carries no
    # per-campaign breakdown.
    print("  Loading marketing performance...")
    # Default to an empty marketing record with the lock screen on. If the
    # tile has data we'll overwrite this; if it doesn't (no marketing for
    # this practice, query failure, etc.) the editor still gets a marketing
    # block with a toggle so users can override the lock manually.
    from .data_schema import MarketingData
    data.marketing = MarketingData(
        ad_spend=0, leads=0, booked=0, completed=0,
        revenue=0.0, total_revenue_all_clients=0.0,
        show_marketing_lock_screen=True,
    )
    try:
        mkt_r = batch1.get("Monthly Marketing Performance", {})

        def _mval(substring, default=0.0):
            v = _val(mkt_r, substring, default=None)
            return float(v) if v is not None else default

        ad_spend = _mval("meta_spend_sum")
        leads = int(_mval("meta_leads_sum"))
        booked = int(_mval("meta_new_clients_booked_appointment_sum"))
        completed = int(_mval("meta_new_clients_completed_appointment_sum"))
        # Revenue comes directly from Omni (net revenue from new clients)
        revenue = _mval("meta_new_clients_completed_appointment_revenue_sum")
        # Total revenue across all clients (new + existing) attributed to marketing
        total_rev_all = _mval("meta_completed_appointment_revenue_sum")

        # ROI = new-client revenue / spend (New Client ROI — the tile's
        # meta_roi uses all-clients revenue, which reads too generous here)
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
            print("  Marketing: all metrics zero — lock screen on by default")

        # Campaign-level breakdown (marketing dashboard) — only meaningful
        # when ad spend exists.
        if ad_spend > 0:
            try:
                from .data_schema import CampaignData
                mkt_dash = _get_dashboard_queries(MARKETING_DASHBOARD_ID, api_key)
                mkt_queries = mkt_dash.get("queries", [])
                if not mkt_queries:
                    raise RuntimeError("marketing dashboard returned no queries")
                cq = copy.deepcopy(mkt_queries[0]["query"])
                _ensure_filters(cq)
                cq["filters"].pop("dbt__moxie_medspas_mart.provider_success_manager_name", None)
                _pf_field, _pf = _practice_filter(practice_name, medspa_id)
                cq["filters"][_pf_field] = _pf
                cq["filters"]["dbt__marketing_medspa_performance_daily_mart.series_date"] = {
                    "kind": "TIME_FOR_INTERVAL_DURATION", "type": "date",
                    "ui_type": "PAST",
                    "left_side": start_date, "right_side": duration,
                    "is_negative": False,
                }
                rev_field = ("dbt__marketing_medspa_performance_daily_mart"
                             ".meta_new_clients_completed_appointment_revenue_sum")
                camp_field = "dbt__marketing_medspa_performance_daily_mart.campaign_category"
                if not isinstance(cq.get("fields"), list):
                    cq["fields"] = []
                for extra_f in (camp_field, rev_field):
                    if extra_f not in cq["fields"]:
                        cq["fields"].append(extra_f)
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
                    if not cn:
                        continue
                    # The id filter already scopes rows to this practice; the
                    # name check only guards mixed rows when the id was absent.
                    cm = camp_medspa[ci] if ci < len(camp_medspa) else None
                    if medspa_id is None and cm and cm != practice_name:
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
                print(f"  Campaigns: {len(campaigns)} active "
                      f"({', '.join(c.campaign_name for c in campaigns)})")
            except Exception as e:
                print(f"  Warning: Could not load campaign data: {e}")
    except Exception as e:
        print(f"  Warning: Could not load marketing data: {e}")

    print(f"  Loaded: Net Rev ${data.monthly_net_revenue:,.2f}, "
          f"{data.total_appointments} appts, "
          f"{len(data.staff)} staff, "
          f"{len(data.services)} service categories")

    return data
