export const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'

export async function request<T>(path: string, body?: unknown): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 120_000)
  try {
    const response = await fetch(API + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : {'Content-Type': 'application/json'},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    })
    const text = await response.text()
    let data
    try { data = JSON.parse(text) } catch { throw new Error('服务响应格式错误，请检查本机后端和 API 地址') }
    if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请重试')
    return data as T
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw new Error('请求超时，请重试')
    if (error instanceof TypeError) throw new Error('无法连接本机服务，请检查后端是否启动')
    throw error
  } finally { clearTimeout(timer) }
}

export async function downloadGame(rules: Rule) {
  const response = await fetch(API + '/api/games/export', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(rules),
  })
  if (!response.ok) {
    const data = await response.json()
    throw new Error(data.detail || '导出失败')
  }
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = rules.game_id + '.pocker-game.zip'
  document.body.append(link); link.click(); link.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export type Message = {role: 'user' | 'assistant'; content: string}
export type Card = {rank: string; suit: string; value: number}
export type Rule = {
  kind?: 'arithmetic' | 'blackjack' | 'shedding';
  game_id: string; title: string; description: string; max_rounds: number;
  players: {min_players: number; max_players: number; starting_hand_size: number};
  deck: {ranks: string[]; suits: string[]}; phases: {name: string; actions: string[]; max_turns: number}[];
  actions: {name: string; amount: number}[];
}
export type ModelConfig = {configured: boolean; has_key: boolean; base_url: string; model: string; warning?: string}
export type GameEvent = Record<string, unknown>
export type Runtime = {
  kind?: 'arithmetic' | 'blackjack' | 'shedding';
  numbers?: number[]; target?: number; instructions?: string; feedback?: string; result_title?: string;
  legal_card_indices?: number[]; active_suit?: string; wild_rank?: string | null; suit_options?: string[];
  session_id: string; revision: number; seed: number; round: number; max_rounds: number; phase: string;
  current_player: string; finished: boolean; winners: string[]; finish_reason: string;
  legal_actions: string[]; players: {id: string; hand: Card[]; score: number; label?: string; hidden_count?: number; total?: number | null}[];
  table: Card[]; events: GameEvent[]; deck_remaining: number;
}
export type ActionArguments = {expression?: string; declared_suit?: string}
export const messageOf = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试'
