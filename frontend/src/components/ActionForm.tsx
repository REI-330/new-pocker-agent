import {useEffect, useMemo, useState} from 'react'
import type {ActionDescriptor, ActionInputDescriptor, Card, SessionState} from '../shared/api'
import {cardIndex, zoneTitle} from '../shared/util'
import {PlayingCard} from './ui'

type SelectionValues = Record<string, string[]>

// A descriptor-driven action form (ADR-0007). It renders exactly the inputs the
// backend declared and submits them as ``input_values``; it never dispatches on
// the action name and never decides legality itself.
export function ActionForm({state, descriptor, busy, onSubmit}: {
  state: SessionState
  descriptor: ActionDescriptor
  busy: boolean
  onSubmit: (actionId: string, inputValues: Record<string, unknown>) => void
}) {
  const cards = useMemo(() => cardIndex(state), [state])
  const [selections, setSelections] = useState<SelectionValues>({})
  const [scalars, setScalars] = useState<Record<string, string>>({})

  // A new revision means the board moved: start from a clean selection so a
  // stale card id is never resubmitted.
  useEffect(() => { setSelections({}); setScalars({}) }, [state.revision, descriptor.id])

  const toggle = (input: ActionInputDescriptor, cardId: string) => {
    setSelections(previous => {
      const current = previous[input.id] ?? []
      if (current.includes(cardId)) {
        return {...previous, [input.id]: current.filter(id => id !== cardId)}
      }
      if (current.length < input.max_count) {
        return {...previous, [input.id]: [...current, cardId]}
      }
      // At the cap a new pick replaces the oldest, so a single-choice input
      // behaves like a normal radio group.
      return {...previous, [input.id]: input.max_count <= 1 ? [cardId] : [...current.slice(1), cardId]}
    })
  }

  const complete = descriptor.inputs.every(input => {
    if (input.kind === 'card_selection') return (selections[input.id]?.length ?? 0) >= input.min_count
    return input.min_count <= 0 || (scalars[input.id] ?? '').trim() !== ''
  })

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (busy || !complete) return
    const inputValues: Record<string, unknown> = {}
    for (const input of descriptor.inputs) {
      if (input.kind === 'card_selection') {
        const chosen = selections[input.id] ?? []
        if (chosen.length) inputValues[input.id] = chosen
      } else {
        const raw = (scalars[input.id] ?? '').trim()
        if (raw !== '') inputValues[input.id] = input.kind === 'number' || input.kind === 'integer'
          ? Number(raw) : raw
      }
    }
    onSubmit(descriptor.id, inputValues)
  }

  return <form className="action-form" onSubmit={submit}>
    <div className="action-form-head">
      <strong>{descriptor.label || descriptor.id}</strong>
      <span className="mono">{descriptor.id}</span>
    </div>
    {descriptor.inputs.map(input => <div className="action-input" key={input.id}>
      <div className="action-input-label">
        <span>{zoneTitle(input.zone)}{input.scope === 'shared' ? '（公共）' : ''}</span>
        <span className="mono">选 {input.min_count}
          {input.max_count !== input.min_count ? `–${input.max_count}` : ''} 张</span>
      </div>
      {input.kind === 'card_selection'
        ? <SelectionField input={input} cards={cards} selected={selections[input.id] ?? []}
            busy={busy} onToggle={cardId => toggle(input, cardId)} />
        : <input className="action-scalar" value={scalars[input.id] ?? ''} disabled={busy}
            inputMode={input.kind === 'number' || input.kind === 'integer' ? 'numeric' : 'text'}
            placeholder={`输入 ${input.id}`}
            onChange={event => setScalars({...scalars, [input.id]: event.target.value})} />}
    </div>)}
    <button className="action-primary" type="submit" disabled={busy || !complete}>
      {busy ? '提交中…' : (descriptor.label || descriptor.id)}
    </button>
  </form>
}

function SelectionField({input, cards, selected, busy, onToggle}: {
  input: ActionInputDescriptor
  cards: Map<string, Card>
  selected: string[]
  busy: boolean
  onToggle: (cardId: string) => void
}) {
  if (!input.options.length) return <p className="muted">当前没有可选的牌。</p>
  return <div className="hand-cards">
    {input.options.map(cardId => {
      const card = cards.get(cardId)
      const isSelected = selected.includes(cardId)
      return <button type="button" key={cardId} disabled={busy}
        className={'card-choice' + (isSelected ? ' selected' : '')} aria-pressed={isSelected}
        onClick={() => onToggle(cardId)}>
        {card ? <PlayingCard card={card} small /> : <span className="mono">{cardId}</span>}
      </button>
    })}
  </div>
}
