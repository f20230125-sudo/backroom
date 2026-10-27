import { useState } from 'react'
import type { FormEvent } from 'react'
import { api, useApi } from '../api'
import { FilterSelect, JsonReport, PageTitle, Panel } from '../components'
import { display } from '../format'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Textarea } from '@/components/ui/textarea'
import { Alert, AlertDescription } from '@/components/ui/alert'
import type { Collection, DocumentRow, Row } from '../types'

export default function Checker() {
  const { data } = useApi<Collection<DocumentRow>>('/documents?kind=invoice&page_size=100')
  const [mode, setMode] = useState('sample'), [sample, setSample] = useState(''), [text, setText] = useState('')
  const [result, setResult] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(''); setResult(null); setBusy(true)
    try {
      const payload = mode === 'sample' ? { doc_id: sample || data?.items[0]?.id } : { invoice: JSON.parse(text) }
      setResult(await api<Row>('/check', payload))
    } catch (error) { setError((error as Error).message) }
    finally { setBusy(false) }
  }
  return <><PageTitle title="Try the checker">Run deterministic rules against a sample or pasted invoice JSON. This check saves nothing.</PageTitle>
    <Panel><form className="space-y-5" onSubmit={submit}>
      <fieldset className="space-y-3"><legend className="mb-2 font-medium">Input</legend><RadioGroup value={mode} onValueChange={setMode}>
        <div className="flex items-center gap-2"><RadioGroupItem id="sample-mode" value="sample" /><Label htmlFor="sample-mode">Sample document</Label></div>
        <div className="flex items-center gap-2"><RadioGroupItem id="json-mode" value="json" /><Label htmlFor="json-mode">Paste invoice JSON</Label></div>
      </RadioGroup></fieldset>
      {mode === 'sample' ? <FilterSelect title="Sample invoice" value={sample || data?.items[0]?.id || ''} onChange={setSample} empty="Choose a sample" options={data?.items.map(d => [d.id, `${d.id} · ${d.held_out ? 'Unseen layout' : d.layout}`]) || []} /> : <div className="space-y-2">
        <Label htmlFor="invoice-json">Invoice JSON</Label><Textarea id="invoice-json" required rows={14} value={text} onChange={e => setText(e.target.value)} spellCheck={false} aria-describedby="json-hint" className="font-mono" />
        <p id="json-hint" className="text-xs text-muted-foreground">Use ISO dates and decimal strings for amounts. Source quote checks need source text; arithmetic, mandatory fields, and duplicate checks still run.</p>
      </div>}
      <Button type="submit" disabled={busy || (mode === 'sample' && !data?.items.length)}>{busy ? 'Checking…' : 'Check invoice'}</Button>
      {error && <Alert variant="destructive"><AlertDescription>{display(error)}</AlertDescription></Alert>}
    </form></Panel>
    {result && <Panel aria-live="polite"><h2>Checker results</h2><p>Nothing was saved.</p><JsonReport value={result} title="Checker results" /></Panel>}
  </>
}
