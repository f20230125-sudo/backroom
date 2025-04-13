export type Json = null | boolean | number | string | Json[] | { [key: string]: Json }
export type Row = Record<string, Json>
export interface Issue { code: string; field: string; message: string }
export interface DocumentRow {
  id: string; kind: string; status: string; layout: string; business_date: string; held_out: boolean
}
export interface DocumentDetail extends DocumentRow {
  text: string; source_error: string | null; quotes: Record<string, string>; issues: Issue[]
  extraction: { record: Row | null; prompt_version: string; model: string | null } | null
}
export interface Collection<T> { items: T[]; total?: number; page?: number; page_size?: number }
export interface ExceptionRow {
  id: string; kind: string; outlet: string | null; date_or_week: string | null
  date_start: string | null; date_end: string | null; amount: string; message: string
}
export interface HoursData {
  assumptions: { name: string; minutes: string; basis: string }[]
  counts: Record<string, number>; full_pipeline_duration_ms: number | null
  estimated_hours_saved: string; formula: string; label: string; duration_state: string
}
export interface OverviewData {
  last_run: Row | null; exceptions_by_kind: Record<string, ExceptionRow[]>
  brief: { body: string; provenance: string } | null; hours_saved: HoursData | null; mode: string
}
export interface NameRow {
  entity_type: string; variant_key: string; variant: string; canonical_id: string | null
  suggested_id: string | null; decision: string; status: string; reason: string | null
  recommendation: boolean | null; score: number | null; candidates: Row[]
}
export interface ToolCall {
  tool_call_id: string; tool: string; args: Row; rows: Row[]; totals: Row
}
export interface Answer {
  answer: string; numbers_used: { tool_call_id: string; value: string }[]
  refused: boolean; reason: string; not_run: boolean; tool_calls: ToolCall[]
}
export interface Question {
  id: string; question: string; in_scope: boolean; state: string
  answer_source: string; result: Answer | null
}
