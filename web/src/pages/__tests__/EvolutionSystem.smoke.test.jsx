import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'

// Mock store before importing component
const mockApiCall = vi.fn()
vi.mock('../../store', () => ({
  useStore: vi.fn(() => ({
    token: 'fake-token',
    user: { username: 'admin', role: 'admin' },
    authExpired: false,
  })),
  apiCall: (...args) => mockApiCall(...args),
}))

import EvolutionSystemPage from '../EvolutionSystem.jsx'

const mockOverview = {
  store: { total_records: 5000, records_by_table: { chat_log: 3000, feedback: 2000 } },
  collector: { total_records: 1000 },
  experiments: { total: 1, active: 1 },
  patterns: { total: 3 },
  templates: { total: 2, active: 1 },
  failover: { current_level: 'normal', rules_count: 5 },
  config: { mining_min_success_rate: 0.9, mining_min_support: 10 },
}

const mockExperiments = { total: 1, experiments: [] }
const mockPatterns = { total: 3, patterns: [] }
const mockTemplates = { total: 2, templates: [], summary: {} }
const mockFailover = { current_level: 'normal', rules_count: 5, history_count: 0, history: [] }

describe('EvolutionSystemPage (Smoke)', () => {
  beforeEach(() => {
    mockApiCall.mockImplementation((url) => {
      const map = {
        '/evolution/overview': mockOverview,
        '/evolution/experiments': mockExperiments,
        '/evolution/patterns': mockPatterns,
        '/evolution/templates': mockTemplates,
        '/evolution/failover': mockFailover,
      }
      return Promise.resolve(map[url] || {})
    })
  })

  it('renders loading state then main content', async () => {
    render(<EvolutionSystemPage />)

    // Initial loading
    expect(screen.getByText(/加载进化系统数据中/)).toBeInTheDocument()

    // After data loads
    await waitFor(() => {
      expect(screen.getByText('🧬 进化系统')).toBeInTheDocument()
    })

    // Verify title and subtitle
    expect(screen.getByText(/Phase 1-3/)).toBeInTheDocument()
  })

  it('renders metric cards with overview data', async () => {
    render(<EvolutionSystemPage />)

    await waitFor(() => {
      expect(screen.getByText('🧬 进化系统')).toBeInTheDocument()
    })

    // Cards rendered from mock overview data
    expect(screen.getByText('活跃实验')).toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByText('挖掘模式')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    // Table-level record cards
    expect(screen.getByText('对话日志')).toBeInTheDocument()
    expect(screen.getByText('3000')).toBeInTheDocument()
    expect(screen.getByText('用户反馈')).toBeInTheDocument()
    expect(screen.getByText('2000')).toBeInTheDocument()
  })

  it('renders all five tabs', async () => {
    render(<EvolutionSystemPage />)

    await waitFor(() => {
      expect(screen.getByText('🧬 进化系统')).toBeInTheDocument()
    })

    // Tab buttons include emoji prefixes, use role=button to distinguish from subtitle
    expect(screen.getByRole('button', { name: /总览/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /A\/B 实验/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /模式挖掘/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /模板注册表/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /降级容错/ })).toBeInTheDocument()
  })

  it('renders refresh button and auto-refresh toggle', async () => {
    render(<EvolutionSystemPage />)

    await waitFor(() => {
      expect(screen.getByText('🧬 进化系统')).toBeInTheDocument()
    })

    expect(screen.getByText('🔄 刷新')).toBeInTheDocument()
    expect(screen.getByText('自动刷新')).toBeInTheDocument()
  })
})
