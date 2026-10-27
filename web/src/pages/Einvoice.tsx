import { LoadState, PageTitle, Table, TableCell, TableHead, TableRow } from '../components'
import { display, label } from '../format'
import { useApi } from '../api'
import type { Collection } from '../types'

interface Finding { id: string; record_type: string; record_id: string; rule: string; message: string }
export default function Einvoice() {
  const { data, error } = useApi<Collection<Finding>>('/einvoice')
  return <><PageTitle title="E-invoicing">Master data and invoice readiness findings. These checks do not submit an e-invoice.</PageTitle>{!data ? <LoadState error={error} /> : <><p>{data.items.length} findings in the current snapshot.</p><Table caption="E-invoice blocking findings by rule" headers={['Rule', 'Record type', 'Record', 'Why it would block']}>{data.items.map(row => <TableRow key={row.id}><TableHead scope="row" className="text-destructive">{label(row.rule)}</TableHead><TableCell>{label(row.record_type)}</TableCell><TableCell>{row.record_id}</TableCell><TableCell>{display(row.message)}</TableCell></TableRow>)}</Table></>}</>
}
