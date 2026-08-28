import React, { useState, useEffect } from 'react'
import { useStore, apiCall } from '../store'

function KnowledgePage() {
  const documents = useStore(s => s.documents)
  const fetchDocuments = useStore(s => s.fetchDocuments)
  const uploadFile = useStore(s => s.uploadFile)
  const deleteDoc = useStore(s => s.deleteDoc)
  const clearKnowledgeBase = useStore(s => s.clearKnowledgeBase)
  const kbStats = useStore(s => s.kbStats)
  const fetchStats = useStore(s => s.fetchStats)

  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState('')
  const [offset, setOffset] = useState(0)
  const [selectedDoc, setSelectedDoc] = useState(null)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [clearing, setClearing] = useState(false)
  const limit = 20

  useEffect(() => {
    fetchDocuments(offset, limit)
    fetchStats()
  }, [offset])

  const handleFileUpload = async (files) => {
    if (!files || files.length === 0) return
    setUploading(true)
    setUploadStatus('')

    let success = 0
    let details = []
    for (const file of files) {
      try {
        const result = await uploadFile(file)
        success++
        
        // 结构化导入反馈
        if (result.metadata?.import_mode === 'row_by_row') {
          details.push(`✅ ${file.name}: 导入 ${result.metadata.table_rows} 条问答记录`)
        } else if (result.total !== undefined) {
          details.push(`✅ ${file.name}: 导入 ${result.total} 条文档`)
        } else if (result.total_chunks !== undefined) {
          details.push(`✅ ${file.name}: 生成 ${result.total_chunks} 个切片`)
        } else {
          details.push(`✅ ${file.name}: 上传成功`)
        }
      } catch (err) {
        details.push(`❌ ${file.name}: ${err.message}`)
      }
    }

    setUploadStatus(details.join('\n'))
    setUploading(false)
    fetchDocuments(offset, limit)
    fetchStats()
  }

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedIds.size === documents.length && documents.length > 0) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(documents.map(d => d.id)))
    }
  }

  // 切换单条选择
  const toggleSelectOne = (id) => {
    const newSet = new Set(selectedIds)
    if (newSet.has(id)) {
      newSet.delete(id)
    } else {
      newSet.add(id)
    }
    setSelectedIds(newSet)
  }

  // 批量删除
  const batchDelete = async () => {
    if (selectedIds.size === 0) return
    if (!confirm(`确定要删除选中的 ${selectedIds.size} 条文档吗？此操作不可撤销。`)) return
    
    let deleted = 0
    for (const id of selectedIds) {
      try {
        await deleteDoc(id)
        deleted++
      } catch (e) {
        console.error('Delete failed:', id, e)
      }
    }
    setSelectedIds(new Set())
    fetchDocuments(offset, limit)
    fetchStats()
    alert(`已删除 ${deleted} 条文档`)
  }

  // 清空知识库
  const handleClearAll = async () => {
    if (!confirm('⚠️ 确定要清空整个知识库吗？所有文档将被删除且不可恢复！')) return
    setClearing(true)
    try {
      await clearKnowledgeBase()
      setSelectedIds(new Set())
      alert('✅ 知识库已清空')
    } catch (e) {
      alert('❌ 清空失败: ' + e.message)
    } finally {
      setClearing(false)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    handleFileUpload(e.dataTransfer.files)
  }

  const handleFileInput = (e) => {
    handleFileUpload(e.target.files)
    e.target.value = ''
  }

  return (
    <div class="p-6 space-y-6">
      {/* 页面标题 */}
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">知识库管理</h1>
          <p class="text-sm text-gray-500 mt-1">上传文档，系统自动解析、切片并建立索引</p>
        </div>
        {kbStats && (
          <div class="flex gap-4 text-sm">
            <div class="bg-blue-50 px-3 py-1.5 rounded-lg">
              <span class="text-blue-700 font-semibold">{kbStats.total_documents}</span>
              <span class="text-blue-500 ml-1">文档</span>
            </div>
            <div class="bg-green-50 px-3 py-1.5 rounded-lg">
              <span class="text-green-700 font-semibold">{Math.round(kbStats.total_characters / 10000)}万</span>
              <span class="text-green-500 ml-1">字符</span>
            </div>
          </div>
        )}
      </div>

      {/* 上传区域 */}
      <div
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        class={`border-2 border-dashed rounded-xl p-8 text-center transition ${
          dragOver ? 'border-blue-500 bg-blue-50' : 'border-gray-300 bg-white hover:bg-gray-50'
        }`}
      >
        <div class="text-4xl mb-3">📎</div>
        <p class="text-gray-600 mb-2">拖拽文件到此处，或点击选择文件</p>
        <p class="text-xs text-gray-400 mb-4">
          支持 PDF / DOCX / MD / TXT / JSONL，单文件 ≤ 50MB
          <br />
          <span class="text-blue-500">MD 表格自动逐条导入 · JSONL 批量导入</span>
        </p>
        <label class="inline-block">
          <span class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg cursor-pointer text-sm">
            {uploading ? '上传中...' : '选择文件'}
          </span>
          <input
            type="file"
            multiple
            accept=".pdf,.docx,.md,.txt,.jsonl"
            onChange={handleFileInput}
            class="hidden"
          />
        </label>
      </div>

      {/* 上传状态 */}
      {uploadStatus && (
        <div class={`rounded-lg p-3 text-sm whitespace-pre-line ${
          uploadStatus.includes('✅') ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
        }`}>
          {uploadStatus}
        </div>
      )}

      {/* 文档列表 */}
      <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <div class="flex items-center justify-between mb-4">
          <h3 class="font-semibold text-gray-800">📚 文档列表</h3>
          <div class="flex gap-2">
            {/* 清空知识库按钮 */}
            <button
              onClick={handleClearAll}
              disabled={clearing || !documents.length}
              class="px-3 py-1.5 text-sm bg-red-50 text-red-600 hover:bg-red-100 rounded-lg disabled:opacity-40"
            >
              {clearing ? '清空中...' : '🗑️ 清空知识库'}
            </button>
            {/* 批量删除按钮 */}
            {selectedIds.size > 0 && (
              <button
                onClick={batchDelete}
                class="px-3 py-1.5 text-sm bg-orange-50 text-orange-600 hover:bg-orange-100 rounded-lg"
              >
                🗑️ 删除选中 ({selectedIds.size})
              </button>
            )}
          </div>
        </div>

        <div class="flex items-center justify-between text-sm text-gray-500 mb-3">
          <div class="flex items-center gap-2">
            <input
              type="checkbox"
              checked={documents.length > 0 && selectedIds.size === documents.length}
              onChange={toggleSelectAll}
              class="w-4 h-4"
            />
            <span>全选 (当前页 {documents.length} 条)</span>
          </div>
          <div class="flex gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - limit))}
              disabled={offset === 0}
              class="px-3 py-1 text-sm border rounded disabled:opacity-40"
            >上一页</button>
            <span class="px-3 py-1">{offset + 1} - {offset + documents.length}</span>
            <button
              onClick={() => setOffset(offset + limit)}
              disabled={documents.length < limit}
              class="px-3 py-1 text-sm border rounded disabled:opacity-40"
            >下一页</button>
          </div>
        </div>

        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-gray-500 border-b bg-gray-50">
                <th class="px-3 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={documents.length > 0 && selectedIds.size === documents.length}
                    onChange={toggleSelectAll}
                    class="w-4 h-4"
                  />
                </th>
                <th class="px-4 py-3">文档ID</th>
                <th class="px-4 py-3">内容预览</th>
                <th class="px-4 py-3">长度</th>
                <th class="px-4 py-3">元数据</th>
                <th class="px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {documents.length > 0 ? documents.map(doc => (
                <tr key={doc.id} class="border-b border-gray-50 hover:bg-gray-50">
                  <td class="px-3 py-2.5">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(doc.id)}
                      onChange={() => toggleSelectOne(doc.id)}
                      class="w-4 h-4"
                    />
                  </td>
                  <td class="px-4 py-2.5">
                    <span class="font-mono text-xs text-gray-600">{doc.id.slice(0, 12)}...</span>
                  </td>
                  <td class="px-4 py-2.5 max-w-md">
                    <span class="text-gray-700 truncate block">{doc.content_preview}</span>
                  </td>
                  <td class="px-4 py-2.5 text-gray-500">{doc.content_length}字</td>
                  <td class="px-4 py-2.5">
                    {doc.metadata?.filename && (
                      <span class="px-2 py-0.5 bg-blue-50 text-blue-600 rounded text-xs">
                        📄 {doc.metadata.filename}
                      </span>
                    )}
                    {doc.metadata?.filetype && (
                      <span class="px-2 py-0.5 bg-gray-100 text-gray-600 rounded text-xs ml-1">
                        {doc.metadata.filetype}
                      </span>
                    )}
                  </td>
                  <td class="px-4 py-2.5">
                    <button
                      onClick={() => deleteDoc(doc.id)}
                      class="text-red-500 hover:text-red-700 text-xs"
                    >删除</button>
                  </td>
                </tr>
              )) : (
                <tr><td colspan="6" class="px-4 py-8 text-center text-gray-400">暂无文档，请上传</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 检索测试 */}
      <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
        <h3 class="font-semibold text-gray-800 mb-3">🔍 检索测试</h3>
        <RetrievalTester />
      </div>
    </div>
  )
}

function RetrievalTester() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)

  const test = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      // M-12: 使用 apiCall 替代直接 fetch + localStorage
      const data = await apiCall(`/knowledge/test?query=${encodeURIComponent(query)}&top_k=5`)
      setResults(data)
    } catch (e) {
      setResults({ error: e.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div class="flex gap-2 mb-4">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && test()}
          placeholder="输入测试问题，如：退货政策是什么？"
          class="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
        />
        <button
          onClick={test}
          disabled={loading}
          class="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-5 py-2 rounded-lg text-sm"
        >{loading ? '检索中...' : '检索'}</button>
      </div>

      {results && !results.error && (
        <div class="space-y-2">
          {results.results?.map((r, i) => (
            <div key={i} class="border border-gray-100 rounded-lg p-3">
              <div class="flex items-center gap-2 mb-1">
                <span class="w-5 h-5 bg-blue-100 text-blue-700 rounded-full text-xs flex items-center justify-center font-bold">
                  {r.rank}
                </span>
                <span class="text-xs text-gray-400">分数: {r.score?.toFixed(3)}</span>
                {r.source && <span class="text-xs text-green-600">📄 {r.source}</span>}
              </div>
              <p class="text-sm text-gray-700">{r.content}</p>
            </div>
          ))}
        </div>
      )}
      {results?.error && (
        <div class="text-red-600 text-sm">❌ {results.error}</div>
      )}
    </div>
  )
}

export default KnowledgePage
