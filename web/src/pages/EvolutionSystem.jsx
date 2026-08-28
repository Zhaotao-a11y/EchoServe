/**
 * EchoServe 进化系统主页面
 *
 * M-9:  使用 apiCall 替代直接 fetch
 * M-10: 401 时通过 store authExpired 软跳转（apiCall 内部处理）
 * M-12: 不再 localStorage.getItem('token')，token 由 apiCall 内部管理
 * M-13: Promise.allSettled 收集具体错误信息
 * M-14: fmt() 对 NaN 返回 '-'
 * M-15: 拆分为子组件，主页面仅负责数据获取和 Tab 导航
 * M-16: 使用公共 UI 组件
 */
import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useStore, apiCall } from '../store'
import { MetricCard } from '../components/ui'
import { fmtBytes } from '../components/evolution/utils'
import { degradeLevelMap } from '../components/evolution/constants'
import OverviewTab from '../components/evolution/OverviewTab'
import ExperimentsTab from '../components/evolution/ExperimentsTab'
import PatternsTab from '../components/evolution/PatternsTab'
import TemplatesTab from '../components/evolution/TemplatesTab'
import FailoverTab from '../components/evolution/FailoverTab'
import ReviewTab from '../components/evolution/ReviewTab'

// ─── 颜色映射（进化系统专用） ─────────────────────────
const cardColorMap = {
  green: { bg: 'border-green-200 bg-green-50', text: 'text-green-700' },
  yellow: { bg: 'border-yellow-200 bg-yellow-50', text: 'text-yellow-700' },
  red: { bg: 'border-red-200 bg-red-50', text: 'text-red-700' },
  blue: { bg: 'border-blue-200 bg-blue-50', text: 'text-blue-700' },
  purple: { bg: 'border-purple-200 bg-purple-50', text: 'text-purple-700' },
  slate: { bg: 'border-slate-200 bg-white', text: 'text-slate-800' },
  orange: { bg: 'border-orange-200 bg-orange-50', text: 'text-orange-700' },
}

export default function EvolutionSystemPage() {
  const [overview, setOverview] = useState(null)
  const [experiments, setExperiments] = useState(null)
  const [patterns, setPatterns] = useState(null)
  const [templates, setTemplates] = useState(null)
  const [failover, setFailover] = useState(null)
  const [review, setReview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState([])
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastUpdate, setLastUpdate] = useState('')
  const [activeTab, setActiveTab] = useState('overview')
  const abortRef = useRef(null)

  // m-24: Tab 懒加载 — 每个 Tab 对应一个端点
  const TAB_ENDPOINTS = {
    overview: '/evolution/overview',
    experiments: '/evolution/experiments',
    patterns: '/evolution/patterns',
    templates: '/evolution/templates',
    failover: '/evolution/failover',
    review: '/evolution/review/pending',
  }

  const TAB_SETTERS = {
    overview: setOverview,
    experiments: setExperiments,
    patterns: setPatterns,
    templates: setTemplates,
    failover: setFailover,
    review: setReview,
  }

  // m-24: 按需拉取当前 Tab 数据（替代一次性拉取全部 5 端点）
  const fetchTabData = useCallback(async (tab) => {
    const url = TAB_ENDPOINTS[tab]
    if (!url) return

    if (abortRef.current) abortRef.current.abort()
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const data = await apiCall(url, { signal: controller.signal })
      TAB_SETTERS[tab]?.(data)
      setErrors([])
      setLastUpdate(new Date().toLocaleTimeString('zh-CN', { hour12: false }))
    } catch (e) {
      if (e.name === 'AbortError') return
      setErrors([`${tab}: ${e.message}`])
      console.error(`Fetch error [${tab}]:`, e)
    } finally {
      if (abortRef.current === controller) abortRef.current = null
      setLoading(false)
    }
  }, [])

  // m-24: Tab 切换时触发懒加载
  useEffect(() => {
    fetchTabData(activeTab)
  }, [activeTab, fetchTabData])

  // m-24: 自动刷新仅刷新当前 Tab
  useEffect(() => {
    if (!autoRefresh) return
    const timer = setInterval(() => fetchTabData(activeTab), 30000)
    return () => {
      clearInterval(timer)
      if (abortRef.current) abortRef.current.abort()
    }
  }, [activeTab, autoRefresh, fetchTabData])

  if (loading && !overview) {
    return <div className="p-6 text-slate-400">加载进化系统数据中...</div>
  }

  // ─── 数据提取 ────────────────────────────────────
  const storeStats = overview?.store || {}
  const expStats = overview?.experiments || {}
  const patternStats = overview?.patterns || {}
  const templateSummary = overview?.templates || {}
  const failoverOverview = overview?.failover || {}
  const config = overview?.config || {}
  const recordsByTable = storeStats.records_by_table || {}

  const degradeLevel = failover?.current_level || failoverOverview.current_level || 'normal'
  const degradeInfo = degradeLevelMap[degradeLevel] || degradeLevelMap.normal
  const degradeColorKey = degradeLevel === 'normal' ? 'green' : degradeLevel === 'level_3' ? 'red' : 'yellow'

  return (
    <div className="p-6 max-w-7xl mx-auto">
      {/* ─── 标题栏 ──────────────────────────────── */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">🧬 进化系统</h1>
          <p className="text-sm text-slate-500 mt-1">
            Phase 1-3: 数据采集 / A/B 实验 / 技能进化 / 降级容错
            {lastUpdate && <span className="ml-2 text-slate-400">| 最后更新: {lastUpdate}</span>}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center text-sm text-slate-600">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={e => setAutoRefresh(e.target.checked)}
              className="mr-2"
            />
            自动刷新
          </label>
          <button
            onClick={fetchTabData}
            className="px-3 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700"
          >
            🔄 刷新
          </button>
        </div>
      </div>

      {/* M-13: 展示具体错误信息 */}
      {errors.length > 0 && (
        <div className="mb-4 px-4 py-2 bg-red-50 border border-red-200 rounded text-sm text-red-600">
          ⚠ 部分数据加载失败: {errors.join(' | ')}
        </div>
      )}

      {/* ─── 核心指标卡片 ────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard
          title="降级级别"
          value={degradeInfo.label}
          icon="🛡️"
          bgClass={cardColorMap[degradeColorKey].bg}
          color={cardColorMap[degradeColorKey].text}
        />
        <MetricCard
          title="活跃实验"
          value={`${expStats.active || 0} / ${expStats.total || 0}`}
          sub="活跃 / 总计"
          icon="🧪"
          bgClass={cardColorMap.blue.bg}
          color={cardColorMap.blue.text}
        />
        <MetricCard
          title="挖掘模式"
          value={patternStats.total || 0}
          icon="📊"
          bgClass={cardColorMap.purple.bg}
          color={cardColorMap.purple.text}
        />
        <MetricCard
          title="注册模板"
          value={templateSummary.total_templates || 0}
          sub={`活跃意图: ${templateSummary.active_intents || 0}`}
          icon="📋"
          bgClass={cardColorMap.green.bg}
          color={cardColorMap.green.text}
        />
      </div>

      {/* ─── 存储与采集卡片 ──────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <MetricCard title="对话日志" value={recordsByTable.chat_log || 0} icon="💬" bgClass={cardColorMap.slate.bg} color={cardColorMap.slate.text} />
        <MetricCard title="技能追踪" value={recordsByTable.skill_trace || 0} icon="🔧" bgClass={cardColorMap.slate.bg} color={cardColorMap.slate.text} />
        <MetricCard title="用户反馈" value={recordsByTable.feedback || 0} icon="👍" bgClass={cardColorMap.slate.bg} color={cardColorMap.slate.text} />
        <MetricCard
          title="存储大小"
          value={fmtBytes(storeStats.db_size_bytes)}
          sub={`冷存储: ${fmtBytes(storeStats.cold_storage_bytes)}`}
          icon="💾"
          bgClass={cardColorMap.slate.bg}
          color={cardColorMap.slate.text}
        />
      </div>

      {/* ─── Tab 导航 ────────────────────────────── */}
      <div className="flex border-b border-slate-200 mb-4">
        {[
          { key: 'overview', label: '📊 总览' },
          { key: 'experiments', label: '🧪 A/B 实验' },
          { key: 'patterns', label: '📊 模式挖掘' },
          { key: 'templates', label: '📋 模板注册表' },
          { key: 'failover', label: '🛡️ 降级容错' },
          { key: 'review', label: '✅ 人工审核' },
        ].map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-4 py-2 rounded-t-lg text-sm font-medium transition-colors ${
              activeTab === tab.key
                ? 'bg-white text-blue-600 border-b-2 border-blue-600'
                : 'bg-slate-100 text-slate-500 hover:bg-slate-200'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* ─── Tab 内容区 ──────────────────────────── */}
      {activeTab === 'overview' && <OverviewTab overview={overview} />}
      {activeTab === 'experiments' && <ExperimentsTab experiments={experiments} />}
      {activeTab === 'patterns' && <PatternsTab patterns={patterns} />}
      {activeTab === 'templates' && <TemplatesTab templates={templates} />}
      {activeTab === 'failover' && <FailoverTab failover={failover} failoverOverview={failoverOverview} />}
      {activeTab === 'review' && <ReviewTab pending={review} onRefresh={() => fetchTabData('review')} />}
    </div>
  )
}
