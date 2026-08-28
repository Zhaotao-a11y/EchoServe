import React, { useState, useEffect } from 'react'
import { useStore, apiCall, apiCallRaw } from '../store'

function AuditPage() {
  const auditLogs = useStore(s => s.auditLogs)
  const fetchAuditLogs = useStore(s => s.fetchAuditLogs)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [keyword, setKeyword] = useState('')
  const [selectedLog, setSelectedLog] = useState(null)
  const [verifyResult, setVerifyResult] = useState(null)

  useEffect(() => {
    fetchAuditLogs({ limit: 100 })
  }, [])

  const handleSearch = () => {
    const params = { limit: 100 }
    if (startDate) params.start_date = startDate
    if (endDate) params.end_date = endDate
    if (keyword) params.keyword = keyword
    fetchAuditLogs(params)
  }

  const handleExport = async () => {
    // M-12: 使用 apiCallRaw 替代直接 fetch + localStorage
    const params = new URLSearchParams()
    if (startDate) params.set('start_date', startDate)
    if (endDate) params.set('end_date', endDate)
    try {
      const resp = await apiCallRaw(`/audit/export?${params.toString()}`)
      const blob = await resp.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `audit_export_${Date.now()}.csv`
      a.click()
    } catch (err) {
      alert(`导出失败: ${err.message}`)
    }
  }

  const handleVerify = async () => {
    try {
      // M-12: 使用 apiCall 替代直接 fetch + localStorage
      const data = await apiCall('/audit/verify')
      setVerifyResult(data)
    } catch (err) {
      setVerifyResult({ valid: false, message: err.message })
    }
  }

  return (
    <div class="p-6 space-y-6">
      {/* 标题 */}
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">审计日志</h1>
          <p class="text-sm text-gray-500 mt-1">查询、筛选和导出系统操作记录</p>
        </div>
        <div class="flex gap-2">
          <button
            onClick={handleVerify}
            class="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg text-sm"
          >
            🔐 验证完整性
          </button>
          <button
            onClick={handleExport}
            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm"
          >
            📥 导出 CSV
          </button>
        </div>
      </div>

      {/* 验证结果 */}
      {verifyResult && (
        <div class={`rounded-lg p-4 ${
          verifyResult.valid ? 'bg-green-50 border border-green-200' : 'bg-red-50 border border-red-200'
        }`}>
          <div class={`text-sm font-medium ${verifyResult.valid ? 'text-green-700' : 'text-red-700'}`}>
            {verifyResult.valid ? '✅ 日志完整性验证通过' : '❌ 检测到日志被篡改'}
          </div>
          <div class="text-xs text-gray-500 mt-1">{verifyResult.message}</div>
        </div>
      )}

      {/* 筛选栏 */}
      <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-4">
        <div class="flex flex-wrap gap-3 items-end">
          <div>
            <label class="block text-xs text-gray-500 mb-1">开始日期</label>
            <input
              type="date"
              value={startDate}
              onChange={e => setStartDate(e.target.value)}
              class="px-3 py-1.5 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
            />
          </div>
          <div>
            <label class="block text-xs text-gray-500 mb-1">结束日期</label>
            <input
              type="date"
              value={endDate}
              onChange={e => setEndDate(e.target.value)}
              class="px-3 py-1.5 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
            />
          </div>
          <div class="flex-1 min-w-[200px]">
            <label class="block text-xs text-gray-500 mb-1">关键词</label>
            <input
              value={keyword}
              onChange={e => setKeyword(e.target.value)}
              placeholder="搜索查询内容或回复..."
              class="w-full px-3 py-1.5 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
            />
          </div>
          <button
            onClick={handleSearch}
            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-1.5 rounded-lg text-sm"
          >搜索</button>
          <button
            onClick={() => { setStartDate(''); setEndDate(''); setKeyword(''); fetchAuditLogs({ limit: 100 }) }}
            class="text-gray-500 hover:text-gray-700 text-sm px-2"
          >重置</button>
        </div>
      </div>

      {/* 日志列表 */}
      <div class="bg-white rounded-xl shadow-sm border border-gray-100">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-gray-500 border-b bg-gray-50">
                <th class="px-4 py-3">ID</th>
                <th class="px-4 py-3">时间</th>
                <th class="px-4 py-3">用户</th>
                <th class="px-4 py-3">动作</th>
                <th class="px-4 py-3">查询内容</th>
                <th class="px-4 py-3">延迟</th>
                <th class="px-4 py-3">渠道</th>
              </tr>
            </thead>
            <tbody>
              {auditLogs.length > 0 ? auditLogs.map(log => (
                <tr
                  key={log.id}
                  onClick={() => setSelectedLog(log)}
                  class="border-b border-gray-50 hover:bg-blue-50 cursor-pointer"
                >
                  <td class="px-4 py-2 font-mono text-xs text-gray-400">#{log.id}</td>
                  <td class="px-4 py-2 text-gray-500 text-xs whitespace-nowrap">
                    {log.timestamp?.slice(0, 19)?.replace('T', ' ')}
                  </td>
                  <td class="px-4 py-2">{log.user_id}</td>
                  <td class="px-4 py-2">
                    <span class={`px-2 py-0.5 rounded text-xs ${
                      log.action === 'chat_query' ? 'bg-blue-50 text-blue-700' :
                      log.action === 'login' ? 'bg-green-50 text-green-700' :
                      'bg-gray-100 text-gray-600'
                    }`}>
                      {log.action}
                    </span>
                  </td>
                  <td class="px-4 py-2 max-w-xs">
                    <span class="text-gray-700 truncate block">{log.query || '--'}</span>
                  </td>
                  <td class="px-4 py-2 text-gray-500 text-xs">{log.latency_ms || 0}ms</td>
                  <td class="px-4 py-2">
                    <span class="text-xs px-2 py-0.5 bg-gray-100 rounded">
                      {log.channel || 'web'}
                    </span>
                  </td>
                </tr>
              )) : (
                <tr><td colspan="7" class="px-4 py-8 text-center text-gray-400">暂无日志记录</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 日志详情弹窗 */}
      {selectedLog && (
        <div
          class="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center z-50"
          onClick={() => setSelectedLog(null)}
        >
          <div
            class="bg-white rounded-2xl p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto"
            onClick={e => e.stopPropagation()}
          >
            <div class="flex items-center justify-between mb-4">
              <h3 class="text-lg font-bold">日志详情 #{selectedLog.id}</h3>
              <button
                onClick={() => setSelectedLog(null)}
                class="text-gray-400 hover:text-gray-600 text-xl"
              >✕</button>
            </div>
            <div class="space-y-3 text-sm">
              <DetailRow label="时间" value={selectedLog.timestamp} />
              <DetailRow label="用户" value={selectedLog.user_id} />
              <DetailRow label="动作" value={selectedLog.action} />
              <DetailRow label="渠道" value={selectedLog.channel} />
              <DetailRow label="延迟" value={`${selectedLog.latency_ms || 0}ms`} />
              {selectedLog.query && (
                <div>
                  <div class="text-gray-500 text-xs mb-1">查询内容</div>
                  <div class="bg-gray-50 p-3 rounded-lg">{selectedLog.query}</div>
                </div>
              )}
              {selectedLog.response_summary && (
                <div>
                  <div class="text-gray-500 text-xs mb-1">回复摘要</div>
                  <div class="bg-blue-50 p-3 rounded-lg">{selectedLog.response_summary}</div>
                </div>
              )}
              {selectedLog.sources?.length > 0 && (
                <div>
                  <div class="text-gray-500 text-xs mb-1">来源文档</div>
                  <div class="space-y-1">
                    {selectedLog.sources.map((s, i) => (
                      <div key={i} class="font-mono text-xs text-blue-600">📄 {s}</div>
                    ))}
                  </div>
                </div>
              )}
              {selectedLog.metadata && Object.keys(selectedLog.metadata).length > 0 && (
                <div>
                  <div class="text-gray-500 text-xs mb-1">元数据</div>
                  <pre class="bg-gray-900 text-green-400 p-3 rounded-lg text-xs overflow-auto">
                    {JSON.stringify(selectedLog.metadata, null, 2)}
                  </pre>
                </div>
              )}
              <DetailRow label="哈希" value={selectedLog.hash?.slice(0, 32) + '...'} mono />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function DetailRow({ label, value, mono }) {
  return (
    <div class="flex gap-3">
      <span class="text-gray-500 text-xs w-16 shrink-0">{label}</span>
      <span class={`text-gray-800 ${mono ? 'font-mono text-xs' : ''}`}>{value || '--'}</span>
    </div>
  )
}

export default AuditPage
