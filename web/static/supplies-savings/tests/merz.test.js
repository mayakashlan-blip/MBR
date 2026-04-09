import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';
import { calcMerz } from '../lib/calc/merz.js';

const DATA_DIR = '/home/user/mydashboard-repo/data';

let rows;
let nameMap;
let beautyHavenName;

beforeAll(() => {
  rows    = JSON.parse(readFileSync(resolve(DATA_DIR, 'transactions_merz.json'), 'utf8'));
  nameMap = JSON.parse(readFileSync(resolve(DATA_DIR, 'name_map.json'), 'utf8'));

  // Find Beauty Haven in name_map — never hardcode the name
  // name_map.merz is { merzName → moxieId }; Beauty Haven has moxieId = 153
  const merzMap = nameMap.merz ?? {};
  beautyHavenName = Object.entries(merzMap).find(([, id]) => id === 153)?.[0];
  if (!beautyHavenName) throw new Error('Beauty Haven (ID=153) not found in name_map.merz');
});

describe('Merz — Beauty Haven (spec/merz.md validation)', () => {
  it('Beauty Haven Merz name from name_map is "The Beauty Haven Medspa"', () => {
    expect(beautyHavenName).toBe('The Beauty Haven Medspa');
  });

  it('All-time spend = $15,841', () => {
    const result = calcMerz(rows, beautyHavenName);
    expect(result.spend).toBe(15841);
  });

  it('All-time savings ≈ $12,673 (Xeomin BOGO at 80% pre-2025)', () => {
    const result = calcMerz(rows, beautyHavenName);
    // Spec: $12,673 (rounded from $12,672.80)
    expect(result.savings).toBeCloseTo(12672.80, 1);
  });

  it('Xeomin spend = Qty × $511 list price (not Gross_Value)', () => {
    // All Beauty Haven rows are Xeomin 100-U
    // Gross_Value in rows varies ($2,480 for early rows, not $511×qty)
    // But spec mandates Qty × $511 hardcoded
    const result = calcMerz(rows, beautyHavenName);
    // 5+10+10+3+3 = 31 vials × $511 = $15,841
    expect(result.spend).toBe(31 * 511);
  });

  it('Xeomin pre-2025 savings rate = 80%', () => {
    // All Beauty Haven rows are pre-2025 → rate = 80%
    // savings = 31 × $511 × 0.80 = $12,672.80
    const result = calcMerz(rows, beautyHavenName);
    expect(result.savings).toBe(12672.80);
  });
});
