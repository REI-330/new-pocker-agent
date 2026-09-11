import React, {useCallback, useEffect, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {API, request, downloadGame, messageOf, type Message, type Rule, type Runtime, type GameEvent, type ModelConfig, type ActionArguments} from './api'
import {ModelSettings} from './components/ModelSettings'
import {GameTable} from './components/GameTable'
import './styles.css'
import './runtime.css'
import './config.css'

type Workbench = {turns: Message[]; messages: Message[]; proposal: Rule | null; confirmed: boolean; events: GameEvent[]; runtimeId: string | null; facts: string[]}
const EMPTY: Workbench = {turns:[],messages:[],proposal:null,confirmed:false,events:[],runtimeId:null,facts:[]}
const STORAGE_KEY = 'pocker-workbench-v2'
function restore(): {work: Workbench; error: string} {
  try {
    const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (!data) return {work:EMPTY,error:''}
    if (!Array.isArray(data.turns) || !Array.isArray(data.messages) || !Array.isArray(data.events)) throw new Error()
    return {work:{...EMPTY,...data},error:''}
  } catch { return {work:EMPTY,error:'本机草稿无法读取，已打开空白工作台'} }
}
function App() {
  const [initial] = useState(restore)
  const [work, setWork] = useState(initial.work)
  const [input,setInput] = useState(initial.work.turns.length ? '' : '我想做一个两人比大小游戏，每人一张牌，翻开后点数高的人获胜。')
  const [runtime,setRuntime] = useState<Runtime | null>(null)
  const [busy,setBusy] = useState(initial.work.runtimeId ? '恢复牌局' : '')
  const [error,setError] = useState(initial.error)
  const [status,setStatus] = useState('描述玩法，开始设计')
  const [showConfig,setShowConfig] = useState(true)
  const [modelConfig,setModelConfig] = useState<ModelConfig | null>(null)
  const [configBlocked,setConfigBlocked] = useState(true)
  const onSaved = useCallback((data: ModelConfig) => setModelConfig(data), [])

  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(work)) }
    catch { setError('浏览器无法保存草稿；请允许此站点使用本机存储') }
  }, [work])
  useEffect(() => {
    if (!initial.work.runtimeId) return
    let active = true
    request<Runtime>('/api/runtime/sessions/' + initial.work.runtimeId)
      .then(data => { if (active) {setRuntime(data);setStatus('已恢复上次牌局')} })
      .catch(err => { if (active) setError(messageOf(err)) })
      .finally(() => { if (active) setBusy('') })
    return () => {active = false}
  }, [initial])

  async function operation(label: string, task: () => Promise<void>) {
    if (busy) return
    setBusy(label);setError('')
    try { await task() } catch (err) {setError(messageOf(err))}
    finally {setBusy('')}
  }
  function ask() {
    if (!input.trim() || !modelConfig?.configured || configBlocked) return
    void operation('生成规则', async () => {
      const content = input.trim()
      const data = await request<{kind:string;message:string;rules:Rule|null;errors:string[];messages:Message[];facts:string[]}>('/api/agent/turn', {
        message: content, messages: work.messages, proposal: work.proposal,
      })
      if (data.kind === 'error') throw new Error([data.message,...data.errors].join('\n'))
      // Any rule conversation invalidates previous confirmation and derived game state.
      setWork({...work,turns:[...work.turns,{role:'user',content},{role:'assistant',content:data.message}],
        messages:data.messages, proposal:data.rules, confirmed:false,events:[],runtimeId:null,facts:data.facts || []})
      setRuntime(null);setInput('')
      setStatus(data.kind === 'question' ? '等待补充规则' : data.kind === 'unsupported' ? '当前玩法尚缺少执行能力' : '规则已生成，请确认')
    })
  }
  function confirmRules() {
    if (!work.proposal) return
    void operation('确认规则', async () => {
      const data = await request<{kind:string;rules:Rule;errors:string[];facts:string[]}>('/api/agent/confirm',{proposal:work.proposal})
      if (data.kind !== 'confirmed') throw new Error(data.errors.join('\n'))
      setWork({...work,proposal:data.rules,confirmed:true,facts:data.facts});setStatus('规则已确认')
    })
  }
  function simulate() {
    if (!work.confirmed || !work.proposal) return
    void operation('运行模拟', async () => {
      const data = await request<{completed:boolean;events:GameEvent[]}>('/api/simulations?seed=7',work.proposal)
      if (!data.completed) throw new Error('模拟尚未完成')
      setWork({...work,events:data.events});setStatus('模拟完成，所有回合已执行')
    })
  }
  function start() {
    if (!work.confirmed || !work.proposal) return
    void operation('开始试玩', async () => {
      const data = await request<Runtime>('/api/runtime/sessions', work.proposal)
      setRuntime(data);setWork({...work,runtimeId:data.session_id});setStatus(data.finished ? '本局已结束' : '轮到你出牌')
    })
  }
  function act(action: string, cardIndex: number, args: ActionArguments = {}) {
    if (!runtime || !work.confirmed) return
    void operation('执行回合', async () => {
      const data = await request<{state:Runtime}>('/api/runtime/sessions/' + runtime.session_id + '/actions/' + action,{
        revision:runtime.revision,card_index:cardIndex,...args,
      })
      setRuntime(data.state);setStatus(data.state.finished ? '本局结束，可以再来一局' : '等待你的下一步操作')
    })
  }
  function refresh() {
    const id = runtime?.session_id || work.runtimeId
    if (!id) return
    void operation('刷新牌局', async () => {setRuntime(await request<Runtime>('/api/runtime/sessions/' + id));setStatus('牌局已刷新')})
  }
  return <main className="shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">♠</span><div><strong>Pocker Agent</strong><small>把规则变成牌局</small></div></div>
      <div className="top-actions"><span className="status" role="status">{busy ? '正在' + busy + '…' : status}</span>
      <button className="config-link" onClick={() => setShowConfig(!showConfig)}>{showConfig ? '收起模型设置' : '模型设置'}</button></div>
    </header>
    <div hidden={!showConfig}><ModelSettings onSaved={onSaved} onBlockedChange={setConfigBlocked} disabled={!!busy} /></div>
    {error && <div className="error" role="alert"><pre>{error}</pre></div>}
    <section className="hero"><p className="eyebrow">FROM IDEA TO PLAY</p><h1>写下规则。<br/>开始一局。</h1><p className="lede">和 Agent 一起补全玩法，确认规则，查看模拟，再与电脑试玩。</p>
      <p className="support-note">描述24点算式练习、无下注21点、疯狂八接牌，或组合已有的摸牌、出牌和计分规则。规则变体会先与你确认。</p></section>
    <section className="workspace">
      <section className="panel conversation"><div className="panel-head"><div><span className="kicker">01 / DESIGN</span><h2>玩法对话</h2></div><button className="secondary" disabled={!!busy} onClick={() => {setWork(EMPTY);setRuntime(null);setError('');setStatus('已开始新的设计')}}>新建游戏</button></div>
        <div className="thread">{!work.turns.length && <p className="empty">例如：两人比大小，使用标准 52 张牌，A 最大。每轮各出一张，最高牌得 1 分，平局各得 1 分，共 3 轮。</p>}
          {work.turns.map((turn,i) => <div className={'bubble ' + turn.role} key={i}><span>{turn.role === 'user' ? '你' : 'Agent'}</span><p>{turn.content}</p></div>)}
        </div>
        <div className="composer"><textarea aria-label="玩法描述" value={input} onChange={e => setInput(e.target.value)} disabled={!!busy} placeholder="描述规则或回答 Agent 的问题…" /><button onClick={ask} disabled={!!busy || configBlocked || !input.trim() || !modelConfig?.configured}>发送</button></div>
        {!modelConfig?.configured && <p className="support-note">请先在模型设置中保存配置。</p>}
        {modelConfig?.configured && configBlocked && <p className="support-note">模型配置正在处理或有未保存的修改，请在模型设置中完成保存或撤销。</p>}
      </section>
      <section className="panel rules"><div className="panel-head"><div><span className="kicker">02 / RULES</span><h2>规则提案</h2></div><span className="pill">{work.confirmed ? '已确认' : '待确认'}</span></div>
        {work.proposal ? <><div className="rule-summary"><h3>{work.proposal.title}</h3><p>{work.proposal.players.min_players} 位玩家 · {work.proposal.max_rounds} 轮</p><ul className="rule-facts">{work.facts.map((fact,i)=><li key={i}>{fact}</li>)}</ul></div>
        <details><summary>查看完整规则 DSL</summary><pre className="dsl">{JSON.stringify(work.proposal,null,2)}</pre></details>
        <div className="rule-actions"><button className="primary" onClick={confirmRules} disabled={!!busy || work.confirmed}>确认规则</button><button className="secondary" onClick={simulate} disabled={!!busy || !work.confirmed}>运行模拟</button></div></> : <div className="empty tall">确认玩法细节后，这里会出现规则提案。</div>}
      </section>
      <section className="panel trace"><div className="panel-head"><div><span className="kicker">03 / VERIFY</span><h2>模拟轨迹</h2></div><span className="pill">{work.events.length} 个事件</span></div>
        {work.events.length ? <div className="events">{work.events.map((event,i) => <div className="event" key={i}><span className="event-index">{i+1}</span><div><strong>{String(event.event)} · 第 {String(event.round)} 轮</strong><code>{JSON.stringify(event)}</code></div></div>)}</div> : <p className="empty tall">运行模拟检查发牌、电脑动作、轮次计分和结果。</p>}
      </section>
      <GameTable state={runtime} busy={!!busy} canStart={work.confirmed} start={start} act={act} refresh={refresh}/>
      <section className="export-bar"><div><span className="kicker">05 / TAKE IT WITH YOU</span><h2>把这局游戏带走</h2><p>{work.proposal?.kind ? '当前玩法支持网页试玩和保存恢复，离线导出尚未支持。' : '解压后打开 index.html 离线游玩。练习局固定发牌，无需 API Key。'}</p></div>
        <button className="primary" disabled={!!busy || !work.confirmed || !!work.proposal?.kind} onClick={() => void operation('导出游戏',async () => {await downloadGame(work.proposal!);setStatus('游戏包已下载，解压后打开 index.html')})}>下载游戏包</button>
      </section>
    </section>
    <footer>牌面素材：<a href="https://github.com/hayeah/playing-cards-assets" target="_blank" rel="noreferrer">Playing Cards Assets</a> · <a href={API + '/assets/cards/LICENSE'}>MIT License</a></footer>
  </main>
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
