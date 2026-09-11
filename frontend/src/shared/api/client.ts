import type {ActionPayload, ActionResponse, ChatMessage, Coverage, GameInfo, LoopResult,
  MetaTool, ModelConfig, SessionState} from './types'

export const API = import.meta.env.VITE_API_URL || ''

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) { super(message); this.name = 'ApiError' }
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
  if (text) { try { data = JSON.parse(text) } catch { throw new ApiError(`服务返回了无效响应（HTTP ${response.status}）`, response.status) } }
  if (!response.ok) {
    const detail = (data as {detail?: string} | null)?.detail
    throw new ApiError(detail || `请求失败（HTTP ${response.status}）`, response.status)
  }
  return data as T
}

export const api = {
  capabilities: () => request<{matrix: import('./types').CapabilityMatrix; coverage: Coverage}>('/api/capabilities'),
  games: () => request<{games: GameInfo[]; coverage: Coverage}>('/api/games'),
  createSession: (game_id: string, seed?: number) =>
    request<SessionState>('/api/sessions', seed === undefined ? {game_id} : {game_id, seed}),
  getSession: (sessionId: string) => request<SessionState>(`/api/sessions/${encodeURIComponent(sessionId)}`),
  act: (sessionId: string, action: string, payload: ActionPayload) =>
    request<ActionResponse>(`/api/sessions/${encodeURIComponent(sessionId)}/actions/${encodeURIComponent(action)}`, payload),
  agentTools: () => request<{meta_tools: MetaTool[]}>('/api/agent/tools'),
  runLoop: (message: string, messages: ChatMessage[] = [], max_steps = 12) =>
    request<LoopResult>('/api/agent/loop', {message, messages, max_steps}),
  listModels: (body: {base_url: string; api_key: string; model?: string}) =>
    request<{models: string[]; base_url: string}>('/api/agent/models', body),
  getConfig: () => request<ModelConfig>('/api/agent/config'),
  saveConfig: (body: {base_url: string; model: string; api_key: string}) =>
    request<ModelConfig>('/api/agent/config', body),
}

export const messageOf = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试'
