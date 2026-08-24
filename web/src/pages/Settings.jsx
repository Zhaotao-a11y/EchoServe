import React, { useState, useEffect } from 'react'
import { useStore } from '../store'

function SettingsPage() {
  const user = useStore(s => s.user)
  const [activeTab, setActiveTab] = useState('profile')
  const [apiKeys, setApiKeys] = useState([])
  const [newKeyName, setNewKeyName] = useState('')
  const [keyResult, setKeyResult] = useState(null)
  
  // 企业微信客服配置
  const [wechatConfig, setWechatConfig] = useState({
    url: '',
    token: '',
    aesKey: '',
    corpId: '',
    secret: '',
  })
  const [wechatSaveMsg, setWechatSaveMsg] = useState('')
  const [wechatLoading, setWechatLoading] = useState(false)

  // 系统信息（动态获取）
  const [systemInfo, setSystemInfo] = useState(null)
  const [systemInfoLoading, setSystemInfoLoading] = useState(false)

  useEffect(() => {
    if (activeTab === 'apikey') loadApiKeys()
    if (activeTab === 'wechat') loadWechatConfig()
    if (activeTab === 'system') loadSystemInfo()
  }, [activeTab])

  const loadWechatConfig = async () => {
    setWechatLoading(true)
    try {
      const token = localStorage.getItem('token')
      const resp = await fetch('/api/settings/wechat-kf', {
        headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      })
      if (resp.ok) {
        const data = await resp.json()
        const cfg = data.config || {}
        setWechatConfig({
          url: cfg.url || '',
          token: cfg.token || '',
          aesKey: cfg.aesKey || '',
          corpId: cfg.corpId || '',
          secret: cfg.secret || '',
        })
      }
    } catch (e) {
      console.error('加载微信配置失败:', e)
    } finally {
      setWechatLoading(false)
    }
  }

  const loadApiKeys = async () => {
    try {
      const token = localStorage.getItem('token')
      const resp = await fetch('/api/auth/api-keys', {
        headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      })
      const data = await resp.json()
      setApiKeys(data.api_keys || [])
    } catch (e) {
      console.error(e)
    }
  }

  const loadSystemInfo = async () => {
    setSystemInfoLoading(true)
    try {
      const token = localStorage.getItem('token')
      const resp = await fetch('/api/settings/system', {
        headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      })
      if (resp.ok) {
        const data = await resp.json()
        if (data.system) setSystemInfo(data.system)
      }
    } catch (e) {
      console.error('加载系统信息失败:', e)
    } finally {
      setSystemInfoLoading(false)
    }
  }

  const createApiKey = async () => {
    try {
      const token = localStorage.getItem('token')
      const resp = await fetch('/api/auth/api-key', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ name: newKeyName || 'default' }),
      })
      const data = await resp.json()
      setKeyResult(data)
      setNewKeyName('')
      loadApiKeys()
    } catch (e) {
      alert(`创建失败: ${e.message}`)
    }
  }

  const revokeKey = async (keyId) => {
    if (!confirm('确定吊销此 API Key?')) return
    try {
      const token = localStorage.getItem('token')
      await fetch(`/api/auth/api-key/${keyId}`, {
        method: 'DELETE',
        headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      })
      loadApiKeys()
    } catch (e) {
      alert(`吊销失败: ${e.message}`)
    }
  }

  return (
    <div class="p-6 space-y-6">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">系统设置</h1>
        <p class="text-sm text-gray-500 mt-1">管理个人信息、API 密钥和系统配置</p>
      </div>

      {/* 标签页 */}
      <div class="flex gap-2 border-b">
        {[
          { id: 'profile', label: '个人信息' },
          { id: 'apikey', label: 'API 密钥' },
          { id: 'wechat', label: '企业微信客服' },
          { id: 'system', label: '系统信息' },
        ].map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            class={`px-4 py-2 text-sm border-b-2 transition ${
              activeTab === tab.id
                ? 'border-blue-600 text-blue-600 font-medium'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >{tab.label}</button>
        ))}
      </div>

      {/* 个人信息 */}
      {activeTab === 'profile' && (
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-6 max-w-lg">
          <div class="space-y-4">
            <div>
              <label class="block text-sm text-gray-600 mb-1">用户名</label>
              <input
                value={user?.username || ''}
                disabled
                class="w-full px-3 py-2 border rounded-lg bg-gray-50 text-gray-500"
              />
            </div>
            <div>
              <label class="block text-sm text-gray-600 mb-1">角色</label>
              <input
                value={user?.role || ''}
                disabled
                class="w-full px-3 py-2 border rounded-lg bg-gray-50 text-gray-500"
              />
            </div>
            <div>
              <label class="block text-sm text-gray-600 mb-1">新密码</label>
              <input
                type="password"
                placeholder="输入新密码（留空则不修改）"
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
              />
              <p class="text-xs text-gray-400 mt-1">密码需≥8位，含大小写字母、数字和特殊字符</p>
            </div>
            <button class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm">
              保存修改
            </button>
          </div>
        </div>
      )}

      {/* API 密钥 */}
      {activeTab === 'apikey' && (
        <div class="space-y-4">
          {/* 创建新 Key */}
          <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
            <h3 class="font-semibold mb-3">创建 API Key</h3>
            <div class="flex gap-2">
              <input
                value={newKeyName}
                onChange={e => setNewKeyName(e.target.value)}
                placeholder="Key 名称（如：数据分析脚本）"
                class="flex-1 px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
              />
              <button
                onClick={createApiKey}
                class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm"
              >创建</button>
            </div>
          </div>

          {/* 新 Key 提示 */}
          {keyResult && (
            <div class="bg-yellow-50 border border-yellow-200 rounded-xl p-4">
              <div class="text-sm font-medium text-yellow-800 mb-2">⚠️ 请立即保存此 Key</div>
              <p class="text-xs text-yellow-700 mb-2">Key 仅在创建时显示一次，请妥善保存。</p>
              <div class="bg-yellow-100 p-2 rounded font-mono text-xs break-all">
                {keyResult.key}
              </div>
              <button
                onClick={() => setKeyResult(null)}
                class="mt-2 text-xs text-yellow-600 hover:text-yellow-800"
              >我已保存，关闭</button>
            </div>
          )}

          {/* Key 列表 */}
          <div class="bg-white rounded-xl shadow-sm border border-gray-100">
            <table class="w-full text-sm">
              <thead>
                <tr class="text-left text-gray-500 border-b bg-gray-50">
                  <th class="px-4 py-3">名称</th>
                  <th class="px-4 py-3">创建时间</th>
                  <th class="px-4 py-3">最后使用</th>
                  <th class="px-4 py-3">状态</th>
                  <th class="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {apiKeys.length > 0 ? apiKeys.map(key => (
                  <tr key={key.key_id} class="border-b border-gray-50">
                    <td class="px-4 py-2">{key.name}</td>
                    <td class="px-4 py-2 text-xs text-gray-500">{key.created_at?.slice(0, 16)}</td>
                    <td class="px-4 py-2 text-xs text-gray-500">{key.last_used?.slice(0, 16) || '从未使用'}</td>
                    <td class="px-4 py-2">
                      <span class={`text-xs px-2 py-0.5 rounded ${key.enabled ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                        {key.enabled ? '启用' : '已吊销'}
                      </span>
                    </td>
                    <td class="px-4 py-2">
                      {key.enabled && (
                        <button
                          onClick={() => revokeKey(key.key_id)}
                          class="text-red-500 hover:text-red-700 text-xs"
                        >吊销</button>
                      )}
                    </td>
                  </tr>
                )) : (
                  <tr><td colspan="5" class="px-4 py-6 text-center text-gray-400">暂无 API Key</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 企业微信客服配置 */}
      {activeTab === 'wechat' && (
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-6 max-w-2xl">
          <h3 class="font-semibold mb-1">企业微信客服</h3>
          <p class="text-sm text-gray-500 mb-5">配置回调 URL、Token、AES Key 和 CorpID</p>
          
          <div class="space-y-4">
            <div>
              <label class="block text-sm text-gray-600 mb-1">回调 URL</label>
              <input
                value={wechatConfig.url}
                onChange={e => setWechatConfig({...wechatConfig, url: e.target.value})}
                placeholder="https://your-domain.com/webhook/wechat_kf"
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
              />
              <p class="text-xs text-gray-400 mt-1">企业微信后台「API 接入」中填写的回调地址</p>
            </div>
            
            <div>
              <label class="block text-sm text-gray-600 mb-1">Token</label>
              <input
                value={wechatConfig.token}
                onChange={e => setWechatConfig({...wechatConfig, token: e.target.value})}
                placeholder="例: Tgl6P"
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
              />
            </div>
            
            <div>
              <label class="block text-sm text-gray-600 mb-1">EncodingAESKey</label>
              <input
                value={wechatConfig.aesKey}
                onChange={e => setWechatConfig({...wechatConfig, aesKey: e.target.value})}
                placeholder="43位 base64 字符串"
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none font-mono text-xs"
              />
              <p class="text-xs text-gray-400 mt-1">企业微信后台生成的 43 位 AES Key</p>
            </div>
            
            <div>
              <label class="block text-sm text-gray-600 mb-1">CorpID</label>
              <input
                value={wechatConfig.corpId}
                onChange={e => setWechatConfig({...wechatConfig, corpId: e.target.value})}
                placeholder="例: wwxxxxxxxxxxxxxxxx"
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none font-mono text-xs"
              />
              <p class="text-xs text-gray-400 mt-1">企业微信「我的企业」页面底部获取</p>
            </div>
            
            <div>
              <label class="block text-sm text-gray-600 mb-1">Secret</label>
              <input
                value={wechatConfig.secret}
                onChange={e => setWechatConfig({...wechatConfig, secret: e.target.value})}
                placeholder="企业微信应用的 Secret"
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none font-mono text-xs"
              />
              <p class="text-xs text-gray-400 mt-1">企业微信「应用管理」中获取，用于获取 access_token 发送主动消息</p>
            </div>
            
            <div class="flex gap-2 pt-2">
              <button
                onClick={async () => {
                  const token = localStorage.getItem('token')
                  if (!token) {
                    setWechatSaveMsg('请先登录')
                    return
                  }
                  try {
                    const resp = await fetch('/api/settings/wechat-kf', {
                      method: 'POST',
                      headers: {
                        'Content-Type': 'application/json',
                        Authorization: `Bearer ${token}`,
                      },
                      body: JSON.stringify(wechatConfig)
                    })
                    const data = await resp.json()
                    if (resp.ok) {
                      setWechatSaveMsg('配置已保存，建议重启服务生效')
                    } else {
                      setWechatSaveMsg(`保存失败: ${data.detail || '未知错误'}`)
                    }
                  } catch (e) {
                    setWechatSaveMsg(`网络错误: ${e.message}`)
                  }
                }}
                class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm"
              >保存配置</button>
              {wechatLoading && (
                <span class="text-sm text-gray-400 self-center">加载中...</span>
              )}
              {wechatSaveMsg && (
                <span class={`text-sm self-center ${wechatSaveMsg.includes('失败') || wechatSaveMsg.includes('错误') ? 'text-red-600' : 'text-green-600'}`}>
                  {wechatSaveMsg}
                </span>
              )}
            </div>
            
            <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-3 mt-4">
              <p class="text-xs text-yellow-700">
                <strong>提示：</strong>配置保存到 .env 文件后，需重启 EchoServe 服务才能完全生效。
                Secret 字段用于获取 access_token 以发送主动回复消息；若不配置，将无法回复客户消息。
              </p>
            </div>
          </div>
        </div>
      )}

      {/* 系统信息 */}
      {activeTab === 'system' && (
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-6 max-w-lg">
          <h3 class="font-semibold mb-4">系统信息</h3>
          <div class="space-y-3 text-sm">
            <InfoRow label="系统名称" value="EchoServe" />
            <InfoRow label="版本" value="0.1.2 (P2)" />
            <InfoRow label="运行环境" value="RTX 3090 24G" />
            <InfoRow label="基础模型" value="Qwen3-8B-Instruct" />
            <InfoRow label="Embedding" value="bge-small-zh-v1.5" />
            <InfoRow label="数据本地化" value="✅ 所有数据不出域" highlight />
            <InfoRow label="安全合规" value="等保2.0 三级（目标）" />
          </div>
        </div>
      )}
    </div>
  )
}

function InfoRow({ label, value, highlight }) {
  return (
    <div class="flex justify-between items-center py-2 border-b border-gray-50">
      <span class="text-gray-500">{label}</span>
      <span class={`font-medium ${highlight ? 'text-green-600' : 'text-gray-800'}`}>{value}</span>
    </div>
  )
}

export default SettingsPage
