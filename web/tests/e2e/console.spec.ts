import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

const pages = [
  ['overview', '/', 'Overview'], ['inbox', '/inbox', 'Inbox'],
  ['document', '/inbox/DOC-001', 'DOC-001'], ['review', '/inbox?tab=review', 'Inbox'],
  ['drafts', '/inbox?tab=drafts', 'Inbox'], ['reconciliation', '/reconciliation', 'Reconciliation'],
  ['names', '/names', 'Names'], ['einvoicing', '/einvoicing', 'E-invoicing'],
  ['ask', '/ask', 'Ask'], ['results', '/results', 'Results'],
  ['checker', '/checker', 'Try the checker'], ['how', '/how-it-works', 'How it works'],
  ['404', '/this-page-does-not-exist', 'Page not found'],
]

for (const [name, path, title] of pages) {
  test(`${name}: accessible, responsive and error-free`, async ({ page }, testInfo) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1, name: title, exact: true })).toBeVisible()
    await page.waitForLoadState('networkidle')
    await expect(page.getByText('Loading snapshot…', { exact: true })).toHaveCount(0)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await expect(page.locator('html')).toHaveClass(testInfo.project.name.endsWith('-dark') ? /dark/ : /^$/)
    if (['overview', 'results', 'reconciliation', 'inbox', 'document', 'review', 'drafts'].includes(name)) {
      expect(await page.locator('body').innerText()).not.toMatch(/\b[a-z]+_[a-z_]+\b/)
    }
    if (name === 'results') {
      const scores = await (await page.request.get('/api/results')).json()
      await expect(page.getByRole('heading', { name: 'Key results', exact: true })).toBeVisible()
      await expect(page.getByTestId('key-result')).toHaveCount(7)
      await expect(page.getByTestId('key-result').first()).toContainText(`${(scores.extraction.summary.agent.held_out.field_accuracy * 100).toFixed(1)}%`)
      await expect(page.getByTestId('key-result').first()).toContainText(`${scores.extraction.summary.agent.held_out.fields_correct} / ${scores.extraction.summary.agent.held_out.fields_total}`)
    }
    const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
    expect(audit.violations).toEqual([])
    expect(errors).toEqual([])
    await page.screenshot({ path: testInfo.outputPath(`${name}-${testInfo.project.name}.png`), fullPage: true })
  })
}

test('keyboard skip link focuses main content', async ({ page }) => {
  await page.goto('/')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#main')).toBeFocused()
})

test('clicking an extracted field highlights its source quote', async ({ page }) => {
  await page.goto('/inbox/DOC-001')
  await page.getByRole('button', { name: 'Invoice number', exact: true }).click()
  await expect(page.locator('.source-text mark')).toBeVisible()
  const quote = await page.locator('.source-text mark').textContent()
  expect(quote?.trim().length).toBeGreaterThan(0)
  await expect(page.getByRole('button', { name: 'Invoice number', exact: true })).toHaveAttribute('aria-pressed', 'true')
  const document = await (await page.request.get('/api/documents/DOC-001')).json()
  const fields = page.getByRole('region', { name: 'Extracted fields and evidence' })
  const rate = document.extraction.record.lines[0].vat_rate
  await expect(fields.getByRole('row').filter({ has: page.getByRole('button', { name: 'Lines 1 · vat rate', exact: true }) })).toContainText(`${Number(rate).toFixed(1)}%`)
  await expect(fields.getByRole('row').filter({ has: page.getByRole('button', { name: 'Supplier address emirate', exact: true }) })).toContainText(document.extraction.record.supplier_address_emirate)
})

test('hosted mode disables approvals and enforces API read-only', async ({ page, request }) => {
  const health = await request.get('/api/health')
  expect((await health.json()).readonly).toBe(true)
  await page.goto('/names')
  await expect(page.getByText("Approvals happen in the operator's local copy", { exact: true })).toBeVisible()
  const approve = page.getByRole('button', { name: 'Approve', exact: true })
  expect(await approve.count()).toBeGreaterThan(0)
  for (const button of await approve.all()) await expect(button).toBeDisabled()
  for (const button of await page.getByRole('button', { name: 'Reject', exact: true }).all()) await expect(button).toBeDisabled()
  const response = await request.post('/api/names/supplier/approve', { data: {} })
  expect(response.status()).toBe(403)
})

test('checker returns issues without saving and calculator updates locally', async ({ page }) => {
  const snapshot = async () => Promise.all(['/overview', '/documents', '/drafts', '/runs'].map(async path => (await page.request.get(`/api${path}`)).json()))
  const beforeCheck = await snapshot()
  await page.goto('/checker')
  await page.getByRole('radio', { name: 'Paste invoice JSON' }).check()
  await page.getByLabel('Invoice JSON', { exact: true }).fill('{"net":"100.00","vat":"9.00","total":"109.00"}')
  const checked = page.waitForResponse(response => response.url().endsWith('/api/check') && response.request().method() === 'POST')
  await page.getByRole('button', { name: 'Check invoice', exact: true }).click()
  expect((await (await checked).json()).writes).toBe(0)
  await expect(page.getByText('Nothing was saved.')).toBeVisible()
  await page.getByRole('button', { name: 'Issues', exact: true }).click()
  await expect(page.getByRole('cell', { name: 'VAT not 5%', exact: true })).toBeVisible()
  expect(await snapshot()).toEqual(beforeCheck)
  await page.goto('/')
  const before = await page.getByLabel('Estimated hours saved').textContent()
  await page.getByLabel(/Auto accepted document/).fill('10')
  await expect(page.getByLabel('Estimated hours saved')).not.toHaveText(before || '')
})

test('theme choice overrides system and persists', async ({ page }, testInfo) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Dark mode' }).click()
  const nextIsDark = !testInfo.project.name.endsWith('-dark')
  await expect(page.locator('html')).toHaveClass(nextIsDark ? /dark/ : /^$/)
  await page.reload()
  await expect(page.locator('html')).toHaveClass(nextIsDark ? /dark/ : /^$/)
  await page.waitForLoadState('networkidle')
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([])
})

test('document filters and evidence remain accessible under the production CSP', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  await page.goto('/inbox')
  await page.getByRole('combobox', { name: 'Layout coverage', exact: true }).click()
  await page.getByRole('option', { name: 'Unseen layout', exact: true }).click()
  await expect(page).toHaveURL(/held_out=true/)
  await expect(page.getByRole('region', { name: 'Documents', exact: true })).toContainText('Unseen layout')
  await page.getByRole('combobox', { name: 'Status', exact: true }).click()
  await expect(page.locator('style')).toHaveCount(0)
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([])
  await page.screenshot({ path: testInfo.outputPath(`filters-${testInfo.project.name}.png`), fullPage: true })
  await page.keyboard.press('Escape')
  await page.goto('/results')
  await page.getByRole('button', { name: 'All extraction and classifier evidence', exact: true }).click()
  await expect(page.getByRole('button', { name: 'Summary', exact: true })).toBeVisible()
  expect(await page.locator('main').innerText()).not.toMatch(/\b[a-z]+_[a-z_]+\b/)
  expect(errors).toEqual([])
})
