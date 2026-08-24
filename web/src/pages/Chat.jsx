import React, { useState, useRef, useEffect } from 'react'
import { useStore } from '../store'

function ChatPage() {
  const sendMessage = useStore(s => s.sendMessage)
  const [messages, setMessages] = useState([
    { role: 'assistant', content: '你好！我是 EchoServe 智能助手，有什么可以帮你的？' }
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [useRag, setUseRag] = useState(false)
  const [sessionId] = useState(() => crypto.randomUUID())
  const messagesEnd = useRef(null)

  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSend = async () => {
    if (!input.trim() || loading) return

    const userMsg = input.trim()
    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: userMsg }])
    setLoading(true)

    // 添加空的助手消息用于流式更新
    setMessages(prev => [...prev, { role: 'assistant', content: '', loading: true }])

    try {
      const token = localStorage.getItem('token')
      const resp = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          session_id: sessionId,
          message: userMsg,
          use_rag: useRag,
        }),
      })

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let fullText = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim()
            if (data === '[DONE]') continue
            try {
              const parsed = JSON.parse(data)
              if (parsed.delta) {
                fullText += parsed.delta
                setMessages(prev => {
                  const newMsgs = [...prev]
                  const last = newMsgs[newMsgs.length - 1]
                  if (last?.role === 'assistant') {
                    newMsgs[newMsgs.length - 1] = { ...last, content: fullText, loading: false }
                  }
                  return newMsgs
                })
              }
            } catch {}
          }
        }
      }
    } catch (err) {
      setMessages(prev => {
        const newMsgs = [...prev]
        newMsgs[newMsgs.length - 1] = { role: 'assistant', content: `❌ 错误: ${err.message}` }
        return newMsgs
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div class="flex flex-col h-[calc(100vh-2rem)] p-6">
      {/* 标题栏 */}
      <div class="flex items-center justify-between mb-4">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">对话测试</h1>
          <p class="text-sm text-gray-500">测试 RAG 检索 + LLM 生成效果</p>
        </div>
        <label class="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={useRag}
            onChange={e => setUseRag(e.target.checked)}
            class="w-4 h-4 text-blue-600 rounded"
          />
          <span class={useRag ? 'text-blue-600 font-medium' : 'text-gray-400'}>
            RAG 检索增强
          </span>
        </label>
      </div>

      {/* 消息区域 */}
      <div class="flex-1 overflow-auto bg-white rounded-xl border border-gray-100 p-4 space-y-4">
        {messages.map((msg, i) => (
          <div key={i} class={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div class={`max-w-2xl px-4 py-3 rounded-2xl ${
              msg.role === 'user'
                ? 'bg-blue-600 text-white rounded-br-sm'
                : 'bg-gray-100 text-gray-800 rounded-bl-sm'
            }`}>
              {msg.role === 'assistant' && !msg.loading && (
                <span class="inline-block w-5 h-5 bg-blue-100 text-blue-600 rounded-full text-xs text-center mr-2">AI</span>
              )}
              <span class="whitespace-pre-wrap">{msg.content}</span>
              {msg.loading && msg.content === '' && (
                <span class="inline-flex gap-1 ml-1">
                  <span class="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"></span>
                  <span class="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.1s' }}></span>
                  <span class="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0.2s' }}></span>
                </span>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEnd} />
      </div>

      {/* 输入栏 */}
      <div class="mt-4 flex gap-2">
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && handleSend()}
          placeholder="输入消息... (Enter 发送)"
          class="flex-1 px-4 py-3 border border-gray-300 rounded-xl focus:ring-2 focus:ring-blue-500 outline-none"
          disabled={loading}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          class="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-300 text-white px-6 py-3 rounded-xl font-medium"
        >
          {loading ? '...' : '发送'}
        </button>
      </div>
    </div>
  )
}

export default ChatPage
