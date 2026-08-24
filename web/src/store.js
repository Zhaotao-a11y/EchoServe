import { create } from 'zustand'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

// ─── API 封装 ───────────────────────────────────────
async function apiCall(url, options = {}) {
  const token = localStorage.getItem('token')
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  }

  const resp = await fetch(`${API_BASE}${url}`, { ...options, headers })
  if (!resp.ok) {
    if (resp.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      window.location.href = '/login'
      throw new Error('Session expired, please login again')
    }
    const err = await resp.json().catch(() => ({ detail: resp.statusText }))
    throw new Error(err.detail || `HTTP ${resp.status}`)
  }
  return resp.json()
}

// ─── Store ──────────────────────────────────────────
export const useStore = create((set, get) => ({
  // 认证
  token: localStorage.getItem('token') || null,
  user: JSON.parse(localStorage.getItem('user') || 'null'),
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
    set({ token: data.access_token, user: { user_id: data.user_id, username: data.username, role: data.role } })
    return data
  },
  logout: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    set({ token: null, user: null })
  },

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
      // stats 端点可能尚未实现
      set({ kbStats: null })
    }
  },
  uploadFile: async (file, metadata = {}) => {
    const token = localStorage.getItem('token')
    const form = new FormData()
    form.append('file', file)
    
    // 根据文件类型选择端点
    const isJsonl = file.name.toLowerCase().endsWith('.jsonl')
    const endpoint = isJsonl ? '/knowledge/ingest' : '/knowledge/upload'
    
    const resp = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: form,
    })
    if (!resp.ok) {
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
