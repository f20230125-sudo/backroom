import { createContext, useContext, useEffect, useId, useRef, useState } from 'react'
import type { ComponentProps, ReactNode, RefObject } from 'react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'

interface SelectState {
  value: string
  choose: (value: string) => void
  open: boolean
  setOpen: (open: boolean) => void
  trigger: RefObject<HTMLButtonElement | null>
  listId: string
}
const Context = createContext<SelectState | null>(null)
function useSelect() {
  const context = useContext(Context)
  if (!context) throw new Error('Select components need a Select parent')
  return context
}

/** Radix Select injects style tags. Compose shadcn primitives to keep the self-only CSP. */
function Select({ value, onValueChange, children }: {
  value: string; onValueChange: (value: string) => void; children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null)
  const listId = useId()
  useEffect(() => {
    if (!open) return
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [open])
  function choose(next: string) {
    onValueChange(next); setOpen(false); trigger.current?.focus()
  }
  return <Context.Provider value={{ value, choose, open, setOpen, trigger, listId }}>
    <Collapsible ref={root} open={open} onOpenChange={setOpen} className="relative min-w-0" data-slot="select">{children}</Collapsible>
  </Context.Provider>
}

function SelectTrigger({ className, children, onKeyDown, ...props }: ComponentProps<typeof Button>) {
  const context = useSelect()
  return <CollapsibleTrigger asChild><Button {...props} ref={context.trigger} variant="outline" type="button"
    role="combobox" aria-haspopup="listbox" aria-expanded={context.open} aria-controls={context.listId}
    className={cn('w-full justify-between font-normal', className)} data-slot="select-trigger"
    onKeyDown={event => {
      onKeyDown?.(event)
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault(); context.setOpen(true)
      }
    }}>{children}</Button></CollapsibleTrigger>
}

function SelectValue({ children }: { children: ReactNode }) {
  return <span data-slot="select-value" className="truncate">{children}</span>
}

function SelectContent({ children }: { children: ReactNode }) {
  const { open, setOpen, trigger, listId } = useSelect()
  const list = useRef<HTMLDivElement>(null)
  const search = useRef({ text: '', at: 0 })
  useEffect(() => {
    if (open) (list.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]') ?? list.current?.querySelector<HTMLButtonElement>('[role="option"]'))?.focus()
  }, [open])
  return <CollapsibleContent ref={list} id={listId} role="listbox" data-slot="select-content" tabIndex={-1}
    aria-label={trigger.current?.labels?.[0]?.textContent || 'Options'}
    className="absolute top-full left-0 z-50 mt-1 max-h-64 w-full min-w-40 overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-md"
    onKeyDown={event => {
      const options = Array.from(list.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') || [])
      const current = options.indexOf(document.activeElement as HTMLButtonElement)
      let next: number | undefined
      if (event.key === 'ArrowDown') next = (current + 1) % options.length
      if (event.key === 'ArrowUp') next = (current - 1 + options.length) % options.length
      if (event.key === 'Home') next = 0
      if (event.key === 'End') next = options.length - 1
      if (event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        const now = Date.now()
        search.current.text = (now - search.current.at < 600 ? search.current.text : '') + event.key.toLowerCase()
        search.current.at = now
        const match = options.findIndex(option => option.textContent?.toLowerCase().startsWith(search.current.text))
        if (match >= 0) next = match
      }
      if (next !== undefined) { event.preventDefault(); options[next]?.focus() }
      if (event.key === 'Escape' || event.key === 'Tab') {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation() }
        setOpen(false); trigger.current?.focus()
      }
    }}>{children}</CollapsibleContent>
}

function SelectItem({ value, children }: { value: string; children: ReactNode }) {
  const context = useSelect()
  return <Button type="button" variant="ghost" role="option" aria-selected={context.value === value}
    tabIndex={-1} data-slot="select-item"
    className={cn('h-auto w-full justify-start whitespace-normal rounded-md px-2 py-1.5 text-left font-normal focus:bg-accent', context.value === value && 'bg-accent font-medium')}
    onClick={() => context.choose(value)}>{children}</Button>
}

export { Select, SelectContent, SelectItem, SelectTrigger, SelectValue }
