"""Load supplies savings — reads pricing from Shannon's dashboard at runtime."""

import json
import re
from datetime import datetime, date
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "web" / "static" / "supplies-savings" / "data"
DASHBOARD_HTML = Path(__file__).parent.parent / "web" / "static" / "supplies-savings" / "app" / "dashboard.html"


def _extract_js_obj(js: str, pattern: str) -> dict:
    """Extract a JS object literal from the dashboard source."""
    m = re.search(pattern, js, re.DOTALL)
    if not m:
        return {}
    raw = m.group(1)
    # JS keys may be unquoted — wrap them in quotes for JSON
    raw = re.sub(r'(\b[A-Za-z_][\w\s/().\-+]*?):', lambda x: f'"{x.group(1).strip()}":', raw)
    # Remove trailing commas before } or ]
    raw = re.sub(r',\s*([}\]])', r'\1', raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _extract_js_array_as_set(js: str, pattern: str) -> set:
    """Extract a JS array of strings as a Python set."""
    m = re.search(pattern, js, re.DOTALL)
    if not m:
        return set()
    raw = m.group(1)
    return set(re.findall(r'"([^"]+)"', raw))


def _load_pricing():
    """Read all pricing tables from Shannon's dashboard.html at runtime."""
    try:
        with open(DASHBOARD_HTML) as f:
            js = f.read()
    except FileNotFoundError:
        print("  Warning: Shannon's dashboard.html not found, using empty pricing")
        return {}

    pricing = {
        "G_ERA1": _extract_js_obj(js, r'const G_ERA1\s*=\s*(\{[^}]+\})'),
        "G_ERA2": _extract_js_obj(js, r'const G_ERA2\s*=\s*(\{[^}]+\})'),
        "AL_BOTOX_MOXIE": _extract_js_obj(js, r'(?:const|var) AL_BOTOX_MOXIE\s*=\s*(\{[^}]+\})'),
        "AL_ERA1": _extract_js_obj(js, r'(?:const|var) AL_ERA1\s*=\s*(\{[^}]+\})'),
        "AL_ERA2": _extract_js_obj(js, r'(?:const|var) AL_ERA2\s*=\s*(\{[^}]+\})'),
        "AL_ERA3": _extract_js_obj(js, r'(?:const|var) AL_ERA3\s*=\s*(\{[^}]+\})'),
        "MZ_XEO": _extract_js_obj(js, r'(?:const|var) MZ_XEO\s*=\s*(\{[^}]+\})'),
        "MZ_STD": _extract_js_obj(js, r'(?:const|var) MZ_STD\s*=\s*(\{[^}]+\})'),
        "RV_LP": _extract_js_obj(js, r'(?:const|var) RV_LP\s*=\s*(\{[^}]+\})'),
    }
    # MZ_NEO is a Set in JS — extract as Python set
    pricing["MZ_NEO"] = _extract_js_array_as_set(js, r'(?:const|var) MZ_NEO\s*=\s*new Set\(\[([^\]]+)\]\)')
    # SKM_BRANDS is an array
    m = re.search(r'(?:const|var) SKM_BRANDS\s*=\s*\[([^\]]+)\]', js)
    if m:
        pricing["SKM_BRANDS"] = re.findall(r"'([^']+)'", m.group(1))
    else:
        pricing["SKM_BRANDS"] = []

    # Extract era dates
    m = re.search(r"var G_E2\s*=\s*new Date\('(\d{4}-\d{2}-\d{2})'\)", js)
    pricing["G_E2_DATE"] = date.fromisoformat(m.group(1)) if m else date(2024, 4, 1)
    m = re.search(r"var AL_E2\s*=\s*new Date\('(\d{4}-\d{2}-\d{2})'\)", js)
    pricing["AL_E2_DATE"] = date.fromisoformat(m.group(1)) if m else date(2024, 3, 1)
    m = re.search(r"var AL_E3\s*=\s*new Date\('(\d{4}-\d{2}-\d{2})'\)", js)
    pricing["AL_E3_DATE"] = date.fromisoformat(m.group(1)) if m else date(2024, 8, 27)
    m = re.search(r"var MZ_BOGO\s*=\s*new Date\('(\d{4}-\d{2}-\d{2})'\)", js)
    pricing["MZ_BOGO_DATE"] = date.fromisoformat(m.group(1)) if m else date(2025, 1, 1)

    return pricing


# Load pricing once at import time (re-reads from disk on each server restart)
_PRICING = None

def _get_pricing():
    global _PRICING
    if _PRICING is None:
        _PRICING = _load_pricing()
    return _PRICING


def _load_json(filename):
    path = DATA_DIR / filename
    if not path.exists():
        return []
    with open(path) as f:
        return json.load(f)


def _pd(s):
    """Parse date — matches Shannon's pd() function."""
    if not s:
        return None
    t = str(s).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', t):
        try:
            return datetime.strptime(t, "%Y-%m-%d").date()
        except ValueError:
            return None
    # Try common date formats
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%dT%H:%M:%S", "%d-%b-%y", "%b %d, %Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except ValueError:
        return None


def _pm(v):
    """Parse money — matches Shannon's pm() function."""
    if v is None:
        return 0.0
    return float(re.sub(r'[$,\s]', '', str(v)) or 0)


def _acc(periods, d, sp, sv, bounds):
    """Accumulate into period buckets — matches Shannon's accP()."""
    periods["all"]["sp"] += sp
    periods["all"]["sv"] += sv
    if d:
        if bounds["mo_st"] <= d <= bounds["end"]:
            periods["mo"]["sp"] += sp
            periods["mo"]["sv"] += sv
        if bounds["m3_st"] <= d <= bounds["end"]:
            periods["m3"]["sp"] += sp
            periods["m3"]["sv"] += sv
        if bounds["ytd_st"] <= d <= bounds["end"]:
            periods["ytd"]["sp"] += sp
            periods["ytd"]["sv"] += sv


def _mk_periods():
    return {"mo": {"sp": 0, "sv": 0}, "m3": {"sp": 0, "sv": 0},
            "ytd": {"sp": 0, "sv": 0}, "all": {"sp": 0, "sv": 0}}


def _calc_galderma(rows, mk_id, moxie_id, bounds):
    """Exact port of calcGalderma()."""
    P = _get_pricing()
    G_ERA1 = P.get("G_ERA1", {})
    G_ERA2 = P.get("G_ERA2", {})
    G_E2_DATE = P.get("G_E2_DATE", date(2024, 4, 1))

    clean = mk_id.lstrip("0") if mk_id else None
    matched = [r for r in rows if
               (moxie_id and r.get("_moxie_id") == moxie_id) or
               (clean and (str(r.get("SHIP TO", "")).lstrip("0") == clean))]
    if not matched:
        return None
    p = _mk_periods()
    for r in matched:
        desc = str(r.get("DESCRIPTION") or r.get("Description") or "").upper()
        if "SHIPPING" in desc:
            continue
        amt = _pm(r.get("EXTENDED AMOUNT") or r.get("Ext Amount") or r.get("extended_amount"))
        qty = _pm(r.get("QTY") or r.get("Qty") or r.get("QUANTITY") or r.get("quantity"))
        d = _pd(r.get("ORDER DATE") or r.get("ORDER_DATE") or r.get("Order Date"))
        if not d or (amt == 0 and qty > 0):
            continue
        sp, sv = 0, 0
        if d < G_E2_DATE:
            sp = amt * 1.029
            k = next((k for k in G_ERA1 if k in desc), None)
            sv = sp * G_ERA1[k] if k else 0
        else:
            k2 = next((k for k in G_ERA2 if k in desc), None)
            if k2:
                lr = G_ERA2[k2]
                sp = qty * lr[1]
                sv = qty * (lr[0] - lr[1])
        _acc(p, d, sp, sv, bounds)
    return p


def _calc_allergan(rows, al_id, bounds):
    """Exact port of calcAllergan()."""
    P = _get_pricing()
    AL_BOTOX_MOXIE = P.get("AL_BOTOX_MOXIE", {})
    AL_ERA1 = P.get("AL_ERA1", {})
    AL_ERA2 = P.get("AL_ERA2", {})
    AL_ERA3 = P.get("AL_ERA3", {})
    AL_E2_DATE = P.get("AL_E2_DATE", date(2024, 3, 1))
    AL_E3_DATE = P.get("AL_E3_DATE", date(2024, 8, 27))
    SKM_BRANDS = P.get("SKM_BRANDS", [])

    if not al_id:
        return None
    al_clean = al_id.strip()
    matched = [r for r in rows if
               str(r.get("Sold-to #", "")).replace(".0", "").strip() == al_clean]
    if not matched:
        return None
    p = _mk_periods()
    seen = set()
    for r in matched:
        key = f"{r.get('Ship-to #', '')}|{r.get('Invoice or Credit Memo', '')}|{r.get('Description', r.get('DESCRIPTION', ''))}"
        if key in seen:
            continue
        seen.add(key)
        amt = _pm(r.get("Amount") or r.get("AMOUNT"))
        qty = _pm(r.get("Quantity") or r.get("QTY") or r.get("Qty"))
        desc = str(r.get("Description") or r.get("DESCRIPTION") or "").upper().strip()
        d = _pd(r.get("DATE") or r.get("Date"))
        if not d:
            continue
        if amt == 0 and qty > 0:
            continue
        if re.search(r'REBATE|BUY [0-9]+ GET|GET [0-9]+ FREE|FREE$|BUY.IN|BUY-IN', desc):
            continue
        prices = AL_ERA1 if d < AL_E2_DATE else AL_ERA2 if d < AL_E3_DATE else AL_ERA3
        sp, sv = 0, 0
        if "BOTOX" in desc:
            u = "200 UNIT" if "200 UNIT" in desc else "50 UNIT" if "50 UNIT" in desc else "100 UNIT"
            sp = abs(amt) if d < AL_E2_DATE else qty * AL_BOTOX_MOXIE.get(u, 656)
            sv = 0
        else:
            filler_key = next((k for k in prices if k in desc), None)
            if filler_key:
                if d < AL_E2_DATE:
                    sp = abs(amt)
                    sv = qty * (prices[filler_key][0] - prices[filler_key][1])
                else:
                    sp = qty * prices[filler_key][1]
                    sv = qty * (prices[filler_key][0] - prices[filler_key][1])
            elif any(b in desc for b in SKM_BRANDS):
                sp = abs(amt)
                if d >= AL_E2_DATE and sp > 0:
                    sv = sp * (1/0.88 - 1)
            else:
                sp = abs(amt)
        _acc(p, d, sp, sv, bounds)
    return p


def _calc_evolus(rows, moxie_id, name_map, bounds):
    """Exact port of calcEvolus()."""
    emap = name_map.get("evolus", {})
    ev_name = next((n for n, mid in emap.items() if mid == moxie_id), None)
    matched = [r for r in rows if
               r.get("_moxie_id") == moxie_id or
               (ev_name and str(r.get("Facility", "")).strip() == ev_name)]
    if not matched:
        return None
    p = _mk_periods()
    for r in matched:
        jqty = _pm(r.get("Jeaveau Vials") or r.get("Jeuveau Vials") or r.get("jeuveau_vials"))
        eqty = _pm(r.get("Evolysse Vials") or r.get("evolysse_vials"))
        d = _pd(r.get("Date") or r.get("date"))
        sp, sv = 0, 0
        if jqty > 0:
            sp += jqty * 390
            sv += jqty * 220
        if eqty > 0:
            sp += eqty * 160
            sv += eqty * 165
        _acc(p, d, sp, sv, bounds)
    return p


def _calc_revance(rows, moxie_id, bounds):
    """Exact port of calcRevance()."""
    P = _get_pricing()
    RV_LP = P.get("RV_LP", {})

    matched = [r for r in rows if
               r.get("_moxie_id") == moxie_id or
               str(r.get("_moxie_id", "")) == str(moxie_id)]
    if not matched:
        return None
    p = _mk_periods()
    for r in matched:
        sales = _pm(r.get("Sales $"))
        qty = _pm(r.get("Boxes / Vials"))
        prod = str(r.get("Product", "")).strip()
        d = _pd(r.get("Date"))
        if not d or sales <= 0 or qty <= 0:
            continue
        lp = RV_LP.get(prod)
        sp = sales
        sv = max((qty * lp) - sales, 0) if lp else 0
        _acc(p, d, sp, sv, bounds)
    return p


def _calc_merz(rows, moxie_id, name_map, bounds):
    """Exact port of calcMerz()."""
    P = _get_pricing()
    MZ_XEO = P.get("MZ_XEO", {})
    MZ_STD = P.get("MZ_STD", {})
    MZ_NEO = P.get("MZ_NEO", set())
    MZ_BOGO_DATE = P.get("MZ_BOGO_DATE", date(2025, 1, 1))

    mmap = name_map.get("merz", {})
    mz_name = next((n for n, mid in mmap.items() if mid == moxie_id), None)
    matched = [r for r in rows if
               r.get("_moxie_id") == moxie_id or
               (mz_name and str(r.get("Ship_To_Name", "")).strip() == mz_name)]
    if not matched:
        return None
    p = _mk_periods()
    for r in matched:
        prod = str(r.get("MaterialDescription", "")).strip()
        qty = _pm(r.get("Billing_qty_in_SKU"))
        gross = _pm(r.get("Gross_Value"))
        d = _pd(r.get("Invoice_Date"))
        if not d or qty == 0:
            continue
        sp, sv = 0, 0
        if prod in MZ_XEO:
            lp = MZ_XEO[prod]
            rate = 1.0 if d >= MZ_BOGO_DATE else 0.8
            sp = qty * lp
            sv = qty * lp * rate
        elif prod in MZ_STD:
            lr = MZ_STD[prod]
            sp = qty * lr[1]
            sv = qty * (lr[0] - lr[1])
        elif prod in MZ_NEO:
            sp = gross
            sv = gross * 0.2
        else:
            sp = gross
        _acc(p, d, sp, sv, bounds)
    return p


def load_savings_for_practice(practice_name: str, month: int, year: int) -> dict:
    """Compute spend and savings matching Shannon's dashboard exactly."""
    medspas = _load_json("medspas.json")
    medspa = next((m for m in medspas if m.get("n", "").lower() == practice_name.lower()), None)
    if not medspa:
        return {}

    moxie_id = medspa.get("id")
    name_map = _load_json("name_map.json") or {}

    # Date boundaries — matching Shannon's bounds()
    sel_end = date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)
    mo_st = date(year, month, 1)
    m3_month = month - 2
    m3_year = year
    if m3_month < 1:
        m3_month += 12
        m3_year -= 1
    m3_st = date(m3_year, m3_month, 1)
    ytd_st = date(year, 1, 1)
    bounds = {"end": sel_end, "mo_st": mo_st, "m3_st": m3_st, "ytd_st": ytd_st}

    # Load transaction files
    vendors = {}
    calcs = {
        "Galderma": lambda: _calc_galderma(
            _load_json("transactions_galderma.json"), medspa.get("mk", ""), moxie_id, bounds),
        "Allergan": lambda: _calc_allergan(
            _load_json("transactions_allergan.json"), medspa.get("al", ""), bounds),
        "Evolus": lambda: _calc_evolus(
            _load_json("transactions_evolus.json"), moxie_id, name_map, bounds),
        "Revance": lambda: _calc_revance(
            _load_json("transactions_revance.json"), moxie_id, bounds),
        "Merz": lambda: _calc_merz(
            _load_json("transactions_merz.json"), moxie_id, name_map, bounds),
    }

    total = _mk_periods()
    by_vendor_3mo = []

    for vendor_name, calc_fn in calcs.items():
        try:
            result = calc_fn()
        except Exception as e:
            print(f"    Warning: {vendor_name} calc failed: {e}")
            continue
        if not result:
            continue
        for period in ["mo", "m3", "ytd", "all"]:
            total[period]["sp"] += result[period]["sp"]
            total[period]["sv"] += result[period]["sv"]
        if result["m3"]["sp"] > 0 or result["m3"]["sv"] > 0:
            by_vendor_3mo.append({
                "vendor": vendor_name,
                "spend": round(result["m3"]["sp"], 2),
                "savings": round(result["m3"]["sv"], 2),
            })

    by_vendor_3mo.sort(key=lambda v: v["spend"], reverse=True)

    return {
        "month": {"spend": round(total["mo"]["sp"], 2), "savings": round(total["mo"]["sv"], 2)},
        "m3": {"spend": round(total["m3"]["sp"], 2), "savings": round(total["m3"]["sv"], 2)},
        "ytd": {"spend": round(total["ytd"]["sp"], 2), "savings": round(total["ytd"]["sv"], 2)},
        "all": {"spend": round(total["all"]["sp"], 2), "savings": round(total["all"]["sv"], 2)},
        "by_vendor_3mo": by_vendor_3mo,
    }
