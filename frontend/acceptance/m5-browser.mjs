/* M5 browser acceptance (Playwright), committed and repeatable.
 *
 *   node acceptance/m5-browser.mjs <base> <designs.json> <phase> <browser> \
 *        <profileDir> <outDir> <midgame.json>
 *
 * Run from frontend/ so `playwright` resolves; normally invoked by
 * scripts/m5_acceptance.py, which prepares the DB, servers and seeds.
 *
 * phase = full    -> three design->confirm->publish->play-to-end flows; rules
 *                    page; mid-game refresh; 409; offline retry; request_id
 *                    idempotency; illegal input; event pagination; hidden cards;
 *                    stale cursor/truncated log; publish interruption; multi-tab
 *                    races; responsive screenshots.
 *         recover -> after a backend restart: same session/version/revision,
 *                    events neither lost nor duplicated, finish the game.
 *
 * It imports no application source; it drives the built app over HTTP/UI.
 */
import {chromium, firefox, webkit} from 'playwright'
import fs from 'node:fs'
import path from 'node:path'

const [BASE, DESIGNS_PATH, PHASE, BROWSER, PROFILE, OUT_DIR, MIDGAME_PATH] = process.argv.slice(2)
const ENGINES = {chromium, firefox, webkit}
const engine = ENGINES[BROWSER]
if (!engine) throw new Error(`unknown browser ${BROWSER}`)
const designs = DESIGNS_PATH && fs.existsSync(DESIGNS_PATH)
  ? JSON.parse(fs.readFileSync(DESIGNS_PATH, 'utf8')) : {}
fs.mkdirSync(OUT_DIR, {recursive: true})
const tag = `${BROWSER}-${PHASE}`

const results = []
const pageErrors = []
function check(name, ok, detail = '') {
  results.push({name, ok: !!ok, detail: String(detail)})
  console.log(`${ok ? 'PASS' : 'FAIL'} [${tag}] ${name}${detail ? ' :: ' + detail : ''}`)
}
function section(title) { console.log(`\n--- [${tag}] ${title} ---`) }

async function fetchJson(page, url, method = 'GET', body) {
  return page.evaluate(async ({url, method, body}) => {
    const response = await fetch(url, {
      method, headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await response.text()
    let data = null
    try { data = text ? JSON.parse(text) : null } catch { data = text }
    return {status: response.status, data}
  }, {url, method, body})
}

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))

async function revision(page) {
  const text = await page.locator('.revision').first().innerText().catch(() => '')
  const match = text.match(/(\d+)/)
  return match ? Number(match[1]) : -1
}

async function waitRevision(page, previous, timeout = 15000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await revision(page) !== previous) return true
    await sleep(60)
  }
  return false
}

async function clickButton(page, text) {
  const button = page.locator('button', {hasText: text}).first()
  await button.waitFor({state: 'visible', timeout: 20000})
  await button.click()
}

// A click without actionability waiting, for genuine concurrent races.
async function raceClick(page, text) {
  return page.evaluate(t => {
    const button = [...document.querySelectorAll('button')].find(item => item.textContent.includes(t))
    if (button && !button.disabled) button.click()
    return !!button
  }, text)
}

async function waitEnabled(page, text, timeout = 20000) {
  await page.waitForFunction(t => {
    const button = [...document.querySelectorAll('button')].find(item => item.textContent.includes(t))
    return !!button && !button.disabled
  }, text, {timeout})
}

async function waitDisabled(page, text, timeout = 20000) {
  await page.waitForFunction(t => {
    const button = [...document.querySelectorAll('button')].find(item => item.textContent.includes(t))
    return !!button && button.disabled
  }, text, {timeout})
}

async function clickDesignNav(page) {
  await page.waitForSelector('.prototype-nav button', {timeout: 25000})
  try {
    await page.locator('.prototype-nav button', {hasText: '新建玩法'}).click({timeout: 20000})
  } catch {
    await page.evaluate(() => {
      const button = [...document.querySelectorAll('.prototype-nav button')]
        .find(item => item.textContent.includes('新建玩法'))
      if (button) button.click()
    })
  }
  await page.waitForSelector('.progress-card', {timeout: 20000})
}

async function openDesign(page, id) {
  await page.evaluate(id => {
    localStorage.clear()
    localStorage.setItem('pocker-design-id', id)
  }, id)
  await page.reload({waitUntil: 'load'})
  await clickDesignNav(page)
}

async function openSession(page, ref) {
  await page.evaluate(ref => {
    localStorage.clear()
    localStorage.setItem('pocker-session', JSON.stringify(ref))
  }, ref)
  await page.reload({waitUntil: 'domcontentloaded'})
  await page.waitForSelector('.felt-table', {timeout: 20000})
}

// Fill every declared input of the form just enough for submit to enable.
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

async function stepComposed(page) {
  const felt = await page.locator('.felt-top').first().innerText().catch(() => '')
  if (/FINISHED/.test(felt)) return false
  const form = page.locator('.action-form').first()
  if (!(await form.count())) return false
  await fillActionForm(form)
  const submit = form.locator('button.action-primary')
  if (!(await submit.isEnabled())) throw new Error('submit never enabled after filling inputs')
  const before = await revision(page)
  await submit.click()
  if (!(await waitRevision(page, before))) {
    const after = await page.locator('.felt-top').first().innerText().catch(() => '')
    if (/FINISHED/.test(after)) return true
    throw new Error(`revision did not advance from ${before}`)
  }
  return true
}

async function playSteps(page, steps) {
  let played = 0
  while (played < steps) {
    if (!(await stepComposed(page))) break
    played += 1
  }
  return played
}

async function finished(page) {
  const felt = await page.locator('.felt-top').first().innerText().catch(() => '')
  return /FINISHED/.test(felt)
}

async function screenshot(page, name) {
  await page.screenshot({path: path.join(OUT_DIR, `${name}-${BROWSER}.png`), fullPage: false})
}

function storedEvents(page, sessionId) {
  return page.evaluate(sid => {
    const raw = localStorage.getItem(`pocker-events:${sid}`)
    return raw ? JSON.parse(raw) : null
  }, sessionId)
}

// --------------------------------------------------------------------- phases
async function runDesignFlow(page, gameId, designId) {
  await openDesign(page, designId)
  await waitEnabled(page, '确认当前规则')
  await clickButton(page, '确认当前规则')
  await waitDisabled(page, '确认当前规则')
  await waitEnabled(page, '注册版本')
  await clickButton(page, '注册版本')
  await page.waitForSelector('.progress-card button:has-text("开始试玩")', {timeout: 45000})
  await screenshot(page, `design-${gameId}-registered-1440`)
  await clickButton(page, '开始试玩')
  await page.waitForSelector('.felt-table', {timeout: 20000})
  await screenshot(page, `workspace-${gameId}-start-1440`)
  await playSteps(page, 240)
  return {finished: await finished(page)}
}

async function phaseFull(context, page) {
  section('design -> confirm -> publish -> play (three composed games)')
  for (const gameId of ['ui-alpha', 'ui-gamma', 'ui-eta']) {
    const entry = designs[gameId]
    if (!entry) { check(`${gameId}: seeded design present`, false); continue }
    try {
      const outcome = await runDesignFlow(page, gameId, entry.session_id)
      check(`${gameId}: played to the end`, outcome.finished,
        `status=${await page.locator('.felt-top').first().innerText().catch(() => '')}`)
    } catch (error) {
      await screenshot(page, `failure-${gameId}-1440`).catch(() => {})
      const text = await page.evaluate(() => document.body.innerText.slice(0, 400)).catch(() => '')
      check(`${gameId}: flow completed`, false, `${error.message} :: ${text.replace(/\s+/g, ' ')}`)
    }
  }

  section('rules page -> version detail -> play; leave a game mid-flight')
  await page.goto(BASE, {waitUntil: 'domcontentloaded'})
  await page.locator('.prototype-nav button', {hasText: '玩法库'}).click()
  await page.locator('.library-row', {hasText: 'theta-draw'}).first().click()
  await page.waitForSelector('.version-chip', {timeout: 20000})
  // The chip comes from the cheap list; the clause view waits on the detail fetch.
  await page.waitForSelector('.source-map-section', {timeout: 20000})
  check('rules: version chip rendered', (await page.locator('.version-chip').count()) >= 1)
  check('rules: clauses section rendered', /条款来源/.test(await page.locator('.actions-card').innerText()))
  check('rules: verification evidence rendered',
    /验证证据/.test(await page.locator('.rule-ledger').innerText()))
  await screenshot(page, 'rules-theta-draw-1440')
  await page.locator('.prototype-nav button', {hasText: '玩法库'}).click()
  await page.waitForSelector('.library-row', {timeout: 15000})
  const composedRow = await page.locator('.library-row', {hasText: 'theta-draw'}).first().innerText()
  check('library: composed game exposes the playtest DTO',
    /v1/.test(composedRow) && /composed_rules/.test(composedRow), composedRow.replace(/\s+/g, ' '))
  await page.locator('.library-row', {hasText: 'theta-draw'}).first().click()
  await page.waitForSelector('.version-chip', {timeout: 15000})
  await clickButton(page, '用这个版本试玩')
  await page.waitForSelector('.action-form', {timeout: 20000})
  const played = await playSteps(page, 2)
  check('mid-game: two actions applied', played === 2, `played=${played}`)
  const midRevision = await revision(page)
  const ref = await page.evaluate(() => JSON.parse(localStorage.getItem('pocker-session')))
  const cursorBefore = (await storedEvents(page, ref.session_id))?.cursor ?? -1
  await screenshot(page, 'workspace-midgame-1440')

  section('refresh keeps the same session/version/revision and the event history')
  await page.reload({waitUntil: 'domcontentloaded'})
  await page.waitForSelector('.felt-table', {timeout: 20000})
  const refAfter = await page.evaluate(() => JSON.parse(localStorage.getItem('pocker-session')))
  check('refresh: same session/version',
    refAfter.session_id === ref.session_id && refAfter.version === ref.version, JSON.stringify(refAfter))
  check('refresh: same revision', await revision(page) === midRevision,
    `${await revision(page)} vs ${midRevision}`)
  check('refresh: event cursor preserved',
    ((await storedEvents(page, ref.session_id))?.cursor ?? -1) >= cursorBefore,
    `cursor=${(await storedEvents(page, ref.session_id))?.cursor}`)

  section('409 conflict refreshes and keeps the session')
  const staleRevision = await revision(page)
  const form = page.locator('.action-form').first()
  check('409: an action form is present', (await form.count()) === 1,
    `forms=${await page.locator('.action-form').count()}`)
  await fillActionForm(form)
  check('409: submit ready before the conflict', await form.locator('button.action-primary').isEnabled())
  const advanced = await page.evaluate(async sid => {
    const state = await (await fetch(`/api/sessions/${sid}`)).json()
    const action = state.actions.find(item => item.inputs.length) || state.actions[0]
    const values = Object.fromEntries(action.inputs.filter(item => item.min_count > 0)
      .map(item => [item.id, item.options.slice(0, item.min_count)]))
    const response = await fetch(`/api/sessions/${sid}/actions`, {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action_id: action.id, input_values: values, revision: state.revision})})
    return (await response.json()).state.revision
  }, ref.session_id)
  check('409: other writer advanced the server', advanced === staleRevision + 1,
    `${staleRevision} -> ${advanced}`)
  await form.locator('button.action-primary').click()
  await page.waitForSelector('.state-banner.notice', {timeout: 20000})
  const notice = await page.locator('.state-banner.notice').innerText()
  check('409: notice shown', /409/.test(notice), notice.replace(/\s+/g, ' '))
  check('409: session kept', (await page.evaluate(() => localStorage.getItem('pocker-session'))) !== null)
  check('409: revision refreshed to the other writer', await revision(page) === advanced,
    `${await revision(page)} vs ${advanced}`)

  section('offline submit fails visibly, then the same request_id retries once')
  const beforeOffline = await revision(page)
  const offlineForm = page.locator('.action-form').first()
  await fillActionForm(offlineForm)
  await context.setOffline(true).catch(() => {})
  const offlineEnforced = await page.evaluate(async () => {
    try { await fetch('/health', {cache: 'no-store'}); return false } catch { return true }
  })
  if (!offlineEnforced) {
    // WebKit does not enforce setOffline; record the skip rather than hang on a
    // submit that would succeed.
    await context.setOffline(false).catch(() => {})
    check('offline: emulation unavailable, retry path covered by request_id test', true,
      'skipped')
  } else {
    await offlineForm.locator('button.action-primary').click()
    await page.waitForSelector('.state-banner.conflict', {timeout: 20000})
    check('offline: connection error shown',
      /无法连接/.test(await page.locator('.state-banner.conflict').innerText()))
    await context.setOffline(false)
    await page.locator('.state-banner.conflict button').click()
    await offlineForm.locator('button.action-primary').click()
    check('offline: retry applies exactly one revision', await waitRevision(page, beforeOffline),
      `${beforeOffline} -> ${await revision(page)}`)
  }

  section('request_id: same key is idempotent, changed revision is a new request')
  const dup = await page.evaluate(async () => {
    const created = await (await fetch('/api/sessions', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({game_id: 'alpha-pairs', version: 1}),
    })).json()
    const action = created.actions.find(item => item.inputs.length)
    const values = Object.fromEntries(action.inputs
      .filter(item => item.min_count > 0)
      .map(item => [item.id, item.options.slice(0, item.min_count)]))
    const body = JSON.stringify({action_id: action.id, input_values: values, revision: 0,
      request_id: 'dup-key-1'})
    const call = () => fetch(`/api/sessions/${created.session_id}/actions`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body}).then(r => r.json())
    const first = await call()
    const second = await call()
    return {first: first.state.revision, second: second.state.revision,
      same: JSON.stringify(first.event) === JSON.stringify(second.event)}
  })
  check('idempotent retry: revision advances once', dup.first === 1 && dup.second === 1, JSON.stringify(dup))
  check('idempotent retry: identical response', dup.same)

  section('illegal input is rejected with a readable 422')
  const illegal = await page.evaluate(async () => {
    const created = await (await fetch('/api/sessions', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({game_id: 'alpha-pairs', version: 1}),
    })).json()
    const action = created.actions.find(item => item.inputs.some(i => i.min_count > 0))
    const missing = await (await fetch(`/api/sessions/${created.session_id}/actions`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action_id: action.id, input_values: {}, revision: 0}),
    })).json()
    const input = action.inputs.find(item => item.min_count > 0)
    const bad = await (await fetch(`/api/sessions/${created.session_id}/actions`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action_id: action.id,
        input_values: {[input.id]: ['not-a-real-card']}, revision: 0}),
    })).json()
    return {missing: missing.detail, bad: bad.detail}
  })
  check('missing input -> missing_action_input', /missing_action_input/.test(illegal.missing || ''), illegal.missing)
  check('unknown card -> card_not_available', /card_not_available/.test(illegal.bad || ''), illegal.bad)
  const guardForm = page.locator('.action-form').first()
  if (await guardForm.count()) {
    const empty = await guardForm.locator('button.action-primary').isEnabled().catch(() => true)
    const needsPick = await guardForm.locator('.action-input .card-choice').count()
    check('submit disabled before the required cards are picked', needsPick === 0 || !empty,
      `needsPick=${needsPick} enabled=${empty}`)
  }

  section('event pagination window is bounded and monotonic')
  const pages = await page.evaluate(async sid => {
    const first = await (await fetch(`/api/sessions/${sid}/events?after=0&limit=2`)).json()
    const next = await (await fetch(`/api/sessions/${sid}/events?after=${first.cursor}&limit=2`)).json()
    return {first, next}
  }, ref.session_id)
  check('events: first page bounded', pages.first.events.length <= 2 && pages.first.cursor === pages.first.events.length,
    JSON.stringify({cursor: pages.first.cursor, len: pages.first.events.length, more: pages.first.has_more}))
  check('events: cursor is monotonic', pages.next.cursor >= pages.first.cursor,
    `${pages.first.cursor} -> ${pages.next.cursor}`)

  section('stale cursor past the log rebuilds the timeline')
  const serverTotal = (await fetchJson(page, `/api/sessions/${ref.session_id}/events?after=0&limit=1`)).data.total
  await page.evaluate(sid => {
    localStorage.setItem(`pocker-events:${sid}`,
      JSON.stringify({cursor: 999999, events: [{event: 'stale_phantom'}]}))
  }, ref.session_id)
  await page.reload({waitUntil: 'domcontentloaded'})
  await page.waitForSelector('.felt-table', {timeout: 20000})
  // The rebuild runs after the first event read; wait for the cache to agree
  // with the server instead of reading it mid-flight (Firefox is slower here).
  await page.waitForFunction(({sid, total}) => {
    const raw = localStorage.getItem(`pocker-events:${sid}`)
    if (!raw) return false
    try {
      const parsed = JSON.parse(raw)
      return parsed.cursor === total && parsed.events.length === total
    } catch { return false }
  }, {sid: ref.session_id, total: serverTotal}, {timeout: 20000}).catch(() => {})
  const rebuilt = await storedEvents(page, ref.session_id)
  check('stale cursor: rebuilt from zero', (rebuilt?.cursor ?? -1) === serverTotal,
    `cursor=${rebuilt?.cursor} total=${serverTotal}`)
  check('stale cursor: no phantom event kept', (rebuilt?.events?.length ?? -1) === serverTotal,
    `events=${rebuilt?.events?.length}`)

  section('hidden cards never leak into the DOM')
  const hidden = await page.evaluate(async () => {
    const created = await (await fetch('/api/sessions', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({game_id: 'crazy_eights', seed: 7}),
    })).json()
    return {session_id: created.session_id, opponentHand: created.players[1].hand,
      hiddenCount: created.players[1].hidden_count}
  })
  check('crazy_eights: opponent hand hidden by the API',
    hidden.opponentHand.length === 0 && hidden.hiddenCount === 5, JSON.stringify(hidden))
  await openSession(page, {session_id: hidden.session_id, game_id: 'crazy_eights', version: null})
  check('crazy_eights: UI marks hidden hands', /隐藏手牌/.test(await page.locator('.players-panel').innerText()))
  const handCards = await page.locator('.hand-cards .playing-card').count()
  const tableCards = await page.locator('.public-zone .playing-card').count()
  const totalCards = await page.locator('.playing-card').count()
  check('crazy_eights: only visible cards render',
    handCards === 5 && totalCards === handCards + tableCards,
    `hand=${handCards} table=${tableCards} total=${totalCards}`)

  section('publish interruption: a lost session commit republishes idempotently')
  const publishEntry = designs['ui-publish']
  if (!publishEntry) {
    check('publish: ui-publish seeded', false)
  } else {
    const first = await page.evaluate(async sid => {
      let session = await (await fetch(`/api/designs/${sid}`)).json()
      if (!session.context.confirmation) {
        await fetch(`/api/designs/${sid}/confirm`, {method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ir_hash: session.ir_hash, expected_revision: session.revision})})
        session = await (await fetch(`/api/designs/${sid}`)).json()
      }
      const response = await fetch(`/api/designs/${sid}/publish`, {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({expected_revision: session.revision})})
      return await response.json()
    }, publishEntry.session_id)
    check('publish: first publication',
      first.published === true && first.idempotent === false,
      JSON.stringify({idempotent: first.idempotent, version: first.artifact?.version}))
    // Simulate the crash between the artifact write and the session commit:
    // forget the published summary, then publish again.
    const retry = await page.evaluate(async sid => {
      const session = await (await fetch(`/api/designs/${sid}`)).json()
      await fetch(`/api/designs/${sid}/update`, {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({expected_revision: session.revision, event: 'published_lost',
          context: {published: null}})})
      const fresh = await (await fetch(`/api/designs/${sid}`)).json()
      const response = await fetch(`/api/designs/${sid}/publish`, {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({expected_revision: fresh.revision})})
      return await response.json()
    }, publishEntry.session_id)
    check('publish: retry after a lost commit reuses the version',
      retry.idempotent === true && retry.artifact.version === first.artifact.version,
      JSON.stringify({idempotent: retry.idempotent, version: retry.artifact?.version}))
    const versions = (await fetchJson(page, '/api/games/ui-publish/versions')).data.versions
    check('publish: exactly one version exists', versions.length === 1, `versions=${versions.length}`)
  }

  section('multi-tab races: concurrent confirm and publish stay consistent')
  const raceEntry = designs['ui-race']
  if (!raceEntry) {
    check('multi-tab: ui-race seeded', false)
  } else {
    const tabA = await context.newPage()
    const tabB = await context.newPage()
    tabA.on('pageerror', error => pageErrors.push(`raceA: ${error.message}`))
    tabB.on('pageerror', error => pageErrors.push(`raceB: ${error.message}`))
    await tabA.goto(BASE, {waitUntil: 'domcontentloaded'})
    await tabB.goto(BASE, {waitUntil: 'domcontentloaded'})
    await openDesign(tabA, raceEntry.session_id)
    await openDesign(tabB, raceEntry.session_id)
    await waitEnabled(tabA, '确认当前规则')
    await waitEnabled(tabB, '确认当前规则')
    await Promise.all([raceClick(tabA, '确认当前规则'), raceClick(tabB, '确认当前规则')])
    await sleep(2500)
    const alertA = await tabA.locator('.chat-card [role="alert"]').count()
    const alertB = await tabB.locator('.chat-card [role="alert"]').count()
    check('multi-tab confirm: exactly one loses the race', alertA + alertB === 1,
      `A=${alertA} B=${alertB}`)
    const confirmed = (await fetchJson(page, `/api/designs/${raceEntry.session_id}`)).data
    check('multi-tab confirm: one confirmation recorded',
      !!confirmed.context.confirmation, JSON.stringify(confirmed.context.confirmation ?? null))
    // Reload both so they share the confirmed revision, then race the publish.
    await Promise.all([tabA.reload({waitUntil: 'domcontentloaded'}), tabB.reload({waitUntil: 'domcontentloaded'})])
    await Promise.all([clickDesignNav(tabA), clickDesignNav(tabB)])
    await Promise.all([waitEnabled(tabA, '注册版本'), waitEnabled(tabB, '注册版本')])
    await Promise.all([raceClick(tabA, '注册版本'), raceClick(tabB, '注册版本')])
    // Publishing re-runs the host gate, so wait for the session to settle.
    let registered = null
    const deadline = Date.now() + 90000
    while (Date.now() < deadline) {
      registered = (await fetchJson(page, `/api/designs/${raceEntry.session_id}`)).data
      if (registered.status === 'registered') break
      await sleep(500)
    }
    const versions = (await fetchJson(page, '/api/games/ui-race/versions')).data.versions
    check('multi-tab publish: exactly one version', versions.length === 1, `versions=${versions.length}`)
    check('multi-tab publish: session registered once',
      registered.status === 'registered', registered.status)
    await tabA.close()
    await tabB.close()
  }

  section('responsive layouts are operable')
  let finalRef = ref
  let finalRevision = midRevision
  let finalCursor = cursorBefore
  for (const [width, height, name] of [[1440, 900, '1440x900'], [720, 900, '720'], [390, 844, '390x844']]) {
    const sized = await context.newPage()
    await sized.setViewportSize({width, height})
    sized.on('pageerror', error => pageErrors.push(`responsive ${name}: ${error.message}`))
    await sized.goto(BASE, {waitUntil: 'domcontentloaded'})
    await openSession(sized, ref)
    const navVisible = await sized.locator('.mobile-nav').isVisible()
    const formVisible = await sized.locator('.action-form').first().isVisible().catch(() => false)
    const overflowing = await sized.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2)
    check(`responsive ${name}: mobile nav ${width <= 720 ? 'shown' : 'hidden'}`, navVisible === (width <= 720))
    check(`responsive ${name}: action form visible and no horizontal overflow`, formVisible && !overflowing,
      `form=${formVisible} overflow=${overflowing}`)
    await screenshot(sized, `workspace-${name}`)
    finalRef = await sized.evaluate(() => JSON.parse(localStorage.getItem('pocker-session')))
    finalRevision = await revision(sized)
    finalCursor = (await storedEvents(sized, ref.session_id))?.cursor ?? 0
    await sized.close()
  }

  fs.writeFileSync(MIDGAME_PATH, JSON.stringify({
    ref: finalRef, revision: finalRevision, cursor: finalCursor,
  }, null, 2))
  check('no uncaught page errors in the whole run', pageErrors.length === 0, pageErrors.join(' | '))
}

async function phaseRecover(context, page) {
  const saved = JSON.parse(fs.readFileSync(MIDGAME_PATH, 'utf8'))
  section('backend restart: same session/version/revision, events intact')
  await page.goto(BASE, {waitUntil: 'domcontentloaded'})
  await page.waitForSelector('.felt-table', {timeout: 25000})
  const ref = await page.evaluate(() => JSON.parse(localStorage.getItem('pocker-session')))
  check('recover: same session/version', ref.session_id === saved.ref.session_id
    && ref.version === saved.ref.version, JSON.stringify(ref))
  const restoredRevision = await revision(page)
  check('recover: revision preserved', restoredRevision === saved.revision,
    `${restoredRevision} === ${saved.revision}`)
  const stored = await storedEvents(page, saved.ref.session_id)
  const server = await fetchJson(page, `/api/sessions/${saved.ref.session_id}/events?after=0&limit=1`)
  const total = server.data.total
  check('recover: cursor equals the server total', (stored?.cursor ?? -1) === total,
    `cursor=${stored?.cursor} total=${total}`)
  check('recover: displayed events match the cursor (no loss, no duplication)',
    (stored?.events?.length ?? -1) === total, `events=${stored?.events?.length} total=${total}`)
  await screenshot(page, 'workspace-after-restart-recovered-1440')

  section('finish the recovered game')
  await playSteps(page, 240)
  check('recover: game finishes', await finished(page),
    await page.locator('.felt-top').first().innerText().catch(() => ''))
  await screenshot(page, 'workspace-finished-1440')
  check('recover: no uncaught page errors', pageErrors.length === 0, pageErrors.join(' | '))
}

async function main() {
  const context = await engine.launchPersistentContext(PROFILE, {
    headless: true, viewport: {width: 1440, height: 900},
  })
  const page = context.pages()[0] ?? await context.newPage()
  page.on('pageerror', error => pageErrors.push(error.stack || error.message))

  if (PHASE === 'full') {
    await page.goto(BASE, {waitUntil: 'domcontentloaded'})
    // The first /api/games after a cold start runs every reference playtest. Let
    // it finish before the first reload, so a browser does not report the
    // aborted mount request (WebKit words it "access control checks") as a JS
    // error. The sidebar footer shows the coverage once /api/games resolved.
    await page.waitForFunction(
      () => (document.querySelector('.sidebar-footer .mono')?.textContent || '—') !== '—',
      null, {timeout: 45000}).catch(() => {})
  }

  if (PHASE === 'full') await phaseFull(context, page)
  else if (PHASE === 'recover') await phaseRecover(context, page)
  else throw new Error(`unknown phase ${PHASE}`)

  await context.close()
  const failed = results.filter(item => !item.ok)
  fs.writeFileSync(path.join(OUT_DIR, `m5-browser-${tag}-results.json`),
    JSON.stringify({browser: BROWSER, phase: PHASE, base: BASE, results, pageErrors}, null, 2))
  console.log(`\n[${tag}] ${results.length - failed.length}/${results.length} checks passed`)
  if (failed.length) {
    console.log(`FAILURES: ${failed.map(item => item.name).join(', ')}`)
    process.exit(1)
  }
}

main().catch(error => {
  console.error(`HARNESS ERROR [${tag}]`, error)
  process.exit(2)
})
