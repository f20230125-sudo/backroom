import { Badge, FilterSelect, JsonReport, LazyDetails, LoadState, PageTitle, Panel, SourceText, Table, TableCell, TableHead, TableRow } from '../components'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { api, useApi } from '../api'
import { cell, day, display, label } from '../format'
import type { Collection, DocumentDetail, DocumentRow, Json, Row } from '../types'

function ReviewQueue() {
  const { data, error } = useApi<Collection<Row>>('/review')
  const health = useApi<{ readonly: boolean }>('/health')
  const [message, setMessage] = useState('')
  const [resolved, setResolved] = useState<string[]>([])
  async function resolve(id: string) {
    try { await api(`/review/${encodeURIComponent(id)}/resolve`, {}); setResolved([...resolved, id]); setMessage('Review item resolved. Re-run the local pipeline to refresh derived findings.') }
    catch (error) { setMessage((error as Error).message) }
  }
  if (!data) return <LoadState error={error} />
  return <><h2>Review queue</h2><p>Items need an operator decision. Resolving records the review; it does not change extracted fields.</p><p role="status">{message}</p><Table caption="Review queue" headers={['Document', 'Reason', 'Status', 'Action']}>{data.items.map(row => <TableRow key={String(row.id)}><TableCell>{row.document_id ? <Link to={`/inbox/${row.document_id}`}>{display(row.document_id)}</Link> : 'Name evidence'}</TableCell><TableCell>{display(row.reason)}</TableCell><TableCell><Badge value={resolved.includes(String(row.id)) ? 'resolved' : String(row.status)} /></TableCell><TableCell><Button disabled={!health.data || health.data.readonly || resolved.includes(String(row.id)) || row.status === 'resolved'} onClick={() => resolve(String(row.id))}>Resolve</Button></TableCell></TableRow>)}</Table>{health.data?.readonly && <p>Approvals happen in the operator's local copy</p>}</>
}

function Drafts() {
  const { data, error } = useApi<Collection<Row>>('/drafts')
  if (!data) return <LoadState error={error} />
  return <><h2>Drafts · not sent</h2><p>Every draft is for human review. There is no send action.</p>{data.items.map(row => <Panel key={String(row.id)}><LazyDetails title={`${display(row.subject)} · not sent`}><p>To: {display(row.to_role)}</p><p className="whitespace-pre-wrap">{display(row.body)}</p><p className="text-xs text-muted-foreground">{display(row.provenance)}</p><LazyDetails title="Source facts"><JsonReport title="Source facts" value={row.facts} /></LazyDetails></LazyDetails></Panel>)}</>
}

function DocumentList() {
  const [params, setParams] = useSearchParams()
  const query = new URLSearchParams(params); query.delete('tab')
  const { data, error } = useApi<Collection<DocumentRow>>(`/documents?${query}`)
  const page = Number(params.get('page') || 1)
  function filter(key: string, value: string) { const next = new URLSearchParams(params); next.delete('page'); value ? next.set(key, value) : next.delete(key); setParams(next) }
  return <><div className="my-5 flex flex-wrap gap-3">
    <FilterSelect title="Kind" value={params.get('kind') || ''} onChange={v => filter('kind', v)} empty="All kinds" options={['invoice', 'credit_note', 'payout_statement', 'email_invoice_body', 'email_with_attachment', 'email_other'].map(k => [k, label(k)])} />
    <FilterSelect title="Status" value={params.get('status') || ''} onChange={v => filter('status', v)} empty="All statuses" options={['accepted', 'review', 'document_problem', 'not_run'].map(k => [k, label(k)])} />
    <FilterSelect title="Layout coverage" value={params.get('held_out') || ''} onChange={v => filter('held_out', v)} empty="All layouts" options={[["true", "Unseen layout"], ["false", "Development layout"]]} />
  </div>
    {!data ? <LoadState error={error} /> : <><Table caption="Documents" headers={['Document', 'Kind', 'Date', 'Layout', 'Status']}>{data.items.map(row => <TableRow key={row.id}><TableHead scope="row"><Link to={`/inbox/${encodeURIComponent(row.id)}`}>{row.id}</Link></TableHead><TableCell>{label(row.kind)}</TableCell><TableCell>{day(row.business_date)}</TableCell><TableCell>{row.held_out ? 'Unseen layout' : row.layout}</TableCell><TableCell><Badge value={row.status} /></TableCell></TableRow>)}</Table><div className="flex flex-wrap items-center gap-3"><Button disabled={page === 1} onClick={() => filter('page', String(page - 1))}>Previous</Button><p>Page {data.page} · {data.total} documents</p><Button disabled={page * (data.page_size || 25) >= (data.total || 0)} onClick={() => filter('page', String(page + 1))}>Next</Button></div></>}
  </>
}

export default function Inbox() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'documents'
  return <><PageTitle title="Inbox">Read the source, check the evidence, and review anything unresolved.</PageTitle>
    <Tabs value={tab} onValueChange={value => { const next = new URLSearchParams(params); next.set('tab', value); setParams(next) }}>
      <TabsList aria-label="Inbox views">{['documents', 'review', 'drafts'].map(t => <TabsTrigger key={t} value={t}>{label(t)}</TabsTrigger>)}</TabsList>
      <TabsContent value="documents"><DocumentList /></TabsContent><TabsContent value="review"><ReviewQueue /></TabsContent><TabsContent value="drafts"><Drafts /></TabsContent>
    </Tabs>
  </>
}

function fields(record: Row, prefix = ''): [string, Json][] {
  return Object.entries(record).flatMap(([key, value]): [string, Json][] => {
    const name = prefix + key
    if (key === 'quotes') return []
    if (Array.isArray(value)) return value.flatMap((line, i) => typeof line === 'object' && line !== null && !Array.isArray(line) ? fields(line, `${name}.${i}.`) : [[`${name}.${i}`, line]])
    return [[name, value]]
  })
}

export function DocumentPage() {
  const { id = '' } = useParams()
  const { data, error } = useApi<DocumentDetail>(`/documents/${encodeURIComponent(id)}`)
  const [selected, setSelected] = useState('')
  const quote = data?.quotes[selected] || ''
  return <><Link to="/inbox">Back to inbox</Link><PageTitle title={id}>Click an extracted field to locate its quoted evidence.</PageTitle>{!data ? <LoadState error={error} /> : <><p><Badge value={data.status} /> {data.held_out && '· Unseen layout'}</p><div className="grid min-w-0 gap-5 lg:grid-cols-2"><Panel className="self-start lg:sticky lg:top-4"><h2>Source text</h2>{data.source_error && <p>{data.source_error}</p>}<SourceText text={data.text} quote={quote} /></Panel><Panel><h2>Extracted fields</h2><p className="text-xs text-muted-foreground">{display(data.extraction?.prompt_version)} · {display(data.extraction?.model || 'Recorded extraction')}</p><p aria-live="polite" className="text-xs text-muted-foreground">{selected ? quote ? `Evidence for ${label(selected)}: ${quote}` : `No quote recorded for ${label(selected)}` : 'Select a field to show its evidence.'}</p>{data.extraction?.record ? <Table caption="Extracted fields and evidence" headers={['Field', 'Value']}>{fields(data.extraction.record).map(([key, value]) => <TableRow key={key}><TableHead scope="row"><Button variant="ghost" className="h-auto max-w-full justify-start whitespace-normal text-left" aria-pressed={selected === key} onClick={() => setSelected(key)}>{label(key)}</Button></TableHead><TableCell className="tabular-nums">{cell(value, key.split('.').at(-1) || key)}</TableCell></TableRow>)}</Table> : <p>No recorded extraction available.</p>}</Panel></div><Panel><h2>Issues</h2>{data.issues.length ? <Alert variant="destructive"><AlertTitle>Document needs attention</AlertTitle><AlertDescription><ul className="list-disc space-y-2 pl-4">{data.issues.map((issue, i) => <li key={i}><strong>{label(issue.code)}</strong> · {label(issue.field)}: {display(issue.message)}</li>)}</ul></AlertDescription></Alert> : <p>No issues recorded.</p>}</Panel></>}</>
}
