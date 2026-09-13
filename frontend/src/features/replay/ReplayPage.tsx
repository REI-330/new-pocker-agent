import type {GameEvent, SessionState} from '../../shared/api'
import {shortId} from '../../shared/util'
import {Pill} from '../../components/ui'
import {EventTimeline} from '../../components/EventTimeline'

export function ReplayPage({state, events}: {state: SessionState | null; events: GameEvent[]}) {
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
        <p>实时状态与回放使用同一套事件模型；revision 单调递增，事件游标可断点续读。</p></div>
      <Pill tone="success">REVISION {state.revision}</Pill>
    </div>
    <div className="replay-layout">
      <div className="replay-snapshot">
        <div className="snapshot-header"><span className="mono">SESSION {shortId(state.session_id)}</span>
          <Pill tone="success">STATE MATCH</Pill></div>
        <div className="snapshot-total"><span className="mono">SCORE</span>
          <strong>{state.players[0]?.score ?? 0}</strong>
          <small>round {state.round} / {state.max_rounds}
            {state.version !== null && state.version !== undefined ? ` · v${state.version}` : ''}</small></div>
        <div className="event-detail"><strong>{state.flow_node}</strong><small>phase {state.phase}</small></div>
      </div>
      <div className="replay-timeline">
        <span className="eyebrow">EVENT TIMELINE · {events.length} events</span>
        <EventTimeline events={events} />
      </div>
    </div>
  </div>
}
