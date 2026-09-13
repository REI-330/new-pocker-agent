/* M6 browser smoke: one registered blind artifact per entry.
 *
 *   node acceptance/m6-artifact-smoke.mjs <base> <artifacts.json> <out.json>
 *
 * artifacts.json: [{"game_id": "...", "version": 1}]
 *
 * For each artifact it opens the app, creates that exact version, submits a
 * dynamic action, refreshes (recovery), and plays to the end. This is the
 * `browser_playable` signal ADR-0020 requires; it is separate from the HTTP
 * `runtime_playable` the Python harness measures.
 */
import {chromium} from 'playwright'
import fs from 'node:fs'

const [BASE, ARTIFACTS_PATH, OUT_PATH] = process.argv.slice(2)
const artifacts = JSON.parse(fs.readFileSync(ARTIFACTS_PATH, 'utf8'))
const results = []

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const revisionText = async page => {
  const text = await page.locator('.revision').first().innerText().catch(() => '')
  const match = text.match(/(\d+)/)
  return match ? Number(match[1]) : -1
}

async function fillActionForm(form) {
  const submit = form.locator('button.action-primary')
  const blocks = form.locator('.action-input')
  const count = await blocks.count()
  for (let index = 0; index < count; index += 1) {
    if (await submit.isEnabled()) break
    const block = blocks.nth(index)
    const picks = block.locator('.card-choice:not([disabled])')
    const total = await picks.count()
    for (let pick = 0; pick < total; pick += 1) {
      if (await submit.isEnabled()) break
      const option = picks.nth(pick)
      const className = (await option.getAttribute('class')) || ''
      if (!className.includes('selected')) await option.click()
    }
    const scalar = block.locator('input.action-scalar')
    if (await scalar.count()) await scalar.fill('1')
  }
}

async function step(page) {
  const felt = await page.locator('.felt-top').first().innerText().catch(() => '')
  if (/FINISHED/.test(felt)) return false
  const form = page.locator('.action-form').first()
  if (!(await form.count())) return false
  await fillActionForm(form)
  const submit = form.locator('button.action-primary')
  if (!(await submit.isEnabled())) throw new Error('submit never enabled')
  const before = await revisionText(page)
  await submit.click()
  const deadline = Date.now() + 15000
  while (Date.now() < deadline) {
    if (await revisionText(page) !== before) return true
    await sleep(60)
  }
  return /FINISHED/.test(await page.locator('.felt-top').first().innerText().catch(() => ''))
}

async function smoke(context, item) {
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const checks = []
  const check = (name, ok, detail = '') => checks.push({name, ok: !!ok, detail: String(detail)})
  try {
    await page.goto(BASE, {waitUntil: 'domcontentloaded'})
    const created = await page.evaluate(async ref => {
      const response = await fetch('/api/sessions', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({game_id: ref.game_id, version: ref.version, seed: 7}),
      })
      return {status: response.status, data: await response.json()}
    }, item)
    check('create session', created.status === 200 && created.data.version === item.version,
      `${created.status} v${created.data && created.data.version}`)
    const session = created.data
    await page.evaluate(ref => {
      localStorage.clear()
      localStorage.setItem('pocker-session', JSON.stringify(ref))
    }, {session_id: session.session_id, game_id: item.game_id, version: item.version})
    await page.reload({waitUntil: 'domcontentloaded'})
    await page.waitForSelector('.felt-table', {timeout: 20000})
    check('opened exact version', (await page.locator('.heading-pills').innerText()).includes(`v${item.version}`))
    check('dynamic action form', (await page.locator('.action-form').count()) >= 1)

    const revBefore = await revisionText(page)
    const advanced = await step(page)
    check('submitted one action', advanced && await revisionText(page) !== revBefore,
      `${revBefore} -> ${await revisionText(page)}`)
    const revAfterAction = await revisionText(page)

    await page.reload({waitUntil: 'domcontentloaded'})
    await page.waitForSelector('.felt-table', {timeout: 20000})
    check('refresh recovered the revision', await revisionText(page) === revAfterAction,
      `${await revisionText(page)} vs ${revAfterAction}`)

    for (let index = 0; index < 400; index += 1) {
      if (!(await step(page))) break
    }
    check('played to the end', /FINISHED/.test(
      await page.locator('.felt-top').first().innerText().catch(() => '')))
    check('no page errors', errors.length === 0, errors.join(' | '))
  } catch (error) {
    check('smoke completed', false, error.message)
  }
  await page.close()
  return {game_id: item.game_id, version: item.version,
          ok: checks.every(item => item.ok), checks}
}

const context = await chromium.launchPersistentContext(OUT_PATH + '.profile', {
  headless: true, viewport: {width: 1440, height: 900},
})
try {
  for (const item of artifacts) {
    console.log(`[browser] ${item.game_id}@${item.version} …`)
    results.push(await smoke(context, item))
  }
} finally {
  await context.close()
  fs.writeFileSync(OUT_PATH, JSON.stringify(results, null, 2))
}
console.log(JSON.stringify(results.map(item => ({game: item.game_id, ok: item.ok}))))
process.exit(results.every(item => item.ok) ? 0 : 1)
