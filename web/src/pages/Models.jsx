import React, { useState, useEffect } from 'react'
import { useStore } from '../store'

const API = ''

export default function ModelsPage() {
  const token = useStore(s => s.token)
  const [models, setModels] = useState([])
  const [current, setCurrent] = useState(null)
  const [loading, setLoading] = useState(true)
  const [switching, setSwitching] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // 加载模型列表
  const loadModels = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/api/model/list`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setModels(data.models || [])
    } catch (e) {
      setError(`加载失败: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  // 加载当前状态
  const loadStatus = async () => {
    try {
      const res = await fetch(`${API}/api/model/status`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setCurrent(data.current_model)
      }
    } catch (e) {
      // ignore
    }
  }

  useEffect(() => {
    loadModels()
    loadStatus()
  }, [])

  // 切换模型
  const handleSwitch = async (modelId) => {
    setSwitching(true)
    setMessage('')
    setError('')
    try {
      const res = await fetch(`${API}/api/model/switch`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ model_id: modelId }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`✅ 已切换到: ${data.model_id}`)
      setCurrent(data.model_id)
      if (data.lora_result) {
        setMessage(m => m + ` (LoRA: ${data.lora_result.status})`)
      }
      loadStatus()
    } catch (e) {
      setError(`切换失败: ${e.message}`)
    } finally {
      setSwitching(false)
    }
  }

  // 加载 adapter
  const handleLoadAdapter = async (adapterName) => {
    try {
      const res = await fetch(`${API}/api/model/adapter/load`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ adapter_name: adapterName }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`✅ Adapter 已加载: ${adapterName}`)
      loadModels()
    } catch (e) {
      setError(`加载失败: ${e.message}`)
    }
  }

  // 卸载 adapter
  const handleUnloadAdapter = async (adapterName) => {
    try {
      const res = await fetch(`${API}/api/model/adapter/unload`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ adapter_name: adapterName }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`)
      setMessage(`✅ Adapter 已卸载: ${adapterName}`)
      loadModels()
    } catch (e) {
      setError(`卸载失败: ${e.message}`)
    }
  }

  const baseModels = models.filter(m => m.type === 'base')
  const adapters = models.filter(m => m.type === 'lora')

  return (
    <div class="p-6 max-w-6xl mx-auto">
      {/* 标题 */}
      <div class="mb-6">
        <h1 class="text-2xl font-bold text-slate-800">🤖 模型管理</h1>
        <p class="text-sm text-slate-500 mt-1">
          管理基础模型和 LoRA adapters，支持热切换无需重启
        </p>
      </div>

      {/* 提示信息 */}
      {message && (
        <div class="mb-4 p-3 bg-green-50 border border-green-200 rounded text-green-700 text-sm">
          {message}
        </div>
      )}
      {error && (
        <div class="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* 当前模型 */}
      <div class="mb-6 p-4 bg-blue-50 border border-blue-200 rounded-lg">
        <div class="text-sm text-blue-600 font-medium">当前活跃模型</div>
        <div class="text-xl font-bold text-blue-900 mt-1">
          {current || '未加载'}
        </div>
      </div>

      {/* 基础模型 */}
      <div class="mb-8">
        <h2 class="text-lg font-semibold text-slate-700 mb-3">基础模型</h2>
        {loading ? (
          <div class="text-slate-400">加载中...</div>
        ) : baseModels.length === 0 ? (
          <div class="text-slate-400 text-sm">未发现基础模型。请将模型文件放入 ./models/ 目录。</div>
        ) : (
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            {baseModels.map(m => (
              <div
                key={m.name}
                class={`p-4 border rounded-lg ${
                  current === m.name
                    ? 'border-blue-500 bg-blue-50'
                    : 'border-slate-200 bg-white hover:border-slate-300'
                }`}
              >
                <div class="flex items-start justify-between">
                  <div>
                    <div class="font-medium text-slate-800">{m.name}</div>
                    <div class="text-xs text-slate-500 mt-1">
                      {m.path}
                    </div>
                    {m.size_mb > 0 && (
                      <div class="text-xs text-slate-400 mt-1">
                        {m.size_mb} MB
                      </div>
                    )}
                  </div>
                  <span
                    class={`text-xs px-2 py-1 rounded ${
                      m.status === 'loaded'
                        ? 'bg-green-100 text-green-700'
                        : 'bg-slate-100 text-slate-500'
                    }`}
                  >
                    {m.status === 'loaded' ? '✅ 已加载' : m.status}
                  </span>
                </div>
                <button
                  onClick={() => handleSwitch(m.name)}
                  disabled={switching || current === m.name}
                  class={`mt-3 w-full py-2 rounded text-sm font-medium transition ${
                    current === m.name
                      ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                      : 'bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50'
                  }`}
                >
                  {current === m.name ? '当前使用中' : '切换至此模型'}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* LoRA Adapters */}
      <div class="mb-8">
        <h2 class="text-lg font-semibold text-slate-700 mb-3">LoRA Adapters</h2>
        {adapters.length === 0 ? (
          <div class="text-slate-400 text-sm">
            暂无 LoRA adapter。完成训练后，adapter 将自动出现在此处。
          </div>
        ) : (
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            {adapters.map(a => (
              <div
                key={a.name}
                class="p-4 border border-slate-200 bg-white rounded-lg"
              >
                <div class="flex items-start justify-between">
                  <div>
                    <div class="font-medium text-slate-800">{a.name}</div>
                    <div class="text-xs text-slate-500 mt-1">
                      基础模型: {a.base_model || 'unknown'}
                    </div>
                    {a.lora_r && (
                      <div class="text-xs text-slate-400">
                        r={a.lora_r} | {a.created_at}
                      </div>
                    )}
                  </div>
                  <span class="text-xs px-2 py-1 rounded bg-purple-100 text-purple-700">
                    LoRA
                  </span>
                </div>
                <div class="flex gap-2 mt-3">
                  <button
                    onClick={() => handleLoadAdapter(a.name)}
                    class="flex-1 py-2 rounded text-sm bg-purple-600 text-white hover:bg-purple-700"
                  >
                    加载
                  </button>
                  <button
                    onClick={() => handleUnloadAdapter(a.name)}
                    class="px-3 py-2 rounded text-sm bg-slate-100 text-slate-600 hover:bg-slate-200"
                  >
                    卸载
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 刷新按钮 */}
      <button
        onClick={() => { loadModels(); loadStatus() }}
        class="px-4 py-2 bg-slate-600 text-white rounded hover:bg-slate-700 text-sm"
      >
        🔄 刷新
      </button>
    </div>
  )
}
