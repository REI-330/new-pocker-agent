import type {
  ActionPayload, ActionResponse, CapabilityResponse, ChatTurn, ConfirmResponse,
  ConnectionTest, Coverage, DesignResult, DesignRunsResponse, DesignRun, DesignSession, DesignSummary,
  EventsResponse, GameInfo, GameVersionDetail, GameVersionsResponse, GenericActionRequest, LoopResult,
  MetaTool, ModelConfig, PublishResponse, SessionState, VerifyResponse,
} from './types'

export const API = import.meta.env.VITE_API_URL || ''

export class ApiError extends Error {
  constructor(message: string, readonly status?: number, readonly detail?: string) {
    super(message)
    this.name = 'ApiError'
  }
  // 409 is a *recoverable* conflict: refetch and keep going. 404 means the
  // object is gone. Everything else is a request problem to show the user.
  get isConflict() { return this.status === 409 }
  get isMissing() { return this.status === 404 }
  get isConflictPrefix() {
    return this.isConflict || /^(stale_revision|request_id_conflict|artifact_version_conflict|plan_changed|plan_already_registered|game_id_reserved)/.test(this.detail ?? '')
  }
}

async function request<T>(path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(API + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError('无法连接本机服务，请确认后端已启动')
  }
  const text = await response.text()
  let data: unknown = null
  if (text) {
    try { data = JSON.parse(text) } catch {
      throw new ApiError(`服务返回了无效响应（HTTP ${response.status}）`, response.status)
    }
  }
  if (!response.ok) {
    const detail = (data as {detail?: string} | null)?.detail
    throw new ApiError(detail || `请求失败（HTTP ${response.status}）`, response.status, detail)
  }
  return data as T
}

export const api = {
  // ----------------------------------------------------------- library view
  capabilities: () => request<CapabilityResponse>('/api/capabilities'),
  games: () => request<{games: GameInfo[]; coverage: Coverage}>('/api/games'),

  // --------------------------------------------------------- published versions
  gameVersions: (gameId: string) =>
    request<GameVersionsResponse>(`/api/games/${encodeURIComponent(gameId)}/versions`),
  gameVersion: (gameId: string, version: number) =>
    request<GameVersionDetail>(
      `/api/games/${encodeURIComponent(gameId)}/versions/${encodeURIComponent(String(version))}`),

  // -------------------------------------------------------------- sessions
  createSession: (game_id: string, seed?: number, version?: number | null) =>
    request<SessionState>('/api/sessions', {
      game_id, ...(seed === undefined ? {} : {seed}),
      ...(version === undefined || version === null ? {} : {version}),
    }),
  getSession: (sessionId: string) =>
    request<SessionState>(`/api/sessions/${encodeURIComponent(sessionId)}`),
  // The generic, descriptor-driven action path used for every game — legacy
  // 0.4 plans accept their raw keys through the same endpoint.
  actGeneric: (sessionId: string, payload: GenericActionRequest) =>
    request<ActionResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/actions`, payload),
  // Kept for callers that still want the field-shaped adapter.
  act: (sessionId: string, action: string, payload: ActionPayload) =>
    request<ActionResponse>(
      `/api/sessions/${encodeURIComponent(sessionId)}/actions/${encodeURIComponent(action)}`, payload),
  events: (sessionId: string, after = 0, viewer = 'player-1', limit = 500) =>
    request<EventsResponse>(
      `/api/sessions/${encodeURIComponent(sessionId)}/events?after=${after}` +
      `&viewer=${encodeURIComponent(viewer)}&limit=${limit}`),

  // -------------------------------------------------------------- designs
  createDesign: (game_id: string, description = '') =>
    request<DesignSession>('/api/designs', {game_id, description}),
  listDesigns: () => request<{designs: DesignSummary[]}>('/api/designs'),
  getDesign: (sessionId: string) =>
    request<DesignSession>(`/api/designs/${encodeURIComponent(sessionId)}`),
  designMessages: (sessionId: string, body: {
    message: string; expected_revision?: number; max_steps?: number; request_id?: string}) =>
    request<DesignResult>(`/api/designs/${encodeURIComponent(sessionId)}/messages`, body),
  designRuns: (sessionId: string, limit = 20) =>
    request<DesignRunsResponse>(
      `/api/designs/${encodeURIComponent(sessionId)}/runs?limit=${limit}`),
  designRun: (sessionId: string, runId: string) =>
    request<DesignRun>(
      `/api/designs/${encodeURIComponent(sessionId)}/runs/${encodeURIComponent(runId)}`),
  verifyDesign: (sessionId: string, body: {expected_revision?: number; request_id?: string}) =>
    request<VerifyResponse>(`/api/designs/${encodeURIComponent(sessionId)}/verify`, body),
  confirmDesign: (sessionId: string, body: {
    ir_hash: string; expected_revision?: number; request_id?: string}) =>
    request<ConfirmResponse>(`/api/designs/${encodeURIComponent(sessionId)}/confirm`, body),
  publishDesign: (sessionId: string, body: {
    expected_revision?: number; request_id?: string; title?: string; version?: number}) =>
    request<PublishResponse>(`/api/designs/${encodeURIComponent(sessionId)}/publish`, body),

  // --------------------------------------------------- legacy design loop
  agentTools: () => request<{meta_tools: MetaTool[]}>('/api/agent/tools'),
  runLoop: (message: string, messages: ChatTurn[] = [], max_steps = 12) =>
    request<LoopResult>('/api/agent/loop', {message, messages, max_steps}),

  // ------------------------------------------------------------ model settings
  listModels: (body: {base_url: string; api_key: string; model?: string}) =>
    request<{models: string[]; base_url: string}>('/api/agent/models', body),
  getConfig: () => request<ModelConfig>('/api/agent/config'),
  saveConfig: (body: {base_url: string; model: string; api_key: string}) =>
    request<ModelConfig>('/api/agent/config', body),
  // Sends one real completion so "saved" and "actually reachable" stay distinct.
  testConnection: (body: {base_url: string; model: string; api_key: string}) =>
    request<ConnectionTest>('/api/agent/test-connection', body),
}

export const messageOf = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试'
