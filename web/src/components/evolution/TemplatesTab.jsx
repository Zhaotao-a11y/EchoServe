/**
 * EchoServe 进化系统 — 模板注册表 Tab
 */
import React from 'react'
import { Badge, EmptyState, MetricCard } from '../ui'
import { fmt, fmtTime } from './utils'
import { tplStatusMap } from './constants'

export default function TemplatesTab({ templates }) {
  return (
    <div className="space-y-4">
      {templates && templates.summary && (
        <div className="grid grid-cols-3 gap-4">
          <MetricCard title="总模板数" value={templates.summary.total_templates || 0} color="text-blue-600" />
          <MetricCard title="活跃意图" value={templates.summary.active_intents || 0} color="text-green-600" />
          <MetricCard title="回滚次数" value={templates.summary.rollback_count || 0} color="text-red-600" />
        </div>
      )}
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        {templates && templates.templates && templates.templates.length > 0 ? (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">模板 ID</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">名称</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">意图</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">状态</th>
                <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">灰度%</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">上一版本</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">生成时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {templates.templates.map(tpl => {
                const stInfo = tplStatusMap[tpl.status] || { label: tpl.status, color: 'bg-slate-100 text-slate-600' }
                return (
                  <tr key={tpl.template_id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-mono text-xs text-slate-600">{tpl.template_id}</td>
                    <td className="px-4 py-3 text-slate-700">{tpl.name || '-'}</td>
                    <td className="px-4 py-3 text-slate-600">{tpl.intent}</td>
                    <td className="px-4 py-3"><Badge color={stInfo.color}>{stInfo.label}</Badge></td>
                    <td className="px-4 py-3 text-center text-slate-600">
                      {fmt(tpl.rollout_percent > 1 ? tpl.rollout_percent : (tpl.rollout_percent || 0) * 100)}%
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{tpl.previous_version || '-'}</td>
                    <td className="px-4 py-3 text-xs text-slate-400">{fmtTime(tpl.generated_at)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        ) : (
          <EmptyState text="暂无注册模板，候选模板经审核后将自动注册" />
        )}
      </div>
    </div>
  )
}
