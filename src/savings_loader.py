"""Load supplies savings — exact port of Shannon's dashboard calculations."""

import json
import re
from datetime import datetime, date
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "web" / "static" / "supplies-savings" / "data"

# ── PRICING TABLES (exact copy from dashboard.html) ──────────────────────────

G_ERA1 = {"DYSPORT":0.2787,"RESTYLANE L":0.5931,"RESTYLANE 1ML":0.5931,"RESTYLANE SILK":0.6755,"RESTYLANE LYFT":0.5931,"RESTYLANE L LYFT":0.5931,"RESTYLANE REFYNE":0.6196,"RESTYLANE DEFYNE":0.5931,"RESTYLANE KYSSE":0.5425,"RESTYLANE CONTOUR":0.5931,"RESTYLANE EYELIGHT":0.5931,"SCULPTRA":0.9055}
G_ERA2 = {"DYSPORT":[622,466.5],"RESTYLANE L":[344,209.84],"RESTYLANE 1ML":[344,209.84],"RESTYLANE SILK":[351,200.07],"RESTYLANE LYFT":[366,219.6],"RESTYLANE L LYFT":[366,219.6],"RESTYLANE REFYNE":[407,240.13],"RESTYLANE DEFYNE":[407,244.2],"RESTYLANE KYSSE":[425,263.5],"RESTYLANE CONTOUR":[425,255],"RESTYLANE EYELIGHT":[255,153],"SCULPTRA":[1040,509.6]}
G_E2_DATE = date(2024, 4, 1)

AL_BOTOX_MOXIE = {"100 UNIT":656,"50 UNIT":362,"200 UNIT":1312}
AL_ERA1 = {"ULTRA PLUS XC 1 ML":[755,551.42],"ULTRA XC 0.55 ML":[621,453.46],"ULTRA XC 1 ML":[755,551.42],"ULTRA PLUS XC 0.55 ML":[621,453.46],"VOLBELLA XC 0.55 ML":[420,356.70],"VOLBELLA XC 1 ML":[770,654.24],"VOLLURE XC 1 ML":[770,654.24],"VOLUMA XC 1 ML":[853,724.71],"VOLUX XC 2 X 1ML":[880,748.20],"SKINVIVE":[380,368.60],"25G CANNULA/23G NEEDLE KIT 10X10":[74,62.64],"25G CANNULA/23G NEEDLE KIT 4X4":[35,29.58]}
AL_ERA2 = {"ULTRA PLUS XC 1 ML":[781,523.50],"ULTRA XC 0.55 ML":[643,430.50],"ULTRA XC 1 ML":[781,523.50],"ULTRA PLUS XC 0.55 ML":[643,430.50],"VOLBELLA XC 0.55 ML":[431,332.10],"VOLBELLA XC 1 ML":[791,609.12],"VOLLURE XC 1 ML":[791,609.12],"VOLUMA XC 1 ML":[876,674.73],"VOLUX XC 2 X 1ML":[905,696.60],"SKINVIVE":[380,368.60],"25G CANNULA/23G NEEDLE KIT 10X10":[76,58.32],"25G CANNULA/23G NEEDLE KIT 4X4":[36,27.54]}
AL_ERA3 = {"ULTRA PLUS XC 1 ML":[698,418.80],"ULTRA XC 0.55 ML":[574,344.40],"ULTRA XC 1 ML":[698,418.80],"ULTRA PLUS XC 0.55 ML":[574,344.40],"VOLBELLA XC 0.55 ML":[410,266.50],"VOLBELLA XC 1 ML":[752,488.80],"VOLLURE XC 1 ML":[752,488.80],"VOLUMA XC 1 ML":[833,541.45],"VOLUX XC 2 X 1ML":[860,559.00],"SKINVIVE":[380,368.60],"25G CANNULA/23G NEEDLE KIT 10X10":[72,46.80],"25G CANNULA/23G NEEDLE KIT 4X4":[34,22.10],"27G CANNULA / 25G NEEDLE KIT 10X10":[72,46.80],"27G CANNULA / 25G NEEDLE KIT 4X4":[34,22.10],"KYBELLA":[1200,1080],"LATISSE 5":[131,114.99],"LATISSE 3":[108,94.99]}
AL_E2_DATE = date(2024, 3, 1)
AL_E3_DATE = date(2024, 8, 27)
SKM_BRANDS = ['SKINMEDICA','LATISSE','KYBELLA','DIAMONDGLOW','TNS','HA5','AHA/BHA','ESD ','LUMIVIVE','RETINOL','NECK CORRECT','INSTANT BRIGHT','EVEN & CORRECT','ULTRA SHEER','TOTAL DEFENSE','VITAMIN C+E','PORE PURIFYING','FACIAL CLEANSER','REJUVENIZE','VITALIZE','ILLUMINIZE','CALMING MASQUE','PURIFYING','SCAR RECOVERY','RESTORATIVE']

MZ_XEO = {"Xeomin 100-U Vials":511,"Xeomin 50 U Vials":268}
MZ_STD = {"BELOTERO Balance 1.0cc US":[285,213.75],"Belotero Balance Lido US 1x1.0ml":[329,246.75],"Belotero 1.0 Lido":[329,246.75],"RADIESSE (Refresh) - 2 X 1.5cc Kit":[780,468],"RADIESSE (+) Lidocaine (Refresh) - 2 X 1.5cc Kit":[780,468]}
MZ_NEO = {"Lumiere Firm Riche 15ml","Journee Firm Riche 50ml","Journee Firm Riche 15ml","Journee Firm 50ml","Journee Firm 15ml","Neo Firm 50g","NeoGentle Cleanser 125ml","NeoCleanse Exfoliating 125ml","Neo Body 200ml","Lumiere Firm 15ml","Perle 30ml RB","Bio Cream Firm Riche 15ml","Bio Cream Firm 15ml","Bio Cream Firm 50ml","Bio Cream Firm 200ml","Bio Gel Firm 15ml","Bio Gel Firm 50ml","Bio Gel Firm 200ml","Bio Serum Firm 30ml","Daily Essentials Kit","Hyalis+ 30ml","Hyalis+ 15ml","Lumiere Firm 200ml","Micro-Gel 50ml","Neocutis After Care 15ml","Neocutis After Care 200ml","NEOCUTIS Neo Restore (6 sachets)"}
MZ_BOGO_DATE = date(2025, 1, 1)

RV_LP = {"RHA2":600, "RHA3":600, "RHA4":600, "Redensity":600, "Daxxify":420}


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
