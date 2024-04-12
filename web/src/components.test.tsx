import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { Badge, DataTable, FilterSelect, HoursCalculator, LoadState, SourceText, quoteRange, savedHours } from './components'
import { CitedAnswer } from './pages/Ask'
import KeyResults from './pages/KeyResults'
import type { HoursData } from './types'
import { useState } from 'react'

const hours: HoursData = {
  assumptions: [{ name: 'auto_accepted_document', minutes: '3', basis: 'assumption' }, { name: 'review_document_overhead', minutes: '1', basis: 'assumption' }],
  counts: { auto_accepted_document: 100, review_document_overhead: 30 },
  full_pipeline_duration_ms: 60000, estimated_hours_saved: '4.48', formula: '(manual minutes - duration) / 60', label: 'Estimate', duration_state: 'measured',
}

it('highlights an original source span after case, comma and whitespace normalisation', () => {
  render(<SourceText text={'Total AED 1,234.50\nSupplier  Example'} quote={'total aed 1234.50'} />)
  expect(document.querySelector('mark')?.textContent).toBe('Total AED 1,234.50')
  expect(quoteRange('Supplier  Example', 'supplier example')).toEqual([0, 17])
  expect(quoteRange('source', 'absent')).toBeNull()
  expect(quoteRange('source', '')).toBeNull()
})

it('renders hostile source text as text', () => {
  render(<SourceText text={'<script>alert(1)</script>'} quote={'alert(1)'} />)
  expect(document.querySelector('script')).toBeNull()
  expect(document.querySelector('mark')?.textContent).toBe('alert(1)')
})

it('changes assumptions locally, subtracts review overhead and runtime, and resets', () => {
  render(<HoursCalculator data={hours} />)
  expect(screen.getByLabelText('Estimated hours saved').textContent).toBe('4.48')
  fireEvent.change(screen.getByLabelText(/Auto accepted document/), { target: { value: '6' } })
  expect(screen.getByLabelText('Estimated hours saved').textContent).toBe('9.48')
  fireEvent.click(screen.getByText('Reset assumptions'))
  expect(screen.getByLabelText('Estimated hours saved').textContent).toBe('4.48')
  expect(savedHours({ ...hours, counts: { review_document_overhead: 60 }, full_pipeline_duration_ms: 0 }, { review_document_overhead: '1' })).toBe('-1.00')
})

it('links each displayed number including words to its cited tool result', () => {
  render(<CitedAnswer id="q" result={{ answer: 'AED 1,234.50 from two orders.', refused: false, reason: '', not_run: false, tool_calls: [], numbers_used: [{ tool_call_id: 'call_1', value: '1234.50' }, { tool_call_id: 'call_2', value: '2' }] }} />)
  expect(screen.getByRole('link', { name: '1,234.50' }).getAttribute('href')).toBe('#q-call_1')
  expect(screen.getByRole('link', { name: 'two' }).getAttribute('href')).toBe('#q-call_2')
})

it('labels statuses, renders loading skeletons and expands structured evidence', () => {
  render(<><Badge value="review" /><LoadState /><DataTable title="Evidence" rows={[{ kind: 'VAT_WRONG', details: { supplier_name: 'Example' } }]} /></>)
  expect(screen.getByText('Needs review').getAttribute('data-variant')).toBe('review')
  expect(screen.getByRole('status').getAttribute('aria-label')).toBe('Loading snapshot')
  expect(document.querySelector('[data-slot="skeleton"]')).not.toBeNull()
  expect(screen.getByText('VAT not 5%')).not.toBeNull()
  expect(screen.queryByText('Example')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'View details' }))
  expect(screen.getByText('Example')).not.toBeNull()
})

it('computes key results from supplied scores and preserves missing wrong-amount counts', () => {
  const split = { field_accuracy: 0.75, fields_correct: 3, fields_total: 4, line_accuracy: 0.5, lines_correct: 1, lines_total: 2, accepted_scored: 7, accepted_but_wrong: 2 }
  const data = { extraction: { summary: { agent: { held_out: split, dev: { accepted_scored: 11, accepted_but_wrong: 1 } }, rules: { held_out: { ...split, field_accuracy: 0.25 } } } }, reconciliation: { totals: { found: 4, missed: 2, not_run: 1, false_alarms: 3 } }, ask: { in_scope: { total: 30, 'not run': 30 }, out_of_scope: { total: 5, 'not run': 5 } } }
  const view = render(<KeyResults data={data} />)
  expect(screen.getAllByTestId('key-result')).toHaveLength(7)
  expect(screen.getByText(/Unseen invoice layouts: 75.0% of fields, 50.0%/)).not.toBeNull()
  expect(screen.getByText(/wrong amount: — of 18 accepted/)).not.toBeNull()
  expect(screen.getByText(/wording error: 3 of 18/)).not.toBeNull()
  expect(screen.getByText(/Planted problems found: 4 of 7, with 3 false alarms/)).not.toBeNull()
  expect(screen.getByText(/Questions answered correctly: not recorded yet/)).not.toBeNull()
  view.rerender(<KeyResults data={{ ...data, extraction: { summary: { agent: { held_out: { ...split, accepted_with_wrong_amount: 2 }, dev: { accepted_scored: 11, accepted_with_wrong_amount: 1 } } } } }} />)
  expect(screen.getByText(/wrong amount: 3 of 18 accepted/)).not.toBeNull()
})

it('supports select keyboard navigation, selection, escape and outside clicks without style tags', () => {
  function Filter() {
    const [value, setValue] = useState('')
    return <FilterSelect title="Status" value={value} onChange={setValue} options={[["accepted", "Accepted"], ["review", "Needs review"]]} />
  }
  render(<Filter />)
  const trigger = screen.getByRole('combobox', { name: 'Status' })
  fireEvent.keyDown(trigger, { key: 'ArrowDown' })
  expect(screen.getByRole('option', { name: 'All' })).toBe(document.activeElement)
  fireEvent.keyDown(document.activeElement!, { key: 'ArrowDown' })
  expect(screen.getByRole('option', { name: 'Accepted' })).toBe(document.activeElement)
  fireEvent.keyDown(document.activeElement!, { key: 'End' })
  fireEvent.click(document.activeElement!)
  expect(trigger.textContent).toBe('Needs review')
  expect(trigger.getAttribute('aria-expanded')).toBe('false')
  expect(document.activeElement).toBe(trigger)
  fireEvent.click(trigger)
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
  expect(trigger.getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(trigger)
  fireEvent.pointerDown(document.body)
  expect(trigger.getAttribute('aria-expanded')).toBe('false')
  expect(document.querySelector('style')).toBeNull()
})
