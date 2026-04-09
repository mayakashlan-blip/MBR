import { describe, it, expect } from 'vitest';
import { calcRevance, getRevanceRate } from '../lib/calc/revance.js';

// NOTE: No transactions_revance.json exists yet — Revance data has not been uploaded.
// These tests verify the calc logic and rate rules using synthetic data.
// When real data is available, add integration tests matching spec validation targets:
//   Citrus Aesthetics (all-time through Feb 28 2026):
//     RHA2 Sales$=$9,110  → savings=$3,188.50 (35%)
//     RHA3 Sales$=$13,150 → savings=$4,602.50 (35%)
//     RHA4 Sales$=$15,880 → savings=$5,558.00 (35%)
//     Total RHA savings = $13,349
//     Daxxify spend = $10,500 ($0 savings)
//     Redensity spend = $1,560 ($0 savings)
//     Total all-time spend = $50,200

describe('Revance — rate rules', () => {
  it('Default rate = 35%', () => {
    expect(getRevanceRate('Citrus Aesthetics')).toBe(0.35);
    expect(getRevanceRate('Some Random Medspa')).toBe(0.35);
  });

  it('Contractual override rate = 22% for 4 specific medspas', () => {
    expect(getRevanceRate('Allure Med Spa')).toBe(0.22);
    expect(getRevanceRate('Lifted Aesthetics Firm')).toBe(0.22);
    expect(getRevanceRate('The Beautox Lounge')).toBe(0.22);
    expect(getRevanceRate('The Method Aesthetics')).toBe(0.22);
  });
});

describe('Revance — calc logic (synthetic data)', () => {
  const rows = [
    { 'Medspa Name': 'Test Spa', 'Date': '2025-06-01', 'Product': 'RHA3',     'Sales$': '1000', 'Boxes/Vials': '5' },
    { 'Medspa Name': 'Test Spa', 'Date': '2025-06-15', 'Product': 'Daxxify',  'Sales$': '2000', 'Boxes/Vials': '10' },
    { 'Medspa Name': 'Test Spa', 'Date': '2025-07-01', 'Product': 'RHA2',     'Sales$': '500',  'Boxes/Vials': '0' },  // monthly billing row (Boxes/Vials=0)
    { 'Medspa Name': 'Test Spa', 'Date': '2025-07-20', 'Product': 'SkinPen',  'Sales$': '300',  'Boxes/Vials': '1' },
    { 'Medspa Name': 'Other',    'Date': '2025-06-01', 'Product': 'RHA4',     'Sales$': '999',  'Boxes/Vials': '3' },
  ];

  it('Spend = Sales$ for ALL products (including Boxes/Vials=0 rows)', () => {
    const result = calcRevance(rows, 'Test Spa');
    expect(result.spend).toBe(3800);  // 1000 + 2000 + 500 + 300
  });

  it('Savings only on RHA2, RHA3, RHA4 at 35%', () => {
    const result = calcRevance(rows, 'Test Spa');
    // RHA3=$1000×0.35=$350, RHA2=$500×0.35=$175
    expect(result.savings).toBeCloseTo(525, 2);
  });

  it('Daxxify, SkinPen: $0 savings', () => {
    // Confirmed by total: $3800 spend, $525 savings (not $3800 × 0.35 = $1330)
    const result = calcRevance(rows, 'Test Spa');
    expect(result.savings).toBe(525);
  });

  it('22% override applies to contractual medspas', () => {
    const override_rows = [
      { 'Medspa Name': 'Allure Med Spa', 'Date': '2025-06-01', 'Product': 'RHA3', 'Sales$': '1000', 'Boxes/Vials': '5' },
    ];
    const result = calcRevance(override_rows, 'Allure Med Spa');
    expect(result.savings).toBeCloseTo(220, 2);  // $1000 × 22%
  });

  it('Date filter works', () => {
    const result = calcRevance(rows, 'Test Spa', { startDate: '2025-07-01' });
    expect(result.spend).toBe(800);   // 500 + 300
    expect(result.savings).toBe(175); // RHA2: 500 × 0.35
  });

  it('Row count excludes other medspas', () => {
    const result = calcRevance(rows, 'Test Spa');
    expect(result.rows).toBe(4);
  });
});
