import {useState} from 'react'
import {API, type Card, type Runtime, type ActionArguments} from '../api'
import {FamilyActions} from './FamilyActions'

const suits: Record<string,string> = {S:'spades',H:'hearts',D:'diamonds',C:'clubs','♠':'spades','♥':'hearts','♦':'diamonds','♣':'clubs'}
const ranks: Record<string,string> = {A:'ace',K:'king',Q:'queen',J:'jack'}
const labels: Record<string,string> = {play:'出牌',draw:'摸牌',discard:'弃牌',pass:'跳过'}
export function PlayingCard({card}: {card: Card}) {
  const [failed, setFailed] = useState(false)
  const filename = (ranks[card.rank] || card.rank) + '_of_' + suits[card.suit] + '.svg'
  return suits[card.suit] && !failed
    ? <img className="playing-card" src={API + '/assets/cards/' + filename} alt={card.rank + card.suit} onError={() => setFailed(true)} />
    : <span className="card">{card.rank}{card.suit}</span>
}
export function GameTable({state, busy, canStart, start, act, refresh}: {
  state: Runtime | null; busy: boolean; canStart: boolean; start: () => void;
  act: (action: string, index: number, args?: ActionArguments) => void; refresh: () => void;
}) {
  const [index, setIndex] = useState(0)
  const finishLabels: Record<string,string> = {round_limit:'已完成约定轮数',deck_exhausted:'剩余牌不足以开始下一轮',no_legal_actions:'规则没有可执行动作',hand_empty:'已出完所有手牌',all_blocked:'所有玩家均无法继续，按约定规则结算'}
  return <section className="panel runtime">
    <div className="panel-head"><div><span className="kicker">04 / PLAY</span><h2>单人试玩</h2></div>
      <span className="pill">{state ? (state.finished ? '本局结束' : '第 ' + state.round + ' / ' + state.max_rounds + ' 轮') : '等待规则确认'}</span>
    </div>
    {!state ? <div className="empty tall"><p>你是 player-1，其他玩家由电脑自动行动。</p><button className="primary" onClick={start} disabled={!canStart || busy}>开始试玩</button></div> : <>
      <p className="runtime-caption">阶段：{state.phase} · 牌堆剩余 {state.deck_remaining} 张{!state.kind && ' · 演示模式展示所有手牌'}</p>
      {state.instructions && <p className="runtime-caption">{state.instructions}</p>}
      {state.numbers && <p className="puzzle-target">本题数值：{state.numbers.join('，')} <strong>目标 {state.target}</strong></p>}
      {state.active_suit && <p className="runtime-caption">当前要跟的花色：{{S:'黑桃 ♠',H:'红桃 ♥',D:'方块 ♦',C:'梅花 ♣'}[state.active_suit] || state.active_suit}</p>}
      {state.kind !== 'blackjack' && <div className="table"><div className="table-label">桌面</div>{state.table.length ? state.table.map((c,i) => <PlayingCard key={i + c.rank + c.suit} card={c} />) : <span className="muted">等待出牌</span>}</div>}
      <div className="hands">{state.players.map(p => <div className="hand" key={p.id}><strong>{p.label || (p.id === 'player-1' ? '你' : p.id + ' · 电脑')} · {p.score} 分{p.total != null ? ' · '+p.total+'点' : ''}</strong>
        <div className="hand-cards">{p.hand.map((c,i) => p.id === 'player-1' && !state.finished && state.kind !== 'blackjack' ?
          <button className={'card-choice ' + (index === i ? 'selected' : '')} aria-label={'选择手牌 ' + (i+1) + ' ' + c.rank + c.suit} aria-pressed={index === i} key={i+c.rank+c.suit} onClick={() => setIndex(i)} disabled={busy || (!!state.legal_card_indices && !state.legal_card_indices.includes(i))} title={state.legal_card_indices && !state.legal_card_indices.includes(i) ? '不符合当前出牌条件' : undefined}><PlayingCard card={c} /></button> :
          <PlayingCard card={c} key={i+c.rank+c.suit} />)}{!!p.hidden_count && <span className="card-back" aria-label={p.hidden_count+'张隐藏手牌'}>♠<small>{p.hidden_count} 张</small></span>}</div>
      </div>)}</div>
      {state.feedback && <p className="game-feedback" role="status">{state.feedback}</p>}
      {state.finished ? <div className="runtime-complete" role="status"><strong>{state.result_title || (state.winners.length === 1 ? (state.winners[0] === 'player-1' ? '你赢了！' : '获胜者：' + state.winners[0]) : '本局平局')}</strong><span>{finishLabels[state.finish_reason] || state.finish_reason}</span><button className="primary" onClick={start} disabled={!canStart || busy}>再来一局</button></div> :
        <FamilyActions key={state.session_id+':'+state.round} state={state} busy={busy || !canStart} index={index} act={(action,i,args)=>{act(action,i,args); if(action!=='submit_expression')setIndex(0)}}/>}
      <div className="actions"><button className="secondary" onClick={refresh} disabled={busy}>刷新牌局</button>
        <button className="secondary" onClick={start} disabled={!canStart || busy}>重新开局</button></div>
      <details className="runtime-log"><summary>本局事件（{state.events.length}）</summary><pre>{JSON.stringify(state.events,null,2)}</pre></details>
    </>}
  </section>
}
