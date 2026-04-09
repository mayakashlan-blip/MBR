// Galderma savings calculator — spec: specs/galderma.md (LOCKED)
// Join key: SHIP TO (normalized, no leading zeros) === Supplies - MKID from Omni (no leading zeros)

const ERA2_START = '2024-04-01'; // ORDER DATE >= this date = Era 2

const DISCOUNT_RATES_ERA1 = {
  'DYSPORT 300IU SDV 1/EA': 0.2787,
  'RESTYLANE L 1ML 1/EA': 0.5931,
  'RESTYLANE 1ML 1/EA': 0.5931,
  'RESTYLANE SILK 1ML 1/EA': 0.6755,
  'RESTYLANE L LYFT 1ML 1/EA': 0.5931,
  'RESTYLANE REFYNE W/LIDO 1ML 1/EA': 0.6196,
  'RESTYLANE DEFYNE W/LIDO 1ML 1/EA': 0.5931,
  'RESTYLANE KYSSE 0.3%+LIDO 1ML 1/EA': 0.5425,
  'RESTYLANE CONTOUR 0.3% + LIDO 1ML 1/EA': 0.5931,
  'RESTYLANE EYELIGHT 0.5ML': 0.5931,
  'SCULPTRA 2X367.5MGVIALS X72 US 2/PAC': 0.9055,
};

const PRICES_ERA2 = {
  'DYSPORT 300IU SDV 1/EA':                   { list: 622.00,  moxie: 466.50 },
  'RESTYLANE L 1ML 1/EA':                     { list: 344.00,  moxie: 209.84 },
  'RESTYLANE 1ML 1/EA':                       { list: 344.00,  moxie: 209.84 },
  'RESTYLANE SILK 1ML 1/EA':                  { list: 351.00,  moxie: 200.07 },
  'RESTYLANE L LYFT 1ML 1/EA':               { list: 366.00,  moxie: 219.60 },
  'RESTYLANE REFYNE W/LIDO 1ML 1/EA':        { list: 407.00,  moxie: 240.13 },
  'RESTYLANE DEFYNE W/LIDO 1ML 1/EA':        { list: 407.00,  moxie: 244.20 },
  'RESTYLANE KYSSE 0.3%+LIDO 1ML 1/EA':      { list: 425.00,  moxie: 263.50 },
  'RESTYLANE CONTOUR 0.3% + LIDO 1ML 1/EA':  { list: 425.00,  moxie: 255.00 },
  'RESTYLANE EYELIGHT 0.5ML':                 { list: 255.00,  moxie: 153.00 },
  'SCULPTRA 2X367.5MGVIALS X72 US 2/PAC':    { list: 1040.00, moxie: 509.60 },
};

/**
 * Normalize a Galderma MKID for comparison — strip leading zeros.
 * Omni stores e.g. "100754270"; transaction SHIP TO stores "0100754270".
 */
export function normalizeId(id) {
  return String(id ?? '').trim().replace(/^0+/, '');
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

/**
 * Calculate Galderma spend and savings for one medspa.
 *
 * @param {Array}  rows  - Full transactions_galderma JSON array
 * @param {string} mkid  - Supplies - MKID from Omni (e.g. "100754270")
 * @param {object} [filter] - Optional { startDate, endDate } ISO strings (inclusive)
 * @returns {{ spend: number, savings: number, rows: number }}
 */
export function calcGalderma(rows, mkid, filter = {}) {
  const normTarget = normalizeId(mkid);

  let spend = 0;
  let savings = 0;
  let count = 0;

  for (const r of rows) {
    // Match medspa by normalized MKID
    if (normalizeId(r['SHIP TO']) !== normTarget) continue;

    // Skip SHIPPING rows
    if ((r['DESCRIPTION'] ?? '').toUpperCase().includes('SHIPPING')) continue;

    const amt = parseFloat(r['EXTENDED AMOUNT']) || 0;
    const qty = parseFloat(r['QTY']) || 0;

    // Free/promo rule: amount=0, qty>0 → skip
    if (amt === 0 && qty > 0) continue;

    const orderDate = r['ORDER DATE'] ?? '';

    // Date filter
    if (filter.startDate && orderDate < filter.startDate) continue;
    if (filter.endDate   && orderDate > filter.endDate)   continue;

    count++;

    if (orderDate < ERA2_START) {
      // Era 1: spend = Extended Amount × 1.029
      const rowSpend = amt * 1.029;
      const rate = DISCOUNT_RATES_ERA1[r['DESCRIPTION']] ?? 0;
      spend   += rowSpend;
      savings += rowSpend * rate;
    } else {
      // Era 2: spend = Qty × Moxie, savings = Qty × (List − Moxie)
      const prices = PRICES_ERA2[r['DESCRIPTION']];
      if (prices) {
        spend   += qty * prices.moxie;
        savings += qty * (prices.list - prices.moxie);
      }
      // Unknown products in Era 2: $0 spend and savings (no price table entry)
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}
