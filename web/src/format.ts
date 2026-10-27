import type { Json } from './types'

// Keep currency arithmetic in decimal text / integer cents, including values above 2^53.
export function aed(value: string | null | undefined): string {
  if (value == null || !/^-?\d+(\.\d+)?$/.test(value)) return 'Unavailable'
  const negative = value.startsWith('-')
  const [whole, fraction = ''] = value.replace(/^-/, '').split('.')
  let cents = BigInt(whole) * 100n + BigInt((fraction + '00').slice(0, 2))
  if (Number(fraction[2] || '0') >= 5) cents += 1n
  const amount = (cents / 100n).toLocaleString('en-US') + '.' + String(cents % 100n).padStart(2, '0')
  return `AED ${negative && cents ? '-' : ''}${amount}`
}

export function day(value: string | null | undefined): string {
  if (!value) return 'Unavailable'
  const parsed = new Date(value.length === 10 ? value + 'T12:00:00Z' : value)
  return Number.isNaN(parsed.valueOf()) ? 'Unavailable' : new Intl.DateTimeFormat('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Dubai',
  }).format(parsed)
}

export function weekNumber(value: string): number {
  const date = new Date(value + 'T12:00:00Z')
  date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7))
  const start = new Date(Date.UTC(date.getUTCFullYear(), 0, 1))
  return Math.ceil(((date.valueOf() - start.valueOf()) / 86400000 + 1) / 7)
}

export function weekLabel(start: string, end?: string): string {
  return `Week ${weekNumber(start)} · ${day(start)}${end ? ' – ' + day(end) : ''}`
}

const labels: Record<string, string> = {
  COMMISSION_OVER_CONTRACT: 'Commission above contract', PAYOUT_SHORT: 'Payout short',
  REFUND_NOT_IN_POS: 'Refund missing from POS', POS_LEDGER_MISMATCH: 'POS and ledger disagree',
  LEDGER_BILL_MISSING: 'Bill missing from ledger', DUPLICATE_INVOICE: 'Duplicate invoice',
  VAT_WRONG: 'VAT not 5%', LINES_NOT_SUM: "Lines don't add up", TRN_INVALID: 'Invalid TRN',
  TRN_SHARED: 'TRN shared by two suppliers', CREDIT_NOTE_UNLINKED: 'Credit note without original',
  MISSING_FIELD: 'Missing mandatory field', accepted: 'Accepted', review: 'Needs review',
  document_problem: 'Document problem', dev: 'Development layouts', held_out: 'Unseen layouts (held out)',
  payouts: 'Payout statements', agent: 'LLM agent', rules: 'Rules-only baseline',
  auto_match: 'Automatic match', not_run: 'Not recorded yet', 'not run': 'Not recorded yet', saved_results: 'Replayed from recordings',
  accepted_but_wrong: 'Accepted with a wording error', accepted_but_wrong_rate: 'Wording error rate among accepted documents',
  accepted_with_wrong_amount: 'Accepted with a wrong amount', accepted_scored: 'Accepted documents scored',
  names_holdout: 'Reviewer-written unseen names', true_positives: 'Correct merges', false_positives: 'Wrong merges',
  false_negatives: 'Missed matches', f1: 'F1 score', field_accuracy: 'Fields read correctly',
  line_accuracy: 'Line items read correctly', review_share: 'Share needing review',
  auto_accepted_document: 'Auto accepted document', review_document_overhead: 'Review document overhead',
  outlet_day_reconciled: 'Outlet day reconciled', draft_produced: 'Draft produced',
  in_scope: 'In-scope questions', out_of_scope: 'Out-of-scope requests',
  supplier_trn: 'Supplier TRN', buyer_trn: 'Buyer TRN', vat: 'VAT', vat_rate: 'VAT rate',
  net: 'Net amount', total: 'Total', unit_price: 'Unit price', line_net: 'Line net amount',
}

/** One vocabulary for machine codes, including future codes with a readable fallback. */
export function label(value: string): string {
  return labels[value] ?? value.replaceAll('_', ' ').replace(/\.(\d+)\./g, (_, n: string) => ` ${Number(n) + 1} · `).replace(/^./, char => char.toUpperCase())
}

export function percent(value: Json | undefined): string {
  return value == null || !Number.isFinite(Number(value)) ? '—' : `${(Number(value) * 100).toFixed(1)}%`
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return 'Unavailable'
  // Stored naive pipeline timestamps use Dubai local time.
  const parsed = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}+04:00`)
  return Number.isNaN(parsed.valueOf()) ? 'Unavailable' : new Intl.DateTimeFormat('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
    hourCycle: 'h23', timeZone: 'Asia/Dubai',
  }).format(parsed)
}

export function readable(value: string): string {
  return value.replace(/\b\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d\d:\d\d)?/g, dateTime)
    .replace(/\b\d{4}-\d{2}-\d{2}\b/g, day)
    .replace(/\b[a-zA-Z]+_[a-zA-Z_]+\b/g, label)
}

export function display(value: Json | undefined): string {
  if (value == null) return 'Unavailable'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString('en-GB') : value.toFixed(2)
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : labels[String(value)] ?? readable(String(value))
}

export function cell(value: Json | undefined, key: string, row: Record<string, Json> = {}): string {
  // Invoice VAT is stored in percentage points; evaluation rates are fractions.
  if (key === 'vat_rate' || key.endsWith('_percent')) return percent(value == null ? null : Number(value) / 100)
  if (/(accuracy|_rate|_share|precision|recall|^f1)$/.test(key)) {
    const counts: Record<string, [string, string]> = {
      field_accuracy: ['fields_correct', 'fields_total'], line_accuracy: ['lines_correct', 'lines_total'],
      accepted_but_wrong_rate: ['accepted_but_wrong', 'accepted_scored'], review_share: ['review', 'documents'],
      precision: ['true_positives', 'false_positives'], recall: ['true_positives', 'false_negatives'],
      accuracy: ['correct', 'run'], review_suggestion_accuracy: ['review_suggestion_correct', 'review_items'],
    }
    if (key === 'review_share' && row.review_items != null && row.variants != null) return `${percent(value)} (${row.review_items} / ${row.variants})`
    if (key === 'f1' && row.true_positives != null && row.false_positives != null && row.false_negatives != null) {
      const numerator = 2 * Number(row.true_positives)
      return `${percent(value)} (${numerator} / ${numerator + Number(row.false_positives) + Number(row.false_negatives)})`
    }
    const pair = counts[key]
    if (pair && row[pair[0]] != null && row[pair[1]] != null) {
      const denominator = key === 'precision' || key === 'recall' ? Number(row[pair[0]]) + Number(row[pair[1]]) : row[pair[1]]
      return `${percent(value)} (${row[pair[0]]} / ${denominator})`
    }
    return percent(value)
  }
  if (/(^|_)(net|vat|total|amount|price|paid|payout)$/.test(key) && typeof value === 'string') return aed(value)
  return display(value)
}
