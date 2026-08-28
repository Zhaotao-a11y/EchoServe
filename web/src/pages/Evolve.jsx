import React, { useState, useEffect } from 'react'
import { apiCall } from '../store'

export default function EvolvePage() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [training, setTraining] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [report, setReport] = useState(null)
  const [dataValidation, setDataValidation] = useState(null)

  // 加载进化状态
  const loadStatus = async () => {
    try {
      const d = await apiCall('/evolve/status')
      setStatus(d)
    } catch (e) {
      // ignore — 401 由 apiCall 内部处理
    } finally {
      setLoading(false)
    }
  }

  // 检查进化建议
  const loadCheck = async () => {
    try {
      return await apiCall('/evolve/check')
    } catch (e) {
      return null
    }
  }

  // 构建训练数据
  const handleBuildData = async () => {
    setMessage('')
    setError('')
    try {
      const d = await apiCall('/evolve/build-data', { method: 'POST' })
      setDataValidation(d.validation)
      setMessage(`✅ 训练数据已构建: ${d.output_path}`)
    } catch (e) {
      setError(`构建失败: ${e.message}`)
    }
  }

  // 触发 LoRA 训练
  const handleTriggerLora = async () => {
    setTraining(true)
    setMessage('')
    setError('')
    try {
      const d = await apiCall('/evolve/trigger/lora', {
        method: 'POST',
        body: JSON.stringify({}),
      })
      setMessage(`✅ LoRA 训练完成! eval_loss=${d.best_eval_loss?.toFixed(4)}`)
      loadStatus()
    } catch (e) {
      setError(`训练失败: ${e.message}`)
    } finally {
      setTraining(false)
    }
  }

  // 运行评估
  const handleEvaluate = async () => {
    setMessage('')
    setError('')
    try {
      const d = await apiCall('/evolve/evaluate', { method: 'POST' })
      setReport(d)
      setMessage(`✅ 评估完成: 准确率 ${(d.accuracy * 100).toFixed(1)}%`)
    } catch (e) {
      setError(`评估失败: ${e.message}`)
    }
  }

  // 获取评估报告
  const loadReport = async () => {
    try {
      const d = await apiCall('/evolve/eval-report')
      setReport(d)
    } catch (e) {}
  }

  useEffect(() => {
    loadStatus()
    loadReport()
  }, [])

  if (loading) {
    return <div class="p-6 text-slate-400">加载中...</div>
  }

  const check = status?.check || {}
  const ev = status?.evolution || {}
  const adapters = status?.adapters || []
  const trainStatus = status?.training_status || 'idle'

  // 阶段颜色
  const stageColors = {
    1: 'bg-green-100 text-green-700',
    2: 'bg-yellow-100 text-yellow-700',
    3: 'bg-purple-100 text-purple-700',
  }

  return (
    <div class="p-6 max-w-6xl mx-auto">
      {/* 标题 */}
      <div class="mb-6">
        <h1 class="text-2xl font-bold text-slate-800">🧬 模型进化引擎</h1>
        <p class="text-sm text-slate-500 mt-1">
          根据知识库规模自动建议进化策略，支持离线 LoRA 微调和全参数微调
        </p>
      </div>

      {/* 提示信息 */}
      {message && (
        <div class="mb-4 p-3 bg-green-50 border border-green-200 rounded text-green-700 text-sm">
          {message}
        </div>
      )}
      {error && (
        <div class="mb-4 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* 进化建议卡片 */}
      <div class="mb-6 p-5 bg-white border border-slate-200 rounded-lg">
        <div class="flex items-center gap-3 mb-3">
          <h2 class="text-lg font-semibold text-slate-700">进化建议</h2>
          {ev.stage && (
            <span class={`text-xs px-2 py-1 rounded font-medium ${stageColors[ev.stage] || 'bg-slate-100'}`}>
              阶段 {ev.stage}
            </span>
          )}
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div>
            <span class="text-slate-500">知识库规模：</span>
            <span class="font-mono font-medium">{ev.kb_size || 0} 条</span>
          </div>
          <div>
            <span class="text-slate-500">训练状态：</span>
            <span class={`font-medium ${
              trainStatus === 'running' ? 'text-blue-600' :
              trainStatus === 'completed' ? 'text-green-600' :
              trainStatus === 'failed' ? 'text-red-600' : 'text-slate-500'
            }`}>
              {trainStatus}
            </span>
          </div>
          <div>
            <span class="text-slate-500">可训练：</span>
            <span class={ev.can_train ? 'text-green-600' : 'text-slate-400'}>
              {ev.can_train ? '✅ 是' : '❌ 否'}
            </span>
          </div>
        </div>
        {ev.recommendation && (
          <div class="mt-3 p-3 bg-blue-50 border border-blue-200 rounded text-sm text-blue-700">
            💡 {ev.recommendation}
          </div>
        )}
      </div>

      {/* 操作按钮 */}
      <div class="mb-6 grid grid-cols-1 md:grid-cols-3 gap-3">
        <button
          onClick={handleBuildData}
          class="p-4 bg-white border border-slate-200 rounded-lg hover:border-blue-400 transition text-left"
        >
          <div class="text-lg mb-1">📦</div>
          <div class="font-medium text-slate-700">构建训练数据</div>
          <div class="text-xs text-slate-400 mt-1">从知识库提取 QA 对 + LLM 生成同义变体</div>
        </button>

        <button
          onClick={handleTriggerLora}
          disabled={training || !ev.can_train}
          class={`p-4 border rounded-lg text-left transition ${
            !ev.can_train
              ? 'bg-slate-50 border-slate-200 cursor-not-allowed opacity-50'
              : 'bg-white border-slate-200 hover:border-purple-400'
          }`}
        >
          <div class="text-lg mb-1">🏋️</div>
          <div class="font-medium text-slate-700">
            {training ? '训练中...' : '触发 LoRA 微调'}
          </div>
          <div class="text-xs text-slate-400 mt-1">
            {ev.train_type === 'lora' ? 'r=8, target=q_proj+v_proj' : '需知识库 ≥2000 条'}
          </div>
        </button>

        <button
          onClick={handleEvaluate}
          class="p-4 bg-white border border-slate-200 rounded-lg hover:border-green-400 transition text-left"
        >
          <div class="text-lg mb-1">📊</div>
          <div class="font-medium text-slate-700">运行评估</div>
          <div class="text-xs text-slate-400 mt-1">在测试集上评估当前模型准确率</div>
        </button>
      </div>

      {/* 数据验证结果 */}
      {dataValidation && (
        <div class="mb-6 p-4 bg-white border border-slate-200 rounded-lg">
          <h3 class="font-medium text-slate-700 mb-2">📋 训练数据验证</h3>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
            <div>
              <span class="text-slate-500">总数：</span>
              <span class="font-mono">{dataValidation.total}</span>
            </div>
            <div>
              <span class="text-slate-500">有效：</span>
              <span class="font-mono text-green-600">{dataValidation.valid}</span>
            </div>
            <div>
              <span class="text-slate-500">无效：</span>
              <span class="font-mono text-red-600">{dataValidation.invalid}</span>
            </div>
            <div>
              <span class="text-slate-500">平均输入长度：</span>
              <span class="font-mono">{dataValidation.avg_input_len} 字符</span>
            </div>
          </div>
          {dataValidation.issues?.length > 0 && (
            <details class="mt-3">
              <summary class="text-xs text-slate-400 cursor-pointer">查看问题 ({dataValidation.issues.length})</summary>
              <ul class="mt-2 text-xs text-red-500 space-y-1">
                {dataValidation.issues.map((iss, i) => (
                  <li key={i}>{iss}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {/* Adapters 列表 */}
      <div class="mb-6">
        <h2 class="text-lg font-semibold text-slate-700 mb-3">📂 已训练 Adapters</h2>
        {adapters.length === 0 ? (
          <div class="text-slate-400 text-sm">暂无已训练的 adapter。触发 LoRA 微调后将出现在此处。</div>
        ) : (
          <div class="space-y-2">
            {adapters.map(a => (
              <div key={a.name} class="p-3 bg-white border border-slate-200 rounded flex items-center justify-between">
                <div>
                  <div class="font-medium text-slate-700">{a.name}</div>
                  <div class="text-xs text-slate-400">
                    {a.created_at} | eval_loss={a.eval_loss?.toFixed(4)}
                  </div>
                </div>
                <span class="text-xs px-2 py-1 rounded bg-purple-100 text-purple-700">
                  LoRA
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 评估报告 */}
      {report && (
        <div class="mb-6 p-5 bg-white border border-slate-200 rounded-lg">
          <h2 class="text-lg font-semibold text-slate-700 mb-3">📊 最新评估报告</h2>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <div class="text-slate-500 text-xs">准确率</div>
              <div class="text-xl font-bold text-blue-600">
                {(report.accuracy * 100).toFixed(1)}%
              </div>
            </div>
            <div>
              <div class="text-slate-500 text-xs">测试总数</div>
              <div class="text-xl font-bold text-slate-700">{report.total}</div>
            </div>
            <div>
              <div class="text-slate-500 text-xs">平均延迟</div>
              <div class="text-xl font-bold text-slate-700">{report.avg_latency_ms}ms</div>
            </div>
            <div>
              <div class="text-slate-500 text-xs">P95 延迟</div>
              <div class="text-xl font-bold text-slate-700">{report.p95_latency_ms}ms</div>
            </div>
          </div>
          {report.notification && (
            <div class="mt-3 text-sm text-slate-500">{report.notification}</div>
          )}
        </div>
      )}

      {/* 刷新按钮 */}
      <button
        onClick={() => { loadStatus(); loadReport() }}
        class="px-4 py-2 bg-slate-600 text-white rounded hover:bg-slate-700 text-sm"
      >
        🔄 刷新状态
      </button>
    </div>
  )
}
