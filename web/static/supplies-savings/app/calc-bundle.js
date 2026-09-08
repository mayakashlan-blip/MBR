// app/calc-bundle.js — browser-compatible calc bundle
// Plain <script> compatible — no import/export.
// Logic copied exactly from lib/calc/. Do not edit here; edit lib/calc/ then re-bundle.
//
// Step 1a: Galderma only.

// ── PRICING HELPER ─────────────────────────────────────────────────────────────
// Copied exactly from lib/calc/pricingHelper.js

function getPricesForDate(pricingEras, vendorId, sku, date) {
  if (!pricingEras || !pricingEras[vendorId]) return null;

  const eras = pricingEras[vendorId];

  const matching = eras.filter(era => {
    if (era.startDate > date) return false;
    if (era.endDate !== null && era.endDate < date) return false;
    return true;
  });

  if (matching.length === 0) return null;

  const era = matching.sort((a, b) => b.startDate.localeCompare(a.startDate))[0];

  if (!era.products || !era.products[sku]) return null;

  return era.products[sku];
}

// ── GALDERMA ───────────────────────────────────────────────────────────────────
// Copied exactly from lib/calc/galderma.js

const ERA2_START = '2024-04-01'; // ORDER DATE >= this date = Era 2

// Normalize M/D/YYYY → YYYY-MM-DD so ISO string comparisons work correctly.
function normalizeDate(str) {
  const parts = String(str || '').split('/');
  if (parts.length === 3) return parts[2] + '-' + parts[0].padStart(2, '0') + '-' + parts[1].padStart(2, '0');
  return str;
}

// Strip leading $ and commas so parseFloat works on values like '$945.44' or '$1,234.00'.
function parseDollars(v) {
  return parseFloat(String(v || '').replace(/[$,]/g, '')) || 0;
}

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
 */
function normalizeId(id) {
  return String(id ?? '').trim().replace(/^0+/, '');
}

function round2(n) {
  return Math.round(n * 100) / 100;
}

/**
 * Calculate Galderma spend and savings for one medspa.
 * Identical to lib/calc/galderma.js calcGalderma().
 * Returns { spend, savings, rows }.
 */
function calcGalderma(rows, mkid, filter = {}, pricingEras = null, moxieId = null) {
  const normTarget = normalizeId(mkid);
  const normMoxieId = moxieId != null ? String(moxieId) : null;

  let spend = 0;
  let savings = 0;
  let count = 0;

  for (const r of rows) {
    // Match medspa by normalized MKID or by _moxie_id
    const shipToMatch = normTarget && normalizeId(r['SHIP TO']) === normTarget;
    const moxieIdMatch = normMoxieId != null &&
      (String(r._moxie_id ?? '') === normMoxieId || Number(r._moxie_id) === Number(moxieId));
    if (!shipToMatch && !moxieIdMatch) continue;

    // Skip SHIPPING rows
    if ((r['DESCRIPTION'] ?? '').toUpperCase().includes('SHIPPING')) continue;

    const amt = parseDollars(r['EXTENDED AMOUNT']);
    const qty = parseFloat(r['QTY']) || 0;

    // Free/promo rule: amount=0, qty>0 → skip
    if (amt === 0 && qty > 0) continue;

    const orderDate = normalizeDate(r['ORDER DATE'] ?? '');
    const descUpper = (r['DESCRIPTION'] ?? '').toUpperCase();

    // Date filter
    if (filter.startDate && orderDate < filter.startDate) continue;
    if (filter.endDate   && orderDate > filter.endDate)   continue;

    count++;

    if (orderDate < ERA2_START) {
      // Era 1: spend = Extended Amount × 1.029
      const rowSpend = amt * 1.029;
      const priceFromData = getPricesForDate(pricingEras, 'galderma', descUpper, orderDate);
      const rate = (priceFromData?.discountRate != null)
        ? priceFromData.discountRate
        : (DISCOUNT_RATES_ERA1[descUpper] ?? 0);
      spend   += rowSpend;
      savings += rowSpend * rate;
    } else {
      // Era 2: spend = Qty × Moxie, savings = Qty × (List − Moxie)
      const priceFromData = getPricesForDate(pricingEras, 'galderma', descUpper, orderDate);
      const prices = priceFromData || PRICES_ERA2[descUpper];
      if (prices) {
        spend   += qty * prices.moxie;
        savings += qty * (prices.list - prices.moxie);
      }
      // Unknown products in Era 2: $0 spend and savings
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}

// ── ALLERGAN ───────────────────────────────────────────────────────────────────
// Copied exactly from lib/calc/allergan.js.
// ERA2_START / ERA3_START renamed AL_ERA2_START / AL_ERA3_START (Galderma already
// owns ERA2_START with a different value). normalizeDate and round2 are shared
// from the Galderma section above — identical implementations, not re-declared.

const AL_ERA2_START = '2024-03-01';
const AL_ERA3_START = '2024-08-27';

const AL_BOTOX_MOXIE = { '100': 656.0, '50': 362.0, '200': 1312.0 };

// Savings rate for SkinMedica / DiamondGlow / ancillary brands in Era 2+.
const AL_SKM_SAVINGS_PCT = 0.1364;

// Material IDs for all Botox variants — looked up by ID, not description.
const AL_BOTOX_MATERIAL_IDS = new Set(['91223US', '92326', '93919', '93921']);

// Substring markers used to identify SkinMedica-family products (category match).
const AL_SKM_BRANDS = [
  'SKINMEDICA','LATISSE','DIAMONDGLOW','TNS','HA5','AHA/BHA',
  'ESD ','LUMIVIVE','RETINOL','NECK CORRECT','INSTANT BRIGHT','EVEN & CORRECT',
  'ULTRA SHEER','TOTAL DEFENSE','VITAMIN C+E','PORE PURIFYING','FACIAL CLEANSER',
  'REJUVENIZE','VITALIZE','ILLUMINIZE','CALMING MASQUE','PURIFYING',
  'SCAR RECOVERY','RESTORATIVE',
];

// Promo / rebate description patterns — these rows are excluded entirely.
const AL_PROMO_RE = /REBATE|BUY \d+ GET|GET \d+ FREE|FREE$|BUY.IN|BUY-IN/;

const AL_FILLER_PRICES = {
  era1: {
    '94155':    { list: 755, moxie: 551.42 },  // JUVEDERM ULTRA PLUS XC 1ML
    '94154':    { list: 755, moxie: 551.42 },  // JUVEDERM ULTRA XC 1ML
    '96207':    { list: 621, moxie: 453.46 },  // JUVEDERM ULTRA XC 0.55ML
    '96209':    { list: 621, moxie: 453.46 },  // JUVEDERM ULTRA PLUS XC 0.55ML
    '96183':    { list: 420, moxie: 356.70 },  // JUVEDERM VOLBELLA XC 0.55ML
    '96181':    { list: 770, moxie: 654.24 },  // JUVEDERM VOLBELLA XC 1ML
    '95661':    { list: 770, moxie: 654.24 },  // JUVEDERM VOLLURE XC 1ML
    '94640':    { list: 853, moxie: 724.71 },  // JUVEDERM VOLUMA XC 1ML
    '20073770': { list: 880, moxie: 748.20 },  // JUVEDERM VOLUX XC 2X1ML
    '98307':    { list: 380, moxie: 368.60 },  // SKINVIVE BY JUVEDERM
    '96688':    { list: 35,  moxie: 29.58  },  // 25G CANNULA 4X4
    '96689':    { list: 74,  moxie: 62.64  },  // 25G CANNULA 10X10
  },
  era2: {
    '94155':    { list: 781, moxie: 523.50 },
    '94154':    { list: 781, moxie: 523.50 },
    '96207':    { list: 643, moxie: 430.50 },
    '96209':    { list: 643, moxie: 430.50 },
    '96183':    { list: 431, moxie: 332.10 },
    '96181':    { list: 791, moxie: 609.12 },
    '95661':    { list: 791, moxie: 609.12 },
    '94640':    { list: 876, moxie: 674.73 },
    '20073770': { list: 905, moxie: 696.60 },
    '98307':    { list: 380, moxie: 368.60 },
    '96688':    { list: 36,  moxie: 27.54  },
    '96689':    { list: 76,  moxie: 58.32  },
  },
  era3: {
    '94155':    { list: 698,  moxie: 418.80  },
    '94154':    { list: 698,  moxie: 418.80  },
    '96207':    { list: 574,  moxie: 344.40  },
    '96209':    { list: 574,  moxie: 344.40  },
    '96183':    { list: 410,  moxie: 266.50  },
    '96181':    { list: 752,  moxie: 488.80  },
    '95661':    { list: 752,  moxie: 488.80  },
    '94640':    { list: 833,  moxie: 541.45  },
    '20073770': { list: 860,  moxie: 559.00  },
    '98307':    { list: 380,  moxie: 368.60  },
    '95706':    { list: 1200, moxie: 1080.00 },  // KYBELLA 2 ML
    '94511':    { list: 131,  moxie: 114.99  },  // LATISSE 5mL
    '94847':    { list: 108,  moxie: 94.99   },  // LATISSE 3mL
    '96688':    { list: 34,   moxie: 22.10   },
    '96689':    { list: 72,   moxie: 46.80   },
    '97069':    { list: 34,   moxie: 22.10   },  // 27G CANNULA 4X4
    '97070':    { list: 72,   moxie: 46.80   },  // 27G CANNULA 10X10
  },
};

/**
 * Normalize Allergan ID for comparison.
 * Omni stores "59625695"; transaction Sold-to # stores "59625695.0" (float artifact).
 */
function normalizeAllerganId(id) {
  return String(id ?? '').trim().split('.')[0];
}

/**
 * Normalize Material ID — strip trailing .0 artifact, trim whitespace.
 */
function normMatId(id) {
  return String(id ?? '').replace(/\.0$/, '').trim();
}

/**
 * Normalize product description for SKM brand detection and promo filtering.
 */
function normDesc(desc) {
  return desc.toUpperCase().trim().replace(/\s+/g, ' ');
}

function alGetEra(dateStr) {
  if (dateStr >= AL_ERA3_START) return 'era3';
  if (dateStr >= AL_ERA2_START) return 'era2';
  return 'era1';
}

/**
 * Return Botox unit size ('100', '50', '200') from Material ID.
 */
function alGetBotoxUnit(matId) {
  if (matId === '93921') return '200';
  if (matId === '93919') return '50';
  return '100'; // 92326, 91223US
}

/**
 * Calculate Allergan spend and savings for one medspa.
 * Identical to lib/calc/allergan.js calcAllergan().
 * Returns { spend, savings, rows }.
 */
function calcAllergan(rows, allerganId, filter = {}, pricingEras = null) {
  const target = normalizeAllerganId(allerganId);

  let spend = 0;
  let savings = 0;
  let count = 0;

  for (const r of rows) {
    const soldTo = normalizeAllerganId(r['Sold-to #'] ?? '');
    if (soldTo !== target) continue;

    const amt   = parseFloat(r['Amount'])   || 0;
    const qty   = parseFloat(r['Quantity']) || 0;
    const desc  = r['Description'] ?? '';
    const date  = normalizeDate(r['DATE'] ?? '');
    const matId = normMatId(r['Material']);

    // Free/promo rule: amount=0 AND qty>0 → skip
    if (amt === 0 && qty > 0) continue;

    // Normalized description — used for promo filtering and SKM brand detection
    const dn = normDesc(desc);

    // Promo/rebate description filter — skip buy-in and rebate credit lines entirely
    if (AL_PROMO_RE.test(dn)) continue;

    // Date filter
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    count++;
    const era = alGetEra(date);

    if (AL_BOTOX_MATERIAL_IDS.has(matId)) {
      // Botox: Era 1 → Amount as reported; Era 2+ → Qty × Moxie. Savings = $0 always.
      if (era === 'era1') {
        spend += amt;
      } else {
        spend += qty * (AL_BOTOX_MOXIE[alGetBotoxUnit(matId)] ?? 656.0);
      }

    } else {
      // Filler / ancillary — look up by Material ID
      const priceFromData = getPricesForDate(pricingEras, 'allergan', matId, date);
      const prices = priceFromData || AL_FILLER_PRICES[era]?.[matId];

      if (prices && prices.moxie != null) {
        // Known filler with explicit moxie/list prices
        if (era === 'era1') {
          spend   += amt;                                  // centralized billing
          savings += qty * (prices.list - prices.moxie);  // savings still apply
        } else {
          if (qty <= 0) continue;
          spend   += qty * prices.moxie;
          savings += qty * (prices.list - prices.moxie);
        }
      } else if (AL_SKM_BRANDS.some(b => dn.includes(b))) {
        // SkinMedica / DiamondGlow / Latisse / ancillary brands (category match)
        const sp = Math.abs(amt);
        spend += sp;
        if (era !== 'era1' && sp > 0) {
          const priceData = getPricesForDate(pricingEras, 'allergan', 'SkinMedica', date);
          const savingsPct = priceData?.savingsPct ?? AL_SKM_SAVINGS_PCT;
          savings += sp * savingsPct;
        }
      } else {
        // Unknown / accessories / equipment / credits
        // Skip negative-amount rows (returns/credits for out-of-program products)
        if (amt > 0) spend += amt;
      }
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}

// ─── EVOLUS ───────────────────────────────────────────────────────────────────
// Ported from lib/calc/evolus.js — no import/export, shared helpers from Galderma section.
// normalizeDate and round2 already defined above.

const EV_JEUVEAU_LIST  = 610;
const EV_JEUVEAU_MOXIE = 390;
const EV_EVOLYSSE_LIST  = 325;
const EV_EVOLYSSE_MOXIE = 160;

function calcEvolus(rows, evolusName, filter = {}, pricingEras = null, moxieId = null) {
  const normName = evolusName.trim();
  const normId   = moxieId != null ? String(moxieId) : null;

  let spend   = 0;
  let savings = 0;
  let count   = 0;

  for (const r of rows) {
    const facilityMatch = (r['Facility'] ?? '').trim() === normName;
    const idMatch       = normId != null && String(r._moxie_id ?? '') === normId;
    if (!facilityMatch && !idMatch) continue;

    const date = normalizeDate(r['Date'] ?? '');

    // Date filter
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    // ── Jeuveau ──────────────────────────────────────────────────────────
    const jQty = parseFloat(r['Jeaveau Vials']) || 0;  // NOTE: "Jeaveau" is the actual column name in data

    if (jQty > 0) {
      count++;
      const jPrices = getPricesForDate(pricingEras, 'evolus', 'Jeuveau', date);
      const jMoxie = jPrices?.moxie ?? EV_JEUVEAU_MOXIE;
      const jList  = jPrices?.list  ?? EV_JEUVEAU_LIST;
      spend   += jQty * jMoxie;
      savings += jQty * (jList - jMoxie);
    }

    // ── Evolysse ─────────────────────────────────────────────────────────
    const eQty = parseFloat(r['Evolysse Vials']) || 0;

    if (eQty > 0) {
      if (jQty === 0) count++; // only increment if Jeuveau didn't already count this row
      const ePrices = getPricesForDate(pricingEras, 'evolus', 'Evolysse', date);
      const eMoxie = ePrices?.moxie ?? EV_EVOLYSSE_MOXIE;
      const eList  = ePrices?.list  ?? EV_EVOLYSSE_LIST;
      spend   += eQty * eMoxie;
      savings += eQty * (eList - eMoxie);
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}

// ─── MERZ ─────────────────────────────────────────────────────────────────────
// Ported from lib/calc/merz.js — no import/export, shared helpers from Galderma section.
// normalizeDate, parseDollars, and round2 already defined above.

const XEOMIN_PRICES = {
  'Xeomin 100-U Vials': { list: 511.00, moxie: 255.50 },
  'Xeomin 50 U Vials':  { list: 268.00, moxie: 134.00 },
};

const STANDARD_PRICES = {
  'BELOTERO Balance 1.0cc US':                        { list: 285.00, moxie: 213.75 },
  'Belotero Balance Lido US 1x1.0ml':                { list: 329.00, moxie: 246.75 },
  'Belotero 1.0 Lido':                               { list: 329.00, moxie: 246.75 },
  'RADIESSE (Refresh) - 2 X 1.5cc Kit':              { list: 780.00, moxie: 468.00 },
  'RADIESSE (+) Lidocaine (Refresh) - 2 X 1.5cc Kit':{ list: 780.00, moxie: 468.00 },
};

const ULTHERAPY_PRICE = { list: 2340.00, moxie: 1989.00 };
const DESCRIBE_PRICE  = { list: 630.00,  moxie: 504.00  };

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

const XEOMIN_ERA2_START = '2025-01-01';

function isUltherapy(desc) {
  return desc.includes('Ultherapy Transducer') ||
    /^UT-[1-4]\b/.test(desc) || desc.startsWith('UT Transducer');
}

function isDescribe(desc) {
  return desc.includes('Describe') && desc.includes('Pack');
}

function calcMerz(rows, merzName, filter = {}, pricingEras = null, moxieId = null) {
  const normMoxieId = moxieId != null ? String(moxieId) : null;
  let spend   = 0;
  let savings = 0;
  let count   = 0;

  for (const r of rows) {
    const nameMatch    = merzName && (r['Ship_To_Name'] ?? '').trim() === merzName.trim();
    const moxieIdMatch = normMoxieId != null &&
      (String(r._moxie_id ?? '') === normMoxieId || Number(r._moxie_id) === Number(moxieId));
    if (!nameMatch && !moxieIdMatch) continue;

    const qty   = parseFloat(r['Billing_qty_in_SKU']) || 0;
    const gross = parseDollars(r['Gross_Value']);
    // Merz files vary the date header: 'Invoice_Date' (historical) vs 'Invoice Date'
    // (recent uploads). Fall back to FirstDayOfMonth when the invoice date is blank,
    // otherwise the row is silently skipped and its spend/savings never show.
    const date  = normalizeDate(r['Invoice_Date'] ?? r['Invoice Date'] ?? r['FirstDayOfMonth'] ?? '');
    const desc  = r['MaterialDescription'] ?? '';

    // Skip rows where Qty = 0 or Invoice_Date is missing/invalid
    if (qty === 0) continue;
    if (!date || date === 'undefined' || !/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;

    // Date filter
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    count++;

    // ── Xeomin (BOGO program) ─────────────────────────────────────────────
    if (XEOMIN_PRICES[desc]) {
      const priceFromData = getPricesForDate(pricingEras, 'merz', desc, date);
      const xData = priceFromData || {};
      const list = xData.list ?? XEOMIN_PRICES[desc].list;
      const rate = xData.bogoRate ?? (date >= XEOMIN_ERA2_START ? 1.0 : 0.8);
      spend   += qty * list;
      savings += qty * list * rate;

    // ── Standard priced products ──────────────────────────────────────────
    } else if (STANDARD_PRICES[desc]) {
      const priceFromData = getPricesForDate(pricingEras, 'merz', desc, date);
      const { list, moxie } = priceFromData || STANDARD_PRICES[desc];
      spend   += qty * moxie;
      savings += qty * (list - moxie);

    // ── Ultherapy transducers ─────────────────────────────────────────────
    } else if (isUltherapy(desc)) {
      const utPrices = getPricesForDate(pricingEras, 'merz', 'Ultherapy', date);
      const utData = utPrices || ULTHERAPY_PRICE;
      spend   += qty * utData.moxie;
      savings += qty * (utData.list - utData.moxie);

    // ── Describe packs ────────────────────────────────────────────────────
    } else if (isDescribe(desc)) {
      const dPrices = getPricesForDate(pricingEras, 'merz', 'Describe', date);
      const dData = dPrices || DESCRIBE_PRICE;
      spend   += qty * dData.moxie;
      savings += qty * (dData.list - dData.moxie);

    // ── Neocutis products ─────────────────────────────────────────────────
    } else if (NEOCUTIS_PRODUCTS.has(desc)) {
      if (gross > 0) {
        const neoPrices = getPricesForDate(pricingEras, 'merz', '__neocutis__', date);
        const neoPct = neoPrices?.savingsPct ?? 0.20;
        spend   += gross;
        savings += gross * neoPct;
      }
      // If Gross_Value = 0: skip (no spend, no savings)

    // ── Other (cannulas, UT variants, etc.) ───────────────────────────────
    } else {
      spend += gross;  // Savings = $0
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}

// ─── REVANCE ──────────────────────────────────────────────────────────────────
// Ported from lib/calc/revance.js — no import/export, shared helpers from Galderma section.
// normalizeDate, parseDollars, and round2 already defined above.

const SAVINGS_PRODUCTS = {
  'RHA2':      { list_price: 600 },
  'RHA3':      { list_price: 600 },
  'RHA4':      { list_price: 600 },
  'Redensity': { list_price: 600 },
  'Daxxify':   { list_price: 420 },
};

function calcRevance(rows, moxieId, filter = {}, pricingEras = null) {
  const id = String(moxieId).trim();
  let spend = 0, savings = 0, count = 0;

  for (const r of rows) {
    // Fall back to _moxie_id when Moxie Medspa ID is null (upload artifact on some rows).
    const rowId = (r['Moxie Medspa ID'] != null)
      ? String(r['Moxie Medspa ID']).trim()
      : String(r._moxie_id ?? '').trim();
    if (rowId !== id) continue;

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

    const priceFromData = getPricesForDate(pricingEras, 'revance', product, date);
    const listPrice = priceFromData?.list ?? SAVINGS_PRODUCTS[product]?.list_price;
    if (listPrice != null) {
      // Savings = list value − what medspa paid; floor at 0 if they paid above list
      savings += Math.max((qty * listPrice) - salesAmt, 0);
    }

    count++;
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}

// ── GENERIC VENDOR ─────────────────────────────────────────────────────────────
// Config-driven engine for any vendor added via the Add Vendor wizard.
// normalizeDate, parseDollars, round2, getPricesForDate already defined above.
function calcGeneric(vendorId, rows, medspaid, filter, pricingEras, vendorConfig) {
  filter = filter || {};
  const cfg = vendorConfig && vendorConfig.find(v => (v.id || v.vendor_id) === vendorId);
  if (!cfg) return { spend: 0, savings: 0, rows: 0 };

  const cols        = cfg.csv_columns || {
    join_col: cfg.csv_join_field,
    date_col: cfg.date_field,
    product_col: cfg.csv_product_field,
    quantity_col: cfg.csv_qty_field,
    amount_col: cfg.csv_amount_field
  };
  const formulaType = cfg.formula_type;
  const normId      = String(medspaid);

  let spend = 0, savings = 0, count = 0;

  for (const r of rows) {
    const joinVal = String(r[cols.join_col] ?? '');
    const idMatch = String(r._moxie_id ?? '') === normId;
    if (joinVal !== normId && !idMatch) continue;

    const date = normalizeDate(r[cols.date_col] ?? '');
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    const product = String(r[cols.product_col] ?? '');
    const qty     = parseFloat(r[cols.quantity_col]) || 0;
    const amount  = parseDollars(r[cols.amount_col]);
    const prices  = getPricesForDate(pricingEras, vendorId, product, date);

    count++;
    const effectiveFormula = formulaType === 'mixed'
      ? (prices?.formula_type ?? 'list_and_moxie')
      : formulaType;

    if (effectiveFormula === 'list_and_moxie') {
      if (!prices) { spend += amount; }
      else { spend += qty * prices.moxie; savings += qty * (prices.list - prices.moxie); }
    } else if (effectiveFormula === 'invoice_vs_list') {
      spend += amount;
      if (prices) savings += Math.max((qty * prices.list) - amount, 0);
    } else if (effectiveFormula === 'flat_pct') {
      spend += amount;
      if (prices) savings += amount * prices.savingsPct;
    } else if (effectiveFormula === 'volume_pricing') {
      if (!prices || !prices.tiers || !prices.tiers.length) {
        spend += amount;
      } else {
        const tier = prices.tiers.find(t => qty >= t.min && (t.max == null || qty <= t.max));
        if (!tier) { spend += amount; }
        else {
          spend += qty * tier.moxie;
          savings += qty * (prices.list - tier.moxie);
        }
      }
    } else {
      spend += amount;
    }
  }

  return { spend: round2(spend), savings: round2(savings), rows: count };
}
