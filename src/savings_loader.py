"""Load supplies savings data from Shannon's transaction files for MBR reports."""

import json
import os
from datetime import datetime, date
from pathlib import Path


DATA_DIR = Path(__file__).parent.parent / "web" / "static" / "supplies-savings" / "data"


def _load_json(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _parse_date(s):
    """Parse various date formats found in vendor files."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S", "%d-%b-%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_num(v):
    if v is None:
        return 0
    return float(str(v).replace("$", "").replace(",", "").strip() or 0)


def _find_medspa(medspas, practice_name, moxie_id=None):
    """Find a medspa in the medspas.json list by name or ID."""
    for m in medspas:
        if moxie_id and m.get("id") == moxie_id:
            return m
        if m.get("n", "").lower() == practice_name.lower():
            return m
    return None


def load_savings_for_practice(practice_name: str, month: int, year: int) -> dict:
    """Compute spend and savings for a practice across all time periods.

    Returns dict with keys: month, m3, ytd, all, by_vendor_3mo
    Each period has {spend, savings}.
    """
    medspas = _load_json("medspas.json")
    medspa = _find_medspa(medspas, practice_name)
    if not medspa:
        return {}

    moxie_id = medspa.get("id")
    mk_id = medspa.get("mk", "")  # galderma
    al_id = medspa.get("al", "")  # allergan
    jv_id = medspa.get("jv", "")  # evolus (Jeaveau vendor ID)
    mz_id = medspa.get("mz", "")  # merz
    rv_id = medspa.get("rv", "")  # revance

    # Define date boundaries
    sel_end = date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)
    sel_start = date(year, month, 1)
    m3_start = date(year, month - 2, 1) if month >= 3 else date(year - 1, month + 10, 1)
    ytd_start = date(year, 1, 1)

    result = {
        "month": {"spend": 0, "savings": 0},
        "m3": {"spend": 0, "savings": 0},
        "ytd": {"spend": 0, "savings": 0},
        "all": {"spend": 0, "savings": 0},
        "by_vendor_3mo": [],
    }

    vendors = {
        "Galderma": {"file": "transactions_galderma.json", "id_field": "SHIP TO", "id_val": mk_id},
        "Allergan": {"file": "transactions_allergan.json", "id_field": "Sold-to #", "id_val": al_id},
        "Evolus": {"file": "transactions_evolus.json", "id_field": "_moxie_id", "id_val": moxie_id},
        "Merz": {"file": "transactions_merz.json", "id_field": "Bill_To_Party", "id_val": mz_id},
        "Revance": {"file": "transactions_revance.json", "id_field": "Moxie Medspa ID", "id_val": moxie_id},
    }

    for vendor_name, cfg in vendors.items():
        if not cfg["id_val"]:
            continue

        try:
            rows = _load_json(cfg["file"])
        except Exception:
            continue

        id_val = str(cfg["id_val"]).strip()
        vendor_spend_3mo = 0
        vendor_savings_3mo = 0

        for row in rows:
            # Match by ID
            row_id = str(row.get(cfg["id_field"], "")).strip()
            if vendor_name == "Evolus":
                # Evolus uses _moxie_id (int)
                row_id = str(row.get("_moxie_id", "")).strip()
                if row_id != id_val:
                    continue
            elif vendor_name == "Revance":
                row_id = str(row.get("Moxie Medspa ID", "")).strip()
                if row_id != id_val:
                    continue
            else:
                # Clean leading zeros for comparison
                if row_id.lstrip("0") != id_val.lstrip("0"):
                    continue

            # Get amount — vendor-specific field names
            if vendor_name == "Galderma":
                amt = _parse_num(row.get("EXTENDED AMOUNT") or row.get("Ext Amount"))
                d = _parse_date(row.get("ORDER DATE") or row.get("Order Date"))
                desc = str(row.get("DESCRIPTION", "")).upper()
                if "SHIPPING" in desc:
                    continue
            elif vendor_name == "Allergan":
                amt = _parse_num(row.get("Amount") or row.get("Ext Amount") or row.get("Total Amount"))
                d = _parse_date(row.get("Ship Date") or row.get("Invoice Date"))
            elif vendor_name == "Evolus":
                # Evolus: use vial count * price
                vials = _parse_num(row.get("Jeaveau Vials", 0)) + _parse_num(row.get("Evolysse Vials", 0))
                amt = vials * 400  # approximate price
                d = _parse_date(row.get("Date"))
            elif vendor_name == "Merz":
                amt = _parse_num(row.get("NetValue") or row.get("Net Amount"))
                d = _parse_date(row.get("FirstDayOfMonth") or row.get("BillingDate"))
            elif vendor_name == "Revance":
                amt = _parse_num(row.get("Revenue") or row.get("Net Revenue"))
                d = _parse_date(row.get("Date") or row.get("Ship Date"))
            else:
                continue

            if not d or amt == 0:
                continue

            # Approximate savings as 15% of spend (simplified — actual savings vary by product)
            # This is a rough estimate; the full dashboard uses product-level pricing
            spend = abs(amt)
            savings = spend * 0.15

            # Accumulate by period
            result["all"]["spend"] += spend
            result["all"]["savings"] += savings

            if sel_start <= d < sel_end:
                result["month"]["spend"] += spend
                result["month"]["savings"] += savings

            if m3_start <= d < sel_end:
                result["m3"]["spend"] += spend
                result["m3"]["savings"] += savings
                vendor_spend_3mo += spend
                vendor_savings_3mo += savings

            if ytd_start <= d < sel_end:
                result["ytd"]["spend"] += spend
                result["ytd"]["savings"] += savings

        if vendor_spend_3mo > 0:
            result["by_vendor_3mo"].append({
                "vendor": vendor_name,
                "spend": round(vendor_spend_3mo, 2),
                "savings": round(vendor_savings_3mo, 2),
            })

    # Sort vendors by spend descending
    result["by_vendor_3mo"].sort(key=lambda v: v["spend"], reverse=True)

    # Round all values
    for period in ["month", "m3", "ytd", "all"]:
        result[period]["spend"] = round(result[period]["spend"], 2)
        result[period]["savings"] = round(result[period]["savings"], 2)

    return result
