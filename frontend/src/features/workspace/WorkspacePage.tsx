import {useState} from 'react'
import type {SessionState} from '../../shared/api'
import {Pill, PlayingCard} from '../../components/ui'

const LABELS: Record<string, string> = {
  submit_expression: '提交答案', no_solution: '我认为无解', give_up: '放弃并看答案',
  play: '出牌', draw: '摸牌', next_round: '下一题 / 下一轮',
}
const SUIT_NAMES: Record<string, string> = {S: '黑桃 ♠', H: '红桃 ♥', D: '方块 ♦', C: '梅花 ♣'}

export function WorkspacePage({state, busy, onAct, onRestart, onRefresh}: {
  state: SessionState | null; busy: boolean
  onAct: (action: string, payload: Record<string, unknown>) => void
  onRestart: () => void; onRefresh: () => void
}) {
  const [expression, setExpression] = useState('')
  const [chosenId, setChosenId] = useState<string | null>(null)
  const [suit, setSuit] = useState('S')
  const [amount, setAmount] = useState(20)

  if (!state) {
    return <div className="prototype-page workspace-page">
      <div className="page-heading"><div><span className="eyebrow">03 / PLAYTEST</span>
        <h1>试玩工作台</h1><p>从玩法库或新建玩法选择一个游戏开始。每个状态都来自后端解释器。</p></div></div>
      <p className="muted">尚未开局。</p>
    </div>
  }

  const mine = state.players[0]
  const legal = state.legal_card_indices ?? []
  const canChooseCards = legal.length > 0
  // Track the chosen card by identity, not by position: a draw, a recycle or a
  // restart moves cards around, and a stale index would silently select a
  // different card (or one that is not playable at all).
  const chosen = (() => {
    const found = mine.hand.findIndex(card => card.id === chosenId)
    if (found >= 0 && (!canChooseCards || legal.includes(found))) return found
    if (!mine.hand.length) return 0
    return Math.min(legal.length ? legal[0] : 0, mine.hand.length - 1)
  })()
  const selected = mine.hand[chosen]
  const isWild = !!selected && (state.wild_ranks ?? []).includes(selected.rank)
  const score = mine.score
  const recent = state.events.slice(-6).reverse()

  const fire = (action: string) => {
    if (busy) return
    const payload: Record<string, unknown> = {}
    if (action === 'play') { payload.card_index = chosen; payload.declared_suit = suit }
    if (action === 'raise') payload.amount = amount
    onAct(action, payload)
  }

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
        <div className="panel-title"><span className="eyebrow">PLAYERS</span><h2>玩家与信息</h2></div>
        {state.players.map((player, seat) => <div className={`player-row ${seat === 0 ? 'active' : ''}`} key={player.id}>
          <span className="player-avatar red">{seat === 0 ? '♠' : '♥'}</span>
          <div><strong>{seat === 0 ? '你' : player.id}</strong>
            <small>{player.hidden_count ? `隐藏手牌 · ${player.hidden_count} 张` : `${player.hand.length} 张可见`}</small></div>
          <b>{String(state.stacks?.[seat] ?? player.score).padStart(2, '0')}</b>
        </div>)}
        <div className="info-note">
          {state.private_hands
            ? '对手手牌由解释器隐藏（info_set：按视角可见性）。'
            : '所有手牌可见。'}
        </div>
      </section>

      <section className="felt-table">
        <div className="felt-top"><span className="mono">REVISION {state.revision}</span>
          <Pill tone={state.finished ? 'success' : 'danger'}>{state.finished ? 'FINISHED' : 'YOUR TURN'}</Pill></div>
        {state.numbers.length > 0 && <div className="table-total">
          <span className="mono">TARGET {state.target}</span>
          <strong>{state.numbers.join(' · ')}</strong>
          <small>用这四张牌各一次，得到 {state.target}</small></div>}
        {state.table.length > 0 && <div className="public-zone">
          <span className="zone-label">TABLE / DISCARD</span>
          {state.table.map(card => <PlayingCard key={card.id} card={card} />)}</div>}
        {state.pot !== undefined && <div className="table-total">
          <span className="mono">POT</span><strong>{state.pot}</strong>
          <small>{state.current_bet ? `本轮最高下注 ${state.current_bet}` : '等待下注'}</small></div>}
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

    <section className="felt-table">
      <div className="felt-top"><span className="mono">YOUR HAND · {mine.hand.length} CARDS</span>
        {canChooseCards && <Pill tone="warning">可选 {legal.length} 张</Pill>}</div>
      <div className="hand-cards">
        {mine.hand.map((card, position) => {
          const playable = !canChooseCards || legal.includes(position)
          return <button className={'card-choice ' + (chosen === position ? 'selected' : '')}
            key={card.id} disabled={busy || !playable}
            title={playable ? undefined : '不符合当前出牌条件'}
            onClick={() => setChosenId(card.id)} aria-pressed={chosen === position}>
            <PlayingCard card={card} /></button>
        })}
      </div>
      {isWild && <label className="suit-choice">万能牌指定花色
        <select value={suit} onChange={event => setSuit(event.target.value)} disabled={busy}>
          {(state.suit_options ?? ['S', 'H', 'D', 'C']).map(option =>
            <option key={option} value={option}>{SUIT_NAMES[option] ?? option}</option>)}
        </select></label>}
    </section>

    {!state.finished && <section className="action-dock">
      <div className="dock-heading">
        <div><span className="eyebrow">LEGAL ACTIONS</span><h2>选择下一步</h2></div>
        <span className="mono action-source">source: legal_actions[] = {state.legal_actions.join(', ')}</span>
      </div>
      {state.legal_actions.includes('submit_expression') && <form className="expression-form"
        onSubmit={event => { event.preventDefault(); if (!busy && expression.trim()) onAct('submit_expression', {expression: expression.trim()}) }}>
        <label htmlFor="expression">输入算式</label>
        <div className="expression-input">
          <input id="expression" value={expression} onChange={event => setExpression(event.target.value)}
            disabled={busy} autoComplete="off" spellCheck={false}
            placeholder="例如 (8 / (3 - 8 / 3))" maxLength={256} />
          <button className="action-primary" type="submit" disabled={busy || !expression.trim()}>提交答案</button>
        </div>
      </form>}
      <div className="action-buttons">
        {state.legal_actions.includes('raise') && <label className="raise-choice">加注到
          <input type="number" min={1} step={1} value={amount} disabled={busy}
            onChange={event => setAmount(Number(event.target.value))} /></label>}
        {state.legal_actions.filter(action => action !== 'submit_expression').map(action =>
          <button className={['give_up', 'no_solution'].includes(action) ? 'action-ghost' : 'action-secondary'}
            key={action} disabled={busy} onClick={() => fire(action)}>{LABELS[action] ?? action}</button>)}
      </div>
    </section>}

    {state.finished && <section className="action-dock">
      <div className="dock-heading"><div><span className="eyebrow">RESULT</span>
        <h2>{state.kind === 'arithmetic' ? `得分 ${score} / ${state.max_rounds}` : (state.winners[0] === 'player-1' ? '你赢了' : `获胜者 ${state.winners.join(', ') || '—'}`)}</h2></div>
        <span className="mono">{state.winners.length ? `winners: ${state.winners.join(', ')}` : 'no winner（练习局只记分）'}</span></div>
      <div className="action-buttons">
        <button className="action-primary" onClick={onRestart}>再来一局</button>
        <button className="action-secondary" onClick={onRefresh}>刷新状态</button>
      </div>
    </section>}

    <section className="action-dock">
      <div className="timeline-row"><span className="mono">EVENT TIMELINE</span>
        <div className="timeline">
          {recent.reverse().map((event, position) =>
            <span key={position} className={position === recent.length - 1 ? 'done' : ''}>
              {String(event.event)}<i /></span>)}
        </div></div>
    </section>
  </div>
}
