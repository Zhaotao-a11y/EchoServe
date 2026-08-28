/**
 * EchoServe 进化系统 — 降级容错 Tab
 */
import React from 'react'
import { Badge, EmptyState } from '../ui'
import { fmtTime } from './utils'
import { degradeLevelMap } from './constants'

export default function FailoverTab({ failover, failoverOverview = {} }) {
  const degradeLevel = failover?.current_level || failoverOverview.current_level || 'normal'
  const degradeInfo = degradeLevelMap[degradeLevel] || degradeLevelMap.normal

  return (
    <div className="space-y-4">
      {/* 当前降级状态 */}
      <div className={`border-2 rounded-lg p-6 ${degradeInfo.color}`}>
        <div className="flex items-center gap-3">
          <span className="text-3xl">{degradeLevel === 'normal' ? '✅' : '⚠️'}</span>
          <div>
            <div className="text-lg font-bold">当前降级级别: {degradeInfo.label}</div>
            <div className="text-sm opacity-75">
              {degradeLevel === 'normal' && '系统正常运行，所有进化功能可用'}
              {degradeLevel === 'level_1' && '单参数实验已暂停，保持当前参数'}
              {degradeLevel === 'level_2' && '灰度模板已禁用，实验已暂停'}
              {degradeLevel === 'level_3' && '进化系统只读模式，需人工介入恢复'}
            </div>
          </div>
        </div>
      </div>

      {/* 降级规则与历史 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">📐 降级规则</h3>
          <div className="space-y-2">
            <div className="flex justify-between">
              <span className="text-sm text-slate-500">已注册规则数</span>
              <span className="text-sm font-bold text-slate-700">{failover?.rules_count || failoverOverview.rules_count || 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-sm text-slate-500">历史记录数</span>
              <span className="text-sm font-bold text-slate-700">{failover?.history_count || 0}</span>
            </div>
          </div>
          <div className="mt-3 text-xs text-slate-400">
            默认规则: 实验指标下跌 → L1 | 灰度失败率过高 → L2 | 存储写入阻塞 → L3
          </div>
        </div>

        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">📜 降级历史</h3>
          {failover && failover.history && failover.history.length > 0 ? (
            <div className="space-y-2 max-h-48 overflow-y-auto">
              {failover.history.map((h, i) => (
                <div key={i} className="text-xs border-l-2 border-slate-200 pl-3 py-1">
                  <span className="text-slate-400">{fmtTime(h?.timestamp)}</span>
                  <span className="ml-2 text-slate-600">{h?.from || '-'} → {h?.to || '-'}</span>
                  <span className="ml-2 text-slate-400">({h?.reason || '-'})</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-slate-400 py-4 text-center">无降级历史记录</div>
          )}
        </div>
      </div>

      {/* 降级级别说明 */}
      <div className="bg-white border border-slate-200 rounded-lg p-4">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">📋 降级级别说明</h3>
        <div className="space-y-2">
          <div className="flex items-center gap-3 text-sm">
            <Badge color={degradeLevelMap.normal.color}>正常</Badge>
            <span className="text-slate-600">所有进化功能正常运行</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <Badge color={degradeLevelMap.level_1.color}>L1</Badge>
            <span className="text-slate-600">暂停单参数实验，保持当前参数不变</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <Badge color={degradeLevelMap.level_2.color}>L2</Badge>
            <span className="text-slate-600">禁用灰度模板 + 暂停所有实验</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <Badge color={degradeLevelMap.level_3.color}>L3</Badge>
            <span className="text-slate-600">进化系统只读模式，需人工介入恢复</span>
          </div>
        </div>
      </div>
    </div>
  )
}
