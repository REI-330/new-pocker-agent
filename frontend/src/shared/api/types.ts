export type Card = {id: string; rank: string; suit: string; value: number}
export type GameEvent = Record<string, unknown>

export type Capability = {id: string; title: string; status: string; covered: boolean; mechanisms: string[]}
export type CapabilityMatrix = {version: string; status_order: string[]; axes: Capability[]}
export type CapabilityGap = {axis: string; status: string; reason: string}

export type CoverageGame = {
  id: string; name: string; category: string; required_axes: string[]
  expressible: boolean; missing: CapabilityGap[]; builtin: string | null
}
export type Coverage = {
  total: number; covered: number; coverage: number
  missing_histogram: Record<string, number>; games: CoverageGame[]
}

export type Playtest = {
  ok: boolean; seeds: number[]; event_counts: Record<string, number>
  failures: string[]; checks: string[]; covered_wait_nodes: string[]
}
export type GameInfo = {id: string; title: string; kind: string; playtest: Playtest}

export type Player = {id: string; hand: Card[]; score: number; hidden_count: number}
export type SessionState = {
  session_id: string; game_id: string; revision: number
  kind: string; round: number; max_rounds: number; phase: string; flow_node: string; execution_mode: string
  current_player: string; finished: boolean; winners: string[]; legal_actions: string[]
  players: Player[]; scores: number[]; table: Card[]
  numbers: number[]; target: number | null; reveal: boolean; solution: string | null
  instructions: string; feedback: string; events: GameEvent[]
}

export type ActionPayload = {
  revision: number; card_index?: number; expression?: string; declared_suit?: string
}
export type ActionResponse = {event: GameEvent; new_events: GameEvent[]; state: SessionState}

export type MetaTool = {name: string; args: Record<string, string>; description: string}
export type LoopObservation = {
  step: number; ok: boolean; tool: string | null; error?: string; kind?: string
  question?: string; message?: string; game_kind?: string
  covered_wait_nodes?: string[]
}
export type LoopResult = {
  kind: string
  message: string
  ir: Record<string, unknown> | null
  plan: Record<string, unknown> | null
  playtest: Playtest | null
  finalized: boolean
  attempts: number
  observations: LoopObservation[]
}
export type ModelConfig = {
  configured: boolean; has_key: boolean; base_url: string; model: string
  warning?: string | null
}
