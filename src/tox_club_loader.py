"""Omni data loader and CSV parser for Tox Club MBR generation."""

import csv
import io
import re

import calendar
import copy
from .omni_loader import _run_query, _api_get


def _date_filter(start_date: str, duration: str = "1 months") -> dict:
    return {
        "kind": "TIME_FOR_INTERVAL_DURATION",
        "type": "date",
        "ui_type": "PAST",
        "left_side": start_date,
        "right_side": duration,
        "is_negative": False,
    }


def _medspa_filter(name: str) -> dict:
    return {"kind": "EQUALS", "type": "string", "values": [name], "is_negative": False}


def _bool_filter(value: bool = True) -> dict:
    return {"kind": "EQUALS", "type": "boolean", "values": [value], "is_negative": False}


def load_tox_club_stats(medspa_name: str, month: int, year: int, api_key: str) -> dict:
    """Pull Tox Club appointment + revenue data for one medspa for a given month.

    Returns a dict with keys:
      total, new_members, returning_members, prebook_rate (0–1 float), revenue (float)
    Any field that can't be determined stays None.
    """
    import calendar
    _, last_day = calendar.monthrange(year, month)
    start_date = f"{year}-{month:02d}-01"
    # Appointment status filter (completed)
    base_filters = {
        "dbt__moxie_appointments_mart.is_tox_club_appointment": _bool_filter(True),
        "dbt__moxie_medspas_mart.medspa_name": _medspa_filter(medspa_name),
        "dbt__moxie_appointments_mart.start_time": _date_filter(start_date, "1 months"),
    }

    result = {
        "total": 0,
        "new_members": None,
        "returning_members": None,
        "prebook_rate": None,
        "revenue": 0.0,
        "debug": {},
    }

    # ── 1. Total completed Tox Club appointments ──────────────────────────────
    try:
        q = {
            "fields": ["dbt__moxie_appointments_mart.count"],
            "filters": {**base_filters},
        }
        r = _run_query(q, api_key)
        result["debug"]["appt_total_raw"] = r
        counts = r.get("dbt__moxie_appointments_mart.count", []) or []
        result["total"] = int(sum(v for v in counts if v is not None))
    except Exception as e:
        result["debug"]["appt_total_error"] = str(e)

    # ── 2. New vs Returning breakdown ─────────────────────────────────────────
    # Try grouping by tox_club_member_type (custom dimension); fall back to
    # service_category if that fails.
    new_returning_loaded = False
    for type_field in [
        "dbt__moxie_appointments_mart.tox_club_member_type",
        "dbt__moxie_appointments_mart.is_new_tox_club_client",
        "dbt__moxie_visits_mart.service_category",
        "dbt__moxie_service_menu_items_mart.service_category",
    ]:
        try:
            q = {
                "fields": ["dbt__moxie_appointments_mart.count", type_field],
                "filters": {**base_filters},
            }
            r = _run_query(q, api_key)
            result["debug"][f"member_type_{type_field}"] = r
            types = r.get(type_field, [])
            counts_col = r.get("dbt__moxie_appointments_mart.count", [])
            if types and counts_col:
                new_ct = 0
                ret_ct = 0
                for t, c in zip(types, counts_col):
                    if t is None or c is None:
                        continue
                    t_str = str(t).lower()
                    c_int = int(c)
                    if "new" in t_str or t_str in ("true", "1"):
                        new_ct += c_int
                    elif "return" in t_str or "existing" in t_str or t_str in ("false", "0"):
                        ret_ct += c_int
                if new_ct > 0 or ret_ct > 0:
                    result["new_members"] = new_ct
                    result["returning_members"] = ret_ct
                    new_returning_loaded = True
                    break
        except Exception as e:
            result["debug"][f"member_type_error_{type_field}"] = str(e)
            continue

    # Derive total from new+returning if total query failed
    if result["total"] == 0 and new_returning_loaded:
        result["total"] = (result["new_members"] or 0) + (result["returning_members"] or 0)

    # If we have total but not breakdown, set new/returning to unknown
    if not new_returning_loaded and result["total"] > 0:
        result["new_members"] = None
        result["returning_members"] = None

    # ── 3. Pre-booking rate ───────────────────────────────────────────────────
    for pb_field in [
        "dbt__moxie_appointments_mart.has_future_appointment",
        "dbt__moxie_appointments_mart.is_pre_booked",
        "dbt__moxie_appointments_mart.has_next_appointment",
    ]:
        try:
            q = {
                "fields": ["dbt__moxie_appointments_mart.count", pb_field],
                "filters": {**base_filters},
            }
            r = _run_query(q, api_key)
            result["debug"][f"prebook_{pb_field}"] = r
            pb_vals = r.get(pb_field, [])
            ct_vals = r.get("dbt__moxie_appointments_mart.count", [])
            if pb_vals and ct_vals:
                prebooked = 0
                total_for_pb = 0
                for pb, ct in zip(pb_vals, ct_vals):
                    if ct is None:
                        continue
                    total_for_pb += int(ct)
                    pb_s = str(pb).lower() if pb is not None else ""
                    if pb_s in ("true", "1", "yes"):
                        prebooked += int(ct)
                if total_for_pb > 0:
                    result["prebook_rate"] = prebooked / total_for_pb
                break
        except Exception as e:
            result["debug"][f"prebook_error_{pb_field}"] = str(e)
            continue

    # ── 4. Revenue ────────────────────────────────────────────────────────────
    try:
        rev_filters = {
            "dbt__moxie_invoices_mart.is_tox_club_appointment": _bool_filter(True),
            "dbt__moxie_medspas_mart.medspa_name": _medspa_filter(medspa_name),
            "dbt__moxie_invoices_mart.invoice_issued_date": _date_filter(start_date, "1 months"),
        }
        q = {
            "fields": ["dbt__moxie_invoices_mart.total_paid_amount"],
            "filters": rev_filters,
        }
        r = _run_query(q, api_key)
        result["debug"]["revenue_raw"] = r
        for key, vals in r.items():
            if vals and "amount" in key.lower() or "revenue" in key.lower() or "paid" in key.lower():
                total_rev = sum(float(v) for v in vals if v is not None)
                if total_rev > 0:
                    result["revenue"] = total_rev
                    break
    except Exception as e:
        result["debug"]["revenue_error"] = str(e)

    return result


def _month_to_quarter_start(month: int, year: int) -> str:
    """Return the YYYY-MM-DD string for the quarter containing this month."""
    q_start_month = ((month - 1) // 3) * 3 + 1
    return f"{year}-{q_start_month:02d}-01"


def parse_tox_club_revenue_csv(csv_text: str) -> dict:
    """Parse the Medspa Tox Club Revenue CSV exported from Omni.

    Returns a dict keyed by medspa_id (int) → {quarter_date: {paid, credits, pct}, ...}
    Rows with no date are the all-time totals — stored under key "total".
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    result = {}  # {medspa_id: {"name": str, "quarters": {date_str: {...}}}}

    for row in reader:
        raw_name = (row.get("Medspa") or "").strip()
        if not raw_name:
            continue
        # Extract ID from "Name (ID)" pattern
        m = re.search(r"\((\d+)\)\s*$", raw_name)
        if not m:
            continue
        medspa_id = int(m.group(1))
        clean_name = raw_name[:m.start()].strip()

        date_str = (row.get("Visit Date (Local Time)") or "").strip()
        try:
            paid = float(row.get("Paid Amount") or 0)
            credits = float(row.get("Tox Club Credits Used") or 0)
            pct = float(row.get("% Tox Club Coverage") or 0)
        except (ValueError, TypeError):
            continue

        if medspa_id not in result:
            result[medspa_id] = {"name": clean_name, "quarters": {}}

        key = date_str if date_str else "total"
        result[medspa_id]["quarters"][key] = {"paid": paid, "credits": credits, "pct": pct}

    return result


def get_revenue_from_csv(csv_data: dict, medspa_id: int, month: int, year: int) -> dict | None:
    """Look up revenue for a medspa for the quarter containing month/year.

    Returns {paid, credits, pct, quarter} or None if not found.
    """
    if not csv_data or medspa_id not in csv_data:
        return None
    quarters = csv_data[medspa_id].get("quarters", {})
    target = _month_to_quarter_start(month, year)
    if target in quarters:
        row = quarters[target]
        return {**row, "quarter": target}
    # Fall back to the all-time total row
    if "total" in quarters:
        return {**quarters["total"], "quarter": "total"}
    return None


def discover_tox_club_fields(api_key: str) -> dict:
    """Run a broad test query and return the fields that come back.
    Useful for tuning field names when the loader returns zeros.
    """
    try:
        q = {
            "fields": [
                "dbt__moxie_appointments_mart.count",
                "dbt__moxie_appointments_mart.tox_club_member_type",
                "dbt__moxie_appointments_mart.has_future_appointment",
                "dbt__moxie_appointments_mart.appointment_status",
                "dbt__moxie_invoices_mart.total_paid_amount",
            ],
            "filters": {
                "dbt__moxie_appointments_mart.is_tox_club_appointment": _bool_filter(True),
            },
            "limit": 5,
        }
        r = _run_query(q, api_key)
        return {"ok": True, "fields_returned": list(r.keys()), "sample": {k: v[:2] if v else v for k, v in r.items()}}
    except Exception as e:
        return {"ok": False, "error": str(e)}
