import { useId, useState } from 'react'
import type { ComponentProps, ReactNode } from 'react'
import { cell, display, label } from './format'
import type { HoursData, Json, Row } from './types'
import { Badge as StatusBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table as UiTable, TableBody, TableCaption, TableHead, TableHeader, TableRow, TableCell } from '@/components/ui/table'
import { cn } from '@/lib/utils'

export { TableRow, TableCell, TableHead }

export function Panel({ className, children, ...props }: ComponentProps<typeof Card>) {
  return <Card className={cn('my-5 min-w-0 overflow-visible', className)} {...props}><CardContent className="min-w-0 space-y-3">{children}</CardContent></Card>
}

export function PageTitle({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="mb-6 space-y-2"><p className="text-xs text-muted-foreground">Saffron &amp; Salt Group · Synthetic operations</p><h1 className="text-2xl font-semibold tracking-tight">{title}</h1>{children && <p className="max-w-3xl text-sm text-muted-foreground">{children}</p>}</div>
}

export function LoadState({ error }: { error?: string }) {
  if (error) return <Alert variant="destructive" role="alert"><AlertTitle>Unable to load snapshot</AlertTitle><AlertDescription>{display(error)}</AlertDescription></Alert>
  return <div role="status" aria-label="Loading snapshot" className="space-y-3"><span className="sr-only">Loading snapshot…</span><Skeleton className="h-8 w-1/3" /><Skeleton className="h-32 w-full" /></div>
}

export function Badge({ value }: { value: string }) {
  const variant = /wrong|exception|document_problem|rejected|missed/.test(value) ? 'destructive' :
    /review|proposed|new|open/.test(value) ? 'review' : /accepted|reconciled/.test(value) ? 'success' : 'outline'
  return <StatusBadge variant={variant}>{label(value)}</StatusBadge>
}

export function FilterSelect({ title, value, onChange, options, empty = 'All' }: {
  title: string; value: string; onChange: (value: string) => void; options: [string, string][]; empty?: string
}) {
  const id = useId()
  return <div className="min-w-0 space-y-2"><Label htmlFor={id}>{title}</Label><Select value={value || '__all'} onValueChange={v => onChange(v === '__all' ? '' : v)}><SelectTrigger id={id} className="w-full min-w-40"><SelectValue>{options.find(([key]) => key === value)?.[1] || empty}</SelectValue></SelectTrigger><SelectContent><SelectItem value="__all">{empty}</SelectItem>{options.map(([key, text]) => <SelectItem key={key} value={key}>{text}</SelectItem>)}</SelectContent></Select></div>
}

export function Table({ caption, headers, children }: { caption: string; headers: string[]; children: ReactNode }) {
  return <div className="my-4 min-w-0 max-w-full rounded-lg border"><UiTable aria-label={caption}><TableCaption className="m-0 border-t p-2 text-left text-xs">{caption}</TableCaption><TableHeader><TableRow>{headers.map(h => <TableHead key={h} scope="col">{h}</TableHead>)}</TableRow></TableHeader><TableBody>{children}</TableBody></UiTable></div>
}

export function DataTable({ title, rows }: { title: string; rows: Row[] }) {
  const headers = [...new Set(rows.flatMap(row => Object.keys(row)))]
  if (!rows.length) return <p className="text-muted-foreground">{title}: no entries in this snapshot.</p>
  return <Table caption={title} headers={headers.map(label)}>{rows.map((row, i) => <TableRow key={i}>{headers.map(key => <TableCell key={key} className="tabular-nums">{typeof row[key] === 'object' && row[key] !== null ? <LazyDetails title={`View ${label(key).toLowerCase()}`}><JsonReport title={label(key)} value={row[key]} /></LazyDetails> : cell(row[key], key, row)}</TableCell>)}</TableRow>)}</Table>
}

export function Metrics({ data }: { data: Row }) {
  return <dl className="grid grid-cols-2 gap-4">{Object.entries(data).map(([key, value]) => <div key={key}><dt className="text-xs text-muted-foreground">{label(key)}</dt><dd className="mt-1 font-medium tabular-nums">{cell(value, key, data)}</dd></div>)}</dl>
}

export function LazyDetails({ title, children }: { title: string; children: ReactNode }) {
  return <Collapsible className="my-2 min-w-0"><CollapsibleTrigger asChild><Button variant="outline" className="h-auto max-w-full justify-start whitespace-normal text-left">{title}</Button></CollapsibleTrigger><CollapsibleContent className="min-w-0 pt-2">{children}</CollapsibleContent></Collapsible>
}

/** Evidence stays structured and labelled; the untouched artifact is downloadable. */
export function JsonReport({ value, title }: { value: Json; title: string }) {
  if (value === null || typeof value !== 'object') return <p>{title}: {display(value)}</p>
  if (Array.isArray(value)) {
    if (value.every(row => row && !Array.isArray(row) && typeof row === 'object')) return <DataTable title={title} rows={value as Row[]} />
    return <ul>{value.map((item, i) => <li key={i}>{typeof item === 'object' && item !== null ? <JsonReport value={item} title={`${title} ${i + 1}`} /> : display(item)}</li>)}</ul>
  }
  const scalars = Object.fromEntries(Object.entries(value).filter(([, v]) => v === null || typeof v !== 'object'))
  const nested = Object.entries(value).filter(([, v]) => v !== null && typeof v === 'object')
  return <div className="min-w-0">{Object.keys(scalars).length > 0 && <DataTable title={title} rows={[scalars]} />}{nested.map(([key, child]) => <LazyDetails key={key} title={label(key)}><JsonReport title={`${title} · ${label(key)}`} value={child} /></LazyDetails>)}</div>
}

function normalisedMap(text: string) {
  let normal = ''
  const offsets: number[] = []
  for (let i = 0; i < text.length; i++) {
    for (const char of text[i].normalize('NFKC').toLowerCase()) {
      if (char === ',') continue
      const next = /\s/.test(char) ? ' ' : char
      if (next === ' ' && normal.endsWith(' ')) continue
      normal += next; offsets.push(i)
    }
  }
  return { normal, offsets }
}

export function quoteRange(text: string, quote: string): [number, number] | null {
  if (!quote.trim()) return null
  const exact = text.toLowerCase().indexOf(quote.toLowerCase())
  if (exact >= 0) return [exact, exact + quote.length]
  const source = normalisedMap(text), needle = normalisedMap(quote).normal.trim()
  const at = source.normal.indexOf(needle)
  return at < 0 || !needle ? null : [source.offsets[at], source.offsets[at + needle.length - 1] + 1]
}

export function SourceText({ text, quote }: { text: string; quote: string }) {
  const range = quoteRange(text, quote)
  return <pre className="source-text max-h-[65vh] overflow-auto whitespace-pre-wrap break-words rounded-md border p-3 font-mono text-xs leading-relaxed" role="region" tabIndex={0} aria-label="Document source">{range ? <>{text.slice(0, range[0])}<mark className="bg-foreground text-background outline-2 outline-offset-2 outline-ring" ref={element => { element?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }) }}>{text.slice(...range)}</mark>{text.slice(range[1])}</> : text || 'Source text unavailable in this snapshot.'}</pre>
}

function hundredths(value: string): bigint {
  if (!/^\d{1,6}(\.\d{0,2})?$/.test(value)) return 0n
  const [whole, part = ''] = value.split('.')
  return BigInt(whole) * 100n + BigInt(part.padEnd(2, '0'))
}

export function savedHours(data: HoursData, assumptions: Record<string, string>): string {
  const hundredthMinutes = Object.entries(data.counts).reduce((total, [name, count]) =>
    total + BigInt(count) * hundredths(assumptions[name] || '0') * (name === 'review_document_overhead' ? -1n : 1n), 0n)
  const numerator = hundredthMinutes * 600n - BigInt(data.full_pipeline_duration_ms || 0)
  const scaled = (numerator < 0n ? -numerator : numerator) * 100n
  const rounded = (scaled + 1800000n) / 3600000n
  return `${numerator < 0n ? '-' : ''}${rounded / 100n}.${String(rounded % 100n).padStart(2, '0')}`
}

export function HoursCalculator({ data }: { data: HoursData }) {
  const initial = Object.fromEntries(data.assumptions.map(a => [a.name, a.minutes]))
  const [minutes, setMinutes] = useState(initial)
  return <Panel><h2>Hours saved · estimate</h2><p><output className="text-3xl font-semibold tabular-nums" aria-label="Estimated hours saved" aria-live="polite">{savedHours(data, minutes)}</output> hours across the loaded run</p><p className="text-muted-foreground">{display(data.label)}. Change the assumptions below; calculations stay in your browser.</p><div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{data.assumptions.map(a => <div className="space-y-2" key={a.name}><Label htmlFor={`minutes-${a.name}`}>{label(a.name)}</Label><p className="text-xs text-muted-foreground">{data.counts[a.name]} × minutes · basis: {display(a.basis)}</p><Input id={`minutes-${a.name}`} type="number" min="0" max="999999" step="0.01" value={minutes[a.name]} onChange={e => setMinutes({ ...minutes, [a.name]: e.target.value })} /></div>)}</div><p className="text-xs text-muted-foreground">Pipeline duration: {data.full_pipeline_duration_ms == null ? 'unavailable; duration omitted' : `${(data.full_pipeline_duration_ms / 1000).toFixed(2)} seconds`} ({display(data.duration_state)}).</p><p className="text-xs text-muted-foreground">Formula: accepted documents × reading minutes + outlet days × reconciliation minutes + drafts × writing minutes − review overhead − pipeline runtime; divided by 60.</p><Button variant="outline" onClick={() => setMinutes(initial)}>Reset assumptions</Button></Panel>
}
