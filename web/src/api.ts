import { useEffect, useState } from 'react'

export async function api<T>(path: string, data?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api${path}`, {
    signal, ...(data === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),
  })
  const value = await response.json()
  if (!response.ok) throw new Error(value.message || 'The snapshot could not be loaded.')
  return value as T
}

export function useApi<T>(path: string) {
  const [state, setState] = useState<{ path: string; data?: T; error?: string }>({ path })
  useEffect(() => {
    const controller = new AbortController()
    api<T>(path, undefined, controller.signal).then(data => setState({ path, data })).catch(error => {
      if (!controller.signal.aborted) setState({ path, error: String(error.message) })
    })
    return () => controller.abort()
  }, [path])
  return state.path === path ? state : { path }
}
