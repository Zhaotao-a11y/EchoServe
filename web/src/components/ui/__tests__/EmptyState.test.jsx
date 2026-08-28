import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import EmptyState from '../EmptyState.jsx'

describe('EmptyState', () => {
  it('renders default icon and text', () => {
    render(<EmptyState />)
    expect(screen.getByText('📭')).toBeInTheDocument()
    expect(screen.getByText('暂无数据')).toBeInTheDocument()
  })

  it('renders custom icon and text', () => {
    render(<EmptyState icon="🔍" text="No results found" />)
    expect(screen.getByText('🔍')).toBeInTheDocument()
    expect(screen.getByText('No results found')).toBeInTheDocument()
  })
})
