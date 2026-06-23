// Evolus savings calculator — spec: specs/evolus.md (LOCKED)
// Join key: Facility === Evolus name from name_map.json (vendor: "evolus")
//
// DATA NOTE: The column in the JSON is "Jeaveau Vials" (not "Jeuveau" — typo in source data)
// The field keys used here match the actual JSON data exactly.

const JEUVEAU_LIST  = 610;  // list price per vial
const JEUVEAU_MOXIE = 390;  // Moxie price per vial upfront
const JEUVEAU_SAVINGS_PER_VIAL = JEUVEAU_LIST - JEUVEAU_MOXIE;  // $220

const EVOLYSSE_LIST  = 325; // list price per syringe
const EVOLYSSE_MOXIE = 160; // Moxie price per syringe ($150/unit in data)
const EVOLYSSE_SAVINGS_PER_VIAL = EVOLYSSE_LIST - EVOLYSSE_MOXIE;  // $165

function round2(n) {
  return Math.round(n * 100) / 100;
}

/**
 * Calculate Evolus spend, savings, and rebates for one medspa.
 *
 * @param {Array}  rows       - Full transactions_evolus JSON array
 * @param {string} evolusName - Evolus facility name from name_map.json
 * @param {object} [filter]   - Optional { startDate, endDate } ISO strings (inclusive)
 * @returns {{ spend: number, savings: number, rebates: number, rows: number }}
 */
export function calcEvolus(rows, evolusName, filter = {}) {
  let spend   = 0;
  let savings = 0;
  let rebates = 0;
  let count   = 0;

  for (const r of rows) {
    if ((r['Facility'] ?? '').trim() !== evolusName.trim()) continue;

    const date = r['Date'] ?? '';

    // Date filter
    if (filter.startDate && date < filter.startDate) continue;
    if (filter.endDate   && date > filter.endDate)   continue;

    // ── Jeuveau ──────────────────────────────────────────────────────────
    const jQty   = parseFloat(r['Jeaveau Vials']) || 0;  // NOTE: "Jeaveau" is the actual column name in data
    const jTotal = parseFloat(r['Total'])         || 0;  // actual charged amount (varies — do NOT use for spend)

    // Free/promo: only skip if Total is explicitly 0 (not null/blank — those are normal rows).
    //
    // DATA-DRIVEN DECISION (not stated in spec):
    //   - 1,103 rows have Total=null; many of these have Rebates>0, confirming they are real
    //     paid orders where the Total column was simply left blank in the source data.
    //   - 59 rows have Total=0 explicitly, but ALL of those rows also have Jeaveau Vials=null,
    //     so the promo filter below is effectively dead code for Jeuveau in this dataset.
    //   - Treating null/blank as non-promo matches the rebate evidence and avoids silently
    //     zeroing out legitimate spend.
    const jTotalIsExplicitZero = r['Total'] !== null && r['Total'] !== '' && r['Total'] !== undefined && jTotal === 0;

    if (jQty > 0) {
      if (jTotalIsExplicitZero) {
        // skip promo Jeuveau rows — do not count spend or savings
      } else {
        count++;
        // Spend = Qty × $390 (regardless of Total column)
        spend   += jQty * JEUVEAU_MOXIE;
        savings += jQty * JEUVEAU_SAVINGS_PER_VIAL;
      }
    }

    // ── Rebates (Jeuveau quarterly rebate) ───────────────────────────────
    // Already computed in the data as dollar amount (Qty × $30).
    // Tracked separately from direct savings.
    const rebateAmt = parseFloat(r['Rebates ($30/vial)']) || 0;
    if (rebateAmt > 0) rebates += rebateAmt;

    // ── Evolysse ─────────────────────────────────────────────────────────
    const eQty   = parseFloat(r['Evolysse Vials']) || 0;
    const eTotal = parseFloat(r['Evolysse Total']) || 0;  // spend = Evolysse Total (col 7)

    if (eQty > 0) {
      if (eTotal === 0) {
        // skip promo Evolysse rows
      } else {
        if (jQty === 0) count++; // only increment if Jeuveau didn't already count this row
        spend   += eTotal;
        savings += eQty * EVOLYSSE_SAVINGS_PER_VIAL;
      }
    }
  }

  return { spend: round2(spend), savings: round2(savings), rebates: round2(rebates), rows: count };
}
