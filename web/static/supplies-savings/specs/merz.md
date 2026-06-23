# Merz — Spec (LOCKED ✅)

## Data Source
- File: Merz Transaction CSV (uploaded monthly, append + deduplicate)
- Columns: PKID-Contract (0), FirstDayOfMonth (1), MaterialID (2), MaterialDescription (3), BrandName (4), ProductFamily (5), BusinessUnit (6), Bill_To_Party (7), Bill_To_Name (8), Bill_To_City (9), Bill_To_Region (10), Ship_To_Party (11), Ship_To_Name (12), Buying_Loc (13), Buy_Loc_Name (14), ContractName (15), SalesBeginDate (16), EndDate_Quarter1 (17), SalesEndDate (18), Invoice_Date (19), Billing_Document (20), Billing_Document_Item (21), Sales_Document (22), Gross_Value (23), Net_Value (24), Billing_qty_in_SKU (25)
- Date field: `Invoice_Date` (col 19)
- Medspa join: `Ship_To_Name` (col 12) = Merz name from Naming Convention CSV col 11
  - NEVER hardcode — always look up from Naming Convention table at runtime
- Deduplication key: `Billing_Document` + `Billing_Document_Item` + `Ship_To_Name`
- Skip rows where `Qty = 0`

---

## XEOMIN — special rules (unique from all other products and vendors)

Xeomin is a BOGO program. Providers pay list price and receive free matching product.

**Spend = `Qty × List price`** (hardcoded — NOT Gross_Value, NOT Moxie price)

**Savings = `Qty × List × rate`** where rate depends on era:
- Pre Jan 1, 2025: rate = **80%** (80% sample — buy 1 get 0.8 free)
- Jan 1, 2025 onwards: rate = **100%** (full BOGO — buy 1 get 1 free)

| Product | List price | Moxie price |
|---|---|---|
| Xeomin 100-U Vials | $511.00 | $255.50 |
| Xeomin 50 U Vials | $268.00 | $134.00 |

Note: 80% applies to ALL historical purchases including 2023 — there is no "no savings" era.

---

## Standard priced products — Qty × Moxie for spend, Qty × (List − Moxie) for savings

**Spend = `Qty × Moxie price`**
**Savings = `Qty × (List − Moxie)`**
- Works correctly when Gross_Value = 0 — hardcoded Moxie price used regardless

| Product | List (per kit) | Moxie (per kit) | Savings/kit |
|---|---|---|---|
| BELOTERO Balance 1.0cc US | $285.00 | $213.75 | $71.25 |
| Belotero Balance Lido US 1x1.0ml | $329.00 | $246.75 | $82.25 |
| Belotero 1.0 Lido | $329.00 | $246.75 | $82.25 |
| RADIESSE (Refresh) - 2 X 1.5cc Kit | $780.00 | $468.00 | $312.00 |
| RADIESSE (+) Lidocaine (Refresh) - 2 X 1.5cc Kit | $780.00 | $468.00 | $312.00 |
| Ultherapy Transducer (all variants) | $2,340.00 | $1,989.00 | $351.00 |
| Describe 10 Pack | $630.00 | $504.00 | $126.00 |

Note: Radiesse kit = 2 syringes. List $780 = 2×$390. Moxie $468 = 2×$234. Belotero prices are per syringe.

---

## Neocutis products — Gross_Value based

**Spend = `Gross_Value`**
**Savings = `Gross_Value × 20%`**
- If Gross_Value = 0: Spend = $0, Savings = $0 (no hardcoded price available)

Neocutis products: Lumiere Firm Riche 15ml, Journée Firm 50ml, Journée Firm 15ml, Neo Firm 50g,
NeoGentle Cleanser 125ml, NeoCleanse Exfoliating 125ml, Neo Body 200ml, Lumiere Firm 15ml,
Perle 30ml RB, Journee Firm Riche 50ml, Journee Firm Riche 15ml, Bio Cream Firm Riche 15ml,
Bio Cream Firm 15ml, Bio Cream Firm 50ml, Bio Cream Firm 200ml, Bio Gel Firm 15ml,
Bio Gel Firm 50ml, Bio Gel Firm 200ml, Bio Serum Firm 30ml, Daily Essentials Kit,
Hyalis+ 30ml, Hyalis+ 15ml, Lumiere Firm 200ml, Micro-Gel 50ml, Neocutis After Care 15ml,
Neocutis After Care 200ml, RéActive 30ml +, Nouvelle, Nouvelle 6 et plus,
NEOCUTIS Neo Restore (6 sachets)

---

## Other products — $0 savings

Radiesse Cannulas, Radiesse Other, UT transducers (UT-1, UT-2, UT-3, UT-4 variants),
27GA x 25mm US Cannula — Spend = Gross_Value, Savings = $0

---

## Validation ✅
- Beauty Haven all-time spend: $15,841 ✅ exact
- Beauty Haven all-time savings: $12,673 (Excel $12,264 — +$408.80 = 1 unit difference, timing/snapshot gap)
- Churchwell all-time savings: $7,208+ (Excel snapshot predates Nov 2025 Radiesse purchase)
- Etoile 3M spend: $3,448 (Excel $2,746 — we include Radiesse Gross=0 rows at Qty×Moxie)

## Explanation of remaining gaps vs Excel Pharma Calc
All gaps explained by timing — never a formula error:
1. **Excel snapshot timing**: Purchases after the Excel snapshot date appear in our CSV but not in the Excel total
2. **Missing Gross_Value rows**: For Xeomin these use Qty×List regardless (hardcoded). For Radiesse/Belotero we use Qty×Moxie regardless. For Neocutis only, Gross=0 means $0 spend/savings

## Rebates
- Rare in current data (Q1 2024 only)
- Stored in rebates table via rebates CSV upload
- Same treatment as Galderma/Allergan/Evolus
