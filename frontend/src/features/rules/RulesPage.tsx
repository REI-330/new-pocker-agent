import type {Coverage, GameInfo} from '../../shared/api'
import {Pill} from '../../components/ui'

export function RulesPage({game, coverage, onPlay}: {
  game: GameInfo | null; coverage: Coverage | null; onPlay: () => void
}) {
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

    <div className="rules-layout">
      <div className="rule-ledger">
        <div className="rule-card"><div><h3>执行面</h3>
          <p>kind {game.kind}<br />单一 Interpreter · 无模型参与运行时</p></div>
          <Pill tone="success">core</Pill></div>
        <div className="rule-card"><div><h3>验证证据</h3>
          <p>种子 {report.seeds.join(' / ')}<br />wait 覆盖 {report.covered_wait_nodes.join(', ') || '—'}</p></div>
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
  </div>
}
