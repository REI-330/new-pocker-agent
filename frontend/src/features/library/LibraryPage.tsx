import type {CapabilityMatrix, Coverage, GameInfo, MacroPromotion} from '../../shared/api'
import {Pill, statusLabel} from '../../components/ui'

export function LibraryPage({games, coverage, matrix, macroPromotion, onOpen}: {
  games: GameInfo[]; coverage: Coverage | null
  matrix: CapabilityMatrix | null; macroPromotion: MacroPromotion[]
  onOpen: (gameId: string) => void
}) {
  return <div className="prototype-page library-page">
    <div className="page-heading">
      <div><span className="eyebrow">01 / PLAYBOOK</span><h1>玩法库</h1>
        <p>只有通过完整对局验证（playtest）的玩法才会出现在“可试玩”。其余列出缺失的能力轴。</p></div>
      {coverage && <Pill tone={coverage.coverage > 0 ? 'success' : 'warning'}>
        覆盖率 {(coverage.coverage * 100).toFixed(0)}% · {coverage.covered}/{coverage.total}
      </Pill>}
    </div>

    <div className="library-section">
      <div className="section-heading"><h2>可试玩（已过 playtest）</h2>
        <span className="mono">{games.length} games</span></div>
      {games.map(game => <button className="library-row" key={game.id} onClick={() => onOpen(game.id)}>
        <span className="stacked-cards"><i>♠</i><i>♥</i><i>♦</i></span>
        <span className="library-name"><strong>{game.title}</strong>
          <small>{game.kind}{game.version ? ` · v${game.version}` : ''} · 种子
            {game.playtest.seeds.join('/') || '—'} · {game.playtest.checks.length} 项检查
            {game.source ? ` · ${game.source}` : ''}</small></span>
        <Pill tone={game.playtest.ok ? 'success' : 'danger'}>{game.playtest.ok ? '已验证' : '未通过'}</Pill>
        <span className="row-arrow">→</span>
      </button>)}
      {!games.length && <p className="muted">正在加载…</p>}
    </div>

    {coverage && <>
      <div className="library-section">
        <div className="section-heading"><h2>能力缺口（按影响排序）</h2>
          <span className="mono">corpus {coverage.total}</span></div>
        {Object.entries(coverage.missing_histogram).map(([axis, count]) => {
          const capability = matrix?.axes.find(item => item.id === axis)
          return <div className="rule-card" key={axis}>
            <div><h3>{capability?.title ?? axis}</h3>
              <p>{count} 个玩法需要它
                {capability?.note ? <><br />{capability.note}</> : null}</p></div>
            <Pill tone={capability?.covered ? 'success' : 'warning'}>
              {statusLabel(capability?.status ?? 'planned')}</Pill>
          </div>
        })}
      </div>

      <div className="library-section">
        <div className="section-heading"><h2>宏提升规则</h2>
          <span className="mono">§6.4 · 阈值 2</span></div>
        {macroPromotion.length
          ? macroPromotion.map(item => <div className="rule-card" key={item.macro}>
              <div><h3>{item.macro}</h3>
                <p>被 {item.uses} 个计划复用：{item.plans.join(', ')}</p></div>
              <Pill tone="warning">待提升为轴</Pill>
            </div>)
          : <div className="rule-card"><div><h3>无待提升项</h3>
              <p>被 ≥2 个计划复用的「机制」宏必须提升为轴；控制流宏不计入
                （当前 match_turn 属控制流，由 crazy_eights 与 uno 复用）。</p></div>
              <Pill tone="success">已对齐</Pill>
            </div>}
      </div>

      <div className="library-section">
        <div className="section-heading"><h2>语料库逐项结果</h2>
          <span className="mono">{coverage.covered}/{coverage.total} 可表达</span></div>
        {coverage.games.map(game => <div className="library-row is-static" key={game.id}>
          <span className="library-name"><strong>{game.name}</strong>
            <small>{game.category} · 需要 {game.required_axes.join(', ')}</small></span>
          {game.expressible
            ? <Pill tone="success">可表达</Pill>
            : <Pill tone="warning">缺 {game.missing.map(gap => gap.axis).join(', ')}</Pill>}
        </div>)}
      </div>
    </>}
  </div>
}

export function statusText(status: string) { return statusLabel(status) }
