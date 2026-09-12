import {useEffect, useState} from 'react'
import {api, messageOf, type ModelConfig} from '../../shared/api'
import {Pill} from '../../components/ui'

// Provider presets: pick one, paste a key, fetch the model list, choose a model.
const PRESETS: Array<{id: string; label: string; base_url: string}> = [
  {id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1'},
  {id: 'deepseek', label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1'},
  {id: 'moonshot', label: 'Moonshot', base_url: 'https://api.moonshot.cn/v1'},
  {id: 'zhipu', label: '智谱 GLM', base_url: 'https://open.bigmodel.cn/api/paas/v4'},
  {id: 'openrouter', label: 'OpenRouter', base_url: 'https://openrouter.ai/api/v1'},
  {id: 'siliconflow', label: '硅基流动', base_url: 'https://api.siliconflow.cn/v1'},
  {id: 'custom', label: '自定义', base_url: ''},
]

const LAST_BASE_URL = 'pocker-base-url'

export function SettingsPage() {
  const [config, setConfig] = useState<ModelConfig | null>(null)
  const [preset, setPreset] = useState('custom')
  const [baseUrl, setBaseUrl] = useState(localStorage.getItem(LAST_BASE_URL) || '')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    api.getConfig()
      .then(data => {
        setConfig(data)
        if (data.base_url) { setBaseUrl(data.base_url); setModel(data.model) }
      })
      .catch(err => setStatus(messageOf(err)))
  }, [])

  const choosePreset = (id: string) => {
    const found = PRESETS.find(item => item.id === id)
    setPreset(id)
    if (found && found.base_url) setBaseUrl(found.base_url)
    setModels([])
  }

  const withStatus = async (label: string, task: () => Promise<void>) => {
    setBusy(label); setStatus('')
    try { await task() } catch (err) { setStatus(messageOf(err)) } finally { setBusy('') }
  }

  const fetchModels = () => void withStatus('获取模型列表', async () => {
    const data = await api.listModels({base_url: baseUrl.trim(), api_key: apiKey, model})
    setModels(data.models)
    if (!model && data.models.length) setModel(data.models[0])
    setStatus(data.models.length ? `找到 ${data.models.length} 个模型，请选择` : '服务未返回任何模型')
  })

  const save = () => void withStatus('保存配置', async () => {
    const saved = await api.saveConfig({base_url: baseUrl.trim(), model: model.trim(), api_key: apiKey})
    setConfig(saved); setApiKey('')
    localStorage.setItem(LAST_BASE_URL, saved.base_url)
    setStatus(saved.configured ? '已保存，可以到「新建玩法」开始对话' : '已保存，但配置不完整')
  })

  // Uses the draft values (including a key typed but not yet saved), so the
  // answer is "will this work", not "did the last save work".
  const testConnection = () => void withStatus('测试连接', async () => {
    const result = await api.testConnection({base_url: baseUrl.trim(), model: model.trim(),
      api_key: apiKey})
    setStatus(result.ok
      ? `连接成功：${result.model || '（未指定模型）'} @ ${result.base_url}`
      : '连接成功，但模型没有返回内容')
  })

  return <div className="prototype-page">
    <div className="page-heading">
      <div><span className="eyebrow">SETTINGS</span><h1>模型设置</h1>
        <p>选一个服务商，填 Key，拉取模型列表后选择。Key 只写入本机系统凭据库，不进入数据库、不回显。</p></div>
      <Pill tone={config?.configured ? 'success' : 'warning'}>
        {config?.configured ? `已配置 · ${config.model}` : '未配置'}</Pill>
    </div>

    <div className="chat-card">
      <span className="eyebrow">PROVIDER</span>
      <div className="prompt-chips">
        {PRESETS.map(item => <button key={item.id}
          className={preset === item.id ? 'primary' : 'secondary'}
          disabled={!!busy} onClick={() => choosePreset(item.id)}>{item.label}</button>)}
      </div>

      <label>API 地址
        <input value={baseUrl} onChange={event => setBaseUrl(event.target.value)} disabled={!!busy}
          placeholder="https://你的服务地址/v1" autoComplete="url" /></label>

      <label>API Key
        <input type="password" value={apiKey} onChange={event => setApiKey(event.target.value)}
          disabled={!!busy} autoComplete="off"
          placeholder={config?.has_key ? '已保存，留空表示保持不变' : '填写 API Key'} /></label>

      <div className="action-buttons">
        <button className="action-secondary" onClick={fetchModels}
          disabled={!!busy || !baseUrl.trim()}>{busy === '获取模型列表' ? '获取中…' : '获取模型列表'}</button>
        <button className="action-secondary" onClick={testConnection}
          disabled={!!busy || !baseUrl.trim() || !model.trim()}>
          {busy === '测试连接' ? '测试中…' : '测试连接'}</button>
      </div>

      <label>模型
        {models.length > 0 &&
          <select value={models.includes(model) ? model : ''} disabled={!!busy}
            onChange={event => setModel(event.target.value)}>
            <option value="" disabled>请选择（共 {models.length} 个）</option>
            {models.map(name => <option key={name} value={name}>{name}</option>)}
          </select>}
        <input value={model} onChange={event => setModel(event.target.value)} disabled={!!busy}
          placeholder={models.length ? '也可以直接输入模型名' : '先获取列表，或直接输入模型名'} /></label>

      <div className="action-buttons">
        <button className="action-primary" onClick={save} disabled={!!busy || !baseUrl.trim()}>
          {busy === '保存配置' ? '保存中…' : '保存配置'}</button>
      </div>

      {status && <p className="muted" role="status">{status}</p>}
      <p className="muted">当前：{config?.base_url || '—'} · {config?.model || '未选择模型'} ·
        {' '}{config?.has_key ? 'Key 已保存' : '无 Key'}</p>
    </div>
  </div>
}
