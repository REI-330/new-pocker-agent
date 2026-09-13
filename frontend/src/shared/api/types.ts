// Types mirror the frozen M5 backend contract (ADR-0016/0017). The frontend
// renders what the backend returns and never re-derives game state.

export type Card = {id: string; rank: string; suit: string; value: number}
export type GameEvent = Record<string, unknown>

// --------------------------------------------------------------- library view
export type Capability = {id: string; title: string; status: string; covered: boolean
  mechanisms: string[]; note: string}
export type CapabilityMatrix = {version: string; status_order: string[]; axes: Capability[]}
export type CapabilityGap = {axis: string; status: string; reason: string}

export type MacroPromotion = {macro: string; plans: string[]; uses: number; action: string}
export type CapabilityResponse = {
  matrix: CapabilityMatrix; coverage: Coverage; macro_promotion: MacroPromotion[]
}

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
export type GameInfo = {
  id: string; title: string; kind: string; playtest: Playtest
  version?: number; verification_id?: string; source?: string
}

// ------------------------------------------------------ generic session state
// One viewer-safe projection of ``state['zones']`` (interpreter.view).
export type Zone = {
  owner: number | null
  visibility: string
  count: number
  cards: Card[]
  visible: boolean
}
export type Zones = Record<string, Zone>

// A declared action input. Only ``card_selection`` exists today; the UI keeps a
// generic fallback so a future typed input is rendered, not dropped.
export type ActionInputDescriptor = {
  id: string
  kind: string
  zone: string
  scope: string
  min_count: number
  max_count: number
  options: string[]
}
export type ActionDescriptor = {id: string; label: string; inputs: ActionInputDescriptor[]}

export type Player = {id: string; hand: Card[]; score: number; hidden_count: number}

export type SessionState = {
  session_id: string; game_id: string; revision: number
  // ``version`` is null for a reference (0.4-plan) game, the immutable artifact
  // version for a published composed game.
  version: number | null
  kind: string; round: number; max_rounds: number; phase: string; flow_node: string
  execution_mode: string
  current_player: string; finished: boolean; winners: string[]; legal_actions: string[]
  players: Player[]; scores: number[]; table: Card[]
  numbers: number[]; target: number | null; reveal: boolean; solution: string | null
  instructions: string; feedback: string; events: GameEvent[]
  legal_card_indices: number[]; wild_ranks: string[]; private_hands: boolean
  suit_options?: string[]
  // Composed (published) games additionally expose zones + action descriptors.
  zones?: Zones
  actions?: ActionDescriptor[]
  // Read-only extra table state, rendered generically when present.
  pot?: number; stacks?: number[]; committed?: number[]; hand_committed?: number[]
  folded?: boolean[]; teams?: string[]; tricks_won?: number[]; trump?: string
  current_bet?: number; min_raise?: number; pairs?: unknown
}

// The legacy field-shaped payload, still accepted as an adapter by the backend.
export type ActionPayload = {
  revision: number; card_index?: number; expression?: string; declared_suit?: string
  amount?: number; request_id?: string
}
export type GenericActionRequest = {
  action_id: string; input_values: Record<string, unknown>
  revision: number; request_id?: string
}
export type ActionResponse = {
  event: GameEvent; new_events: GameEvent[]; state: SessionState; action_id?: string
}

export type SessionRef = {session_id: string; game_id: string; version: number | null}

// ------------------------------------------------------- bounded event cursor
export type EventsResponse = {
  session_id: string; game_id: string; revision: number; version: number | null
  viewer: string; cursor: number; total: number; has_more: boolean; events: GameEvent[]
}

// ----------------------------------------------------- published game versions
export type ArtifactSummary = {
  schema_version: string; game_id: string; version: number; title: string
  generation_source: string; plan_hash: string; ir_hash: string | null
  compiler_version: string | null; registry_contract_hash: string
  verification_id: string; approval_ir_hash: string | null
}
export type VerificationResult = {
  verification_id: string; ok: boolean; plan_hash: string; registry_contract_hash: string
  strategies: string[]; seeds: number[]; ir_hash: string | null
  compiler_version: string | null; covered_wait_nodes: string[]; failures: string[]
  checks: string[]; contract: Record<string, unknown>
}
export type SourceMap = {
  version?: string; compiler_version?: string; ir_hash?: string
  nodes?: Record<string, {path: string; clause: string}>
  paths?: Record<string, string[]>
  clauses?: Record<string, string[]>
}
export type GameVersionDetail = {
  game_id: string; version: number; artifact: ArtifactSummary
  rules: Record<string, unknown> | null; source_map: SourceMap
  verification: VerificationResult | null; plan: Record<string, unknown> | null
}
export type GameVersionsResponse = {game_id: string; versions: ArtifactSummary[]}

// ------------------------------------------------------------ design lifecycle
// ADR-0017 freezes the vocabulary: draft -> diagnosed -> compiled -> verified ->
// awaiting_confirmation -> registered, plus failed.
export type DesignStatus =
  | 'draft' | 'diagnosed' | 'compiled' | 'verified'
  | 'awaiting_confirmation' | 'registered' | 'failed'

export type ChatTurn = {role: 'user' | 'assistant'; content: string}
export type DesignConfirmation = {ir_hash: string; revision: number; verification_id: string}
export type DesignContext = {
  chat?: ChatTurn[]
  requirements?: unknown[]
  compiled?: Record<string, unknown>
  verification?: VerificationResult
  questions?: unknown[]
  budget?: Record<string, unknown>
  used?: Record<string, unknown>
  failure?: unknown
  artifact?: ArtifactSummary
  confirmation?: DesignConfirmation
  published?: ArtifactSummary
  verify_requests?: Record<string, unknown>
}
export type DesignHistoryEntry = {seq: number; event: string; revision: number}
export type DesignSession = {
  session_id: string; game_id: string; description: string
  ir: Record<string, unknown> | null; ir_hash: string | null
  diagnosis: Record<string, unknown> | null; revision: number
  status: DesignStatus; context: DesignContext
  history: DesignHistoryEntry[]; processed: Record<string, unknown>
}
export type DesignSummary = {
  session_id: string; game_id: string; revision: number; status: DesignStatus
  ir_hash: string | null; events: number; messages: number
}
export type DesignKind = 'question' | 'finalized' | 'unsupported' | 'error' | 'budget_exhausted'

export type DesignResult = {
  kind: DesignKind | string
  message: string
  session_id: string
  revision: number
  status: DesignStatus
  artifact: ArtifactSummary | null
  verification: VerificationResult | null
  attempts: number
  observations: LoopObservation[]
  messages: ChatTurn[]
  used: Record<string, unknown>
  registered: false
  run_id: string
  session: DesignSession
}

export type DesignRunStatus = 'running' | 'completed' | 'failed'
export type DesignRun = {
  run_id: string; session_id: string; seq: number; message: string
  status: DesignRunStatus; kind: string | null; revision: number
  budget: Record<string, unknown>; attempts: number; used: Record<string, unknown>
  observations: Record<string, unknown>[]; artifact: ArtifactSummary | null
  verification: VerificationResult | null; error: string | null
  request_id: string | null; response: DesignResult | null; started_at: number
}
export type DesignRunSummary = Omit<DesignRun, 'response' | 'budget' | 'observations'> & {
  observations: number
}
export type DesignRunsResponse = {session_id: string; runs: DesignRunSummary[]}

export type VerifyResponse = {
  ok: boolean; observation: Record<string, unknown>
  revision: number; session: DesignSession
}
export type ConfirmResponse = {
  confirmed: true; confirmation: DesignConfirmation; session: DesignSession
}
export type PublishResponse = {
  published: true; idempotent: boolean; artifact: ArtifactSummary; session: DesignSession
}

// ----------------------------------------------------- legacy design loop (v0.3)
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
  messages: ChatTurn[]
  registered?: boolean
  registration_error?: string
}

// -------------------------------------------------------------- model settings
export type ModelConfig = {
  configured: boolean; has_key: boolean; base_url: string; model: string
  warning?: string | null
}
export type ConnectionTest = {ok: boolean; model: string; base_url: string}
