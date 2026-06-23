import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { calcEvolus } from '../lib/calc/evolus.js';

const DATA_DIR = '/home/user/mydashboard-repo/data';

let rows;
let nameMap;
let citrusEvolusName;

beforeAll(() => {
  rows    = JSON.parse(readFileSync(resolve(DATA_DIR, 'transactions_evolus.json'), 'utf8'));
  nameMap = JSON.parse(readFileSync(resolve(DATA_DIR, 'name_map.json'), 'utf8'));

  // Find Citrus Aesthetics Evolus name from name_map — never hardcode
  // name_map.evolus is { evolusName → moxieId }; Citrus has moxieId = 6
  const evolusMap = nameMap.evolus ?? {};
  citrusEvolusName = Object.entries(evolusMap).find(([, id]) => id === 6)?.[0];
  if (!citrusEvolusName) throw new Error('Citrus Aesthetics (ID=6) not found in name_map.evolus');
});

describe('Evolus — Citrus Aesthetics (spec/evolus.md validation)', () => {
  it('Citrus Evolus name from name_map is "Citrus Aesthetics"', () => {
    expect(citrusEvolusName).toBe('Citrus Aesthetics');
  });

  it('All-time (through Feb 28 2026) Jeuveau qty = 40 vials', () => {
    const filtered = rows.filter(r =>
      r['Facility'] === 'Citrus Aesthetics' && (r['Date'] ?? '') <= '2026-02-28'
    );
    const qty = filtered.reduce((s, r) => s + (parseFloat(r['Jeaveau Vials']) || 0), 0);
    expect(qty).toBe(40);
  });

  it('All-time (through Feb 28 2026) Jeuveau spend = $15,600', () => {
    const result = calcEvolus(rows, citrusEvolusName, { endDate: '2026-02-28' });
    expect(result.spend).toBe(15600);
  });

  it('All-time (through Feb 28 2026) Jeuveau savings = $8,800', () => {
    const result = calcEvolus(rows, citrusEvolusName, { endDate: '2026-02-28' });
    expect(result.savings).toBe(8800);
  });

  it('All-time (through Feb 28 2026) rebates = $1,200', () => {
    const result = calcEvolus(rows, citrusEvolusName, { endDate: '2026-02-28' });
    expect(result.rebates).toBe(1200);
  });

  it('DATA INTEGRITY: Evolus column name in JSON is "Jeaveau Vials" (typo in source)', () => {
    // Confirm the actual field name in case someone tries to fix the "typo"
    const sample = rows[0];
    expect(Object.keys(sample)).toContain('Jeaveau Vials');
    expect(Object.keys(sample)).not.toContain('Jeuveau Vials');
  });
});
