// Merz savings calculator — spec: specs/merz.md (LOCKED)
// Join key: Ship_To_Name === Merz name from name_map.json (vendor: "merz")

// ─── XEOMIN ──────────────────────────────────────────────────────────────────
// Spend = Qty × List price (hardcoded — NOT Gross_Value, NOT Moxie price)
// Savings = Qty × List × rate
//   rate = 80% (pre Jan 1, 2025) | 100% (Jan 1, 2025 onwards)

const XEOMIN_PRICES = {
  'Xeomin 100-U Vials': { list: 511.00, moxie: 255.50 },
  'Xeomin 50 U Vials':  { list: 268.00, moxie: 134.00 },
};

// ─── STANDARD PRICED PRODUCTS ────────────────────────────────────────────────
// Spend = Qty × Moxie | Savings = Qty × (List − Moxie)

const STANDARD_PRICES = {
  'BELOTERO Balance 1.0cc US':                        { list: 285.00, moxie: 213.75 },
  'Belotero Balance Lido US 1x1.0ml':                { list: 329.00, moxie: 246.75 },
  'Belotero 1.0 Lido':                               { list: 329.00, moxie: 246.75 },
  'RADIESSE (Refresh) - 2 X 1.5cc Kit':              { list: 780.00, moxie: 468.00 },
  'RADIESSE (+) Lidocaine (Refresh) - 2 X 1.5cc Kit':{ list: 780.00, moxie: 468.00 },
};

// Ultherapy and Describe — per spec
const ULTHERAPY_PRICE = { list: 2340.00, moxie: 1989.00 };
const DESCRIBE_PRICE  = { list: 630.00,  moxie: 504.00  };

// ─── NEOCUTIS PRODUCTS ───────────────────────────────────────────────────────
// Spend = Gross_Value | Savings = Gross_Value × 20%
// If Gross_Value = 0: Spend = $0, Savings = $0

const NEOCUTIS_PRODUCTS = new Set([
  'Lumiere Firm Riche 15ml', 'Journée Firm 50ml', 'Journée Firm 15ml', 'Neo Firm 50g',
  'NeoGentle Cleanser 125ml', 'NeoCleanse Exfoliating 125ml', 'Neo Body 200ml',
  'Lumiere Firm 15ml', 'Perle 30ml RB', 'Journee Firm Riche 50ml', 'Journee Firm Riche 15ml',
  'Bio Cream Firm Riche 15ml', 'Bio Cream Firm 15ml', 'Bio Cream Firm 50ml',
  'Bio Cream Firm 200ml', 'Bio Gel Firm 15ml', 'Bio Gel Firm 50ml', 'Bio Gel Firm 200ml',
  'Bio Serum Firm 30ml', 'Daily Essentials Kit', 'Hyalis+ 30ml', 'Hyalis+ 15ml',
  'Lumiere Firm 200ml', 'Micro-Gel 50ml', 'Neocutis After Care 15ml',
  'Neocutis After Care 200ml', 'RéActive 30ml +', 'Nouvelle', 'Nouvelle 6 et plus',
  'NEOCUTIS Neo Restore (6 sachets)',
]);

// ─── OTHER (no savings) ───────────────────────────────────────────────────────
// Spend = Gross_Value, Savings = $0
// Radiesse Cannulas, certain UT transducers, 27GA cannula

const XEOMIN_ERA2_START = '2025-01-01';

function round2(n) {
  return Math.round(n * 100) / 100;
}

function isUltherapy(desc) {
  return desc.includes('Ultherapy Transducer') ||
    /^UT-[1-4]\b/.test(desc) || desc.startsWith('UT Transducer');
}

function isDescribe(desc) {
  return desc.includes('Describe') && desc.includes('Pack');
}

/**
 * Calculate Merz spend and savings for one medspa.
 *
 * @param {Array}  rows     - Full transactions_merz JSON array
 * @param {string} merzName - Ship_To_Name (from name_map.json vendor: "merz")
 * @param {object} [filter] - Optional { startDate, endDate } ISO strings (inclusive)
 * @returns {{ spend: number, savings: number, rows: number }}
 */
export function calcMerz(rows, merzName, filter = {}) {
  let spend   = 0;
  let savings = 0;
  let count   = 0;

  for (const r of rows) {
    if ((r['Ship_To_Name'] ?? '').trim() !== merzName.trim()) continue;

    const qty   = parseFloat(r['Billing_qty_in_SKU']) || 0;
    const gross = parseFloat(r['Gross_Value'])         || 0;
    const date  = r['Invoice_Date'] ?? '';
    const desc  = r['MaterialDescription'] ?? '';

    // Skip rows where Qty = 0
    if (qty === 0) continue;

    // Date filter
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    count++;

    // ── Xeomin (BOGO program) ─────────────────────────────────────────────
    if (XEOMIN_PRICES[desc]) {
      const { list } = XEOMIN_PRICES[desc];
      const rate = date >= XEOMIN_ERA2_START ? 1.0 : 0.8;  // 100% / 80%
      spend   += qty * list;
      savings += qty * list * rate;

    // ── Standard priced products ──────────────────────────────────────────
    } else if (STANDARD_PRICES[desc]) {
      const { list, moxie } = STANDARD_PRICES[desc];
      spend   += qty * moxie;
      savings += qty * (list - moxie);

    // ── Ultherapy transducers ─────────────────────────────────────────────
    } else if (isUltherapy(desc)) {
      spend   += qty * ULTHERAPY_PRICE.moxie;
      savings += qty * (ULTHERAPY_PRICE.list - ULTHERAPY_PRICE.moxie);

    // ── Describe packs ────────────────────────────────────────────────────
    } else if (isDescribe(desc)) {
      spend   += qty * DESCRIBE_PRICE.moxie;
      savings += qty * (DESCRIBE_PRICE.list - DESCRIBE_PRICE.moxie);

    // ── Neocutis products ─────────────────────────────────────────────────
    } else if (NEOCUTIS_PRODUCTS.has(desc)) {
      if (gross > 0) {
        spend   += gross;
        savings += gross * 0.20;
      }
      // If Gross_Value = 0: skip (no spend, no savings)

    // ── Other (cannulas, UT variants, etc.) ───────────────────────────────
    } else {
      spend += gross;  // Savings = $0
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}
