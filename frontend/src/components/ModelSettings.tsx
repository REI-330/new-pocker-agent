import {useEffect, useState} from 'react'
import {request, messageOf, type ModelConfig} from '../api'

const EMPTY: ModelConfig = {configured:false, has_key:false, base_url:'', model:''}
function canonicalUrl(value: string) {
  try {
    const url = new URL(value.trim())
    url.pathname = url.pathname.replace(/\/(chat\/completions|models)\/?$/, '').replace(/\/$/, '') || '/v1'
    return url.toString().replace(/\/$/, '')
  } catch { return value.trim() }
}

export function ModelSettings({onSaved, onBlockedChange, disabled}: {onSaved: (config: ModelConfig) => void; onBlockedChange: (blocked: boolean) => void; disabled: boolean}) {
  const [saved, setSaved] = useState(EMPTY)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [custom, setCustom] = useState(false)
  const [pending, setPending] = useState('读取配置')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    request<ModelConfig>('/api/agent/config').then(data => {
      if (!active) return
      setSaved(data); setBaseUrl(data.base_url); setModel(data.model); onSaved(data)
    }).catch(err => { if (active) setError(messageOf(err)) })
      .finally(() => { if (active) setPending('') })
    return () => { active = false }
  }, [onSaved])

  const reuseKey = saved.has_key && canonicalUrl(baseUrl) === saved.base_url
  const canRequest = !!baseUrl.trim() && (!!apiKey.trim() || reuseKey)
  const dirty = !!apiKey || canonicalUrl(baseUrl) !== saved.base_url || model.trim() !== saved.model
  useEffect(() => { onBlockedChange(dirty || !!pending) }, [dirty, pending, onBlockedChange])

  function discardDraft() {
    setBaseUrl(saved.base_url); setModel(saved.model); setApiKey('')
    setModels([]); setCustom(false); setNotice('已恢复保存的配置'); setError('')
  }
  function editProvider(value: string, key = false) {
    if (key) setApiKey(value); else setBaseUrl(value)
    // A new credential can expose a completely different model entitlement.
    if ((key && value.trim()) || (!key && canonicalUrl(value) !== saved.base_url)) {
      setModel(''); setCustom(false)
    } else if (key && !value.trim() && canonicalUrl(baseUrl) === saved.base_url) {
      setModel(saved.model)
    }
    setModels([]); setNotice(''); setError('')
  }
  async function perform(action: 'models' | 'test-connection' | 'config') {
    if (pending || disabled) return
    setPending(action); setError(''); setNotice('')
    try {
      const body = {base_url: baseUrl.trim(), api_key: apiKey, model: model.trim()}
      if (action === 'models') {
        const result = await request<{models: string[]}>('/api/agent/models', body)
        if (!Array.isArray(result.models) || result.models.some(x => typeof x !== 'string')) throw new Error('模型列表格式错误')
        setModels(result.models)
        setNotice(result.models.length ? '已获取 ' + result.models.length + ' 个模型，请从下方选择' : '服务未返回模型，可手动填写模型名称')
      } else if (action === 'test-connection') {
        const result = await request<{model: string}>('/api/agent/test-connection', body)
        setNotice('连接测试通过 · ' + result.model + (dirty ? ' · 尚未保存' : ''))
      } else {
        const result = await request<ModelConfig>('/api/agent/config', body)
        setSaved(result); setBaseUrl(result.base_url); setModel(result.model); setApiKey('')
        setNotice(result.warning || '配置已保存，刷新页面或重启服务后仍可使用')
        onSaved(result)
      }
    } catch (err) {
      if (action === 'models') setModels([])
      setError(messageOf(err))
    } finally { setPending('') }
  }

  return <section className="config-panel" aria-label="模型配置">
    <div className="config-intro"><span className="kicker">MODEL CONNECTION</span><h2>连接你的模型</h2>
      <p>填写任意兼容 Chat Completions 的 API 地址。模型列表由你的服务返回。</p>
      <p>Key 存在本机系统凭据库。获取列表和测试连接不会改动已保存配置。</p>
      <strong>{saved.configured ? '当前已保存：' + saved.model : '尚未保存模型配置'}</strong>
      {dirty && saved.configured && <p className="draft-note">有未保存的修改。保存或撤销后才能发送玩法，避免调用错误的配置。</p>}
    </div>
    <div className="config-form">
      <fieldset disabled={!!pending || disabled}>
        <label>API 地址<input value={baseUrl} onChange={e => editProvider(e.target.value)} placeholder="https://你的服务地址/v1" autoComplete="url" /></label>
        <label>API Key<input type="password" value={apiKey} onChange={e => editProvider(e.target.value, true)} placeholder={reuseKey ? '已安全保存，留空表示保持原 Key' : '填写此服务的 API Key'} autoComplete="off" /></label>
        <button className="secondary" disabled={!canRequest} onClick={() => perform('models')}>获取模型列表</button>
        {models.length > 0 && <label>可选模型（{models.length} 个）
          <select value={custom || (model && !models.includes(model)) ? '__custom__' : model} onChange={e => {
            setNotice(''); setCustom(e.target.value === '__custom__')
            if (e.target.value !== '__custom__') setModel(e.target.value)
          }}>
            <option value="" disabled>请选择模型</option>
            {models.map(name => <option key={name} value={name}>{name}</option>)}
            <option value="__custom__">手动填写模型名称</option>
          </select>
        </label>}
        {(models.length === 0 || custom || (!!model && !models.includes(model))) &&
          <label>模型名称<input value={model} onChange={e => {setModel(e.target.value); setNotice('')}} placeholder="由服务提供的模型 ID" /></label>}
        <div className="config-buttons">
          <button className="secondary" disabled={!canRequest || !model.trim()} onClick={() => perform('test-connection')}>测试连接</button>
          <button className="primary" disabled={!canRequest || !model.trim()} onClick={() => perform('config')}>保存配置</button>
          {dirty && <button className="secondary" onClick={discardDraft}>撤销修改</button>}
        </div>
      </fieldset>
      <div aria-live="polite" className="config-feedback">{pending ? '正在' + ({models:'获取模型列表', config:'保存配置', 'test-connection':'测试连接'}[pending] || pending) + '…' : notice}</div>
      {error && <p className="inline-error" role="alert">{error}</p>}
    </div>
  </section>
}
