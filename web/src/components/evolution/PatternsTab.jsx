/**
 * EchoServe 进化系统 — 模式挖掘 Tab
 */
import React from 'react'
import { EmptyState } from '../ui'
import { fmt } from './utils'

// m-16: skill_sequence 非数组防御
const safeArray = (v) => Array.isArray(v) ? v : []

export default function PatternsTab({ patterns }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      {patterns && patterns.patterns && patterns.patterns.length > 0 ? (
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">意图</th>
              <th className="px-4 py-3 text-left text-xs font-medium text-slate-500">技能序列</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">频率</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">成功率</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">平均延迟</th>
              <th className="px-4 py-3 text-center text-xs font-medium text-slate-500">置信度</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {patterns.patterns.map((p, i) => (
              <tr key={i} className="hover:bg-slate-50">
                <td className="px-4 py-3 text-slate-700">{p.intent}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {safeArray(p.skill_sequence).map((s, idx) => (
                      <span key={idx} className="px-1.5 py-0.5 bg-blue-50 text-blue-600 rounded text-xs font-mono">
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-4 py-3 text-center text-slate-600">{p.frequency}</td>
                <td className="px-4 py-3 text-center">
                  <span className={`font-medium ${((p.success_rate || 0) >= 0.9) ? 'text-green-600' : 'text-yellow-600'}`}>
                    {fmt((p.success_rate || 0) * 100)}%
                  </span>
                </td>
                <td className="px-4 py-3 text-center text-slate-600">{fmt(p.avg_latency_ms, 0)}ms</td>
                <td className="px-4 py-3 text-center text-slate-600">{fmt(p.confidence, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <EmptyState text="暂无挖掘模式，需积累足够技能追踪数据后自动挖掘" />
      )}
    </div>
  )
}
