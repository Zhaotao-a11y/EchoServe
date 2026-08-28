import { create } from 'zustand'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

// ─── Store ──────────────────────────────────────────
export const useStore = create((set, get) => ({
  // 认证
  token: localStorage.getItem('token') || null,
  user: JSON.parse(localStorage.getItem('user') || 'null'),
  authExpired: false,  // M-11: 软跳转标志，App.jsx watch 此字段

  /** M-12: 统一 Token 获取入口，所有组件通过此方法取 token */
  getToken: () => get().token,

  login: async (username, password) => {
    const data = await apiCall('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    localStorage.setItem('token', data.access_token)
    localStorage.setItem('user', JSON.stringify({
      user_id: data.user_id,
      username: data.username,
      role: data.role,
    }))
    set({ token: data.access_token, user: { user_id: data.user_id, username: data.username, role: data.role }, authExpired: false })
    return data
  },
  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    set({ token: null, user: null, authExpired: false })
  },
  /** M-11: 清除 authExpired 状态（App 跳转后调用） */
  clearAuthExpired: () => set({ authExpired: false }),

  // 知识库
  documents: [],
  kbStats: null,
  fetchDocuments: async (offset = 0, limit = 50) => {
    const data = await apiCall(`/knowledge?offset=${offset}&limit=${limit}`)
    set({ documents: data.documents })
    return data
  },
  fetchStats: async () => {
    try {
      const data = await apiCall('/knowledge/stats')
      set({ kbStats: data })
    } catch (e) {
      set({ kbStats: null })
    }
  },
  uploadFile: async (file, metadata = {}) => {
    const token = get().token  // M-12: 统一从 store 获取
    const form = new FormData()
    form.append('file', file)

    const isJsonl = file.name.toLowerCase().endsWith('.jsonl')
    const endpoint = isJsonl ? '/knowledge/ingest' : '/knowledge/upload'

    const resp = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: form,
    })
    if (!resp.ok) {
      if (resp.status === 401) {
        set({ authExpired: true, token: null, user: null })
        localStorage.removeItem('token')
        localStorage.removeItem('user')
        throw new Error('Session expired')
      }
      const err = await resp.json().catch(() => ({ detail: resp.statusText }))
      throw new Error(err.detail || `Upload failed: ${resp.statusText}`)
    }
    return resp.json()
  },
  deleteDoc: async (docId) => {
    await apiCall(`/knowledge/${docId}`, { method: 'DELETE' })
    set({ documents: get().documents.filter(d => d.id !== docId) })
  },
  clearKnowledgeBase: async () => {
    await apiCall('/knowledge/all', { method: 'DELETE' })
    set({ documents: [], kbStats: null })
  },

  // 对话
  chatHistory: [],
  sendMessage: async (message, sessionId = null) => {
    const data = await apiCall('/chat', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sessionId,
        message,
        use_rag: false,
      }),
    })
    return data
  },

  // 审计日志
  auditLogs: [],
  fetchAuditLogs: async (params = {}) => {
    const qs = new URLSearchParams(params).toString()
    const data = await apiCall(`/audit/logs?${qs}`)
    set({ auditLogs: data.logs })
    return data
  },

  // 用户管理
  users: [],
  fetchUsers: async () => {
    const data = await apiCall('/users')
    set({ users: data.users })
    return data
  },
  createUser: async (userData) => {
    const data = await apiCall('/users', {
      method: 'POST',
      body: JSON.stringify(userData),
    })
    await get().fetchUsers()
    return data
  },
  deleteUser: async (userId) => {
    await apiCall(`/users/${userId}`, { method: 'DELETE' })
    await get().fetchUsers()
  },
  updateRole: async (userId, role) => {
    await apiCall(`/users/${userId}/role`, {
      method: 'PUT',
      body: JSON.stringify({ role }),
    })
    await get().fetchUsers()
  },
}))

// ─── API 封装 ───────────────────────────────────────
// M-12: 统一从 store 获取 token，不再直接读 localStorage
// m-20: 30s 请求超时保护
const DEFAULT_TIMEOUT_MS = 30000

function _withTimeout(options = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), options.timeoutMs || DEFAULT_TIMEOUT_MS)
  const { timeoutMs, ...rest } = options
  return { signal: controller.signal, _timer: timer, ...rest }
}

function _clearTimer(opts) {
  if (opts._timer) clearTimeout(opts._timer)
}

async function apiCall(url, options = {}) {
  const token = useStore.getState().token
  const opts = _withTimeout(options)
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }

  let resp
  try {
    resp = await fetch(`${API_BASE}${url}`, { ...opts, headers })
  } catch (e) {
    _clearTimer(opts)
    if (e.name === 'AbortError') throw new Error('请求超时 (30s)')
    throw e
  }
  _clearTimer(opts)
  if (!resp.ok) {
    // M-11: 401 软跳转 — 设置 authExpired 标志，由 App.jsx 监听后路由跳转
    if (resp.status === 401) {
      useStore.setState({ authExpired: true, token: null, user: null })
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      throw new Error('Session expired, please login again')
    }
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

/**
 * M-12: 流式请求封装 — 供 Chat.jsx 等需要 ReadableStream 的场景使用
 * 返回原始 Response 对象，调用方自行处理流读取
 */
export async function apiCallStream(url, options = {}) {
  const token = useStore.getState().token
  const opts = _withTimeout(options)
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }

  let resp
  try {
    resp = await fetch(`${API_BASE}${url}`, { ...opts, headers })
  } catch (e) {
    _clearTimer(opts)
    if (e.name === 'AbortError') throw new Error('请求超时 (30s)')
    throw e
  }
  _clearTimer(opts)
  if (!resp.ok) {
    if (resp.status === 401) {
      useStore.setState({ authExpired: true, token: null, user: null })
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      throw new Error('Session expired, please login again')
    }
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp
}

/**
 * M-12: 带 auth header 的 fetch 封装 — 供需要 blob/非 JSON 响应的场景使用
 * 返回原始 Response 对象
 */
export async function apiCallRaw(url, options = {}) {
  const token = useStore.getState().token
  const opts = _withTimeout(options)
  const headers = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }

  let resp
  try {
    resp = await fetch(`${API_BASE}${url}`, { ...opts, headers })
  } catch (e) {
    _clearTimer(opts)
    if (e.name === 'AbortError') throw new Error('请求超时 (30s)')
    throw e
  }
  _clearTimer(opts)
  if (!resp.ok) {
    if (resp.status === 401) {
      useStore.setState({ authExpired: true, token: null, user: null })
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      throw new Error('Session expired, please login again')
    }
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp
}

export { apiCall }
