import { DataTable, JsonReport, LazyDetails, LoadState, PageTitle, Panel } from '../components'
import { useApi } from '../api'
import { dateTime, label } from '../format'
import KeyResults from './KeyResults'
import type { Json, Row } from '../types'

function object(value: Json | undefined): Row {
  return value && !Array.isArray(value) && typeof value === 'object' ? value : {}
}

export function EvalTables({ name, value }: { name: string; value: Json }) {
  const data = object(value)
  if (name === 'extraction') {
    const summary = object(data.summary)
    const rows = Object.entries(summary).flatMap(([engine, splits]) => Object.entries(object(splits)).map(([split, metrics]) => ({ engine, split, ...object(metrics) })))
    const accuracy = rows.map(row => Object.fromEntries(['engine', 'split', 'field_accuracy', 'fields_correct', 'fields_total', 'line_accuracy', 'lines_correct', 'lines_total', 'review_share', 'review', 'documents'].map(key => [key, (row as Row)[key] ?? null])))
    const wrong = rows.map(row => Object.fromEntries(['engine', 'split', 'accepted_scored', 'accepted_but_wrong', 'accepted_but_wrong_rate'].map(key => [key, (row as Row)[key] ?? null])))
    const classifier = object(data.classifier)
    const classifierRows = Array.isArray(classifier.documents) ? classifier.documents.map(object) : []
    const classifierSummary = { ...Object.fromEntries(Object.entries(classifier).filter(([key]) => key !== 'documents')), correct: classifierRows.filter(row => row.correct === true).length }
    return <><h3>Extraction · agent vs rules baseline</h3><DataTable title="Extraction by development, unseen and payout layouts" rows={accuracy} /><h3>Accepted with a wording error</h3><DataTable title="Accepted documents with errors" rows={wrong} /><h3>Classifier</h3><DataTable title="Classifier accuracy and coverage" rows={[classifierSummary]} /><LazyDetails title="All extraction and classifier evidence"><JsonReport value={value} title="Extraction evidence" /></LazyDetails></>
  }
  if (name === 'reconciliation') return <><DataTable title="Reconciliation found, missed and false alarms by kind" rows={Object.entries(object(data.by_kind)).map(([kind, counts]) => ({ kind, ...object(counts) }))} /><DataTable title="Reconciliation totals" rows={[object(data.totals)]} /><LazyDetails title="Source-linked reconciliation evidence"><JsonReport value={value} title="Reconciliation evidence" /></LazyDetails></>
  if (name === 'ask') return <><DataTable title="Ask correct, refused, wrong and not run" rows={['in_scope', 'out_of_scope'].filter(key => key in data).map(scope => ({ scope, ...object(data[scope]) }))} /><LazyDetails title="All Ask evaluation evidence"><JsonReport value={value} title="Ask evidence" /></LazyDetails></>
  return <JsonReport value={value} title={label(name)} />
}

export default function Results() {
  const { data, error } = useApi<Row>('/results')
  return <><PageTitle title="Results">Measured quality and stated assumptions. All values below come from the evaluation artifact.</PageTitle>{!data ? <LoadState error={error} /> : <><KeyResults data={data} /><p className="text-xs text-muted-foreground">Generated: {dateTime(String(data.generated_at || ''))}. Expand the evidence tables to inspect individual records. “Not recorded yet” means no recorded response was available.</p>{Object.entries(data).filter(([key]) => key !== 'generated_at').map(([key, value]) => <Panel key={key}><h2>{label(key)}</h2><EvalTables name={key} value={value} /></Panel>)}<a href="/api/results" download="results.json">Download evaluation JSON</a></>}</>
}
