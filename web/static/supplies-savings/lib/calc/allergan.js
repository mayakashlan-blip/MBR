// Allergan savings calculator — spec: specs/allergan.md (LOCKED)
// Join key: Sold-to # (normalize by stripping .0 suffix) === Supplies - Allergan ID from Omni
//
// Allergan confirmed Sold-to # is the correct customer identifier (not Ship-to #).
// Omni's "Supplies - Allergan ID" field is being updated to store Sold-to IDs.
//
// NOTE ON ERA 1 BOTOX:
// The $656/100U Moxie price was established at Era 2 (Mar 1 2024).
// For Era 1 Botox rows, use Amount as reported (reflects the then-current contract price).
// For Era 2+ Botox, use Qty × $656 (overrides any billing discrepancies).

const ERA2_START = '2024-03-01';
const ERA3_START = '2024-08-27';

const BOTOX_MOXIE = { '100': 656.0, '50': 362.0, '200': 1312.0 };

/**
 * Normalize Allergan ID for comparison.
 * Omni stores "59625695"; transaction Sold-to # stores "59625695.0" (float artifact).
 */
export function normalizeAllerganId(id) {
  return String(id ?? '').trim().split('.')[0];
}

/**
 * Normalize product description for price table lookup.
 * Data has mixed case / spacing (e.g., "JUVeDERM ULTRA XC 1 mL, 2syr").
 */
function normDesc(desc) {
  return desc.toUpperCase().trim().replace(/\s+/g, ' ');
}

function getEra(dateStr) {
  if (dateStr >= ERA3_START) return 'era3';
  if (dateStr >= ERA2_START) return 'era2';
  return 'era1';
}

function getBotoxUnit(desc) {
  const d = desc.toUpperCase();
  if (d.includes('200')) return '200';
  if (d.includes('100')) return '100';
  if (d.includes('50'))  return '50';
  return null;
}

// ─── PRICE TABLES ────────────────────────────────────────────────────────────

const FILLER_PRICES = {
  era1: {
    'JUVEDERM ULTRA PLUS XC 1 ML , 2SYR':   { list: 755, moxie: 551.42 },
    'JUVEDERM ULTRA XC 0.55 ML, 2SYR':      { list: 621, moxie: 453.46 },
    'JUVEDERM ULTRA XC 1 ML, 2SYR':         { list: 755, moxie: 551.42 },
    'JUVEDERM VOLBELLA XC 0.55 ML, 2SYR':   { list: 420, moxie: 356.70 },
    'JUVEDERM VOLBELLA XC 1 ML, 2SYR':      { list: 770, moxie: 654.24 },
    'JUVEDERM VOLLURE XC 1 ML, 2SYR':       { list: 770, moxie: 654.24 },
    'JUVEDERM VOLUMA XC 1 ML, 2SYR':        { list: 853, moxie: 724.71 },
    'JUVEDERM VOLUX XC 2 X 1ML':            { list: 880, moxie: 748.20 },
    'SKINVIVE BY JUVEDERM 2X1 ML':          { list: 380, moxie: 368.60 },
    '25G CANNULA/23G NEEDLE KIT 10X10':     { list: 74,  moxie: 62.64  },
    '25G CANNULA/23G NEEDLE KIT 4X4':       { list: 35,  moxie: 29.58  },
  },
  era2: {
    'JUVEDERM ULTRA PLUS XC 1 ML , 2SYR':   { list: 781, moxie: 523.50 },
    'JUVEDERM ULTRA XC 0.55 ML, 2SYR':      { list: 643, moxie: 430.50 },
    'JUVEDERM ULTRA XC 1 ML, 2SYR':         { list: 781, moxie: 523.50 },
    'JUVEDERM VOLBELLA XC 0.55 ML, 2SYR':   { list: 431, moxie: 332.10 },
    'JUVEDERM VOLBELLA XC 1 ML, 2SYR':      { list: 791, moxie: 609.12 },
    'JUVEDERM VOLLURE XC 1 ML, 2SYR':       { list: 791, moxie: 609.12 },
    'JUVEDERM VOLUMA XC 1 ML, 2SYR':        { list: 876, moxie: 674.73 },
    'JUVEDERM VOLUX XC 2 X 1ML':            { list: 905, moxie: 696.60 },
    'SKINVIVE BY JUVEDERM 2X1 ML':          { list: 380, moxie: 368.60 },
    '25G CANNULA/23G NEEDLE KIT 10X10':     { list: 76,  moxie: 58.32  },
    '25G CANNULA/23G NEEDLE KIT 4X4':       { list: 36,  moxie: 27.54  },
  },
  era3: {
    'JUVEDERM ULTRA PLUS XC 1 ML , 2SYR':   { list: 698, moxie: 418.80 },
    'JUVEDERM ULTRA XC 0.55 ML, 2SYR':      { list: 574, moxie: 344.40 },
    'JUVEDERM ULTRA XC 1 ML, 2SYR':         { list: 698, moxie: 418.80 },
    'JUVEDERM VOLBELLA XC 0.55 ML, 2SYR':   { list: 410, moxie: 266.50 },
    'JUVEDERM VOLBELLA XC 1 ML, 2SYR':      { list: 752, moxie: 488.80 },
    'JUVEDERM VOLLURE XC 1 ML, 2SYR':       { list: 752, moxie: 488.80 },
    'JUVEDERM VOLUMA XC 1 ML, 2SYR':        { list: 833, moxie: 541.45 },
    'JUVEDERM VOLUX XC 2 X 1ML':            { list: 860, moxie: 559.00 },
    'SKINVIVE BY JUVEDERM 2X1 ML':          { list: 380, moxie: 368.60 },
    '25G CANNULA/23G NEEDLE KIT 10X10':     { list: 72,  moxie: 46.80  },
    '25G CANNULA/23G NEEDLE KIT 4X4':       { list: 34,  moxie: 22.10  },
    '27G CANNULA / 25G NEEDLE KIT 10X10':   { list: 72,  moxie: 46.80  },
    '27G CANNULA / 25G NEEDLE KIT 4X4':     { list: 34,  moxie: 22.10  },
  },
};

function round2(n) {
  return Math.round(n * 100) / 100;
}

/**
 * Calculate Allergan spend and savings for one medspa.
 *
 * @param {Array}  rows       - Full transactions_allergan JSON array
 * @param {string} allerganId - Supplies - Allergan ID from Omni (e.g. "59625695")
 * @param {object} [filter]   - Optional { startDate, endDate } ISO strings (inclusive)
 * @returns {{ spend: number, savings: number, rows: number }}
 */
export function calcAllergan(rows, allerganId, filter = {}) {
  const target = normalizeAllerganId(allerganId);

  let spend = 0;
  let savings = 0;
  let count = 0;

  for (const r of rows) {
    const soldTo = normalizeAllerganId(r['Sold-to #'] ?? '');
    if (soldTo !== target) continue;

    const amt = parseFloat(r['Amount']) || 0;
    const qty = parseFloat(r['Quantity']) || 0;
    const desc = r['Description'] ?? '';
    const date = r['DATE'] ?? '';

    // Free/promo rule: amount=0 AND qty>0 → skip
    if (amt === 0 && qty > 0) continue;

    // Date filter
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    count++;
    const era = getEra(date);
    const dn  = normDesc(desc);

    if (dn.includes('BOTOX')) {
      // Botox spend:
      //   Era 1: Amount as reported (Moxie's $656 contract wasn't in place yet)
      //   Era 2+: Qty × Moxie price (overrides any invoice discrepancies)
      // Botox savings = $0 always
      if (era === 'era1') {
        spend += amt;
      } else {
        const unit = getBotoxUnit(dn);
        spend += qty * (BOTOX_MOXIE[unit] ?? 656.0);
      }

    } else if (FILLER_PRICES[era][dn]) {
      // Known filler
      const prices = FILLER_PRICES[era][dn];
      if (era === 'era1') {
        spend   += amt;                                  // centralized billing
        savings += qty * (prices.list - prices.moxie);  // savings still apply
      } else {
        spend   += qty * prices.moxie;
        savings += qty * (prices.list - prices.moxie);
      }

    } else {
      // SkinMedica / Latisse / Kybella / DiamondGlow / credits / unknown
      // Spend = Amount as reported (includes credits which are negative)
      spend += amt;
      // Savings for SkinMedica/Latisse/Kybella/DiamondGlow Era2+: List = Moxie/0.88
      // No price table in spec for these → $0 savings (savings calculated separately via price file)
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}
