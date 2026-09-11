import {useState} from 'react'
import {api, messageOf, type LoopObservation, type LoopResult} from '../../shared/api'
import {Pill} from '../../components/ui'

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
  const [goal, setGoal] = useState('两人各抽一张比大小，点数大者得 1 分，共 5 轮')
  const [result, setResult] = useState<LoopResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async () => {
    if (!goal.trim() || busy) return
    setBusy(true); setError(''); setResult(null)
    try {
      setResult(await api.runLoop(goal.trim()))
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  const gameId = result?.ir && typeof result.ir.game_id === 'string' ? result.ir.game_id : null

  return <div className="prototype-page new-page">
    <div className="page-heading">
      <div><span className="eyebrow">02 / DESIGN</span><h1>和 Agent 一起设计玩法</h1>
        <p>Agent 只能调用元工具操作规则与计划；每个游戏必须先通过 playtest 才能试玩。</p></div>
      {result && <Pill tone={result.finalized ? 'success' : result.kind === 'unsupported' ? 'warning' : 'danger'}>
        {result.finalized ? '已冻结' : result.kind}</Pill>}
    </div>

    <div className="chat-card">
      <div className="chat-card-head"><div><span className="eyebrow">GOAL</span><h2>描述玩法</h2></div>
        <span className="mono">meta-tools only</span></div>
      <textarea value={goal} onChange={event => setGoal(event.target.value)} disabled={busy}
        placeholder="例如：两人各抽一张比大小，共 5 轮" rows={3} />
      <div className="action-buttons">
        <button className="action-primary" onClick={run} disabled={busy || !goal.trim()}>
          {busy ? '设计中…' : '让 Agent 设计'}
        </button>
      </div>
      {error && <p className="muted">{error}</p>}
      <p className="muted">需要先在「模型设置」保存 API 配置；否则后端会明确返回“请先保存模型配置”。</p>
    </div>

    {result && <>
      <div className="rule-ledger">
        <div className="rule-card"><div><h3>结论</h3><p>{result.message}</p></div>
          <Pill tone={result.finalized ? 'success' : result.kind === 'unsupported' ? 'warning' : 'danger'}>
            {result.kind} · {result.attempts} 步</Pill></div>
        {result.ir && <div className="rule-card"><div><h3>RulesIR</h3>
          <p>kind {String(result.ir.kind)}<br />game_id {String(result.ir.game_id)}<br />
            max_rounds {String(result.ir.max_rounds)}</p></div>
          <Pill tone="success">用户可核对</Pill></div>}
        {result.playtest && <div className="rule-card"><div><h3>Playtest</h3>
          <p>种子 {result.playtest.seeds.join(' / ')}<br />
            wait 覆盖 {result.playtest.covered_wait_nodes.join(', ') || '—'}</p></div>
          <Pill tone={result.playtest.ok ? 'success' : 'danger'}>
            {result.playtest.ok ? '通过' : `${result.playtest.failures.length} 失败`}</Pill></div>}
      </div>

      <div className="chat-card">
        <div className="chat-card-head"><div><span className="eyebrow">OBSERVATIONS</span>
          <h2>元工具调用与返回</h2></div><span className="mono">{result.observations.length}</span></div>
        <div className="replay-timeline">
          {result.observations.map(observation => <div className="replay-event" key={observation.step}>
            <span className="mono">#{observation.step}</span>
            <span className="event-dot" />
            <div><strong>{observation.tool ?? 'parse'} · {observation.ok ? 'ok' : 'rejected'}</strong>
              <small>{observationDetail(observation)}</small></div>
          </div>)}
        </div>
        {result.finalized && gameId &&
          <button className="action-primary" onClick={() => onPlay(gameId)}>开始试玩 <span>↗</span></button>}
      </div>
    </>}
  </div>
}
