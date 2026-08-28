import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import Badge from '../Badge.jsx'

describe('Badge', () => {
  it('renders children with default color', () => {
    render(<Badge>Active</Badge>)
    const el = screen.getByText('Active')
    expect(el).toBeInTheDocument()
    expect(el.className).toContain('bg-gray-100')
  })

  it('applies custom color class', () => {
    render(<Badge color="bg-green-100 text-green-700">Running</Badge>)
    const el = screen.getByText('Running')
    expect(el.className).toContain('bg-green-100')
    expect(el.className).toContain('text-green-700')
  })
})
