import type {Card, DesignStatus, GameEvent, SessionState, VerificationResult, Zone} from './api'

// ---------------------------------------------------------------- card lookup
// The descriptor options are card *ids*; the zones/players carry the card
// objects. One index lets a selection input render an option as a real card
// without the frontend knowing which zone a game family puts it in.
export function cardIndex(state: SessionState | null): Map<string, Card> {
  const index = new Map<string, Card>()
  const add = (card: Card | undefined) => { if (card) index.set(card.id, card) }
  if (state?.zones) for (const zone of Object.values(state.zones)) zone.cards.forEach(add)
  state?.players.forEach(player => player.hand.forEach(add))
  state?.table.forEach(add)
  return index
}

export function zoneCardIds(zone: Zone | undefined): string[] {
  return zone?.cards.map(card => card.id) ?? []
}

// ----------------------------------------------------------------- design id
// A design session needs a stable game_id before the model proposes an IR. The
// user does not type one, so derive it from the first message; a CJK-only title
// has no ascii slug, so fall back to a short time-based id.
export function slugify(text: string): string {
  const base = text.normalize('NFKD').replace(/[^\w\s-]/g, ' ').trim().toLowerCase()
    .replace(/[\s_]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '').slice(0, 40)
  return base || `game-${Date.now().toString(36)}`
}

// --------------------------------------------------------------- design stage
export type DesignStage = {id: string; label: string; hint: string}
export const DESIGN_STAGES: DesignStage[] = [
  {id: 'clarify', label: '澄清', hint: '说明玩法，Agent 提问补全需求'},
  {id: 'compose', label: '组合', hint: '把需求编译为可执行的规则与计划'},
  {id: 'verify', label: '验证', hint: '宿主运行正式验证门槛'},
  {id: 'confirm', label: '待核对', hint: '用户核对并确认这一版规则'},
  {id: 'registered', label: '已注册', hint: '不可变版本已注册，可以试玩'},
]

const STATUS_STAGE: Record<DesignStatus, number> = {
  draft: 0, diagnosed: 0, compiled: 1, verified: 2,
  awaiting_confirmation: 3, registered: 4, failed: -1,
}

export function designStage(status: DesignStatus): {index: number; failed: boolean} {
  const index = STATUS_STAGE[status] ?? 0
  return {index, failed: status === 'failed'}
}

export function statusLabelZh(status: DesignStatus | string): string {
  return ({
    draft: '草案', diagnosed: '已诊断', compiled: '已编译', verified: '已验证',
    awaiting_confirmation: '待核对', registered: '已注册', failed: '失败',
    // A design *result* kind is not a status; label the ones we receive.
    question: '需要澄清', finalized: '候选已冻结', unsupported: '无法表达',
    error: '出错了', budget_exhausted: '预算用尽',
  } as Record<string, string>)[status] ?? status
}

// ------------------------------------------------------------------- events
const EVENT_LABELS: Record<string, string> = {
  game_started: '对局开始',
  game_finished: '对局结束',
  flow_wait: '等待玩家操作',
  tool_called: '执行规则动作',
}

// A human label per host operation. The operation id and raw payload stay in a
// collapsed diagnostics block so the timeline reads like play, not a debug log.
const OPERATION_LABELS: Record<string, string> = {
  'deck.cards': '建立牌堆', 'deck.shuffled': '洗牌', 'deck.deal': '发牌', 'deck.draw': '摸牌',
  'zones.select': '选择牌', 'zones.select_matching': '筛选匹配牌', 'zones.move': '移动牌',
  'zones.top': '取顶牌', 'zones.cards': '查看牌区', 'zones.count': '统计牌区',
  'zones.count_zone': '统计牌区', 'zones.select_duplicates': '选出对子', 'zones.verify': '核对牌区',
  'matching.play': '出牌', 'matching.draw': '摸牌',
  'trick.legal': '判断可出牌', 'trick.play': '出一张牌',
  'hand_rank.best': '计算最佳牌型', 'hand_rank.compare': '比较牌型',
  'point_total.total': '计算点数', 'point_total.dealer_play': '庄家行动',
  'point_total.settle': '结算点数',
  'betting.legal': '判断下注合法性', 'betting.act': '下注',
  'ledger.commit': '记入底注', 'ledger.pots': '统计底池', 'ledger.total': '统计筹码',
  'ledger.settle': '结算筹码',
  'rank_compare.call': '比较牌面', 'score_settle.call': '结算得分',
  'winner_resolve.call': '判定赢家', 'trigger.apply': '触发特殊效果',
  'hidden_draw.askable': '检查可询问对象', 'hidden_draw.ask': '询问手牌',
  'hidden_draw.discard_pairs': '移除对子', 'hidden_draw.refill': '补牌',
  'hidden_draw.is_finished': '检查是否结束',
  'exact_expression.solve': '求解算式', 'exact_expression.validate': '校验算式',
  'exact_expression.assert_unsolvable': '确认无解',
  'solvable_deal.deal': '发可解牌', 'logic.evaluate': '计算表达式',
  'pattern.match': '匹配牌型', 'pattern.choices': '列出可用牌', 'pattern.describe': '描述牌型',
  'pattern.classify': '分类牌型', 'pattern.beats': '比较牌型大小',
  'state.update': '更新局面',
}

export function describeEvent(event: GameEvent): {title: string; detail: string; code: string} {
  const name = String(event.event ?? 'event')
  const tool = typeof event.tool === 'string' ? event.tool : ''
  const operation = typeof event.operation === 'string' ? event.operation : ''
  const code = operation || name
  if (name === 'tool_called') {
    const title = OPERATION_LABELS[operation] ?? operation ?? '执行动作'
    const actor = typeof event.player === 'number' ? `玩家 ${Number(event.player) + 1}` : ''
    const card = typeof event.card_id === 'string' ? event.card_id
      : typeof event.card === 'string' ? event.card : ''
    const detail = [actor, card && `牌 ${card}`, operation && !OPERATION_LABELS[operation] ? operation : '']
      .filter(Boolean).join(' · ') || (tool ? `${tool}.${operation}` : '')
    return {title, detail, code}
  }
  if (name === 'game_finished') {
    const winners = Array.isArray(event.winners) ? event.winners : []
    return {title: EVENT_LABELS[name], detail: winners.length ? `赢家 ${winners.join(', ')}` : '', code}
  }
  return {title: EVENT_LABELS[name] ?? name, detail: '', code}
}

// --------------------------------------------------------------- verification
export function verificationProblems(verification: VerificationResult | null | undefined): string[] {
  if (!verification) return []
  const failures = verification.failures ?? []
  const contract = verification.contract as {failures?: unknown; ok?: unknown} | undefined
  const contractFailures = Array.isArray(contract?.failures) ? contract!.failures as string[] : []
  return [...failures, ...contractFailures]
}

// -------------------------------------------------------------------- misc
export function shortId(value: string | null | undefined): string {
  return value ? value.slice(0, 8) : '—'
}

export function zoneTitle(zoneId: string): string {
  return zoneId.replace(/[-_]/g, ' ')
}

export function visibilityLabel(visibility: string): string {
  return ({public: '公开', owner_only: '仅拥有者可见', hidden: '隐藏'}[visibility]) ?? visibility
}
