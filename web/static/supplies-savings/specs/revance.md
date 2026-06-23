# Revance — Spec (LOCKED ✅)

## Data Source
- Sheet: `Revance Transactions` in Excel workbook
- Columns: Year (0), Month (1), Date (2), Zone (3), Region (4), Territory (5), SalesRepName (6), Parent Name (7), Account Name (8), Medspa Name (9), Physician Name (10), City (11), State (12), Zip (13), Product (14), Sales$ (15), Boxes/Vials (16), Notes (17)
- Date field: `Date` (col 2)
- Medspa join: `Medspa Name` (col 9) = Revance name from Naming Convention CSV col 6
  - NEVER hardcode — always look up from Naming Convention table at runtime
- Deduplication key: `Medspa Name` + `Date` + `Product` + `Sales$`

## Formula

### Spend
`Spend = Sales$` for ALL products.
- Includes rows where Boxes/Vials = 0 (monthly billing rows) — Sales$ is always correct
- Do NOT use Boxes/Vials qty for spend calculation

### Savings
`Savings = Sales$ × rate` for RHA2, RHA3, RHA4 ONLY.

$0 savings for all other products:
- Daxxify, Redensity, BioJuve
- SkinPen, SkinPen Elite, SkinPen Treatment Kit, SkinPen Elite Treatment Kit
- ProGen, EVO Pen, Other

### Savings Rate
Rate = `(List − Moxie) / List` stored per medspa in the database.
NEVER hardcode — always look up from rates table at runtime.

| Rate | Applies to |
|---|---|
| 35% | All medspas (default) — (300−195)/300 = 35% |
| 22% | Allure Med Spa, Lifted Aesthetics Firm, The Beautox Lounge, The Method Aesthetics |

The 22% override is contractual for those 4 medspas — does not derive from tier structure, does not change with volume. Stored as a per-medspa override in a rates config table (Naming Convention CSV or separate upload).

### Why Sales$ × rate works for all rows
- qty > 0 rows: Sales$ = Qty × Moxie tier price → correct savings
- qty = 0 rows: Sales$ = monthly billing amount → correct savings
- Post Aug 1 2025 pricing (list=$600, moxie=$390 tier 5-34): (600−390)/600 = 35% ✅ same rate
- No date-based era switching needed — formula is self-consistent across all pricing periods

## Important Notes
- Account Name (col 8) can vary — ALWAYS match on Medspa Name (col 9) not Account Name
- Daxxify: list=$420, moxie=$295–$275 tiered — $0 savings per spec
- BioJuve, SkinPen etc: count in spend, $0 savings

## Validation ✅
Citrus Aesthetics (Revance name: "Citrus Aesthetics"), all-time through Feb 28 2026:
- RHA2 Sales$: $9,110 × 35% = $3,188.50 ✅
- RHA3 Sales$: $13,150 × 35% = $4,602.50 ✅
- RHA4 Sales$: $15,880 × 35% = $5,558.00 ✅
- Total RHA savings: $13,349 ✅ exact match to Excel
- Daxxify spend: $10,500 ✅ ($0 savings)
- Redensity spend: $1,560 ✅ ($0 savings)
- Total all-time purchases: $50,200 ✅
