import type { Json, Row } from '../types'
import { display, percent } from '../format'
import { Panel } from '../components'

export function object(value: Json | undefined): Row {
  return value && !Array.isArray(value) && typeof value === 'object' ? value : {}
}

function count(value: Json | undefined): string { return value == null ? '—' : display(value) }

/** Preserve missing measurements instead of turning an absent key into a perfect score. */
function sum(rows: Row[], key: string): number | null {
  return rows.length && rows.every(row => typeof row[key] === 'number') ? rows.reduce((n, row) => n + Number(row[key]), 0) : null
}

export default function KeyResults({ data }: { data: Row }) {
  const extraction = object(data.extraction), summary = object(extraction.summary)
  const agent = object(summary.agent), held = object(agent.held_out), baseline = object(object(summary.rules).held_out)
  const splits = Object.values(agent).map(object)
  const wrong = sum(splits, 'accepted_with_wrong_amount'), accepted = sum(splits, 'accepted_scored')
  const wording = sum(splits, 'accepted_but_wrong')
  const recon = object(object(data.reconciliation).totals)
  const problems = ['found', 'missed', 'not_run'].every(k => typeof recon[k] === 'number') ? Number(recon.found) + Number(recon.missed) + Number(recon.not_run) : null
  const names = object(data.names), holdout = object(data.names_holdout)
  const ask = object(data.ask), inside = object(ask.in_scope), outside = object(ask.out_of_scope)
  const recorded = (row: Row) => row.total != null && Number(row.total) > Number(row['not run'] ?? row.not_run ?? 0)
  const security = Array.isArray(data.security) ? data.security.map(object) : []
  const passed = security.filter(row => row.flagged === true && row.unchanged === true && row.passed === true).length
  const hours = object(data.hours_saved), counts = object(hours.counts)
  return <section aria-labelledby="key-results-title"><h2 id="key-results-title">Key results</h2><div className="grid min-w-0 gap-x-4 md:grid-cols-2 xl:grid-cols-3">
    <Panel data-testid="key-result"><h3 className="mt-0">Unseen invoice layouts</h3><p className="text-3xl font-semibold tabular-nums">{percent(held.field_accuracy)}</p><p>Unseen invoice layouts: {percent(held.field_accuracy)} of fields, {percent(held.line_accuracy)} of line items read correctly (rules-only baseline: {percent(baseline.field_accuracy)} / {percent(baseline.line_accuracy)}).</p><p className="text-xs text-muted-foreground">Fields: {count(held.fields_correct)} / {count(held.fields_total)}; line items: {count(held.lines_correct)} / {count(held.lines_total)}. Baseline: {count(baseline.fields_correct)} / {count(baseline.fields_total)} fields; {count(baseline.lines_correct)} / {count(baseline.lines_total)} line items.</p></Panel>
    <Panel data-testid="key-result"><h3 className="mt-0">Accepted documents with a wrong amount</h3><p className="text-3xl font-semibold tabular-nums">{count(wrong)} <span className="text-base font-normal">of {count(accepted)}</span></p><p>Accepted documents with a wrong amount: {count(wrong)} of {count(accepted)} accepted documents scored across all splits.</p><p className="text-xs text-muted-foreground">Accepted with a wording error: {count(wording)} of {count(accepted)}. This is the evaluation’s broader “accepted but wrong” measure.</p></Panel>
    <Panel data-testid="key-result"><h3 className="mt-0">Planted problems found</h3><p className="text-3xl font-semibold tabular-nums">{count(recon.found)} <span className="text-base font-normal">of {count(problems)}</span></p><p>Planted problems found: {count(recon.found)} of {count(problems)}, with {count(recon.false_alarms)} false alarms.</p><p className="text-xs text-muted-foreground">{count(recon.missed)} missed; {count(recon.not_run)} not evaluated.</p></Panel>
    <Panel data-testid="key-result"><h3 className="mt-0">Name matching</h3><p className="text-3xl font-semibold tabular-nums">{percent(names.precision)}</p><p>Name matching: precision {percent(names.precision)}, recall {percent(names.recall)}; on reviewer-written unseen names: {count(holdout.auto_matched_correctly)} of {count(holdout.variants)} correct automatic matches, {count(holdout.wrong_merges)} wrong merges, {count(holdout.traps_merged)} of {count(holdout.traps)} look-alike traps merged.</p><p className="text-xs text-muted-foreground">Precision: {count(names.true_positives)} / {names.true_positives != null && names.false_positives != null ? Number(names.true_positives) + Number(names.false_positives) : '—'}; recall: {count(names.true_positives)} / {names.true_positives != null && names.false_negatives != null ? Number(names.true_positives) + Number(names.false_negatives) : '—'}. Unseen names left for review: {count(holdout.left_for_review)}; correct review suggestions: {count(holdout.review_suggestion_correct)}.</p></Panel>
    <Panel data-testid="key-result"><h3 className="mt-0">Questions answered correctly</h3><p className="text-3xl font-semibold tabular-nums">{recorded(inside) ? `${count(inside.correct)} / ${count(inside.total)}` : '—'}</p><p>Questions answered correctly: {recorded(inside) ? `${count(inside.correct)} of ${count(inside.total)}` : 'not recorded yet'}; out-of-scope requests refused: {recorded(outside) ? `${count(outside.refused)} of ${count(outside.total)}` : 'not recorded yet'}.</p><p className="text-xs text-muted-foreground">In scope: {count(inside.refused)} refused, {count(inside.wrong)} wrong, {count(inside['not run'] ?? inside.not_run)} not recorded yet.</p></Panel>
    <Panel data-testid="key-result"><h3 className="mt-0">Prompt-injection documents</h3><p className="text-3xl font-semibold tabular-nums">{security.length ? passed : '—'} <span className="text-base font-normal">of {security.length || '—'}</span></p><p>Prompt-injection documents flagged without effect: {security.length ? passed : '—'} of {security.length || '—'}.</p><p className="text-xs text-muted-foreground">Requires a flag, unchanged extracted facts and status, and a passing security check.</p></Panel>
    <Panel data-testid="key-result"><h3 className="mt-0">Estimated hours saved</h3><p className="text-3xl font-semibold tabular-nums">{count(hours.estimated_hours_saved)}</p><p>Estimated hours saved: {count(hours.estimated_hours_saved)} (an estimate from stated assumptions).</p><p className="text-xs text-muted-foreground">Based on {count(counts.auto_accepted_document)} accepted documents, {count(counts.review_document_overhead)} reviews, {count(counts.outlet_day_reconciled)} outlet days and {count(counts.draft_produced)} drafts. Assumptions and runtime appear below.</p></Panel>
  </div></section>
}
