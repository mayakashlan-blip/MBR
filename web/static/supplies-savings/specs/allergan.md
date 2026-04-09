# Allergan — Spec (LOCKED ✅)

## Data Source
- Sheet: `Allergan Transactions` in Excel workbook
- Date field: `DATE` column (col 15)
- Medspa join: `Ship-to #` (col 4) = Allergan ID from Omni `medspas_and_clients` → `Supplies - Allergan ID`
  - NEVER use Ship-to Name or Sold-to Name — only Ship-to # matters
  - NEVER hardcode — always pull from Omni at runtime
- Deduplication key: `Ship-to #` + `Invoice or Credit Memo` + `Description`
- Free/promo rule: if `Amount = $0` and `Qty > 0` → skip row entirely
- Credits/returns: included in spend (net against purchases), savings = $0

## Formula

### Spend
- **Botox (all eras):** `Qty × Moxie price` ($656 for 100U, $362 for 50U, $1,312 for 200U)
- **Fillers/SkinMedica Era 2+:** `Qty × Moxie price`
- **Era 1 fillers:** `Amount` as reported (centralized billing)
- **Credits:** `Amount` as reported (negative)
- **Unknown products:** `Amount` as reported

### Savings
- **Botox:** $0 always (rebate handled separately)
- **Fillers:** `Qty × (List − Moxie)`
- **SkinMedica/Latisse/Kybella/DiamondGlow:** `Qty × (List − Moxie)` — Era 2 and 3 only, Era 1 = $0
- **Credits:** $0
- **Note:** List price for SkinMedica = `Moxie ÷ 0.88` (NOT Moxie × 1.12)

## Era Boundaries
- **Era 1:** pre March 1, 2024
- **Era 2:** March 1, 2024 – August 26, 2024
- **Era 3:** August 27, 2024 onwards

## Era 1 Prices (pre Mar 1, 2024)
| Product | List | Moxie |
|---|---|---|
| JUVEDERM ULTRA PLUS XC 1 ML , 2SYR | $755 | $551.42 |
| JUVEDERM ULTRA XC 0.55 ML, 2SYR | $621 | $453.46 |
| JUVEDERM ULTRA XC 1 ML, 2SYR | $755 | $551.42 |
| JUVEDERM VOLBELLA XC 0.55 ML, 2SYR | $420 | $356.70 |
| JUVEDERM VOLBELLA XC 1 ML, 2SYR | $770 | $654.24 |
| JUVEDERM VOLLURE XC 1 ML, 2SYR | $770 | $654.24 |
| JUVEDERM VOLUMA XC 1 ML, 2SYR | $853 | $724.71 |
| JUVEDERM VOLUX XC 2 X 1ML | $880 | $748.20 |
| SKINVIVE BY JUVEDERM 2X1 ML | $380 | $368.60 |
| 25G CANNULA/23G NEEDLE KIT 10X10 | $74 | $62.64 |
| 25G CANNULA/23G NEEDLE KIT 4X4 | $35 | $29.58 |
| Era 1 spend = Amount; savings = Qty × (List − Moxie) for fillers only; SkinMedica = $0 |

## Era 2 Prices (Mar 1 – Aug 26, 2024)
| Product | List | Moxie |
|---|---|---|
| JUVEDERM ULTRA PLUS XC 1 ML , 2SYR | $781 | $523.50 |
| JUVEDERM ULTRA XC 0.55 ML, 2SYR | $643 | $430.50 |
| JUVEDERM ULTRA XC 1 ML, 2SYR | $781 | $523.50 |
| JUVEDERM VOLBELLA XC 0.55 ML, 2SYR | $431 | $332.10 |
| JUVEDERM VOLBELLA XC 1 ML, 2SYR | $791 | $609.12 |
| JUVEDERM VOLLURE XC 1 ML, 2SYR | $791 | $609.12 |
| JUVEDERM VOLUMA XC 1 ML, 2SYR | $876 | $674.73 |
| JUVEDERM VOLUX XC 2 X 1ML | $905 | $696.60 |
| SKINVIVE BY JUVEDERM 2X1 ML | $380 | $368.60 |
| 25G CANNULA/23G NEEDLE KIT 10X10 | $76 | $58.32 |
| 25G CANNULA/23G NEEDLE KIT 4X4 | $36 | $27.54 |

## Era 3 Prices (Aug 27, 2024 onwards)
| Product | List | Moxie |
|---|---|---|
| JUVEDERM ULTRA PLUS XC 1 ML , 2SYR | $698 | $418.80 |
| JUVEDERM ULTRA XC 0.55 ML, 2SYR | $574 | $344.40 |
| JUVEDERM ULTRA XC 1 ML, 2SYR | $698 | $418.80 |
| JUVEDERM VOLBELLA XC 0.55 ML, 2SYR | $410 | $266.50 |
| JUVEDERM VOLBELLA XC 1 ML, 2SYR | $752 | $488.80 |
| JUVEDERM VOLLURE XC 1 ML, 2SYR | $752 | $488.80 |
| JUVEDERM VOLUMA XC 1 ML, 2SYR | $833 | $541.45 |
| JUVEDERM VOLUX XC 2 X 1ML | $860 | $559.00 |
| SKINVIVE BY JUVEDERM 2X1 ML | $380 | $368.60 |
| 25G CANNULA/23G NEEDLE KIT 10X10 | $72 | $46.80 |
| 25G CANNULA/23G NEEDLE KIT 4X4 | $34 | $22.10 |
| 27G CANNULA / 25G NEEDLE KIT 10X10 | $72 | $46.80 |
| 27G CANNULA / 25G NEEDLE KIT 4X4 | $34 | $22.10 |

## SkinMedica / Latisse / Kybella / DiamondGlow (Era 2 & 3 only)
Savings = `Qty × (List − Moxie)`. List = `Moxie ÷ 0.88`. Era 1 = $0 savings.

See `allergan_skinmedica_prices.md` for full SKU price table.

## Rebates (Allergan Botox)
- Formula: `Total Purchase Qty × Moxie Price × 10%` minus shipping fees and taxes
- Botox Moxie rebate prices: 100U = $590.40, 50U = $347.52
- Applied quarterly, stored in rebates table by Applied Date

## Validation ✅
La Miel Aesthetics (Ship-to# 59625695):
- 3M spend: $6,389.74 ✅
- All-time spend: $93,945.79 ✅
