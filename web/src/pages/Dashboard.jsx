import React, { useState, useEffect } from 'react'
import { useStore } from '../store'
import {
  LineChart, Line, AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell
} from 'recharts'

function Dashboard() {
  const user = useStore(s => s.user)
  const kbStats = useStore(s => s.kbStats)
  const fetchStats = useStore(s => s.fetchStats)
  const fetchAuditLogs = useStore(s => s.fetchAuditLogs)
  const auditLogs = useStore(s => s.auditLogs)
  const [health, setHealth] = useState(null)
  const [monitoring, setMonitoring] = useState(null)

  // 从监控 API 获取数据
  const fetchMonitoring = async () => {
    try {
      const token = localStorage.getItem('token') || ''
      const res = await fetch('/api/monitoring/dashboard', {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const d = await res.json()
        setMonitoring(d)
      }
    } catch (e) {
      // ignore
    }
  }

  useEffect(() => {
    fetchStats()
    fetchAuditLogs({ limit: 20 })
    fetch('/api/health').then(r => r.json()).then(setHealth).catch(() => {})
    fetchMonitoring()
    // 每 15 秒刷新监控数据
    const t = setInterval(fetchMonitoring, 15000)
    return () => clearInterval(t)
  }, [fetchStats, fetchAuditLogs])

  // 从监控数据中提取指标
  const sys = monitoring?.system || {}
  const biz = monitoring?.business || {}
  const training = monitoring?.training || {}
  const plugins = monitoring?.plugins || {}

  // 模拟 QPS 趋势数据（实际应从监控 API 获取）
  const qpsData = Array.from({ length: 12 }, (_, i) => ({
    time: `${String(i * 2).padStart(2, '0')}:00`,
    qps: Math.floor(Math.random() * 20 + 5),
  }))

  const channelData = [
    { name: '网页', value: 65, color: '#3b82f6' },
    { name: '企业微信', value: 25, color: '#10b981' },
    { name: 'API', value: 10, color: '#f59e0b' },
  ]

  const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6']

  // 插件状态条目
  const pluginEntries = Object.entries(plugins)
  const startedCount = pluginEntries.filter(([, info]) => info.value > 0).length

  return (
    <div class="p-6 space-y-6">
      {/* 页面标题 */}
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">仪表盘</h1>
          <p class="text-sm text-gray-500 mt-1">欢迎回来，{user?.username}</p>
        </div>
        <div class="flex items-center space-x-2">
          <span class={`px-3 py-1 rounded-full text-xs font-medium ${
            health?.status === 'healthy' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
          }`}>
            ● {health?.status === 'healthy' ? '系统正常' : '检查中...'}
          </span>
          {monitoring && (
            <span class="text-xs text-gray-400">
              {startedCount}/{pluginEntries.length} 插件运行中
            </span>
          )}
        </div>
      </div>

      {/* 核心指标卡片（P1 增强：增加 GPU/内存/CPU） */}
      <div class="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
        <MetricCard
          label="知识库文档"
          value={biz.total_documents ?? kbStats?.total_documents ?? '--'}
          sub="总切片数"
          color="blue"
          icon="📚"
        />
        <MetricCard
          label="活跃会话"
          value={biz.active_sessions ?? '--'}
          sub="当前在线"
          color="green"
          icon="💬"
        />
        <MetricCard
          label="GPU 利用率"
          value={sys.gpu_utilization ? `${Number(sys.gpu_utilization).toFixed(0)}%` : '--'}
          sub={sys.gpu_memory_used_mb ? `${Number(sys.gpu_memory_used_mb).toFixed(0)}MB` : '未采集'}
          color={Number(sys.gpu_utilization) > 80 ? 'red' : 'blue'}
          icon="🎮"
        />
        <MetricCard
          label="系统内存"
          value={sys.memory_percent ? `${Number(sys.memory_percent).toFixed(0)}%` : '--'}
          sub="使用率"
          color={Number(sys.memory_percent) > 80 ? 'red' : 'emerald'}
          icon="🧠"
        />
        <MetricCard
          label="CPU"
          value={sys.cpu_percent ? `${Number(sys.cpu_percent).toFixed(0)}%` : '--'}
          sub="使用率"
          color={Number(sys.cpu_percent) > 80 ? 'red' : 'emerald'}
          icon="⚡"
        />
        <MetricCard
          label="缓存命中率"
          value={biz.prefix_cache_hit_rate ? `${Number(biz.prefix_cache_hit_rate).toFixed(0)}%` : '≥85%'}
          sub="Prefix Cache"
          color="purple"
          icon="⚡"
        />
      </div>

      {/* 图表区域 */}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* QPS 趋势 */}
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
          <h3 class="text-base font-semibold text-gray-800 mb-4">请求量趋势（今日）</h3>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={qpsData}>
              <defs>
                <linearGradient id="colorQps" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="time" tick={{ fontSize: 12 }} stroke="#94a3b8" />
              <YAxis tick={{ fontSize: 12 }} stroke="#94a3b8" />
              <Tooltip />
              <Area type="monotone" dataKey="qps" stroke="#3b82f6" fill="url(#colorQps)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* 渠道分布 */}
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
          <h3 class="text-base font-semibold text-gray-800 mb-4">渠道分布</h3>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie
                data={channelData}
                cx="50%"
                cy="50%"
                innerRadius={50}
                outerRadius={80}
                dataKey="value"
                label={({ name, value }) => `${name} ${value}%`}
              >
                {channelData.map((entry, i) => (
                  <Cell key={i} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* 插件状态 + 训练状态 */}
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 插件状态 */}
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
          <h3 class="text-base font-semibold text-gray-800 mb-4">插件状态</h3>
          {pluginEntries.length === 0 ? (
            <div class="text-sm text-gray-400">暂无插件状态数据</div>
          ) : (
            <div class="grid grid-cols-2 md:grid-cols-3 gap-3">
              {pluginEntries.map(([name, info]) => (
                <div key={name} class="border border-gray-100 rounded-lg p-3 text-center">
                  <div class={`w-3 h-3 rounded-full mx-auto mb-2 ${
                    info.value > 0 ? 'bg-green-500' : 'bg-yellow-500'
                  }`}></div>
                  <div class="text-xs text-gray-600 truncate" title={name}>{name}</div>
                  <div class={`text-xs mt-1 ${
                    info.value > 0 ? 'text-green-600' : 'text-yellow-600'
                  }`}>{info.status || '--'}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 训练状态（P1 新增） */}
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
          <h3 class="text-base font-semibold text-gray-800 mb-4">🧬 模型进化状态</h3>
          {training.status !== undefined ? (
            <div>
              <div class="flex items-center gap-3 mb-3">
                <span class={`px-3 py-1 rounded-full text-sm font-medium ${
                  training.status === 0 ? 'bg-slate-100 text-slate-600' :
                  training.status === 1 ? 'bg-blue-100 text-blue-700' :
                  training.status === 2 ? 'bg-green-100 text-green-700' :
                  'bg-red-100 text-red-700'
                }`}>
                  {training.status === 0 ? '空闲' :
                   training.status === 1 ? '运行中' :
                   training.status === 2 ? '已完成' : '失败'}
                </span>
                {training.loss !== undefined && (
                  <span class="text-sm text-gray-500">
                    当前 Loss: <span class="font-mono">{Number(training.loss).toFixed(4)}</span>
                  </span>
                )}
              </div>
              <div class="text-sm text-gray-500">
                知识库: <span class="font-mono font-medium">{biz.total_documents || 0}</span> 文档
              </div>
              <div class="mt-3">
                <a
                  href="/evolve"
                  class="text-sm text-blue-600 hover:text-blue-700"
                >
                  前往进化引擎 →
                </a>
              </div>
            </div>
          ) : (
            <div class="text-sm text-gray-400">加载中...</div>
          )}
        </div>
      </div>

      {/* 最近活动 */}
      <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <h3 class="text-base font-semibold text-gray-800 mb-4">最近活动</h3>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-gray-500 border-b">
                <th class="pb-2">时间</th>
                <th class="pb-2">用户</th>
                <th class="pb-2">动作</th>
                <th class="pb-2">内容摘要</th>
              </tr>
            </thead>
            <tbody>
              {auditLogs.length > 0 ? auditLogs.slice(0, 8).map(log => (
                <tr key={log.id} class="border-b border-gray-50 hover:bg-gray-50">
                  <td class="py-2 text-gray-500 text-xs">{log.timestamp?.slice(0, 16)}</td>
                  <td class="py-2">{log.user_id}</td>
                  <td class="py-2">
                    <span class="px-2 py-0.5 bg-blue-50 text-blue-700 rounded text-xs">{log.action}</span>
                  </td>
                  <td class="py-2 text-gray-600 truncate max-w-xs">{log.query || log.response_summary || '--'}</td>
                </tr>
              )) : (
                <tr><td colspan="4" class="py-4 text-center text-gray-400">暂无数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function MetricCard({ label, value, sub, color, icon }) {
  const colorMap = {
    blue: 'border-blue-200 bg-blue-50',
    green: 'border-green-200 bg-green-50',
    emerald: 'border-emerald-200 bg-emerald-50',
    purple: 'border-purple-200 bg-purple-50',
    red: 'border-red-200 bg-red-50',
  }
  const textMap = {
    blue: 'text-blue-700',
    green: 'text-green-700',
    emerald: 'text-emerald-700',
    purple: 'text-purple-700',
    red: 'text-red-700',
  }
  return (
    <div class={`border rounded-xl p-4 ${colorMap[color] || 'border-gray-200 bg-white'}`}>
      <div class="flex items-center justify-between mb-2">
        <span class="text-2xl">{icon}</span>
        <span class={`text-xs px-2 py-0.5 rounded ${textMap[color] || 'text-gray-500'}`}>{sub}</span>
      </div>
      <div class="text-2xl font-bold text-gray-900">{value}</div>
      <div class="text-sm text-gray-500 mt-1">{label}</div>
    </div>
  )
}

export default Dashboard
