import React, { useState, useEffect, lazy, Suspense } from 'react'
import { Routes, Route, NavLink, Navigate, useNavigate } from 'react-router-dom'
import { useStore } from './store'

// m-17: 路由级代码分割 — React.lazy 按需加载页面组件
const LoginPage = lazy(() => import('./pages/Login'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const KnowledgePage = lazy(() => import('./pages/Knowledge'))
const ChatPage = lazy(() => import('./pages/Chat'))
const UsersPage = lazy(() => import('./pages/Users'))
const AuditPage = lazy(() => import('./pages/Audit'))
const SettingsPage = lazy(() => import('./pages/Settings'))
const ModelsPage = lazy(() => import('./pages/Models'))
const MonitoringPage = lazy(() => import('./pages/Monitoring'))
const EvolvePage = lazy(() => import('./pages/Evolve'))
const EvolutionSystemPage = lazy(() => import('./pages/EvolutionSystem'))

// m-17: Suspense fallback
const PageLoading = () => (
  <div className="flex items-center justify-center h-full p-8">
    <div className="text-slate-400 text-sm">页面加载中...</div>
  </div>
)

// ─── 导航项（P1 新增：模型管理 / 监控 / 进化引擎）──
const navItems = [
  { path: '/dashboard',   label: '仪表盘',   icon: '📊' },
  { path: '/chat',       label: '对话测试', icon: '💬' },
  { path: '/knowledge',  label: '知识库',   icon: '📚' },
  { path: '/models',     label: '模型管理', icon: '🤖' },  // P1
  { path: '/evolve',     label: '模型进化', icon: '🧬' },  // P1
  { path: '/evolution',  label: '进化系统', icon: '🔬' },  // Phase 1-3
  { path: '/monitoring', label: '监控',     icon: '📈' },  // P1
  { path: '/users',      label: '用户管理', icon: '👥' },
  { path: '/audit',      label: '审计日志', icon: '🔍' },
  { path: '/settings',  label: '系统设置', icon: '⚙️' },
]

function Layout({ children }) {
  const user = useStore(s => s.user)
  const logout = useStore(s => s.logout)
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div class="flex h-screen bg-gray-50">
      {/* 侧边栏 — 展开 200px，图标文字保持原大 */}
      <aside class={`${collapsed ? 'w-24' : 'w-[200px]'} bg-slate-900 text-white transition-all duration-200 flex flex-col`}>
        {/* Logo — 放大50% */}
        <div class="h-24 flex items-center justify-center border-b border-slate-700">
          {collapsed ? (
            <span class="text-xl font-bold text-blue-400">O</span>
          ) : (
              <div class="text-center">
                <div class="h-10 flex items-center justify-center">
                  <img src="/logo.jpg" alt="EchoServe" class="h-8" />
                </div>
                <div class="text-xs text-slate-400 mt-1">EchoServe V0.2.0</div>
              </div>
          )}
        </div>

        {/* 导航 — 垂直排列：放大50% */}
        <div class="flex-1 overflow-y-auto py-3 flex flex-col">
          {navItems.map(item => (
            <NavLink
              key={item.path}
              to={item.path}
              class={({ isActive }) =>
                `block w-full text-center px-4 py-5 mb-2 rounded-lg text-base transition-colors ${
                  isActive
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                }`
              }
            >
              <div class="flex flex-col items-center justify-center">
                <span class="text-2xl mb-2">{item.icon}</span>
                {!collapsed && <span class="text-sm font-medium">{item.label}</span>}
              </div>
            </NavLink>
          ))}
        </div>

        {/* 用户信息 */}
        <div class="border-t border-slate-700 p-4">
          {!collapsed && user && (
            <div class="mb-3">
              <div class="text-sm text-white font-medium">{user.username}</div>
              <div class="text-xs text-slate-400">角色: {user.role}</div>
            </div>
          )}
          <button
            onClick={() => setCollapsed(!collapsed)}
            class="w-full text-slate-400 hover:text-white text-sm py-1"
          >
            {collapsed ? '▶' : '◀ 收起'}
          </button>
          {!collapsed && (
            <button
              onClick={logout}
              class="w-full mt-2 bg-red-600 hover:bg-red-700 text-white text-sm py-2 rounded"
            >
              退出登录
            </button>
          )}
        </div>
      </aside>

      {/* 主内容区 */}
      <main class="flex-1 overflow-auto">
        {children}
      </main>
    </div>
  )
}

function App() {
  const token = useStore(s => s.token)
  const authExpired = useStore(s => s.authExpired)
  const clearAuthExpired = useStore(s => s.clearAuthExpired)
  const navigate = useNavigate()

  // M-11: 监听 authExpired 软跳转标志，替代 window.location.href 硬跳转
  useEffect(() => {
    if (authExpired) {
      clearAuthExpired()
      navigate('/login', { replace: true })
    }
  }, [authExpired, clearAuthExpired, navigate])

  if (!token) {
    return <Suspense fallback={<PageLoading />}><LoginPage /></Suspense>
  }

  return (
    <Layout>
      <Suspense fallback={<PageLoading />}>
        <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/knowledge" element={<KnowledgePage />} />
        <Route path="/models" element={<ModelsPage />} />           {/* P1 */}
        <Route path="/evolve" element={<EvolvePage />} />         {/* P1 */}
        <Route path="/evolution" element={<EvolutionSystemPage />} />  {/* Phase 1-3 */}
        <Route path="/monitoring" element={<MonitoringPage />} />  {/* P1 */}
        <Route path="/users" element={<UsersPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  )
}

export default App
