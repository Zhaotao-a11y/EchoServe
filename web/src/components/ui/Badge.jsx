/**
 * EchoServe — 通用徽章组件
 * 统一 EvolutionSystem / Users 等页面重复的 Badge 定义
 *
 * Props:
 *   color    Tailwind 颜色类名（如 "bg-green-100 text-green-700"）
 *   children 徽章内容
 */
export default function Badge({ color = 'bg-gray-100 text-gray-600', children }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${color}`}>
      {children}
    </span>
  )
}
