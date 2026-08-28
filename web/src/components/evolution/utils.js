/**
 * EchoServe 进化系统 — 共享工具函数
 * M-14: fmt() 对 NaN 做防御，返回 '-' 而非原始值
 */

export const fmt = (val, digits = 1) => {
  if (val === undefined || val === null) return '-'
  const n = Number(val)
  if (isNaN(n)) return '-'
  return n.toFixed(digits)
}

export const fmtBytes = (bytes) => {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`
}

export const fmtTime = (ts) => {
  if (!ts) return '-'
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch {
    return String(ts)
  }
}
