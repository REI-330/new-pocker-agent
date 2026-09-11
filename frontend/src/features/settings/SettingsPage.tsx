import {useEffect, useState} from 'react'
import {api, messageOf, type ModelConfig} from '../../shared/api'
import {Pill} from '../../components/ui'

export function SettingsPage() {
  const [config, setConfig] = useState<ModelConfig | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getConfig()
      .then(data => { setConfig(data); setBaseUrl(data.base_url); setModel(data.model) })
      .catch(err => setStatus(messageOf(err)))
  }, [])

  const save = async () => {
    if (busy) return
    setBusy(true); setStatus('')
    try {
      const saved = await api.saveConfig({base_url: baseUrl.trim(), model: model.trim(), api_key: apiKey})
      setConfig(saved); setApiKey('')
      setStatus(saved.configured ? '已保存，可以开始设计玩法' : '已保存，但配置不完整')
    } catch (err) {
      setStatus(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  return <div className="prototype-page">
    <div className="page-heading">
      <div><span className="eyebrow">SETTINGS</span><h1>模型设置</h1>
        <p>只保存在本机：Key 写入系统凭据库，不进入数据库，也不会回显。</p></div>
      <Pill tone={config?.configured ? 'success' : 'warning'}>
        {config?.configured ? '已配置' : '未配置'}</Pill>
    </div>
    <div className="chat-card">
      <label>API 地址
        <input value={baseUrl} onChange={event => setBaseUrl(event.target.value)}
          placeholder="https://你的服务地址/v1" /></label>
      <label>模型
        <input value={model} onChange={event => setModel(event.target.value)} placeholder="模型名称" /></label>
      <label>API Key
        <input type="password" value={apiKey} onChange={event => setApiKey(event.target.value)}
          placeholder={config?.has_key ? '已保存，留空表示保持不变' : '填写 API Key'} /></label>
      <div className="action-buttons">
        <button className="action-primary" onClick={save} disabled={busy}>
          {busy ? '保存中…' : '保存配置'}</button>
      </div>
      {status && <p className="muted" role="status">{status}</p>}
    </div>
  </div>
}
