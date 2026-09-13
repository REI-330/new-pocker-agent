import {useCallback, useEffect, useRef, useState} from 'react'
import {ApiError, api, messageOf, type ChatTurn, type DesignResult, type DesignRun,
  type DesignSession} from '../../shared/api'
import {Pill} from '../../components/ui'
import {DESIGN_STAGES, designStage, shortId, slugify, statusLabelZh,
  verificationProblems} from '../../shared/util'

const STARTERS = [
  '两人各抽一张比大小，点数大者得分，共 5 轮',
  '两人疯狂八，隐藏手牌，8 可以指定花色',
  '做个 UNO 类：同花或同点接牌，2 让下家摸两张',
  '公开市场：每回合用一张手牌换一张市场牌，同点成对移出并加 2 分',
]
const DESIGN_KEY = 'pocker-design-id'
const sleep = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID().slice(0, 32)
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function observationRows(items: unknown[]): Array<Record<string, unknown>> {
  return items.map(item => (item && typeof item === 'object' ? item : {value: item}) as Record<string, unknown>)
}

function observationText(row: Record<string, unknown>): string {
  for (const key of ['error', 'question', 'message', 'detail', 'kind', 'game_kind']) {
    const value = row[key]
    if (typeof value === 'string' && value) return value
  }
  if (Array.isArray(row.covered_wait_nodes) && row.covered_wait_nodes.length) {
    return `wait 覆盖 ${row.covered_wait_nodes.join(', ')}`
  }
  return ''
}

export function DesignPage({onPlay}: {onPlay: (gameId: string, version?: number | null) => void}) {
  const [designId, setDesignId] = useState<string | null>(() => localStorage.getItem(DESIGN_KEY))
  const [session, setSession] = useState<DesignSession | null>(null)
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [result, setResult] = useState<DesignResult | null>(null)
  const [run, setRun] = useState<DesignRun | null>(null)
  const pending = useRef<{message: string; request_id: string} | null>(null)
  const polling = useRef(false)

  const loadDesign = useCallback(async (id: string) => {
    try {
      const restored = await api.getDesign(id)
      setSession(restored)
      setTurns(restored.context.chat ?? [])
      // Recover the latest run: if it is still running (a long turn that spanned
      // a refresh), poll it rather than pretending it finished.
      const runs = await api.designRuns(id, 1).catch(() => null)
      const latest = runs?.runs?.[0]
      if (latest) {
        const full = await api.designRun(id, latest.run_id).catch(() => null)
        if (full) { setRun(full); if (full.status === 'running') void pollRun(id, full.run_id) }
      }
    } catch (err) {
      if (err instanceof ApiError && err.isMissing) {
        localStorage.removeItem(DESIGN_KEY); setDesignId(null)
      } else setError(messageOf(err))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => { if (designId) void loadDesign(designId) }, [designId, loadDesign])

  const pollRun = useCallback(async (id: string, runId: string) => {
    if (polling.current) return
    polling.current = true
    try {
      for (let attempt = 0; attempt < 60; attempt += 1) {
        const current = await api.designRun(id, runId)
        setRun(current)
        if (current.status !== 'running') {
          if (current.response?.session) setSession(current.response.session)
          return
        }
        await sleep(1200)
      }
    } catch { /* a dropped poll is not fatal; the session read is the truth */ }
    finally { polling.current = false }
  }, [])

  const ensureSession = async (message: string): Promise<DesignSession> => {
    if (session) return session
    const created = await api.createDesign(slugify(message), message.slice(0, 800))
    localStorage.setItem(DESIGN_KEY, created.session_id)
    setDesignId(created.session_id)
    setSession(created)
    return created
  }

  const refreshSession = async (id: string) => {
    const fresh = await api.getDesign(id).catch(() => null)
    if (fresh) { setSession(fresh); setTurns(fresh.context.chat ?? []) }
  }

  const send = async (starter?: string) => {
    const message = (starter ?? input).trim()
    if (!message || busy) return
    let active = session
    setError(''); setNotice(''); setBusy('发送中')
    try {
      active = await ensureSession(message)
      const requestId = pending.current?.message === message
        ? pending.current.request_id : newRequestId()
      pending.current = {message, request_id: requestId}
      const data = await api.designMessages(active.session_id, {
        message, expected_revision: active.revision, request_id: requestId,
      })
      pending.current = null
      setSession(data.session)
      setTurns(data.messages?.length ? data.messages : (data.session.context.chat ?? []))
      setResult(data)
      setInput('')
      if (data.run_id) void pollRun(active.session_id, data.run_id)
    } catch (err) {
      if (active && err instanceof ApiError && err.isConflict) {
        // 409: someone else moved the session. Refetch, keep the id, let the
        // user retry with the same request_id instead of losing the draft.
        await refreshSession(active.session_id)
        setNotice('会话已被更新（409 stale_revision），已刷新到最新草案。请重试刚才的消息。')
      } else {
        setError(messageOf(err))
      }
      setInput(message)
    } finally { setBusy('') }
  }

  const withSession = async (label: string, task: (active: DesignSession) => Promise<void>) => {
    if (!session || busy) return
    setBusy(label); setError(''); setNotice('')
    try { await task(session) } catch (err) { setError(messageOf(err)) } finally { setBusy('') }
  }

  const verify = () => withSession('正式验证', async active => {
    const response = await api.verifyDesign(active.session_id, {expected_revision: active.revision})
    setSession(response.session)
    setNotice(response.ok ? '正式验证通过；可以核对并确认。' : '正式验证未通过，请查看失败项。')
  })

  const confirm = () => withSession('确认规则', async active => {
    if (!active.ir_hash) return
    const response = await api.confirmDesign(active.session_id,
      {ir_hash: active.ir_hash, expected_revision: active.revision})
    setSession(response.session)
    setNotice('已记录你对这一版规则的确认。')
  })

  const publish = () => withSession('注册版本', async active => {
    const response = await api.publishDesign(active.session_id,
      {expected_revision: active.revision, ...(title.trim() ? {title: title.trim()} : {})})
    setSession(response.session)
    setNotice(response.idempotent
      ? `这一版规则已经注册过，复用版本 v${response.artifact.version}。`
      : `已注册不可变版本 v${response.artifact.version}。`)
  })

  const irHash = session?.ir_hash ?? null
  const verification = session?.context.verification
  const confirmation = session?.context.confirmation
  const published = session?.context.published
  const verified = !!verification?.ok && verification.ir_hash === irHash
  const confirmed = !!confirmation && confirmation.ir_hash === irHash
  const registered = !!published && published.ir_hash === irHash
  const confirmationStale = !!confirmation && !!irHash && confirmation.ir_hash !== irHash
  const problems = verificationProblems(verification)
  const observations = observationRows(run?.observations?.length
    ? run.observations : (result?.observations ?? []))
  const stage = designStage(session?.status ?? 'draft')
  const needsModel = /模型配置/.test(error)

  return <div className="prototype-page new-page">
    <div className="page-heading">
      <div><span className="eyebrow">02 / DESIGN</span><h1>和 Agent 一起设计玩法</h1>
        <p>像聊天一样把规则说清楚。Agent 只能产生候选规则；正式验证、用户确认与注册是三个独立步骤。</p></div>
      {session && <Pill tone={registered ? 'success' : session.status === 'failed' ? 'danger' : 'neutral'}>
        {statusLabelZh(session.status)} · rev {session.revision}</Pill>}
    </div>

    <StageTracker status={session?.status ?? 'draft'} />

    <div className="new-layout new-chat-layout">
      <div className="chat-card">
        <div className="chat-card-head">
          <div><span className="eyebrow">RULE CO-CREATION</span><h2>规则共创对话</h2>
            {session && <span className="mono">{session.game_id} · {shortId(session.session_id)}</span>}</div>
          <span className="mono">{turns.length} 条消息</span>
        </div>

        <div className="chat-thread">
          {!turns.length && <div className="chat-suggestion">
            <span className="eyebrow">先试一个</span>
            {STARTERS.map(starter =>
              <button className="secondary" key={starter} disabled={!!busy}
                onClick={() => void send(starter)}>{starter}</button>)}
          </div>}
          {turns.map((turn, index) => <div className={`chat-bubble ${turn.role}`} key={index}>
            <span className="chat-role">{turn.role === 'user' ? 'YOU' : 'AGENT'}</span>
            <p>{turn.content}</p>
          </div>)}
          {busy === '发送中' && <div className="chat-bubble assistant"><span className="chat-role">AGENT</span>
            <p>正在理解规则、组装计划并运行验证…</p></div>}
        </div>

        <div className="chat-composer">
          <textarea value={input} onChange={event => setInput(event.target.value)} disabled={!!busy}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault(); void send() } }}
            placeholder="描述玩法，或回答 Agent 的问题（Enter 发送，Shift+Enter 换行）" rows={2} />
          <button className="action-primary" onClick={() => void send()} disabled={!!busy || !input.trim()}>
            {busy === '发送中' ? '设计中…' : '发送'}</button>
        </div>
        {error && <p className="muted" role="alert">{error}
          {needsModel && '（到「模型设置」保存配置后重试）'}</p>}
        {notice && <p className="muted" role="status">{notice}</p>}
      </div>

      <div className="progress-card">
        <span className="eyebrow">LIFECYCLE</span>
        <LifecyclePanel session={session} verified={verified} confirmed={confirmed}
          registered={registered} confirmationStale={confirmationStale} problems={problems}
          busy={!!busy} onVerify={verify} onConfirm={confirm} onPublish={publish}
          title={title} setTitle={setTitle}
          onPlay={() => session && onPlay(session.game_id, published?.version ?? null)} />

        {result && <div className="rule-card"><div><h3>本轮结论</h3>
          <p>{result.message}</p></div>
          <Pill tone={result.kind === 'finalized' ? 'success' : result.kind === 'unsupported' ? 'warning' : 'neutral'}>
            {statusLabelZh(result.kind)} · {result.attempts} 步</Pill></div>}

        {observations.length > 0 && <details className="runtime-log">
          <summary>元工具调用记录（{observations.length}）</summary>
          <div className="replay-timeline">
            {observations.map((row, index) => <div className="replay-event"
              key={String(row.step ?? index)}>
              <span className="mono">#{String(row.step ?? index + 1)}</span>
              <span className="event-dot" />
              <div><strong>{String(row.tool ?? 'parse')} · {row.ok === false ? 'rejected' : 'ok'}</strong>
                <small>{observationText(row)}</small></div>
            </div>)}
          </div>
        </details>}
      </div>
    </div>
  </div>
}

function StageTracker({status}: {status: DesignSession['status']}) {
  const {index, failed} = designStage(status)
  return <ol className="stage-tracker">
    {DESIGN_STAGES.map((stage, position) => {
      const state = failed && position === 0 ? 'is-failed'
        : position < index ? 'is-done' : position === index ? 'is-current' : ''
      return <li className={`stage-step ${state}`} key={stage.id}>
        <span className="stage-dot">{failed && position === 0 ? '!' : position + 1}</span>
        <div><strong>{stage.label}</strong><small>{stage.hint}</small></div>
      </li>
    })}
  </ol>
}

function LifecyclePanel({session, verified, confirmed, registered, confirmationStale, problems,
  busy, onVerify, onConfirm, onPublish, title, setTitle, onPlay}: {
  session: DesignSession | null
  verified: boolean; confirmed: boolean; registered: boolean; confirmationStale: boolean
  problems: string[]; busy: boolean
  onVerify: () => void; onConfirm: () => void; onPublish: () => void
  title: string; setTitle: (value: string) => void; onPlay: () => void
}) {
  if (!session) {
    return <p className="muted">还没有草案。先聊几句，Agent 会在信息足够时提交候选规则。</p>
  }
  const ir = session.ir
  const failure = session.context.failure
  const compiled = session.context.compiled
  return <>
    <div className="rule-card"><div><h3>当前规则</h3>
      {ir ? <p>ir_hash {shortId(session.ir_hash)}<br />
        kind {String((ir as Record<string, unknown>).kind ?? '—')} ·
        game_id {String((ir as Record<string, unknown>).game_id ?? session.game_id)}</p>
        : <p>尚未提交规则草案。</p>}</div>
      <Pill tone={ir ? 'success' : 'neutral'}>{ir ? '可核对' : '待生成'}</Pill></div>

    {failure !== undefined && failure !== null && <div className="rule-card"><div><h3>编译诊断</h3>
      <p className="mono">{JSON.stringify(failure).slice(0, 400)}</p></div>
      <Pill tone="warning">可修复</Pill></div>}

    {compiled !== undefined && <div className="rule-card"><div><h3>已编译计划</h3>
      <p className="mono">{JSON.stringify(compiled).slice(0, 300)}</p></div>
      <Pill tone="neutral">候选</Pill></div>}

    <div className="rule-card"><div><h3>步骤</h3>
      <p>正式验证 {verified ? '已通过' : '未通过'}<br />
        用户确认 {confirmed ? '已确认' : '未确认'}<br />
        注册状态 {registered ? '已注册' : '未注册'}</p></div>
      <Pill tone={verified ? 'success' : 'warning'}>{verified ? 'verified' : 'pending'}</Pill></div>

    {problems.length > 0 && <div className="rule-card"><div><h3>验证失败项</h3>
      <ul className="axis-list">{problems.map((problem, index) => <li key={index}>{problem}</li>)}</ul></div>
      <Pill tone="danger">{problems.length}</Pill></div>}

    {confirmationStale && <div className="state-banner conflict"><span className="state-icon">!</span>
      <div><strong>规则已修改，之前的确认已失效。</strong>
        <small>请重新运行正式验证，再确认当前规则版本。</small></div></div>}

    <div className="action-buttons lifecycle-actions">
      <button className="action-secondary" onClick={onVerify}
        disabled={busy || !ir}>运行正式验证</button>
      <button className="action-secondary" onClick={onConfirm}
        disabled={busy || !ir || !verified || confirmed}>确认当前规则</button>
      <button className="action-primary" onClick={onPublish}
        disabled={busy || !ir || !confirmed || registered}>注册版本</button>
    </div>

    {!registered && <label className="title-field">版本标题（可选）
      <input value={title} onChange={event => setTitle(event.target.value)}
        disabled={busy} placeholder="默认使用玩法描述" maxLength={128} /></label>}

    {registered && <button className="action-primary" onClick={onPlay}>
      开始试玩 v{session.context.published?.version ?? ''} <span>↗</span></button>}
  </>
}
