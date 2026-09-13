import type {GameEvent} from '../shared/api'
import {describeEvent} from '../shared/util'

// The event timeline renders the human meaning first and keeps the raw host
// operation in a collapsed diagnostics row (frontend spec §7 / §M5).
export function EventTimeline({events, emptyHint}: {events: GameEvent[]; emptyHint?: string}) {
  if (!events.length) return <p className="muted">{emptyHint ?? '暂无事件。'}</p>
  return <div className="replay-timeline">
    {events.map((event, index) => {
      const {title, detail, code} = describeEvent(event)
      const isLast = index === events.length - 1
      return <div className={`replay-event ${isLast ? 'is-active' : ''}`} key={index}>
        <span className="mono">#{String(index + 1).padStart(3, '0')}</span>
        <span className="event-dot" />
        <div>
          <strong>{title}</strong>
          {detail && <small>{detail}</small>}
          <details className="event-raw">
            <summary>诊断 · {code}</summary>
            <pre>{JSON.stringify(event, null, 2)}</pre>
          </details>
        </div>
      </div>
    })}
  </div>
}
