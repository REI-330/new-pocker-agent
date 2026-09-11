import {useState} from 'react'
import {api, messageOf, type ChatMessage, type LoopObservation, type LoopResult} from '../../shared/api'
import {Pill} from '../../components/ui'

const STARTERS = [
  '两人各抽一张比大小，点数大者得分，共 5 轮',
  '两人疯狂八，隐藏手牌，8 可以指定花色',
  '做个 UNO 类：同花或同点接牌，2 让下家摸两张',
  '无下注 21 点，我对庄家，共 3 轮',
]

function observationDetail(observation: LoopObservation): string {
  if (observation.error) return observation.error
  if (observation.question) return observation.question
  if (observation.message) return observation.message
  if (observation.covered_wait_nodes?.length) {
    return `wait 覆盖 ${observation.covered_wait_nodes.join(', ')}`
  }
  if (observation.game_kind) return observation.game_kind
  return ''
}

export function DesignPage({onPlay}: {onPlay: (gameId: string) => void}) {
  const [turns, setTurns] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [result, setResult] = useState<LoopResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const send = async (text?: string) => {
    const message = (text ?? input).trim()
    if (!message || busy) return
    const history = turns
    setTurns([...history, {role: 'user', content: message}])
    setInput(''); setError(''); setBusy(true)
    try {
      const data = await api.runLoop(message, history)
      setResult(data)
      // the server returns the clean transcript, so the thread survives re-renders
      setTurns(data.messages?.length ? data.messages : [...history, {role: 'user', content: message}])
    } catch (err) {
      setError(messageOf(err))
      setTurns(history)
      setInput(message)
    } finally {
      setBusy(false)
    }
  }

  const gameId = result?.ir && typeof result.ir.game_id === 'string' ? result.ir.game_id : null
  const needsModel = /模型配置/.test(error)

  return <div className="prototype-page new-page">
    <div className="page-heading">
      <div><span className="eyebrow">02 / DESIGN</span><h1>和 Agent 一起设计玩法</h1>
        <p>像聊天一样把规则说清楚。Agent 只能调用元工具操作规则与计划；先通过 playtest 才能试玩。</p></div>
      {result && <Pill tone={result.finalized ? 'success' : result.kind === 'unsupported' ? 'warning' : 'neutral'}>
        {result.finalized ? '已冻结 · 可试玩' : result.kind}</Pill>}
    </div>

    <div className="new-layout new-chat-layout">
      <div className="chat-card">
        <div className="chat-card-head">
          <div><span className="eyebrow">RULE CO-CREATION</span><h2>规则共创对话</h2></div>
          <span className="mono">{turns.length} 条消息</span>
        </div>

        <div className="chat-thread">
          {!turns.length && <div className="chat-suggestion">
            <span className="eyebrow">先试一个</span>
            {STARTERS.map(starter =>
              <button className="secondary" key={starter} disabled={busy}
                onClick={() => void send(starter)}>{starter}</button>)}
          </div>}
          {turns.map((turn, index) => <div className={`chat-bubble ${turn.role}`} key={index}>
            <span className="chat-role">{turn.role === 'user' ? 'YOU' : 'AGENT'}</span>
            <p>{turn.content}</p>
          </div>)}
          {busy && <div className="chat-bubble assistant"><span className="chat-role">AGENT</span>
            <p>正在理解规则、组装计划并运行 playtest…</p></div>}
        </div>

        <div className="chat-composer">
          <textarea value={input} onChange={event => setInput(event.target.value)} disabled={busy}
            onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send() } }}
            placeholder="描述玩法，或回答 Agent 的问题（Enter 发送，Shift+Enter 换行）" rows={2} />
          <button className="action-primary" onClick={() => void send()} disabled={busy || !input.trim()}>
            {busy ? '设计中…' : '发送'}</button>
        </div>
        {error && <p className="muted" role="alert">{error}
          {needsModel && '（到「模型设置」保存配置后重试）'}</p>}
      </div>

      <div className="progress-card">
        <span className="eyebrow">RESULT</span>
        {!result && <p className="muted">还没有结果。先聊几句，Agent 会在信息足够时提交规则草案。</p>}
        {result && <>
          <div className="rule-card"><div><h3>结论</h3><p>{result.message}</p></div>
            <Pill tone={result.finalized ? 'success' : result.kind === 'unsupported' ? 'warning' : 'danger'}>
              {result.kind} · {result.attempts} 步</Pill></div>
          {result.ir && <div className="rule-card"><div><h3>RulesIR</h3>
            <p>kind {String(result.ir.kind)}<br />game_id {String(result.ir.game_id)}<br />
               max_rounds {String(result.ir.max_rounds ?? '—')}</p></div>
            <Pill tone="success">可核对</Pill></div>}
          {result.playtest && <div className="rule-card"><div><h3>Playtest</h3>
            <p>种子 {result.playtest.seeds.join(' / ')}<br />
               wait 覆盖 {result.playtest.covered_wait_nodes.join(', ') || '—'}</p></div>
            <Pill tone={result.playtest.ok ? 'success' : 'danger'}>
              {result.playtest.ok ? '通过' : `${result.playtest.failures.length} 失败`}</Pill></div>}
          {result.finalized && gameId &&
            <button className="action-primary" onClick={() => onPlay(gameId)}>开始试玩</button>}
          {result.observations.length > 0 && <details className="runtime-log">
            <summary>元工具调用记录（{result.observations.length}）</summary>
            <div className="replay-timeline">
              {result.observations.map(observation => <div className="replay-event" key={observation.step}>
                <span className="mono">#{observation.step}</span>
                <span className="event-dot" />
                <div><strong>{observation.tool ?? 'parse'} · {observation.ok ? 'ok' : 'rejected'}</strong>
                  <small>{observationDetail(observation)}</small></div>
              </div>)}
            </div>
          </details>}
        </>}
      </div>
    </div>
  </div>
}
