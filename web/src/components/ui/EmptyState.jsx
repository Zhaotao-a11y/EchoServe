/**
 * EchoServe — 空状态占位组件
 * 统一 EvolutionSystem / Dashboard 等页面重复的 EmptyState 定义
 *
 * Props:
 *   icon     前缀图标（emoji 或文本）
 *   text     提示文本
 */
export default function EmptyState({ icon = '📭', text = '暂无数据' }) {
  return (
    <div className="text-center text-gray-400 py-8">
      <div className="text-3xl mb-2">{icon}</div>
      <p className="text-sm">{text}</p>
    </div>
  )
}
