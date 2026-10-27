import { HoursCalculator, LoadState, Metrics, PageTitle, Panel, Table, TableCell, TableHead, TableRow } from '../components'
import { Link } from 'react-router'
import { useApi } from '../api'
import { Badge } from '@/components/ui/badge'
import { dateTime, day, display, label } from '../format'
import type { OverviewData } from '../types'

export default function Overview() {
  const { data, error } = useApi<OverviewData>('/overview')
  return <><PageTitle title="Overview">The latest pipeline snapshot. Exceptions stay visible until an operator reviews the source.</PageTitle>{!data ? <LoadState error={error} /> : <>
    <div className="mt-5 flex flex-wrap items-center justify-between gap-3"><h2>Exceptions by kind</h2><Link to="/reconciliation">Open reconciliation</Link></div>
    <Table caption="Exceptions in the latest snapshot" headers={['Kind', 'Count', 'Next step']}>{Object.entries(data.exceptions_by_kind).map(([kind, rows]) => <TableRow key={kind}><TableHead scope="row" className="text-destructive">{label(kind)}</TableHead><TableCell className="tabular-nums"><Badge variant="destructive">{rows.length}</Badge></TableCell><TableCell><Link to={`/reconciliation?kind=${encodeURIComponent(kind)}`}>Review exceptions</Link></TableCell></TableRow>)}</Table>
    {!Object.keys(data.exceptions_by_kind).length && <p>No exceptions in this snapshot.</p>}
    <div className="grid gap-5 lg:grid-cols-2"><Panel><h2>Last run</h2>{data.last_run ? <><p>{dateTime(String(data.last_run.started))} · through {day(String(data.last_run.watermark))}</p><Metrics data={{ 'Days covered': data.last_run.days ?? null, Documents: data.last_run.documents ?? null, 'Model calls this run': data.last_run.model_calls ?? null, 'Recorded responses replayed this run': data.last_run.cache_hits ?? null, 'Duration in seconds': data.last_run.duration_ms == null ? null : (Number(data.last_run.duration_ms) / 1000).toFixed(2) }} /><p className="text-xs text-muted-foreground">Model responses: {data.last_run.input_mode === 'saved_results' ? 'replayed from recordings' : display(data.last_run.input_mode)}. Imported extraction snapshot: {display(data.last_run.saved_model_calls)} recorded model responses.</p></> : <p>No completed runs.</p>}</Panel>
    <Panel><h2>Daily brief</h2>{data.brief ? <><p className="leading-relaxed">{display(data.brief.body)}</p><p className="text-xs text-muted-foreground">{display(data.brief.provenance)}</p></> : <p>No brief recorded.</p>}<Link to="/inbox?tab=drafts">Review drafts · not sent</Link></Panel></div>
    {data.hours_saved && <HoursCalculator data={data.hours_saved} />}
  </>}</>
}
