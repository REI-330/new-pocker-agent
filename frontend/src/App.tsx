import {useCallback, useEffect, useState} from 'react'
import {Pill} from './components/ui'
import {DesignPage} from './features/design/DesignPage'
import {LibraryPage} from './features/library/LibraryPage'
import {ReplayPage} from './features/replay/ReplayPage'
import {RulesPage} from './features/rules/RulesPage'
import {SettingsPage} from './features/settings/SettingsPage'
import {WorkspacePage} from './features/workspace/WorkspacePage'
import {api, messageOf, type CapabilityMatrix, type Coverage, type GameInfo,
  type MacroPromotion, type SessionState} from './shared/api'

type Route = 'library' | 'design' | 'rules' | 'workspace' | 'replay' | 'settings'

// The backend persists sessions, so remembering which one is open is all the
// frontend needs to survive a refresh (README promises refresh keeps the game).
const SESSION_KEY = 'pocker-session-id'

const NAV: Array<{id: Route; label: string; glyph: string}> = [
  {id: 'library', label: '玩法库', glyph: '♠'},
  {id: 'design', label: '新建玩法', glyph: '✦'},
  {id: 'rules', label: '规则与能力', glyph: '♦'},
  {id: 'workspace', label: '试玩工作台', glyph: '♣'},
  {id: 'replay', label: '回放与诊断', glyph: '↺'},
  {id: 'settings', label: '模型设置', glyph: '⚙'},
]

export function App() {
  const [route, setRoute] = useState<Route>('library')
  const [games, setGames] = useState<GameInfo[]>([])
  const [coverage, setCoverage] = useState<Coverage | null>(null)
  const [matrix, setMatrix] = useState<CapabilityMatrix | null>(null)
  const [macroPromotion, setMacroPromotion] = useState<MacroPromotion[]>([])
  const [gameId, setGameId] = useState<string | null>(null)
  const [session, setSession] = useState<SessionState | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const currentGame = games.find(game => game.id === gameId) ?? null

  const run = useCallback(async (label: string, task: () => Promise<void>) => {
    setBusy(label); setError('')
    try { await task() } catch (err) { setError(messageOf(err)) } finally { setBusy('') }
  }, [])

  const openGame = (id: string) => { setGameId(id); setSession(null); setRoute('rules') }
  const refreshGames = useCallback(() => {
    api.games().then(data => { setGames(data.games); setCoverage(data.coverage) })
      .catch(err => setError(messageOf(err)))
    api.capabilities().then(data => { setMatrix(data.matrix); setMacroPromotion(data.macro_promotion) })
      .catch(err => setError(messageOf(err)))
  }, [])
  useEffect(refreshGames, [refreshGames])

  // Resume the session this browser had open, and forget it if the server no
  // longer has it (a restarted backend with a fresh database).
  useEffect(() => {
    const saved = localStorage.getItem(SESSION_KEY)
    if (!saved) return
    api.getSession(saved)
      .then(state => { setSession(state); setGameId(state.game_id); setRoute('workspace') })
      .catch(() => localStorage.removeItem(SESSION_KEY))
  }, [])

  const start = (target?: string) => {
    const id = target ?? gameId
    if (!id) return
    void run('开始试玩', async () => {
      setGameId(id)
      const created = await api.createSession(id)
      localStorage.setItem(SESSION_KEY, created.session_id)
      setSession(created)
      setRoute('workspace')
      refreshGames()
    })
  }
  const act = (action: string, payload: Record<string, unknown>) => {
    if (!session) return
    void run('执行动作', async () => {
      const data = await api.act(session.session_id, action, {revision: session.revision, card_index: 0, ...payload})
      setSession(data.state)
    })
  }
  const refresh = () => {
    if (!session) return
    void run('刷新状态', async () => setSession(await api.getSession(session.session_id)))
  }

  const status = busy || (session
    ? (session.finished ? '本局已结束' : `第 ${session.round} / ${session.max_rounds} 轮`)
    : '选择玩法开始')

  return <main className="prototype-shell">
    <aside className="prototype-sidebar">
      <div className="prototype-brand">
        <span className="brand-stamp">P</span>
        <span><strong>POCKER AGENT</strong><small>Tabletop Atelier · v0.4</small></span>
      </div>
      <div className="sidebar-kicker">WORKSPACE</div>
      <nav className="prototype-nav">
        {NAV.map(item => <button key={item.id} className={route === item.id ? 'is-active' : ''}
          onClick={() => setRoute(item.id)}>
          <span className="nav-glyph">{item.glyph}</span>{item.label}
        </button>)}
      </nav>
      <div className="sidebar-divider" />
      <div className="current-game">
        <span className="sidebar-kicker">CURRENT GAME</span>
        <strong>{currentGame?.title ?? '未选择'}</strong>
        <small>{session ? `revision ${session.revision}` : currentGame ? currentGame.kind : '从玩法库开始'}</small>
        <span className="suit-line">♠ ♥ ♦ ♣</span>
      </div>
      <div className="sidebar-footer"><span className="online-dot" /> 本机后端
        <span className="mono">{coverage ? `${coverage.covered}/${coverage.total}` : '—'}</span></div>
    </aside>

    <section className="prototype-main">
      <header className="prototype-topbar">
        <div className="breadcrumbs">玩法库 <span>/</span> {NAV.find(item => item.id === route)?.label}</div>
        <div className="topbar-actions">
          <Pill tone={error ? 'danger' : session?.finished ? 'success' : 'neutral'}>
            {error ? 'ERROR' : session?.finished ? 'FINISHED' : 'CORE · READY'}</Pill>
          {session && <span className="revision mono">rev {session.revision}</span>}
        </div>
      </header>

      {error && <div className="state-banner conflict"><span className="state-icon">!</span>
        <div><strong>{error}</strong><small>操作未提交；状态保持不变。</small></div></div>}

      {route === 'library' && <LibraryPage games={games} coverage={coverage} matrix={matrix}
        macroPromotion={macroPromotion} onOpen={openGame} />}
      {route === 'design' && <DesignPage onPlay={id => start(id)} />}
      {route === 'settings' && <SettingsPage />}
      {route === 'rules' && <RulesPage game={currentGame} coverage={coverage} onPlay={() => start()} />}
      {route === 'workspace' && (session
        ? <WorkspacePage state={session} busy={!!busy} onAct={act} onRestart={() => start()} onRefresh={refresh} />
        : <div className="prototype-page workspace-page">
            <div className="page-heading"><div><span className="eyebrow">03 / PLAYTEST</span>
              <h1>试玩工作台</h1><p>先选择一个玩法，然后开始一局。</p></div></div>
            <button className="action-primary" disabled={!currentGame} onClick={() => start()}>开始试玩</button>
          </div>)}
      {route === 'replay' && <ReplayPage state={session} />}
    </section>

    {/* Rendered on small screens only: the sidebar is hidden there, and without
        this there is no way back out of the workspace. */}
    <nav className="mobile-nav" aria-label="主导航">
      {NAV.map(item => <button key={item.id} className={route === item.id ? 'is-active' : ''}
        onClick={() => setRoute(item.id)}>
        <span className="nav-glyph">{item.glyph}</span>{item.label}
      </button>)}
    </nav>
  </main>
}
