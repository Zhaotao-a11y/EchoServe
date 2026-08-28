import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import MetricCard from '../MetricCard.jsx'

describe('MetricCard', () => {
  it('renders title and value', () => {
    render(<MetricCard title="Total Calls" value={1234} />)
    expect(screen.getByText('Total Calls')).toBeInTheDocument()
    expect(screen.getByText('1234')).toBeInTheDocument()
  })

  it('renders label prop as title (backward compat)', () => {
    render(<MetricCard label="Latency" value="50ms" />)
    expect(screen.getByText('Latency')).toBeInTheDocument()
    expect(screen.getByText('50ms')).toBeInTheDocument()
  })

  it('renders children over value when provided', () => {
    render(<MetricCard title="Custom" value="ignored">child-content</MetricCard>)
    expect(screen.getByText('child-content')).toBeInTheDocument()
    expect(screen.queryByText('ignored')).not.toBeInTheDocument()
  })

  it('renders dash when no value and no children', () => {
    render(<MetricCard title="Empty" />)
    expect(screen.getByText('-')).toBeInTheDocument()
  })

  it('renders sub text and icon', () => {
    render(<MetricCard title="Rate" value="95%" sub="vs last week" icon="📈" />)
    expect(screen.getByText('95%')).toBeInTheDocument()
    expect(screen.getByText('vs last week')).toBeInTheDocument()
    expect(screen.getByText('📈')).toBeInTheDocument()
  })
})
