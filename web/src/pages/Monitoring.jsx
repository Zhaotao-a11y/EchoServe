import React, { useState, useEffect, useCallback } from 'react'
import { apiCall } from '../store'
import { MetricCard } from '../components/ui'

// M-16: 颜色名 → CSS 类名映射（Monitoring 专用）
const cardColorMap = {
  green: { bg: 'border-green-200 bg-green-50', text: 'text-green-700' },
  yellow: { bg: 'border-yellow-200 bg-yellow-50', text: 'text-yellow-700' },
  red: { bg: 'border-red-200 bg-red-50', text: 'text-red-700' },
  blue: { bg: 'border-blue-200 bg-blue-50', text: 'text-blue-700' },
  purple: { bg: 'border-purple-200 bg-purple-50', text: 'text-purple-700' },
}
const cc = (name) => cardColorMap[name] || { bg: 'border-gray-200 bg-white', text: 'text-gray-800' }

// M-14: 简易数字格式化 — NaN 时返回 '-' 而非原始值
const fmt = (val, digits = 1) => {
  if (val === undefined || val === null) return '-'
  const n = Number(val)
  if (isNaN(n)) return '-'
  return n.toFixed(digits)
}

// 简易百分比
const pct = (val) => {
  if (val === undefined || val === null) return '-'
  return `${fmt(Number(val), 1)}%`
}

export default function MonitoringPage() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastUpdate, setLastUpdate] = useState('')

  // M-12: 使用 apiCall 替代直接 fetch + localStorage
  const fetchData = useCallback(async () => {
    try {
      const d = await apiCall('/monitoring/dashboard')
      setData(d)
      setLastUpdate(new Date().toLocaleTimeString())
    } catch (e) {
      // ignore — 401 已由 apiCall 内部处理
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    if (!autoRefresh) return
    const timer = setInterval(fetchData, 15000) // 15s 刷新
    return () => clearInterval(timer)
  }, [fetchData, autoRefresh])

  if (loading && !data) {
    return <div class="p-6 text-slate-400">加载监控数据中...</div>
  }

  const sys = data?.system || {}
  const biz = data?.business || {}
  const training = data?.training || {}
  const plugins = data?.plugins || {}

  // 训练状态映射
  const trainStatusMap = {
    0: { label: '空闲', color: 'bg-slate-100 text-slate-600' },
    1: { label: '运行中', color: 'bg-blue-100 text-blue-700' },
    2: { label: '已完成', color: 'bg-green-100 text-green-700' },
    3: { label: '失败', color: 'bg-red-100 text-red-700' },
  }
  const ts = trainStatusMap[training.status] || { label: '未知', color: 'bg-slate-100' }

  return (
    <div class="p-6 max-w-7xl mx-auto">
      {/* 标题栏 */}
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold text-slate-800">📈 系统监控</h1>
          <p class="text-sm text-slate-500 mt-1">
            数据每 15 秒自动刷新
            {lastUpdate && <span class="ml-2 text-slate-400">最后更新: {lastUpdate}</span>}
          </p>
        </div>
        <div class="flex items-center gap-3">
          <label class="flex items-center text-sm text-slate-600">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
              class="mr-2"
            />
            自动刷新
          </label>
          <button
            onClick={fetchData}
            class="px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
          >
            🔄 刷新
          </button>
        </div>
      </div>

      {/* 系统指标卡片 */}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        {(() => { const c = cc(Number(sys.gpu_utilization) > 80 ? 'red' : Number(sys.gpu_utilization) > 50 ? 'yellow' : 'green'); return (
        <MetricCard title="GPU 利用率" value={pct(sys.gpu_utilization)} icon="🎮" bgClass={c.bg} color={c.text} />
        ) })()}
        {(() => { const c = cc(Number(sys.gpu_memory_used_mb) / Number(sys.gpu_memory_total_mb || 1) > 0.9 ? 'red' : 'blue'); return (
        <MetricCard title="GPU 显存" value={`${fmt(sys.gpu_memory_used_mb, 0)} / ${fmt(sys.gpu_memory_total_mb, 0)} MB`} icon="💾" bgClass={c.bg} color={c.text} />
        ) })()}
        {(() => { const c = cc(Number(sys.memory_percent) > 80 ? 'red' : 'green'); return (
        <MetricCard title="系统内存" value={pct(sys.memory_percent)} icon="🧠" bgClass={c.bg} color={c.text} />
        ) })()}
        {(() => { const c = cc(Number(sys.cpu_percent) > 80 ? 'red' : 'green'); return (
        <MetricCard title="CPU 使用率" value={pct(sys.cpu_percent)} icon="⚡" bgClass={c.bg} color={c.text} />
        ) })()}
      </div>

      {/* 业务指标卡片 */}
      <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard title="活跃会话" value={biz.active_sessions || 0} icon="💬" bgClass={cc('blue').bg} color={cc('blue').text} />
        <MetricCard title="知识库文档" value={biz.total_documents || 0} icon="📚" bgClass={cc('purple').bg} color={cc('purple').text} />
        <MetricCard title="知识库分片" value={biz.total_chunks || 0} icon="📄" bgClass={cc('purple').bg} color={cc('purple').text} />
        {(() => { const c = cc(Number(biz.prefix_cache_hit_rate) > 80 ? 'green' : 'yellow'); return (
        <MetricCard title="Prefix Cache 命中率" value={pct(biz.prefix_cache_hit_rate)} icon="⚡" bgClass={c.bg} color={c.text} />
        ) })()}
      </div>

      {/* 训练状态 + 插件状态 */}
      <div class="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
        {/* 训练状态 */}
        <div class="bg-white rounded-lg border border-slate-200 p-5">
          <h2 class="text-lg font-semibold text-slate-700 mb-3">🧬 训练状态</h2>
          <div class="flex items-center gap-3 mb-3">
            <span class={`px-3 py-1 rounded-full text-sm font-medium ${ts.color}`}>
              {ts.label}
            </span>
            {training.loss !== undefined && (
              <span class="text-sm text-slate-500">
                当前 Loss: <span class="font-mono">{fmt(training.loss, 4)}</span>
              </span>
            )}
          </div>
          {biz.total_requests !== undefined && (
            <div class="text-sm text-slate-500">
              累计请求: <span class="font-mono font-medium">{biz.total_requests}</span>
            </div>
          )}
        </div>

        {/* 插件状态 */}
        <div class="bg-white rounded-lg border border-slate-200 p-5">
          <h2 class="text-lg font-semibold text-slate-700 mb-3">🔌 插件状态</h2>
          {Object.keys(plugins).length === 0 ? (
            <div class="text-sm text-slate-400">暂无插件状态数据</div>
          ) : (
            <div class="space-y-2">
              {Object.entries(plugins).map(([pluginId, info]) => (
                <div key={pluginId} class="flex items-center justify-between text-sm">
                  <span class="text-slate-600 font-mono text-xs">{pluginId}</span>
                  <span class={`px-2 py-0.5 rounded text-xs ${
                    info.value > 0
                      ? 'bg-green-100 text-green-700'
                      : info.value === 0
                      ? 'bg-slate-100 text-slate-500'
                      : 'bg-red-100 text-red-700'
                  }`}>
                    {info.status || 'unknown'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 外部监控链接 */}
      <div class="bg-white rounded-lg border border-slate-200 p-5">
        <h2 class="text-lg font-semibold text-slate-700 mb-3">🔗 外部监控工具</h2>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
          <a
            href="http://localhost:9090"
            target="_blank"
            rel="noreferrer"
            class="p-3 border border-slate-200 rounded hover:border-blue-400 hover:bg-blue-50 transition"
          >
            <div class="font-medium text-blue-600">📊 Prometheus</div>
            <div class="text-xs text-slate-400 mt-1">http://localhost:9090</div>
          </a>
          <a
            href="http://localhost:3001"
            target="_blank"
            rel="noreferrer"
            class="p-3 border border-slate-200 rounded hover:border-purple-400 hover:bg-purple-50 transition"
          >
            <div class="font-medium text-purple-600">📈 Grafana</div>
            <div class="text-xs text-slate-400 mt-1">http://localhost:3001 (admin / echoseve_admin_2026)</div>
          </a>
        </div>
      </div>
    </div>
  )
}


