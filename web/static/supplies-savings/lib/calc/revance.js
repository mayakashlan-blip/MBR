// Revance savings calculator
// Join key: Moxie Medspa ID (from CSV column "Moxie Medspa ID") — no Omni lookup needed
// Name map: auto-built on every upload from (Account Name → Moxie Medspa ID)
//
// Row inclusion: only rows where "Count towards savings" === "TRUE" and Moxie Medspa ID is set
// Promo exclusion: sales <= 0 OR qty <= 0 → skip entirely

// ─── SAVINGS-ELIGIBLE PRODUCTS ───────────────────────────────────────────────
// Savings = max((qty × list_price) − sales, 0)   — never negative
// All other products: spend = sales, savings = $0

const SAVINGS_PRODUCTS = {
  'RHA2':      { list_price: 600 },   // per box
  'RHA3':      { list_price: 600 },
  'RHA4':      { list_price: 600 },
  'Redensity': { list_price: 600 },
  'Daxxify':   { list_price: 420 },   // per vial
};

function round2(n) {
  return Math.round(n * 100) / 100;
}

function parseDollars(v) {
  return parseFloat(String(v || '').replace(/[$,]/g, '')) || 0;
}

// Normalise M/D/YYYY → YYYY-MM-DD so ISO string comparisons work correctly
function normalizeDate(v) {
  const s = String(v || '').trim();
  const m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) return `${m[3]}-${m[1].padStart(2,'0')}-${m[2].padStart(2,'0')}`;
  return s; // already ISO or unknown — return as-is
}

/**
 * Calculate Revance spend and savings for one medspa.
 *
 * Rows must already be filtered to:
 *   - "Count towards savings" === "TRUE"
 *   - "Moxie Medspa ID" present (nulls excluded at upload time)
 *
 * @param {Array}         rows    - Full transactions_revance JSON array
 * @param {number|string} moxieId - Moxie Medspa ID to aggregate
 * @param {object}       [filter] - Optional { startDate, endDate } ISO date strings (inclusive)
 * @returns {{ spend: number, savings: number, rows: number }}
 */
export function calcRevance(rows, moxieId, filter = {}) {
  const id = String(moxieId).trim();
  let spend = 0, savings = 0, count = 0;

  for (const r of rows) {
    if (String(r['Moxie Medspa ID'] ?? '').trim() !== id) continue;

    const date     = normalizeDate(r['Date']);
    const product  = (r['Product'] ?? '').trim();
    const salesAmt = parseDollars(r['Sales $']);
    const qty      = parseFloat(r['Boxes / Vials']) || 0;

    // Date filter
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    // Free / promo — exclude entirely
    if (salesAmt <= 0 || qty <= 0) continue;

    spend += salesAmt;

    const p = SAVINGS_PRODUCTS[product];
    if (p) {
      // Savings = list value − what medspa paid; floor at 0 if they paid above list
      savings += Math.max((qty * p.list_price) - salesAmt, 0);
    }

    count++;
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}
