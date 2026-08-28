/**
 * EchoServe 进化系统 — A/B 实验 Tab
 */
import React from 'react'
import { Badge, EmptyState } from '../ui'
import { fmt, fmtTime } from './utils'
import { expStatusMap } from './constants'

export default function ExperimentsTab({ experiments }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      {experiments && experiments.experiments && experiments.experiments.length > 0 ? (
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">实验 ID</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">参数名</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">候选值</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">评估指标</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">状态</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">流量%</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">对照组</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">实验组</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">创建时间</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {experiments.experiments.map(exp => {
              const stInfo = expStatusMap[exp.status] || { label: exp.status, color: 'bg-slate-100 text-slate-600' }
              return (
                <tr key={exp.exp_id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-mono text-xs text-slate-600">{exp.exp_id}</td>
                  <td className="px-4 py-3 text-slate-700">{exp.param_name}</td>
                  <td className="px-4 py-3 text-slate-600">{(exp.candidate_values || []).join(', ')}</td>
                  <td className="px-4 py-3 text-slate-600">{exp.eval_metric}</td>
                  <td className="px-4 py-3"><Badge color={stInfo.color}>{stInfo.label}</Badge></td>
                  <td className="px-4 py-3 text-center text-slate-600">{exp.traffic_percent}</td>
                  <td className="px-4 py-3 text-center text-slate-600">{exp.control_group_size}</td>
                  <td className="px-4 py-3 text-center text-slate-600">{exp.treatment_group_size}</td>
                  <td className="px-4 py-3 text-xs text-slate-400">{fmtTime(exp.created_at)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      ) : (
        <EmptyState text="暂无 A/B 实验数据，实验将由进化循环自动创建" />
      )}
    </div>
  )
}
