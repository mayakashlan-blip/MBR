# Evolus — Spec (LOCKED ✅)

## Data Source
- Sheet: `Evolus Transactions` in Excel workbook
- Columns: Facility (0), Order# (1), Date (2), Jeuveau Vials (3), Total (4), Evolysse Vials (5), Rebates $30/vial (6), Evolysse Total (7)
- Date field: `Date` (col 2)
- Medspa join: `Facility` (col 0) = Evolus name from Naming Convention CSV col 12
  - NEVER hardcode — always look up from Naming Convention table at runtime
- Deduplication key: `Facility` + `Order Number` + `Date`
- Free/promo rule: if amount = $0 and qty > 0 → skip row entirely

## Formula

### Jeuveau (col 3 = qty)
- List price: $610/vial
- Moxie price: $390/vial upfront
- Spend = `Qty × $390`
- Savings = `Qty × ($610 − $390)` = `Qty × $220`
- Note: `Total` column (col 4) reflects actual charged amounts which vary — do NOT use for spend. Always use `Qty × $390`.

### Evolysse Smooth & Evolysse Form (col 5 = combined qty, no variant distinction in data)
- List price: $325/syringe
- Moxie price: $160/syringe
- Spend = `Evolysse Total` (col 7) — consistently $150/unit in data
- Savings = `Qty × ($325 − $160)` = `Qty × $165`

### Rebates (col 6)
- Value = `Jeuveau Qty × $30` quarterly rebate — already computed in the sheet
- Store as direct dollar amount per row
- Tracked separately from direct savings
- NOT included in monthly/3M/YTD hero card totals
- Included in all-time total saved with "incl. $X in quarterly rebates" note

## Pricing note
Old Excel used Moxie price of $359.90 (which embedded the $30 rebate into the price).
Correct price = $390 upfront, $30 rebate tracked separately.
Our numbers will differ from old Excel for Jeuveau spend/savings — this is intentional and correct.

## Validation ✅
Citrus Aesthetics (Evolus name: "Citrus Aesthetics"), all-time through Feb 28 2026:
- Jeuveau qty: 40 vials
- Spend: 40 × $390 = $15,600
- Savings: 40 × $220 = $8,800
- Rebates: $1,200 ✅ (exact match to rebates CSV)
