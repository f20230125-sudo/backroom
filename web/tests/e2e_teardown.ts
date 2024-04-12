import { readFile, unlink } from 'node:fs/promises'
import path from 'node:path'

/** Stop the exact fixture PID before Playwright tries Windows process-tree cleanup. */
export default async function teardown() {
  if (process.env.BASE_URL) return
  const port = process.env.BACKROOM_E2E_PORT || '5081'
  const file = path.resolve(import.meta.dirname, `../../.local/codex-e2e-${port}.pid`)
  const pid = Number(await readFile(file, 'ascii'))
  if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error('Invalid e2e fixture PID')
  process.kill(pid)
  await unlink(file)
  // Let the Python launcher and its command shell close their stdio handles.
  await new Promise(resolve => setTimeout(resolve, 500))
}
