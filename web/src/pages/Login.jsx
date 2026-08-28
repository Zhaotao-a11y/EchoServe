import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useStore } from '../store'

function LoginPage() {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const login = useStore(s => s.login)
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username, password)
      navigate('/dashboard')
    } catch (err) {
      setError(err.message || '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div class="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-blue-900 to-slate-800">
      <div class="bg-white rounded-2xl shadow-2xl p-8 w-full max-w-md">
        {/* Logo */}
        <div class="text-center mb-8">
          <div class="inline-flex items-center justify-center w-16 h-16 bg-blue-600 rounded-2xl mb-4">
            <span class="text-2xl text-white font-bold">O</span>
          </div>
          <h1 class="text-2xl font-bold text-gray-900">EchoServe</h1>
          <p class="text-sm text-gray-500 mt-1">企业级本地知识库问答系统</p>
          <span class="inline-block mt-2 px-2 py-1 bg-blue-50 text-blue-600 text-xs rounded">V0.2.0</span>
        </div>

        {/* 登录表单 */}
        <form onSubmit={handleSubmit} class="space-y-4">
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">用户名</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="请输入用户名"
              class="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition"
              required
            />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700 mb-1">密码</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="请输入密码"
              class="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition"
              required
            />
          </div>

          {error && (
            <div class="bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg px-4 py-2.5">
              ⚠️ {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            class="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-blue-400 text-white font-medium py-2.5 rounded-lg transition"
          >
            {loading ? '登录中...' : '登 录'}
          </button>
        </form>

        {/* 提示 */}
        <div class="mt-6 text-center text-xs text-gray-400">
          <p>默认管理员: admin / [请在系统设置中配置密码]</p>
          <p class="mt-1">⚠️ 首次登录后请立即修改密码</p>
        </div>
      </div>
    </div>
  )
}

export default LoginPage
