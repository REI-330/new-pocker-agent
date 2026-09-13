import {useEffect, useState} from 'react'
import {api, messageOf, type ArtifactSummary, type Coverage, type GameInfo,
  type GameVersionDetail} from '../../shared/api'
import {Pill} from '../../components/ui'
import {shortId, verificationProblems} from '../../shared/util'

const rec = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : []
const text = (value: unknown, fallback = '—'): string =>
  value === null || value === undefined || value === '' ? fallback : String(value)

export function RulesPage({game, coverage, onPlay}: {
  game: GameInfo | null; coverage: Coverage | null
  onPlay: (gameId: string, version?: number | null) => void
}) {
  const [versions, setVersions] = useState<ArtifactSummary[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [detail, setDetail] = useState<GameVersionDetail | null>(null)
  const [error, setError] = useState('')

  // Reference (0.4) games have no registered versions: the list is empty and
  // the page keeps its playtest view.
  useEffect(() => {
    setVersions([]); setDetail(null); setError(''); setSelected(null)
    if (!game) return
    api.gameVersions(game.id)
      .then(data => {
        setVersions(data.versions)
        setSelected(game.version ?? data.versions[data.versions.length - 1]?.version ?? null)
      })
      .catch(() => setVersions([]))
  }, [game?.id, game?.version])

  useEffect(() => {
    if (!game || selected === null) { setDetail(null); return }
    let cancelled = false
    api.gameVersion(game.id, selected)
      .then(data => { if (!cancelled) setDetail(data) })
      .catch(err => { if (!cancelled) setError(messageOf(err)) })
    return () => { cancelled = true }
  }, [game?.id, selected])

  if (!game) {
    return <div className="prototype-page rules-page">
      <div className="page-heading"><div><span className="eyebrow">02 / CONTRACT</span>
        <h1>规则与能力</h1><p>从玩法库选择一个玩法，查看它的执行合同与验证证据。</p></div></div>
      <p className="muted">尚未选择玩法。</p>
    </div>
  }

  const corpusGame = coverage?.games.find(item => item.builtin === game.id)
  const report = game.playtest

  return <div className="prototype-page rules-page">
    <div className="page-heading">
      <div><span className="eyebrow">02 / CONTRACT</span><h1>{game.title}</h1>
        <p>规则以数据（GamePlan）表达；这份合同来自实际执行的计划，而不是模型摘要。</p></div>
      <Pill tone={report.ok ? 'success' : 'danger'}>{report.ok ? 'PLAYTEST 通过' : 'PLAYTEST 失败'}</Pill>
    </div>

    {versions.length > 0 && <div className="library-section">
      <div className="section-heading"><h2>已注册版本</h2>
        <span className="mono">{versions.length} versions · generation_source = composed_rules</span></div>
      <div className="version-row">
        {versions.map(item => <button key={item.version}
          className={'version-chip' + (selected === item.version ? ' is-active' : '')}
          onClick={() => setSelected(item.version)}>
          v{item.version}<small>{item.title}</small></button>)}
      </div>
    </div>}

    {error && <div className="state-banner conflict"><span className="state-icon">!</span>
      <div><strong>{error}</strong><small>无法读取该版本详情。</small></div></div>}

    {detail
      ? <VersionDetail detail={detail} onPlay={() => onPlay(game.id, detail.version)} />
      : <ReferenceDetail game={game} corpusGame={corpusGame} report={report}
          onPlay={() => onPlay(game.id, game.version ?? null)} />}
  </div>
}

function VersionDetail({detail, onPlay}: {detail: GameVersionDetail; onPlay: () => void}) {
  const artifact = detail.artifact
  const verification = detail.verification
  const problems = verificationProblems(verification)
  const rules = rec(detail.rules)
  const sourceMap = detail.source_map ?? {}
  const clauses = rec(sourceMap.clauses)
  const actions = list(rules.actions).map(rec)
  const terminal = rec(rules.terminal)
  const meta = rec(rules.meta)
  const deck = rec(rules.deck)

  return <div className="rules-layout">
    <div className="rule-ledger">
      <div className="rule-card"><div><h3>产物摘要</h3>
        <p>game_id {artifact.game_id} · v{artifact.version} · {artifact.title}<br />
          generation_source {artifact.generation_source}<br />
          plan_hash {shortId(artifact.plan_hash)} · ir_hash {shortId(artifact.ir_hash)}<br />
          compiler_version {text(artifact.compiler_version)}<br />
          approval_ir_hash {shortId(artifact.approval_ir_hash)}</p></div>
        <Pill tone="success">已注册</Pill></div>

      <div className="rule-card"><div><h3>验证证据</h3>
        <p>verification_id {shortId(verification?.verification_id)}<br />
          策略 {verification?.strategies.join(', ') || '—'}<br />
          种子 {verification?.seeds.join(' / ') ?? '—'}<br />
          wait 覆盖 {verification?.covered_wait_nodes.join(', ') || '—'}</p></div>
        <Pill tone={verification?.ok ? 'success' : 'danger'}>
          {verification?.ok ? '通过' : '未通过'}</Pill></div>

      {problems.length > 0 && <div className="rule-card"><div><h3>未通过的检查</h3>
        <ul className="axis-list">{problems.map((problem, index) =>
          <li key={index}>{problem}</li>)}</ul></div>
        <Pill tone="danger">{problems.length} 项</Pill></div>}

      {verification && verification.checks.length > 0 && <div className="rule-card"><div>
        <h3>已检查的条款</h3>
        <ul className="axis-list">{verification.checks.map((check, index) =>
          <li key={index}>{check}</li>)}</ul></div>
        <Pill tone="success">{verification.checks.length} 项</Pill></div>}
    </div>

    <div className="actions-card">
      <span className="eyebrow">RULES IR · 条款来源</span>
      <h2>{text(meta.title, artifact.title)}</h2>
      <div className="action-rule"><span>玩家</span>
        <strong>{text(rules.players ?? meta.players)}</strong>
        <code>牌 {list(deck.ranks).join('') || '—'} / 花色 {list(deck.suits).join('') || '—'} · zones {Object.keys(rec(rules.zones)).length}</code></div>
      {actions.map((action, index) => {
        const inputs = list(action.inputs).map(rec)
        return <div className="action-rule" key={index}>
          <span>{text(action.id)}</span>
          <strong>{text(action.actor, 'current')} · {inputs.length} 个输入</strong>
          <code>{inputs.map(input =>
            `${text(input.id)}: ${text(input.kind)}@${text(input.zone)} ${text(input.min_count, '1')}–${text(input.max_count, '1')}`)
            .join(' · ') || '无输入'}</code>
        </div>
      })}
      <div className="action-rule"><span>结束</span>
        <strong>{text(terminal.winner, '—')} · 最多 {text(terminal.max_rounds ?? terminal.max_actor_actions)}</strong>
        <code>{Object.keys(terminal).join(', ') || '无显式结束条件'}</code></div>
      <button className="action-primary" onClick={onPlay}>用这个版本试玩 <span>↗</span></button>
    </div>

    <div className="library-section source-map-section">
      <div className="section-heading"><h2>来源映射（条款 → 节点）</h2>
        <span className="mono">source_map {text(sourceMap.version)} · nodes {Object.keys(rec(sourceMap.nodes)).length}</span></div>
      {Object.entries(clauses).length
        ? Object.entries(clauses).map(([clause, nodes]) => <div className="rule-card" key={clause}>
            <div><h3>{clause}</h3>
              <p>{list(nodes).map(String).join(', ')}</p></div>
            <Pill tone="neutral">{list(nodes).length} 节点</Pill></div>)
        : <p className="muted">这个版本没有条款映射。</p>}
      <details className="runtime-log">
        <summary>开发诊断：完整 IR 与 plan</summary>
        <pre>{JSON.stringify({rules: detail.rules, plan: detail.plan, source_map: detail.source_map}, null, 2)}</pre>
      </details>
    </div>
  </div>
}

function ReferenceDetail({game, corpusGame, report, onPlay}: {
  game: GameInfo; corpusGame: Coverage['games'][number] | undefined
  report: GameInfo['playtest']; onPlay: () => void
}) {
  return <div className="rules-layout">
    <div className="rule-ledger">
      <div className="rule-card"><div><h3>执行面</h3>
        <p>kind {game.kind}<br />单一 Interpreter · 无模型参与运行时</p></div>
        <Pill tone="success">core</Pill></div>
      <div className="rule-card"><div><h3>验证证据</h3>
        <p>证据来源 {report.evidence}<br />种子 {report.seeds.join(' / ') || '—'}<br />
          wait 覆盖 {report.covered_wait_nodes.join(', ') || '—'}</p></div>
        <Pill tone={report.ok ? 'success' : 'danger'}>{report.failures.length} 失败</Pill></div>
      {corpusGame && <div className="rule-card"><div><h3>需要的能力轴</h3>
        <ul className="axis-list">{corpusGame.required_axes.map(axis => <li key={axis}>{axis}</li>)}</ul>
      </div>
        <Pill tone={corpusGame.expressible ? 'success' : 'warning'}>
          {corpusGame.expressible ? '全部 stable' : '存在待开发轴'}</Pill></div>}
    </div>

    <div className="actions-card">
      <span className="eyebrow">PLAYTEST REPORT</span>
      <h2>逐项检查</h2>
      <ul className="check-list">
        {report.checks.map(check => <li key={check}><span>✓</span>{check}</li>)}
        {report.failures.map(failure => <li key={failure}><span>✗</span>{failure}</li>)}
      </ul>
      <button className="action-primary" onClick={onPlay}>开始试玩 <span>↗</span></button>
    </div>
  </div>
}
