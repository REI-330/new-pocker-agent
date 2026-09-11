import type {Capability, Card} from '../shared/api'

const SUIT_FILE: Record<string, string> = {S: 'spades', H: 'hearts', D: 'diamonds', C: 'clubs'}
const RANK_FILE: Record<string, string> = {A: 'ace', K: 'king', Q: 'queen', J: 'jack'}

export function PlayingCard({card, small}: {card: Card; small?: boolean}) {
  const file = `${RANK_FILE[card.rank] ?? card.rank}_of_${SUIT_FILE[card.suit] ?? 'spades'}.svg`
  return <img className={'playing-card' + (small ? ' is-small' : '')}
              src={`/assets/cards/${file}`} alt={`${card.rank}${card.suit}`} />
}

const TONE: Record<string, string> = {
  stable: 'success', experimental: 'warning', planned: 'neutral', deprecated: 'danger',
}

export function Pill({children, tone = 'neutral'}: {children: React.ReactNode; tone?: string}) {
  return <span className={`prototype-pill tone-${tone}`}>{children}</span>
}

export function capabilityTone(capability: Capability | string): string {
  const status = typeof capability === 'string' ? capability : capability.status
  return TONE[status] ?? 'neutral'
}

export function statusLabel(status: string): string {
  return {stable: '可表达', experimental: '草案', planned: '待开发', deprecated: '已弃用'}[status] ?? status
}
