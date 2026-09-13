import {useState} from 'react'
import type {GameEvent, SessionState} from '../../shared/api'
import {shortId} from '../../shared/util'
import {Pill, PlayingCard} from '../../components/ui'
import {ActionForm} from '../../components/ActionForm'
import {EventTimeline} from '../../components/EventTimeline'
import {ZoneView} from '../../components/ZoneView'

const LABELS: Record<string, string> = {
  submit_expression: '提交答案', no_solution: '我认为无解', give_up: '放弃并看答案',
  play: '出牌', draw: '摸牌', next_round: '下一题 / 下一轮',
}
const SUIT_NAMES: Record<string, string> = {S: '黑桃 ♠', H: '红桃 ♥', D: '方块 ♦', C: '梅花 ♣'}

// One workbench for every game. Published composed games are rendered entirely
// from ``zones`` + ``actions``; the 0.4 reference games have neither, so they
// fall back to their field-shaped controls. Neither branch decides legality.
export function WorkspacePage({state, events, busy, onAct, onRestart, onRefresh}: {
  state: SessionState | null; events: GameEvent[]; busy: boolean
  onAct: (actionId: string, inputValues: Record<string, unknown>) => void
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
  const descriptorDriven = !!state.actions?.length
  const zones = state.zones ?? {}
  const versionLabel = state.version === null || state.version === undefined
    ? '内置玩法' : `v${state.version}`
  const recent = events.slice(-40)

  // ----- legacy reference-game controls (no descriptors in a 0.4 plan) -----
  const legal = state.legal_card_indices ?? []
  const canChooseCards = legal.length > 0
  const chosen = (() => {
    if (!mine) return 0
    const found = mine.hand.findIndex(card => card.id === chosenId)
    if (found >= 0 && (!canChooseCards || legal.includes(found))) return found
    if (!mine.hand.length) return 0
    return Math.min(legal.length ? legal[0] : 0, mine.hand.length - 1)
  })()
  const selected = mine?.hand[chosen]
  const isWild = !!selected && (state.wild_ranks ?? []).includes(selected.rank)
  const score = mine?.score ?? 0

  const fireLegacy = (action: string) => {
    if (busy) return
    const inputValues: Record<string, unknown> = {}
    if (action === 'play') { inputValues.card_index = chosen; inputValues.declared_suit = suit }
    if (action === 'raise') inputValues.amount = amount
    onAct(action, inputValues)
  }


  return <div className="prototype-page workspace-page">
    <div className="page-heading">
      <div><span className="eyebrow">03 / PLAYTEST · {state.execution_mode}</span>
        <h1>{state.game_id}</h1>
        <p>{state.instructions || `${state.kind} · 第 ${state.round} / ${state.max_rounds} 轮`}</p></div>
      <div className="heading-pills">
        <Pill tone="neutral">{versionLabel}</Pill>
        <Pill tone={state.finished ? 'success' : 'success'}>
          {state.finished ? '本局已结束' : `第 ${state.round} / ${state.max_rounds} 轮`}</Pill>
      </div>
    </div>

    <div className="workspace-grid">
      <section className="side-panel players-panel">
        <div className="panel-title"><span className="eyebrow">PLAYERS</span><h2>玩家与信息</h2></div>
        {state.players.map((player, seat) => <div
          className={`player-row ${seat === 0 ? 'active' : ''}`} key={player.id}>
          <span className="player-avatar red">{seat === 0 ? '♠' : '♥'}</span>
          <div><strong>{seat === 0 ? '你' : player.id}</strong>
            <small>{player.hidden_count
              ? `隐藏手牌 · ${player.hidden_count} 张`
              : player.hand.length ? `${player.hand.length} 张可见` : '手牌见牌区'}</small></div>
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
          <Pill tone={state.finished ? 'success' : 'danger'}>
            {state.finished ? 'FINISHED' : `${state.current_player} · YOUR TURN`}</Pill></div>
        {descriptorDriven
          ? <div className="zone-grid">
              {Object.entries(zones).map(([zoneId, zone]) =>
                <ZoneView key={zoneId} zoneId={zoneId} zone={zone} compact />)}
            </div>
          : <LegacyFelt state={state} />}
      </section>

      <section className="side-panel inspector-panel">
        <div className="panel-title"><span className="eyebrow">STATE CHECK</span><h2>规则与事件</h2></div>
        <ul className="check-list">
          <li><span>✓</span>flow node {state.flow_node}</li>
          <li><span>✓</span>phase {state.phase}</li>
          <li><span>✓</span>revision {state.revision} · {recent.length} 事件</li>
        </ul>
        <div className="inspector-divider" />
        <span className="eyebrow">EXTRA STATE</span>
        <ExtraState state={state} />
      </section>
    </div>

    {!descriptorDriven && mine && <section className="felt-table">
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
    </section>}

    {!state.finished && <section className="action-dock">
      <div className="dock-heading">
        <div><span className="eyebrow">LEGAL ACTIONS</span><h2>选择下一步</h2></div>
        <span className="mono action-source">source: legal_actions[] = {state.legal_actions.join(', ')}</span>
      </div>
      {descriptorDriven
        ? <div className="action-forms">
            {state.legal_actions.map(actionId => {
              const descriptor = state.actions?.find(item => item.id === actionId)
              if (!descriptor) return <button className="action-secondary" key={actionId}
                disabled={busy} onClick={() => onAct(actionId, {})}>{actionId}</button>
              return <ActionForm key={`${actionId}-${state.revision}`} state={state}
                descriptor={descriptor} busy={busy} onSubmit={onAct} />
            })}
            {!state.legal_actions.length && <p className="muted">当前没有可用动作。</p>}
          </div>
        : <LegacyActions state={state} busy={busy} expression={expression}
            setExpression={setExpression} amount={amount} setAmount={setAmount}
            onFire={fireLegacy} onAct={onAct} />}
    </section>}

    {state.finished && <section className="action-dock">
      <div className="dock-heading"><div><span className="eyebrow">RESULT</span>
        <h2>{state.kind === 'arithmetic'
          ? `得分 ${score} / ${state.max_rounds}`
          : (state.winners[0] === 'player-1' ? '你赢了' : `获胜者 ${state.winners.join(', ') || '—'}`)}</h2></div>
        <span className="mono">{state.winners.length ? `winners: ${state.winners.join(', ')}` : 'no winner（练习局只记分）'}</span></div>
      <div className="action-buttons">
        <button className="action-primary" onClick={onRestart}>再来一局</button>
        <button className="action-secondary" onClick={onRefresh}>刷新状态</button>
      </div>
    </section>}

    <section className="action-dock">
      <div className="dock-heading"><div><span className="eyebrow">EVENT TIMELINE</span>
        <h2>事件与变化</h2></div>
        <span className="mono">{events.length} loaded</span></div>
      <EventTimeline events={recent} emptyHint="这一局还没有事件。" />
    </section>
  </div>
}

function ExtraState({state}: {state: SessionState}) {
  const rows: Array<[string, unknown]> = [
    ['scores', state.scores], ['pot', state.pot], ['stacks', state.stacks],
    ['committed', state.committed], ['teams', state.teams], ['tricks', state.tricks_won],
    ['trump', state.trump], ['current_bet', state.current_bet], ['min_raise', state.min_raise],
    ['pairs', state.pairs],
  ]
  const shown = rows.filter(([, value]) => value !== undefined && value !== null)
  if (!shown.length) return <p className="muted">此玩法没有额外计分状态。</p>
  return <dl className="extra-state">
    {shown.map(([key, value]) => <div key={key}>
      <dt className="mono">{key}</dt>
      <dd>{Array.isArray(value) ? value.join(' · ') : String(value)}</dd>
    </div>)}
  </dl>
}

// The 0.4 reference games keep their table fields; the composed path never
// reaches here because it renders zones instead.
function LegacyFelt({state}: {state: SessionState}) {
  return <>
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
  </>
}

function LegacyActions({state, busy, expression, setExpression, amount, setAmount, onFire, onAct}: {
  state: SessionState; busy: boolean
  expression: string; setExpression: (value: string) => void
  amount: number; setAmount: (value: number) => void
  onFire: (action: string) => void
  onAct: (actionId: string, inputValues: Record<string, unknown>) => void
}) {
  return <>
    {state.legal_actions.includes('submit_expression') && <form className="expression-form"
      onSubmit={event => { event.preventDefault()
        if (!busy && expression.trim()) onAct('submit_expression', {expression: expression.trim()}) }}>
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
          key={action} disabled={busy} onClick={() => onFire(action)}>{LABELS[action] ?? action}</button>)}
    </div>
  </>
}

export function snapshotLabel(state: SessionState | null): string {
  return state ? `${state.game_id} · rev ${state.revision} · ${shortId(state.session_id)}` : '—'
}
