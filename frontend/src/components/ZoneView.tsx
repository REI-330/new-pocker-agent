import type {Zone} from '../shared/api'
import {visibilityLabel, zoneTitle} from '../shared/util'
import {PlayingCard} from './ui'

// One generic zone. It renders whatever the backend projection contains: a
// hidden zone shows face-down backs and its count, never its card identities.
export function ZoneView({zoneId, zone, selected = [], selectable, onToggle, busy, compact}: {
  zoneId: string
  zone: Zone
  selected?: string[]
  selectable?: string[]
  onToggle?: (cardId: string) => void
  busy?: boolean
  compact?: boolean
}) {
  const canPick = (id: string) => !!selectable?.includes(id) && !!onToggle
  return <section className={'zone-view' + (compact ? ' is-compact' : '')}>
    <header className="zone-head">
      <span className="zone-name">{zoneTitle(zoneId)}</span>
      <span className="mono zone-meta">{zone.count} 张 · {visibilityLabel(zone.visibility)}</span>
    </header>
    {zone.visible && zone.cards.length > 0
      ? <div className="zone-cards">
          {zone.cards.map(card => {
            const pickable = canPick(card.id)
            const isSelected = selected.includes(card.id)
            return <button type="button" key={card.id}
              className={'card-choice' + (isSelected ? ' selected' : '') + (pickable ? '' : ' is-locked')}
              disabled={busy || !pickable} aria-pressed={isSelected}
              title={pickable ? '选择这张牌' : undefined}
              onClick={() => onToggle?.(card.id)}>
              <PlayingCard card={card} small={compact} />
            </button>
          })}
        </div>
      : <div className="zone-cards">
          {Array.from({length: zone.visible ? 0 : zone.count}).map((_, index) =>
            <span className="back-card" key={index} title="隐藏牌">♠</span>)}
          {zone.visible && zone.cards.length === 0 && <span className="muted">空</span>}
          {!zone.visible && zone.count === 0 && <span className="muted">空</span>}
        </div>}
    {zone.owner !== null && zone.owner !== undefined &&
      <span className="zone-owner">owner · player-{zone.owner + 1}</span>}
  </section>
}
