# Galderma — Spec (LOCKED ✅)

## Data Source
- File: Galderma CSV (uploaded monthly, append + deduplicate)
- Date field: `ORDER DATE` column only
- Medspa join: `SHIP TO` = MKID from Omni `medspas_and_clients` → `Supplies - MKID`
  - MKID always has leading zero (e.g. `0100754270`)
  - NEVER hardcode — always pull from Omni at runtime
- Deduplication key: `SHIP TO` + `DELIVERY #` + `DESCRIPTION`
- Exclude: rows where `DESCRIPTION` contains `SHIPPING`
- Free/promo rule: if `EXTENDED AMOUNT = $0` and `QTY > 0` → skip row entirely

## Formula

### Era 1 — pre April 1, 2024
- Spend = `Extended Amount × 1.029`
- Savings = `Spend × discount_rate` (per product table below)

| Product | Discount Rate |
|---|---|
| DYSPORT 300IU SDV 1/EA | 27.87% |
| RESTYLANE L 1ML 1/EA | 59.31% |
| RESTYLANE 1ML 1/EA | 59.31% |
| RESTYLANE SILK 1ML 1/EA | 67.55% |
| RESTYLANE L LYFT 1ML 1/EA | 59.31% |
| RESTYLANE REFYNE W/LIDO 1ML 1/EA | 61.96% |
| RESTYLANE DEFYNE W/LIDO 1ML 1/EA | 59.31% |
| RESTYLANE KYSSE 0.3%+LIDO 1ML 1/EA | 54.25% |
| RESTYLANE CONTOUR 0.3% + LIDO 1ML 1/EA | 59.31% |
| RESTYLANE EYELIGHT 0.5ML | 59.31% |
| SCULPTRA 2X367.5MGVIALS X72 US 2/PAC | 90.55% |

### Era 2 — April 1, 2024 onwards
- Spend = `Qty × Moxie price`
- Savings = `Qty × (List price − Moxie price)`

| Product | List | Moxie |
|---|---|---|
| DYSPORT 300IU SDV 1/EA | $622.00 | $466.50 |
| RESTYLANE L 1ML 1/EA | $344.00 | $209.84 |
| RESTYLANE 1ML 1/EA | $344.00 | $209.84 |
| RESTYLANE SILK 1ML 1/EA | $351.00 | $200.07 |
| RESTYLANE L LYFT 1ML 1/EA | $366.00 | $219.60 |
| RESTYLANE REFYNE W/LIDO 1ML 1/EA | $407.00 | $240.13 |
| RESTYLANE DEFYNE W/LIDO 1ML 1/EA | $407.00 | $244.20 |
| RESTYLANE KYSSE 0.3%+LIDO 1ML 1/EA | $425.00 | $263.50 |
| RESTYLANE CONTOUR 0.3% + LIDO 1ML 1/EA | $425.00 | $255.00 |
| RESTYLANE EYELIGHT 0.5ML | $255.00 | $153.00 |
| SCULPTRA 2X367.5MGVIALS X72 US 2/PAC | $1,040.00 | $509.60 |

## Rebates (Galderma — Gold Tier only)
- Formula: `Total Spend Amount × 8%` minus shipping fees and taxes
- Applied quarterly, stored in rebates table by Applied Date
- Shown separately from direct savings

## Validation ✅
Citrus Aesthetics (ID=6, MKID=0100754270):
- Era 1 all-time spend: $24,074.37 ✅
- Era 1 all-time savings: $10,574.11 ✅
- Era 2 3M savings: $3,742.00 ✅
