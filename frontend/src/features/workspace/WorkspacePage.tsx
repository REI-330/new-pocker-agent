import {useState} from 'react'
import type {SessionState} from '../../shared/api'
import {Pill, PlayingCard} from '../../components/ui'

const LABELS: Record<string, string> = {
  submit_expression: '提交答案', no_solution: '我认为无解', give_up: '放弃并看答案',
  next_round: '下一题 / 下一轮',
}

export function WorkspacePage({state, busy, onAct, onRestart, onRefresh}: {
  state: SessionState | null; busy: boolean
  onAct: (action: string, payload: Record<string, unknown>) => void
  onRestart: () => void; onRefresh: () => void
}) {
  const [expression, setExpression] = useState('')

  if (!state) {
    return <div className="prototype-page workspace-page">
      <div className="page-heading"><div><span className="eyebrow">03 / PLAYTEST</span>
        <h1>试玩工作台</h1><p>从玩法库选择并开始一局。这里的每个状态都来自后端解释器。</p></div></div>
      <p className="muted">尚未开局。</p>
    </div>
  }

  const score = state.players[0]?.score ?? 0
  const submit = () => { if (!busy && expression.trim()) onAct('submit_expression', {expression: expression.trim()}) }
  const recent = state.events.slice(-6).reverse()

  return <div className="prototype-page workspace-page">
    <div className="page-heading">
      <div><span className="eyebrow">03 / PLAYTEST · {state.execution_mode}</span>
        <h1>{state.game_id}</h1>
        <p>{state.instructions || `${state.kind} · 第 ${state.round} / ${state.max_rounds} 轮`}</p></div>
      <Pill tone={state.finished ? 'success' : 'success'}>
        {state.finished ? '本局已结束' : `第 ${state.round} / ${state.max_rounds} 轮`}</Pill>
    </div>

    <div className="workspace-grid">
      <section className="side-panel players-panel">
        <div className="panel-title"><span className="eyebrow">PLAYERS</span><h2>玩家与分数</h2></div>
        {state.players.map((player, index) => <div className={`player-row ${index === 0 ? 'active' : ''}`} key={player.id}>
          <span className="player-avatar red">{index === 0 ? '♠' : '♥'}</span>
          <div><strong>{index === 0 ? '你' : player.id}</strong>
            <small>{player.hidden_count ? `隐藏手牌 · ${player.hidden_count} 张` : '可见'}</small></div>
          <b>{String(player.score).padStart(2, '0')}</b>
        </div>)}
        <div className="info-note">分数来自 <code>scores[]</code>；回合由解释器的 wait 节点决定。</div>
      </section>

      <section className="felt-table">
        <div className="felt-top"><span className="mono">REVISION {state.revision}</span>
          <Pill tone={state.finished ? 'success' : 'danger'}>{state.finished ? 'FINISHED' : 'YOUR TURN'}</Pill></div>
        {state.numbers.length > 0 && <div className="table-total">
          <span className="mono">TARGET {state.target}</span>
          <strong>{state.numbers.join(' · ')}</strong>
          <small>用这四张牌各一次，得到 {state.target}</small></div>}
        {state.table.length > 0 && <div className="public-zone">
          <span className="zone-label">TABLE · {state.table.length} CARDS</span>
          {state.table.map(card => <PlayingCard key={card.id} card={card} small />)}
        </div>}
        {state.reveal && state.solution && <div className="state-banner finished">
          <span className="state-icon">✓</span>
          <div><strong>参考答案 {state.solution} = {state.target}</strong>
            <small>{state.feedback || '本题结束'}</small></div></div>}
      </section>

      <section className="side-panel inspector-panel">
        <div className="panel-title"><span className="eyebrow">STATE CHECK</span><h2>规则与事件</h2></div>
        <ul className="check-list">
          <li><span>✓</span>Plan playtested</li>
          <li><span>✓</span>flow node {state.flow_node}</li>
          <li><span>✓</span>phase {state.phase}</li>
        </ul>
        <div className="inspector-divider" />
        <span className="eyebrow">EVENT DETAIL</span>
        <div className="event-detail">
          <strong>{String(recent[0]?.event ?? '—')}</strong>
          <small>revision {state.revision - 1} → {state.revision}</small>
        </div>
      </section>
    </div>

    {!state.finished && <section className="action-dock">
      <div className="dock-heading">
        <div><span className="eyebrow">LEGAL ACTIONS</span><h2>选择下一步</h2></div>
        <span className="mono action-source">source: legal_actions[] = {state.legal_actions.join(', ')}</span>
      </div>
      {state.legal_actions.includes('submit_expression') && <form className="expression-form"
        onSubmit={event => { event.preventDefault(); submit() }}>
        <label htmlFor="expression">输入算式</label>
        <div className="expression-input">
          <input id="expression" value={expression} onChange={event => setExpression(event.target.value)}
            disabled={busy} autoComplete="off" spellCheck={false}
            placeholder="例如 (8 / (3 - 8 / 3))" maxLength={256} />
          <button className="action-primary" type="submit" disabled={busy || !expression.trim()}>提交答案</button>
        </div>
      </form>}
      <div className="action-buttons">
        {state.legal_actions.filter(action => action !== 'submit_expression').map(action =>
          <button className={action === 'give_up' ? 'action-ghost' : 'action-secondary'} key={action}
            disabled={busy} onClick={() => onAct(action, {})}>{LABELS[action] ?? action}</button>)}
      </div>
    </section>}

    {state.finished && <section className="action-dock">
      <div className="dock-heading"><div><span className="eyebrow">RESULT</span>
        <h2>得分 {score} / {state.max_rounds}</h2></div>
        <span className="mono">{state.winners.length ? `winners: ${state.winners.join(', ')}` : 'no winner（练习局只记分）'}</span></div>
      <div className="action-buttons">
        <button className="action-primary" onClick={onRestart}>再来一局</button>
        <button className="action-secondary" onClick={onRefresh}>刷新状态</button>
      </div>
    </section>}

    <section className="action-dock">
      <div className="timeline-row"><span className="mono">EVENT TIMELINE</span>
        <div className="timeline">
          {recent.reverse().map((event, index) =>
            <span key={index} className={index === recent.length - 1 ? 'done' : ''}>
              {String(event.event)}<i /></span>)}
        </div></div>
    </section>
  </div>
}
