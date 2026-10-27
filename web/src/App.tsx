import { useEffect, useState } from 'react'
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from 'react-router'
import { Moon, Sun } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { PageTitle } from './components'
import Overview from './pages/Overview'
import Inbox, { DocumentPage } from './pages/Inbox'
import Reconciliation from './pages/Reconciliation'
import Names from './pages/Names'
import Einvoice from './pages/Einvoice'
import Ask from './pages/Ask'
import Results from './pages/Results'
import Checker from './pages/Checker'
import How from './pages/How'

const links = [['/', 'Overview'], ['/inbox', 'Inbox'], ['/reconciliation', 'Reconciliation'], ['/names', 'Names'], ['/einvoicing', 'E-invoicing'], ['/ask', 'Ask'], ['/results', 'Results'], ['/checker', 'Try the checker'], ['/how-it-works', 'How it works']]

function initialTheme(): string {
  try { const stored = localStorage.getItem('backroom-theme'); if (stored === 'dark' || stored === 'light') return stored } catch { /* Storage can be unavailable in private or embedded browsers. */ }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function Shell() {
  const [theme, setTheme] = useState(initialTheme)
  const location = useLocation()
  useEffect(() => { document.documentElement.classList.toggle('dark', theme === 'dark'); try { localStorage.setItem('backroom-theme', theme) } catch { /* Keep the in-memory choice. */ } }, [theme])
  useEffect(() => { document.title = `${links.find(([path]) => path === location.pathname)?.[1] || (location.pathname.startsWith('/inbox/') ? 'Document' : 'Page not found')} · Backroom`; window.scrollTo(0, 0) }, [location.pathname])
  return <><a className="fixed -top-20 left-4 z-50 rounded-md border bg-background px-4 py-2 focus:top-3" href="#main" onClick={() => document.getElementById('main')?.focus()}>Skip to content</a><header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-4 md:px-6"><Link className="text-xl font-semibold tracking-tight no-underline" to="/">Backroom<span className="mt-1 block text-[10px] font-medium tracking-widest text-muted-foreground">SAFFRON &amp; SALT GROUP</span></Link><div className="flex items-center gap-4"><span className="hidden text-xs text-muted-foreground sm:inline">Synthetic data · recorded responses</span><Button variant="outline" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-pressed={theme === 'dark'}>{theme === 'dark' ? <Sun aria-hidden="true" /> : <Moon aria-hidden="true" />} Dark mode</Button></div></header><div className="mx-auto grid max-w-[1800px] md:grid-cols-[190px_minmax(0,1fr)]"><aside className="min-w-0 border-b p-3 md:min-h-[calc(100vh-85px)] md:border-r md:border-b-0"><nav aria-label="Primary" className="flex max-w-full flex-wrap gap-1 md:flex-col">{links.map(([path, title]) => <NavLink className={({ isActive }) => `rounded-md px-3 py-2 text-sm no-underline hover:bg-accent ${isActive ? 'bg-accent font-semibold' : 'text-muted-foreground'}`} key={path} to={path} end={path === '/'}>{title}</NavLink>)}</nav><p className="hidden px-3 pt-8 text-xs leading-6 text-muted-foreground md:block">Operations console<br />Jul–Sep 2026<br />Dubai · AED</p></aside><main className="min-w-0 p-4 text-sm md:px-7 md:pt-7" id="main" tabIndex={-1}><Routes><Route path="/" element={<Overview />} /><Route path="/inbox" element={<Inbox />} /><Route path="/inbox/:id" element={<DocumentPage />} /><Route path="/reconciliation" element={<Reconciliation />} /><Route path="/names" element={<Names />} /><Route path="/einvoicing" element={<Einvoice />} /><Route path="/ask" element={<Ask />} /><Route path="/results" element={<Results />} /><Route path="/checker" element={<Checker />} /><Route path="/how-it-works" element={<How />} /><Route path="*" element={<><PageTitle title="Page not found">This address does not match a page in Backroom.</PageTitle><Link to="/">Return to overview</Link></>} /></Routes><Separator className="mt-8" /><footer className="py-5 text-xs text-muted-foreground">Synthetic data. Drafts are never sent. <Link to="/how-it-works">About this demo</Link></footer></main></div></>
}

export default function App() { return <BrowserRouter><Shell /></BrowserRouter> }
