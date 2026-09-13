import {useCallback, useEffect, useRef, useState} from 'react'
import {Pill} from './components/ui'
import {DesignPage} from './features/design/DesignPage'
import {LibraryPage} from './features/library/LibraryPage'
import {ReplayPage} from './features/replay/ReplayPage'
import {RulesPage} from './features/rules/RulesPage'
import {SettingsPage} from './features/settings/SettingsPage'
import {WorkspacePage} from './features/workspace/WorkspacePage'
import {ApiError, api, messageOf, type CapabilityMatrix, type Coverage, type GameEvent,
  type GameInfo, type MacroPromotion, type SessionRef, type SessionState} from './shared/api'

type Route = 'library' | 'design' | 'rules' | 'workspace' | 'replay' | 'settings'

// Recovery is keyed by game_id + version + session_id (ADR-0017): a refresh
// restores the exact session, and the key is only forgotten when the backend
// says the session no longer exists (404) — never on a transient error.
const SESSION_KEY = 'pocker-session'
const LEGACY_SESSION_KEY = 'pocker-session-id'
const cursorKey = (sessionId: string) => `pocker-cursor:${sessionId}`
const eventsKey = (sessionId: string) => `pocker-events:${sessionId}`
const MAX_STORED_EVENTS = 400
type StoredEvents = {cursor: number; events: GameEvent[]}

function readStoredEvents(sessionId: string): StoredEvents | null {
  const raw = localStorage.getItem(eventsKey(sessionId))
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<StoredEvents>
    if (Array.isArray(parsed?.events) && typeof parsed?.cursor === 'number') {
      return {cursor: parsed.cursor, events: parsed.events}
    }
  } catch { /* a corrupt cache is rebuilt from the server */ }
  return null
}

// The cursor and the events already on screen are persisted together, so a
// reload resumes the delta *and* keeps the history it displayed. Resuming from
// a bare cursor would show an empty timeline that starts mid-game.
function writeStoredEvents(sessionId: string, cursor: number, events: GameEvent[]) {
  localStorage.setItem(eventsKey(sessionId),
    JSON.stringify({cursor, events: events.slice(-MAX_STORED_EVENTS)}))
}

const NAV: Array<{id: Route; label: string; glyph: string}> = [
  {id: 'library', label: '玩法库', glyph: '♠'},
  {id: 'design', label: '新建玩法', glyph: '✦'},
  {id: 'rules', label: '规则与能力', glyph: '♦'},
  {id: 'workspace', label: '试玩工作台', glyph: '♣'},
  {id: 'replay', label: '回放与诊断', glyph: '↺'},
  {id: 'settings', label: '模型设置', glyph: '⚙'},
]

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID().slice(0, 32)
  return `req-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

function readStoredRef(): SessionRef | null {
  const raw = localStorage.getItem(SESSION_KEY)
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as Partial<SessionRef>
      if (parsed?.session_id) {
        return {session_id: parsed.session_id, game_id: parsed.game_id ?? '', version: parsed.version ?? null}
      }
    } catch { /* fall through to the legacy key */ }
  }
  const legacy = localStorage.getItem(LEGACY_SESSION_KEY)
  return legacy ? {session_id: legacy, game_id: '', version: null} : null
}

export function App() {
  const [route, setRoute] = useState<Route>('library')
  const [games, setGames] = useState<GameInfo[]>([])
  const [coverage, setCoverage] = useState<Coverage | null>(null)
  const [matrix, setMatrix] = useState<CapabilityMatrix | null>(null)
  const [macroPromotion, setMacroPromotion] = useState<MacroPromotion[]>([])
  const [gameId, setGameId] = useState<string | null>(null)
  const [session, setSession] = useState<SessionState | null>(null)
  const [events, setEvents] = useState<GameEvent[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const cursorRef = useRef(0)
  const eventsRef = useRef<GameEvent[]>([])
  const pendingAct = useRef<{key: string; request_id: string} | null>(null)

  const currentGame = games.find(game => game.id === gameId) ?? null

  const run = useCallback(async (label: string, task: () => Promise<void>) => {
    setBusy(label); setError(''); setNotice('')
    try { await task() } catch (err) { setError(messageOf(err)) } finally { setBusy('') }
  }, [])

  const refreshGames = useCallback(() => {
    api.games().then(data => { setGames(data.games); setCoverage(data.coverage) })
      .catch(err => setError(messageOf(err)))
    api.capabilities().then(data => { setMatrix(data.matrix); setMacroPromotion(data.macro_promotion) })
      .catch(err => setError(messageOf(err)))
  }, [])
  useEffect(refreshGames, [refreshGames])

  const persistRef = useCallback((state: SessionState) => {
    localStorage.setItem(SESSION_KEY, JSON.stringify({
      session_id: state.session_id, game_id: state.game_id, version: state.version ?? null,
    }))
    localStorage.removeItem(LEGACY_SESSION_KEY)
  }, [])

  // One place writes the cursor, the on-screen events and the cache together, so
  // the three can never disagree about what has already been shown.
  const commitEvents = useCallback((sessionId: string, cursor: number, next: GameEvent[]) => {
    cursorRef.current = cursor
    eventsRef.current = next
    writeStoredEvents(sessionId, cursor, next)
    setEvents(next)
  }, [])

  // Append everything the cursor has not seen yet. If the cached cursor is past
  // this session's log (a stale cache), rebuild from zero instead of silently
  // skipping the events in between.
  const syncEvents = useCallback(async (sessionId: string) => {
    let cursor = cursorRef.current
    let collected = eventsRef.current
    for (let page = 0; page < 100; page += 1) {
      const data = await api.events(sessionId, cursor)
      if (page === 0 && data.total < cursor) {
        cursor = 0; collected = []; continue
      }
      collected = [...collected, ...data.events]
      cursor = data.cursor
      if (!data.has_more) break
    }
    commitEvents(sessionId, cursor, collected)
  }, [commitEvents])

  const clearSession = useCallback((sessionId?: string) => {
    localStorage.removeItem(SESSION_KEY)
    localStorage.removeItem(LEGACY_SESSION_KEY)
    if (sessionId) {
      localStorage.removeItem(cursorKey(sessionId))
      localStorage.removeItem(eventsKey(sessionId))
    }
    setSession(null); setEvents([]); cursorRef.current = 0; eventsRef.current = []
  }, [])

  // Resume the session this browser had open. A transient failure keeps the
  // stored key; only a 404 (the backend lost it) forgets it.
  useEffect(() => {
    const stored = readStoredRef()
    if (!stored) return
    api.getSession(stored.session_id)
      .then(async state => {
        setSession(state); setGameId(state.game_id); persistRef(state); setRoute('workspace')
        const cached = readStoredEvents(state.session_id)
        commitEvents(state.session_id, cached?.cursor ?? 0, cached?.events ?? [])
        await syncEvents(state.session_id)
      })
      .catch(err => {
        if (err instanceof ApiError && err.isMissing) clearSession(stored.session_id)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openGame = (id: string) => { setGameId(id); setSession(null); setRoute('rules') }

  const start = useCallback((target?: string, version?: number | null) => {
    const id = target ?? gameId
    if (!id) return
    void run('开始试玩', async () => {
      setGameId(id)
      const created = await api.createSession(id, undefined, version ?? null)
      persistRef(created)
      setSession(created)
      localStorage.removeItem(eventsKey(created.session_id))
      commitEvents(created.session_id, 0, [])
      await syncEvents(created.session_id)
      setRoute('workspace')
      refreshGames()
    })
  }, [gameId, run, persistRef, commitEvents, syncEvents, refreshGames])

  const act = (actionId: string, inputValues: Record<string, unknown>) => {
    if (!session) return
    // A request_id is reused only for a retry of the *same* request: the key
    // includes the action, its inputs and the revision, so changing any of them
    // (or a refreshed revision after a conflict) mints a fresh id.
    const key = JSON.stringify({actionId, inputValues, revision: session.revision})
    const requestId = pendingAct.current?.key === key
      ? pendingAct.current.request_id : newRequestId()
    pendingAct.current = {key, request_id: requestId}
    void run('执行动作', async () => {
      try {
        const data = await api.actGeneric(session.session_id, {
          action_id: actionId, input_values: inputValues, revision: session.revision,
          request_id: requestId,
        })
        pendingAct.current = null
        setSession(data.state)
        const delta = data.new_events ?? []
        commitEvents(session.session_id, cursorRef.current + delta.length,
          [...eventsRef.current, ...delta])
      } catch (err) {
        if (err instanceof ApiError && err.isMissing) {
          pendingAct.current = null
          clearSession(session.session_id)
          setError('这一局已不存在，请重新开始。')
          return
        }
        if (err instanceof ApiError && err.isConflictPrefix) {
          // Conflict is recoverable: refetch the authoritative state, keep the
          // session id, and catch the cursor up with the other writer's events.
          const fresh = await api.getSession(session.session_id)
          setSession(fresh)
          await syncEvents(session.session_id)
          setNotice('局面已更新（409），已刷新到最新状态，请重新选择动作。')
          return
        }
        throw err
      }
    })
  }

  const refresh = () => {
    if (!session) return
    void run('刷新状态', async () => {
      setSession(await api.getSession(session.session_id))
      await syncEvents(session.session_id)
    })
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
        <strong>{currentGame?.title ?? session?.game_id ?? '未选择'}</strong>
        <small>{session
          ? `revision ${session.revision}${session.version !== null && session.version !== undefined ? ` · v${session.version}` : ''}`
          : currentGame ? currentGame.kind : '从玩法库开始'}</small>
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
          {session && <button className="icon-button topbar-refresh" title="刷新状态"
            onClick={refresh} disabled={!!busy}>↻</button>}
        </div>
      </header>

      {error && <div className="state-banner conflict"><span className="state-icon">!</span>
        <div><strong>{error}</strong><small>操作未提交；状态保持不变。</small></div>
        <button onClick={() => setError('')}>关闭</button></div>}
      {notice && <div className="state-banner notice"><span className="state-icon">i</span>
        <div><strong>{notice}</strong><small>{status}</small></div>
        <button onClick={() => setNotice('')}>关闭</button></div>}

      {route === 'library' && <LibraryPage games={games} coverage={coverage} matrix={matrix}
        macroPromotion={macroPromotion} onOpen={openGame} />}
      {route === 'design' && <DesignPage onPlay={start} />}
      {route === 'settings' && <SettingsPage />}
      {route === 'rules' && <RulesPage game={currentGame} coverage={coverage} onPlay={start} />}
      {route === 'workspace' && (session
        ? <WorkspacePage state={session} events={events} busy={!!busy}
            onAct={act} onRestart={() => start()} onRefresh={refresh} />
        : <div className="prototype-page workspace-page">
            <div className="page-heading"><div><span className="eyebrow">03 / PLAYTEST</span>
              <h1>试玩工作台</h1><p>先选择一个玩法，然后开始一局。</p></div></div>
            <button className="action-primary" disabled={!currentGame} onClick={() => start()}>开始试玩</button>
          </div>)}
      {route === 'replay' && <ReplayPage state={session} events={events} />}
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
