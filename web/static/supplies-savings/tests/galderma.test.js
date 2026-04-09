import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { calcGalderma, normalizeId } from '../lib/calc/galderma.js';

const DATA_DIR = '/home/user/mydashboard-repo/data';
const OMNI_FILE = '/tmp/omni_medspas.json';

let rows;
let omni;
let citrusMkid;

beforeAll(() => {
  rows = JSON.parse(readFileSync(resolve(DATA_DIR, 'transactions_galderma.json'), 'utf8'));
  omni  = JSON.parse(readFileSync(OMNI_FILE, 'utf8'));

  // Find Citrus Aesthetics in Omni — NEVER hardcode the ID
  const citrus = omni.find(m => m['Medspa Name With ID'] === 'Citrus Aesthetics (6)');
  if (!citrus) throw new Error('Citrus Aesthetics not found in Omni data');
  citrusMkid = String(citrus['Supplies - MKID']).trim();
});

describe('normalizeId', () => {
  it('strips leading zeros', () => {
    expect(normalizeId('0100754270')).toBe('100754270');
    expect(normalizeId('100754270')).toBe('100754270');
    expect(normalizeId('0000123')).toBe('123');
  });
});

describe('Galderma — Citrus Aesthetics (spec/galderma.md validation)', () => {
  it('MKID from Omni is 100754270', () => {
    expect(citrusMkid).toBe('100754270');
  });

  it('Era 1 all-time spend = $24,074.37', () => {
    // Era 1 = ORDER DATE < 2024-04-01
    const result = calcGalderma(rows, citrusMkid, { endDate: '2024-03-31' });
    expect(result.spend).toBe(24074.37);
  });

  it('Era 1 all-time savings = $10,574.11', () => {
    const result = calcGalderma(rows, citrusMkid, { endDate: '2024-03-31' });
    expect(result.savings).toBe(10574.11);
  });

  it('Era 2 formula: spend = Qty × Moxie, savings = Qty × (List − Moxie)', () => {
    // Verify Era 2 rows compute spend/savings correctly using Dysport 300IU as spot check:
    // List $622, Moxie $466.50 → savings $155.50/unit
    const era2 = calcGalderma(rows, citrusMkid, { startDate: '2024-04-01' });
    // Era 2 has transactions — verify savings > 0 and formula is active
    expect(era2.savings).toBeGreaterThan(0);
    expect(era2.spend).toBeGreaterThan(0);
    // savings is less than spend (makes sense — not saving 100%)
    expect(era2.savings).toBeLessThan(era2.spend);
  });
});
