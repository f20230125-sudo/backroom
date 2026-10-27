import { Badge, JsonReport, LazyDetails, LoadState, PageTitle, Table, TableCell, TableHead, TableRow } from '../components'
import { Button } from '@/components/ui/button'
import { useState } from 'react'
import { api, useApi } from '../api'
import { display } from '../format'
import type { NameRow } from '../types'

export default function Names() {
  const { data, error } = useApi<{ items: NameRow[]; readonly: boolean; approval_message: string }>('/names')
  const [decisions, setDecisions] = useState<Record<string, string>>({})
  const [message, setMessage] = useState('')
  async function decide(row: NameRow, action: string) {
    const key = row.entity_type + ':' + row.variant_key
    setDecisions(d => ({ ...d, [key]: 'pending' }))
    try {
      const result = await api<{ status: string }>(`/names/${row.entity_type}/${action}`, { variant: row.variant, canonical_id: row.canonical_id || row.suggested_id })
      setDecisions(d => ({ ...d, [key]: result.status })); setMessage('Decision saved. Re-run the local pipeline to refresh derived results.')
    } catch (error) { setMessage((error as Error).message); setDecisions(d => ({ ...d, [key]: '' })) }
  }
  return <><PageTitle title="Names">Name evidence proposes an identity. A suggestion is kept separate from an operator's decision.</PageTitle>{!data ? <LoadState error={error} /> : <>{data.readonly && <p className="rounded-md border p-3">Approvals happen in the operator's local copy</p>}<p role="status">{message}</p><Table caption="Name proposals and evidence" headers={['Variant', 'Suggested canonical', 'Decision', 'Evidence', 'Status', 'Actions']}>{data.items.map(row => { const key = row.entity_type + ':' + row.variant_key; return <TableRow key={key}><TableHead scope="row">{row.variant}<span className="block text-xs text-muted-foreground">{row.entity_type}</span></TableHead><TableCell>{row.canonical_id || row.suggested_id || 'No supported identity'}</TableCell><TableCell><Badge value={row.decision} /></TableCell><TableCell>{display(row.reason) !== 'Unavailable' ? display(row.reason) : (row.decision === 'auto_match' ? 'Deterministic name evidence' : 'Human review required')}<LazyDetails title="Candidate evidence"><p>Score: {display(row.score)}. Model recommendation: {display(row.recommendation)}</p><JsonReport title="Candidates" value={row.candidates} /></LazyDetails></TableCell><TableCell><Badge value={decisions[key] || row.status} /></TableCell><TableCell><div className="flex gap-2"><Button disabled={data.readonly || !!decisions[key] || (!row.canonical_id && !row.suggested_id)} onClick={() => decide(row, 'approve')}>Approve</Button><Button disabled={data.readonly || !!decisions[key] || (!row.canonical_id && !row.suggested_id)} onClick={() => decide(row, 'reject')}>Reject</Button></div></TableCell></TableRow>})}</Table></>}</>
}
