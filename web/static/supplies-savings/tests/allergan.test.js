import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { calcAllergan, normalizeAllerganId } from '../lib/calc/allergan.js';

// Join field: Sold-to # (per Allergan guidance — confirmed customer identifier)
// Omni's "Supplies - Allergan ID" field is being updated to store Sold-to IDs.
// Validation medspa: Étoile Aesthetics SD (948), whose Omni ID is already the correct Sold-to ID.
//
// NOTE: La Miel Aesthetics (44) was the original spec validation target ($93,945.79 all-time).
// La Miel's Omni ID (59625695) was a Ship-to ID; its Sold-to ID is 59625696.
// Once Omni updates (~24h), calcAllergan(rows, '59625696') will also produce $93,945.79.

const DATA_DIR = '/home/user/mydashboard-repo/data';
const OMNI_FILE = '/tmp/omni_medspas.json';

let rows;
let omni;
let etoileAllerganId;

beforeAll(() => {
  rows = JSON.parse(readFileSync(resolve(DATA_DIR, 'transactions_allergan.json'), 'utf8'));
  omni  = JSON.parse(readFileSync(OMNI_FILE, 'utf8'));

  // Find Étoile Aesthetics SD in Omni — NEVER hardcode the ID
  const etoile = omni.find(m => m['Medspa Name With ID'] === 'Étoile Aesthetics SD (948)');
  if (!etoile) throw new Error('Étoile Aesthetics SD not found in Omni data');
  etoileAllerganId = String(etoile['Supplies - Allergan ID']).trim();
});

describe('normalizeAllerganId', () => {
  it('strips .0 suffix from transaction data format', () => {
    expect(normalizeAllerganId('59625695.0')).toBe('59625695');
    expect(normalizeAllerganId('59625695')).toBe('59625695');
    expect(normalizeAllerganId('59940816.0')).toBe('59940816');
  });
});

describe('Allergan — Étoile Aesthetics SD (948)', () => {
  it('Allergan ID from Omni is 59940816 (Sold-to #)', () => {
    expect(etoileAllerganId).toBe('59940816');
  });

  it('All-time spend = $64,483.88', () => {
    const result = calcAllergan(rows, etoileAllerganId);
    expect(result.spend).toBe(64483.88);
  });

  it('All-time savings = $1,792.44', () => {
    const result = calcAllergan(rows, etoileAllerganId);
    expect(result.savings).toBe(1792.44);
  });

  it('All-time row count = 32', () => {
    const result = calcAllergan(rows, etoileAllerganId);
    expect(result.rows).toBe(32);
  });

  it('3M spend = $6,560 (Dec 2025 – Feb 2026)', () => {
    const result = calcAllergan(rows, etoileAllerganId, {
      startDate: '2025-12-01',
      endDate:   '2026-02-28',
    });
    expect(result.spend).toBe(6560);
  });
});
