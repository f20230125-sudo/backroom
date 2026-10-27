import { describe, expect, it } from 'vitest'
import { aed, cell, dateTime, day, display, label, percent, weekLabel, weekNumber } from './format'

describe('AED formatting with decimal strings', () => {
  it('groups, pads, rounds half up and preserves large values', () => {
    expect(aed('1234.5')).toBe('AED 1,234.50')
    expect(aed('-1234.565')).toBe('AED -1,234.57')
    expect(aed('999.999')).toBe('AED 1,000.00')
    expect(aed('9007199254740993.01')).toBe('AED 9,007,199,254,740,993.01')
    expect(aed(null)).toBe('Unavailable')
  })
})

it('formats dates in Dubai and distinguishes unavailable dates', () => {
  expect(day('2026-07-01')).toBe('1 Jul 2026')
  expect(day('2026-07-01T22:00:00Z')).toBe('2 Jul 2026')
  expect(day('bad')).toBe('Unavailable')
})

it('uses ISO weeks across month and year boundaries', () => {
  expect(weekNumber('2026-08-17')).toBe(34)
  expect(weekNumber('2027-01-01')).toBe(53)
  expect(weekLabel('2026-08-31', '2026-09-06')).toBe('Week 36 · 31 Aug 2026 – 6 Sept 2026')
})

it('uses the owner vocabulary and readable fallback labels', () => {
  const expected = {
    COMMISSION_OVER_CONTRACT: 'Commission above contract', PAYOUT_SHORT: 'Payout short',
    REFUND_NOT_IN_POS: 'Refund missing from POS', POS_LEDGER_MISMATCH: 'POS and ledger disagree',
    LEDGER_BILL_MISSING: 'Bill missing from ledger', DUPLICATE_INVOICE: 'Duplicate invoice',
    VAT_WRONG: 'VAT not 5%', LINES_NOT_SUM: "Lines don't add up", TRN_INVALID: 'Invalid TRN',
    TRN_SHARED: 'TRN shared by two suppliers', CREDIT_NOTE_UNLINKED: 'Credit note without original',
    MISSING_FIELD: 'Missing mandatory field', accepted: 'Accepted', review: 'Needs review',
    document_problem: 'Document problem', held_out: 'Unseen layouts (held out)',
    dev: 'Development layouts', payouts: 'Payout statements', agent: 'LLM agent', rules: 'Rules-only baseline',
  }
  for (const [code, words] of Object.entries(expected)) expect(label(code)).toBe(words)
  expect(label('future_machine_code')).toBe('Future machine code')
  expect(display('Missing supplier_name on 2026-10-27')).toBe('Missing Supplier name on 27 Oct 2026')
})

it('formats percentages with one decimal and shows their denominators', () => {
  expect(percent(415 / 416)).toBe('99.8%')
  expect(percent(0)).toBe('0.0%')
  expect(percent(1)).toBe('100.0%')
  expect(percent(null)).toBe('—')
  expect(percent(undefined)).toBe('—')
  expect(cell(415 / 416, 'field_accuracy', { fields_correct: 415, fields_total: 416 })).toBe('99.8% (415 / 416)')
  expect(cell(0.75, 'precision', { true_positives: 3, false_positives: 1 })).toBe('75.0% (3 / 4)')
  expect(cell('5', 'vat_rate')).toBe('5.0%')
  expect(cell('37.25', 'food_cost_percent')).toBe('37.3%')
  expect(cell('DU', 'supplier_address_emirate')).toBe('DU')
})

it('formats UTC and local timestamps in Dubai with date and time', () => {
  expect(dateTime('2026-10-27T12:02:00Z')).toBe('27 Oct 2026, 16:02')
  expect(dateTime('2026-10-27T16:02:00')).toBe('27 Oct 2026, 16:02')
  expect(dateTime('bad')).toBe('Unavailable')
  expect(dateTime(null)).toBe('Unavailable')
})
