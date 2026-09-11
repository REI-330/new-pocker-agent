import type {SessionState} from '../../shared/api'
import {Pill} from '../../components/ui'

export function ReplayPage({state}: {state: SessionState | null}) {
  if (!state) {
    return <div className="prototype-page replay-page">
      <div className="page-heading"><div><span className="eyebrow">04 / REPLAY</span>
        <h1>回放与诊断</h1><p>先开始一局，这里会显示完整事件序列。</p></div></div>
      <p className="muted">暂无事件。</p>
    </div>
  }
  return <div className="prototype-page replay-page">
    <div className="page-heading">
      <div><span className="eyebrow">04 / REPLAY</span><h1>回放与诊断</h1>
        <p>实时状态与回放使用同一套事件模型；revision 单调递增，可精确重放。</p></div>
      <Pill tone="success">REVISION {state.revision}</Pill>
    </div>
    <div className="replay-layout">
      <div className="replay-snapshot">
        <div className="snapshot-header"><span className="mono">SESSION {state.session_id.slice(0, 8)}</span>
          <Pill tone="success">STATE MATCH</Pill></div>
        <div className="snapshot-total"><span className="mono">SCORE</span>
          <strong>{state.players[0]?.score ?? 0}</strong>
          <small>round {state.round} / {state.max_rounds}</small></div>
        <div className="event-detail"><strong>{state.flow_node}</strong><small>phase {state.phase}</small></div>
      </div>
      <div className="replay-timeline">
        <span className="eyebrow">EVENT TIMELINE · {state.events.length} events</span>
        {state.events.map((event, index) =>
          <div className={`replay-event ${index === state.events.length - 1 ? 'is-active' : ''}`} key={index}>
            <span className="mono">#{String(index + 1).padStart(3, '0')}</span>
            <span className="event-dot" />
            <div><strong>{String(event.event)}</strong>
              <small>{JSON.stringify(event).slice(0, 120)}</small></div>
          </div>)}
      </div>
    </div>
  </div>
}
