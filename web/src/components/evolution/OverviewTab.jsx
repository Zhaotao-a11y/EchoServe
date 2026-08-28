/**
 * EchoServe 进化系统 — 总览 Tab
 */
import React from 'react'
import { Badge } from '../ui'
import { fmt, fmtBytes } from './utils'
import { tplStatusMap } from './constants'

export default function OverviewTab({ overview }) {
  if (!overview) return null

  const storeStats = overview.store || {}
  const collectorStats = overview.collector || {}
  const config = overview.config || {}
  const templateSummary = overview.templates || {}
  const recordsByTable = storeStats.records_by_table || {}

  return (
    <div className="space-y-6">
      {/* 采集器状态 */}
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">📦 数据采集器</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div>
            <div className="text-xs text-slate-400">已接收</div>
            <div className="text-lg font-bold text-slate-800">{collectorStats.total_received || 0}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">已写入</div>
            <div className="text-lg font-bold text-slate-800">{collectorStats.total_written || 0}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">写入失败</div>
            <div className="text-lg font-bold text-red-600">{collectorStats.write_failures || 0}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">积压</div>
            <div className="text-lg font-bold text-yellow-600">{collectorStats.backlog_size || 0}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">完整率</div>
            <div className="text-lg font-bold text-green-600">{fmt((collectorStats.completeness_rate || 0) * 100)}%</div>
          </div>
        </div>
      </div>

      {/* 配置参数 */}
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">⚙️ 进化配置</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <div className="text-xs text-slate-400">挖掘最小成功率</div>
            <div className="text-sm font-medium text-slate-700">{fmt((config.mining_min_success_rate || 0) * 100)}%</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">挖掘最小支持度</div>
            <div className="text-sm font-medium text-slate-700">{config.mining_min_support}</div>
          </div>
          <div>
            <div className="text-xs text-slate-400">自动全量发布</div>
            <div className="text-sm font-medium text-slate-700">
              {config.template_auto_promote ? '✅ 启用' : '❌ 禁用'}
            </div>
          </div>
          <div>
            <div className="text-xs text-slate-400">评估间隔</div>
            <div className="text-sm font-medium text-slate-700">{config.eval_interval}s</div>
          </div>
        </div>
      </div>

      {/* 模板状态分布 */}
      {templateSummary.status_breakdown && Object.keys(templateSummary.status_breakdown).length > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">📋 模板状态分布</h3>
          <div className="flex flex-wrap gap-2">
            {Object.entries(templateSummary.status_breakdown).map(([status, count]) => {
              const info = tplStatusMap[status] || { label: status, color: 'bg-slate-100 text-slate-600' }
              return (
                <Badge key={status} color={info.color}>
                  {info.label}: {count}
                </Badge>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
