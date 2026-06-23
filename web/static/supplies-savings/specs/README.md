# Supplies Savings — Vendor Calculation Specs

## Critical Rules

### NEVER guess or hardcode vendor IDs
Every medspa-to-vendor join MUST come from an authoritative source. There is a specific rule for each vendor below. If the ID is not available from the source, show no data — never fall back to a guess.

### Omni — medspa dropdown + vendor IDs
**Dashboard:** https://moxie.omniapp.co/dashboards/01807819
**Topic:** `medspas_and_clients`
**Model ID:** `66cd7b98-e632-43b3-9724-75969a1d8961`

Query this topic at runtime to populate:
1. The medspa dropdown (field: `Medspa Name With ID`)
2. All vendor account IDs for that medspa

**Exact Omni field names:**
| Field | Used for |
|---|---|
| `Medspa Name With ID` | Dropdown label (e.g. "Citrus Aesthetics (6)") |
| `Medspa ID` | Moxie medspa ID |
| `Supplies - MKID` | Galderma join key |
| `Supplies - Allergan ID` | Allergan join key |
| `Supplies - Merz ID` | Merz — not used yet (Merz uses Naming Convention) |
| `Supplies - Revance ID` | Revance — not used yet (Revance uses Naming Convention) |
| `Supplies - Jeuveau ID` | Evolus — not used yet (Evolus uses Naming Convention) |

### ID sources by vendor
| Vendor | Transaction join field | Authoritative source | Exact field/column |
|---|---|---|---|
| Galderma | `SHIP TO` | Omni `medspas_and_clients` | `Supplies - MKID` |
| Allergan | `Ship-to #` (col 4) | Omni `medspas_and_clients` | `Supplies - Allergan ID` |
| Merz | `Ship_To_Name` (col 12) | Naming Convention CSV | Col 11 (Merz) |
| Revance | `Medspa Name` (col 9) | Naming Convention CSV | Col 6 (Revance) |
| Evolus | `Facility` (col 0) | Naming Convention CSV | Col 12 (Evolus) |
| Cherry | `COMPANY` | Naming Convention CSV | Col 7 (Cherry) |
| Revanesse | `Medspa Name` | Naming Convention CSV | Col 14 (Revanesse) |

### Naming Convention CSV
- Uploaded monthly as a **full replace** (not append)
- This is a lookup table — always use the latest version
- Columns: 0=Moxie ID, 1=Moxie Name, 2=Segment, 3=Gold Tier Date, 4=Galderma name, 5=Allergan name, 6=Revance name, 7=Cherry name, 8=Galderma ID, 9=Allergan ID, 10=Affirm name, 11=Merz name, 12=Evolus name, 13=Olympia name, 14=Revanesse name

### Monthly upload process
1. Transaction CSVs (Galderma, Allergan, Merz, Evolus, Revance, Cherry, Affirm, Revanesse) — **append + deduplicate**
2. Rebates CSV — **append**
3. Naming Convention CSV — **full replace**

### Free/promo items rule
If `amount = $0` and `qty > 0` → skip row entirely. Spend = $0, savings = $0.

### Rebates
- Stored separately from transaction savings
- Shown as separate "Rebates" column in all-time vendor table (italic aubergine)
- Included in all-time hero card total with note "incl. $X in quarterly rebates"
- NOT included in monthly/3M/YTD hero card savings numbers
- Applied on the `Applied Date` in the rebates file (not the purchase date)

---

## Vendors

- [Galderma](./galderma.md)
- [Allergan](./allergan.md)
- [Evolus](./evolus.md)
- [Merz](./merz.md) — formula confirmed, full spec pending
- [Revance](./revance.md) — under investigation
- Cherry — pending
- Affirm — pending
- Revanesse — pending
