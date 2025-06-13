import { Badge, DataTable, JsonReport, LazyDetails, LoadState, PageTitle, Panel } from '../components'
import { Badge as ArgumentChip } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { display, label, readable } from '../format'
import { Fragment } from 'react'
import { useApi } from '../api'
import type { Answer, Json, Question, Row } from '../types'
import { EvalTables } from './Results'

const words = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(' ')
const tens = 'twenty thirty forty fifty sixty seventy eighty ninety'.split(' ')
const scales: Record<string, bigint> = { hundred: 100n, thousand: 1000n, million: 1000000n, billion: 1000000000n, k: 1000n, m: 1000000n, b: 1000000000n }
const names = [...words, ...tens, 'hundred', 'thousand', 'million', 'billion'].join('|')
const tokens = new RegExp(`(?<![\\d.])[-+−]?(?:\\d+(?:,\\d{3})*(?:\\.\\d+)?|\\.\\d+)(?:[eE][-+]?\\d+)?(?:\\s*(?:million|billion|thousand)\\b|[kKmMbB]\\b)?|\\b(?:${names})(?:[- ]+(?:and[- ]+)?(?:${names}))*\\b`, 'gi')

function numberValue(raw: string): bigint | null {
  const text = raw.toLowerCase().replaceAll(',', '').replace('−', '-')
  if (/^[a-z]/.test(text)) {
    let total = 0n, group = 0n
    for (const word of text.split(/[- ]+/)) {
      if (words.includes(word)) group += BigInt(words.indexOf(word))
      else if (tens.includes(word)) group += BigInt((tens.indexOf(word) + 2) * 10)
      else if (word === 'hundred') group = (group || 1n) * 100n
      else if (scales[word]) { total += (group || 1n) * scales[word]; group = 0n }
    }
    return (total + group) * 10000n
  }
  const match = text.match(/^([-+]?)(\d*)(?:\.(\d+))?(?:e([-+]?\d+))?\s*(thousand|million|billion|[kmb])?$/)
  if (!match) return null
  const exponent = Number(match[4] || '0')
  if (Math.abs(exponent) > 20) return null
  const digits = BigInt((match[2] || '0') + (match[3] || ''))
  const places = 4 + exponent - (match[3]?.length || 0)
  return (match[1] === '-' ? -1n : 1n) * (places >= 0 ? digits * 10n ** BigInt(places) : digits / 10n ** BigInt(-places)) * (scales[match[5]] || 1n)
}

export function CitedAnswer({ result, id }: { result: Answer; id: string }) {
  const answer = readable(result.answer)
  const parts = []; let offset = 0
  for (const match of answer.matchAll(tokens)) {
    const at = match.index, raw = match[0]
    let value = numberValue(raw)
    if (raw.startsWith('-') && at && /[\da-z]/i.test(answer[at - 1])) value = numberValue(raw.slice(1))
    const citation = result.numbers_used.find(c => {
      const allowed = numberValue(c.value)
      return value !== null && allowed !== null && (value > allowed ? value - allowed : allowed - value) <= 100n
    })
    const target = citation && `${id}-${citation.tool_call_id}`
    parts.push(<Fragment key={at}>{answer.slice(offset, at)}{target ? <a href={`#${target}`} onClick={() => {
      const element = document.getElementById(target)
      element?.focus()
    }} title="Show the tool call for this number"><mark className="rounded-sm bg-muted px-0.5 text-foreground">{raw}</mark></a> : raw}</Fragment>)
    offset = at + raw.length
  }
  parts.push(answer.slice(offset))
  return <p className="leading-relaxed">{parts}</p>
}

export default function Ask() {
  const { data, error } = useApi<{ items: Question[]; evaluation: Json; basis: string; coverage: Row }>('/ask/recorded')
  return <><PageTitle title="Ask">Recorded questions, typed SQL tools, and answers with evidence. No live model calls are made here.</PageTitle>
    {!data ? <LoadState error={error} /> : <>
      <p className="text-xs text-muted-foreground">{display(data.basis)}</p>
      <LazyDetails title="Answer-key snapshot coverage"><JsonReport value={data.coverage} title="Answer-key snapshot coverage" /></LazyDetails>
      {data.items.map(q => <Panel key={q.id}>
        <h2>{display(q.question)}</h2><p><Badge value={q.state} /> <span className="text-xs text-muted-foreground">{q.in_scope ? 'In scope' : 'Out of scope'} · {display(q.answer_source)}</span></p>
        {q.result ? <>
          {q.result.tool_calls.map((call, i) => <div key={call.tool_call_id} id={`${q.id}-${call.tool_call_id}`} tabIndex={-1} className="space-y-2 rounded-md border p-3">
            <h3 className="mt-0">Tool {i + 1}: {label(call.tool)} · {call.rows.length} rows</h3>
            <div className="flex flex-wrap gap-2">{Object.entries(call.args).map(([key, value]) => <ArgumentChip key={key} variant="outline" className="h-auto max-w-full whitespace-normal">{label(key)}: {value == null ? 'All' : display(value)}</ArgumentChip>)}</div>
            <LazyDetails title="Tool evidence"><DataTable title={`${q.id} ? ${label(call.tool)} totals`} rows={[call.totals]} /><DataTable title={`${q.id} ? ${label(call.tool)} result rows`} rows={call.rows} /></LazyDetails>
          </div>)}
          <CitedAnswer result={q.result} id={q.id} />
          {(q.result.refused || q.result.not_run) && <Alert><AlertTitle>{q.result.not_run ? 'Not recorded' : 'Refused'}</AlertTitle><AlertDescription>{display(q.result.reason)}</AlertDescription></Alert>}
          {!q.result.tool_calls.length && <p className="text-xs text-muted-foreground">No tool calls recorded.</p>}
        </> : <p>No answer recorded.</p>}
      </Panel>)}
      <Panel><h2>Ask evaluation</h2><EvalTables name="ask" value={data.evaluation} /></Panel>
    </>}
  </>
}
