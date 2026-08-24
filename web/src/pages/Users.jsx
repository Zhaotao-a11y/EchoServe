import React, { useState, useEffect } from 'react'
import { useStore } from '../store'

const ROLES = [
  { value: 'super_admin', label: '超级管理员' },
  { value: 'admin', label: '管理员' },
  { value: 'editor', label: '编辑者' },
  { value: 'user', label: '普通用户' },
  { value: 'readonly', label: '只读用户' },
]

const ROLE_COLORS = {
  super_admin: 'bg-red-100 text-red-700',
  admin: 'bg-purple-100 text-purple-700',
  editor: 'bg-blue-100 text-blue-700',
  user: 'bg-green-100 text-green-700',
  readonly: 'bg-gray-100 text-gray-600',
}

function UsersPage() {
  const users = useStore(s => s.users)
  const fetchUsers = useStore(s => s.fetchUsers)
  const createUser = useStore(s => s.createUser)
  const deleteUser = useStore(s => s.deleteUser)
  const updateRole = useStore(s => s.updateRole)
  const currentUser = useStore(s => s.user)

  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', role: 'user', department: 'default' })
  const [error, setError] = useState('')

  useEffect(() => { fetchUsers() }, [])

  const handleCreate = async (e) => {
    e.preventDefault()
    setError('')
    try {
      await createUser(form)
      setShowCreate(false)
      setForm({ username: '', password: '', role: 'user', department: 'default' })
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div class="p-6 space-y-6">
      <div class="flex items-center justify-between">
        <div>
          <h1 class="text-2xl font-bold text-gray-900">用户管理</h1>
          <p class="text-sm text-gray-500 mt-1">管理系统用户和角色权限</p>
        </div>
        <button
          onClick={() => setShowCreate(!showCreate)}
          class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm"
        >
          {showCreate ? '取消' : '+ 新建用户'}
        </button>
      </div>

      {/* 创建表单 */}
      {showCreate && (
        <div class="bg-white rounded-xl shadow-sm border border-gray-100 p-5">
          <h3 class="font-semibold text-gray-800 mb-4">创建新用户</h3>
          <form onSubmit={handleCreate} class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label class="block text-sm text-gray-600 mb-1">用户名</label>
              <input
                value={form.username}
                onChange={e => setForm({...form, username: e.target.value})}
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                required minLength={3}
              />
            </div>
            <div>
              <label class="block text-sm text-gray-600 mb-1">密码</label>
              <input
                type="password"
                value={form.password}
                onChange={e => setForm({...form, password: e.target.value})}
                placeholder="≥8位，含大小写+数字+特殊字符"
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
                required minLength={8}
              />
            </div>
            <div>
              <label class="block text-sm text-gray-600 mb-1">角色</label>
              <select
                value={form.role}
                onChange={e => setForm({...form, role: e.target.value})}
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
              >
                {ROLES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
            </div>
            <div>
              <label class="block text-sm text-gray-600 mb-1">部门</label>
              <input
                value={form.department}
                onChange={e => setForm({...form, department: e.target.value})}
                class="w-full px-3 py-2 border rounded-lg focus:ring-2 focus:ring-blue-500 outline-none"
              />
            </div>
            {error && <div class="md:col-span-2 text-red-600 text-sm">❌ {error}</div>}
            <div class="md:col-span-2">
              <button type="submit" class="bg-blue-600 hover:bg-blue-700 text-white px-5 py-2 rounded-lg text-sm">
                创建用户
              </button>
            </div>
          </form>
        </div>
      )}

      {/* 用户列表 */}
      <div class="bg-white rounded-xl shadow-sm border border-gray-100">
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-gray-500 border-b bg-gray-50">
                <th class="px-4 py-3">用户名</th>
                <th class="px-4 py-3">角色</th>
                <th class="px-4 py-3">部门</th>
                <th class="px-4 py-3">最后登录</th>
                <th class="px-4 py-3">状态</th>
                <th class="px-4 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {users.length > 0 ? users.map(user => (
                <tr key={user.user_id} class="border-b border-gray-50 hover:bg-gray-50">
                  <td class="px-4 py-2.5 font-medium">{user.username}</td>
                  <td class="px-4 py-2.5">
                    <select
                      value={user.role}
                      onChange={e => updateRole(user.user_id, e.target.value)}
                      class={`text-xs px-2 py-0.5 rounded ${ROLE_COLORS[user.role] || 'bg-gray-100'}`}
                      disabled={user.user_id === currentUser?.user_id}
                    >
                      {ROLES.map(r => <option key={r.value} value={r.value}>{r.label}</option>)}
                    </select>
                  </td>
                  <td class="px-4 py-2.5 text-gray-500">{user.department || '--'}</td>
                  <td class="px-4 py-2.5 text-gray-500 text-xs">{user.last_login?.slice(0, 16) || '从未登录'}</td>
                  <td class="px-4 py-2.5">
                    <span class={`text-xs px-2 py-0.5 rounded ${user.enabled ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}`}>
                      {user.enabled ? '启用' : '禁用'}
                    </span>
                  </td>
                  <td class="px-4 py-2.5">
                    {user.user_id !== currentUser?.user_id && (
                      <button
                        onClick={() => { if (confirm(`确定删除用户 ${user.username}?`)) deleteUser(user.user_id) }}
                        class="text-red-500 hover:text-red-700 text-xs"
                      >删除</button>
                    )}
                  </td>
                </tr>
              )) : (
                <tr><td colspan="6" class="px-4 py-8 text-center text-gray-400">暂无用户</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

export default UsersPage
