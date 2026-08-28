/**
 * EchoServe 进化系统 — 人工审核工作台 Tab
 *
 * 功能：
 * - 展示审核队列统计（待审/已批准/已驳回/总计）
 * - 展示待审核候选模板列表，支持展开查看详情
 * - 支持批准/驳回操作，可填写审核意见
 * - 操作后自动刷新列表和统计
 */
import React, { useState, useEffect, useCallback } from 'react'
import { apiCall } from '../../store'
import { Badge, EmptyState, MetricCard } from '../ui'
import { fmt, fmtTime } from './utils'

export default function ReviewTab({ pending, onRefresh }) {
  const [stats, setStats] = useState(null)
  const [actionLoading, setActionLoading] = useState(null)
  const [commentsMap, setCommentsMap] = useState({})
  const [expandedId, setExpandedId] = useState(null)
  const [errorMsg, setErrorMsg] = useState('')

  const fetchStats = useCallback(async () => {
    try {
      const data = await apiCall('/evolution/review/stats')
      setStats(data)
    } catch (e) {
      console.error('Fetch review stats failed:', e)
    }
  }, [])

  useEffect(() => {
    fetchStats()
  }, [fetchStats])

  const handleAction = useCallback(
    async (candidateId, action) => {
      setActionLoading(candidateId)
      setErrorMsg('')
      try {
        const body = { comments: commentsMap[candidateId] || null }
        await apiCall(`/evolution/review/${candidateId}/${action}`, {
          method: 'POST',
          body: JSON.stringify(body),
        })
        setCommentsMap((prev) => {
          const next = { ...prev }
          delete next[candidateId]
          return next
        })
        setExpandedId(null)
        await Promise.all([onRefresh?.(), fetchStats()])
      } catch (e) {
        setErrorMsg(
          `${action === 'approve' ? '批准' : '驳回'}失败: ${e.message}`,
        )
      } finally {
        setActionLoading(null)
      }
    },
    [commentsMap, onRefresh, fetchStats],
  )

  const candidates = pending?.pending || []

  return (
    <div className="space-y-4">
      {/* 审核统计卡片 */}
      {stats && (
        <div className="grid grid-cols-4 gap-4">
          <MetricCard
            title="待审核"
            value={stats.pending || 0}
            color="text-orange-600"
          />
          <MetricCard
            title="已批准"
            value={stats.approved || 0}
            color="text-green-600"
          />
          <MetricCard
            title="已驳回"
            value={stats.rejected || 0}
            color="text-red-600"
          />
          <MetricCard
            title="总计"
            value={stats.total || 0}
            color="text-blue-600"
          />
        </div>
      )}

      {/* 错误提示 */}
      {errorMsg && (
        <div className="px-4 py-2 bg-red-50 border border-red-200 rounded text-sm text-red-600">
          {'⚠ ' + errorMsg}
        </div>
      )}

      {/* 待审核候选模板列表 */}
      <div className="space-y-3">
        {candidates.length > 0 ? (
          candidates.map((c) => (
            <div
              key={c.id}
              className="bg-white border border-slate-200 rounded-lg overflow-hidden"
            >
              {/* 模板摘要行 */}
              <div
                className="flex items-center justify-between px-4 py-3 cursor-pointer hover:bg-slate-50"
                onClick={() =>
                  setExpandedId(expandedId === c.id ? null : c.id)
                }
              >
                <div className="flex items-center gap-3">
                  <span className="text-sm font-medium text-slate-700">
                    {c.name || c.id}
                  </span>
                  <Badge color="bg-orange-100 text-orange-700">
                    {c.status}
                  </Badge>
                  <span className="text-xs text-slate-400">{c.intent}</span>
                </div>
                <div className="flex items-center gap-3 text-xs text-slate-400">
                  <span>
                    通过率:{' '}
                    {fmt(
                      c.simulation_pass_rate
                        ? c.simulation_pass_rate * 100
                        : 0,
                    )}
                    %
                  </span>
                  <span>{fmtTime(c.generated_at)}</span>
                  <span>{expandedId === c.id ? '▲' : '▼'}</span>
                </div>
              </div>

              {/* 展开详情 */}
              {expandedId === c.id && (
                <div className="border-t border-slate-100 px-4 py-4 bg-slate-50">
                  {/* 模板详情 */}
                  <div className="grid grid-cols-2 gap-4 mb-4 text-sm">
                    <div>
                      <span className="text-slate-400">触发条件:</span>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.trigger_conditions?.map((tc, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 bg-slate-200 rounded text-xs text-slate-600"
                          >
                            {tc}
                          </span>
                        ))}
                      </div>
                    </div>
                    <div>
                      <span className="text-slate-400">技能序列:</span>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.skill_sequence?.map((ss, i) => (
                          <span
                            key={i}
                            className="px-2 py-0.5 bg-blue-100 rounded text-xs text-blue-700"
                          >
                            {ss}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>

                  {c.parameter_mapping &&
                    Object.keys(c.parameter_mapping).length > 0 && (
                      <div className="mb-4 text-sm">
                        <span className="text-slate-400">参数映射:</span>
                        <pre className="mt-1 p-2 bg-white border border-slate-200 rounded text-xs overflow-x-auto">
                          {JSON.stringify(c.parameter_mapping, null, 2)}
                        </pre>
                      </div>
                    )}

                  {c.expected_output_template && (
                    <div className="mb-4 text-sm">
                      <span className="text-slate-400">预期输出模板:</span>
                      <pre className="mt-1 p-2 bg-white border border-slate-200 rounded text-xs overflow-x-auto">
                        {c.expected_output_template}
                      </pre>
                    </div>
                  )}

                  {/* 审核操作区 */}
                  <div className="border-t border-slate-200 pt-3">
                    <textarea
                      className="w-full px-3 py-2 border border-slate-300 rounded text-sm resize-y focus:outline-none focus:border-blue-400"
                      rows={2}
                      placeholder="审核意见（可选）"
                      value={commentsMap[c.id] || ''}
                      onChange={(e) =>
                        setCommentsMap((prev) => ({
                          ...prev,
                          [c.id]: e.target.value,
                        }))
                      }
                      onClick={(e) => e.stopPropagation()}
                    />
                    <div className="flex gap-2 mt-2">
                      <button
                        className="px-4 py-1.5 bg-green-600 text-white rounded text-sm hover:bg-green-700 disabled:opacity-50"
                        disabled={actionLoading === c.id}
                        onClick={(e) => {
                          e.stopPropagation()
                          handleAction(c.id, 'approve')
                        }}
                      >
                        {actionLoading === c.id
                          ? '处理中...'
                          : '✓ 批准'}
                      </button>
                      <button
                        className="px-4 py-1.5 bg-red-600 text-white rounded text-sm hover:bg-red-700 disabled:opacity-50"
                        disabled={actionLoading === c.id}
                        onClick={(e) => {
                          e.stopPropagation()
                          handleAction(c.id, 'reject')
                        }}
                      >
                        {actionLoading === c.id
                          ? '处理中...'
                          : '✗ 驳回'}
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))
        ) : (
          <div className="bg-white border border-slate-200 rounded-lg">
            <EmptyState text="暂无待审核的候选模板" />
          </div>
        )}
      </div>
    </div>
  )
}
