/**
 * EchoServe — 通用指标卡片组件
 * 统一 Dashboard / Monitoring / EvolutionSystem 三处重复定义的 MetricCard
 *
 * Props:
 *   title    卡片标题（与 label 等效，向后兼容）
 *   label    同 title（Dashboard 旧版命名）
 *   value    主数值
 *   sub      副文本（可选）
 *   color    数值颜色类名（如 "text-blue-600"）
 *   children 自定义内容（优先于 value）
 */
export default function MetricCard({ title, label, value, sub, color, icon, bgClass, children }) {
  const displayTitle = title || label
  return (
    <div className={`${bgClass || 'bg-white'} rounded-xl shadow-sm border border-gray-100 p-4 flex flex-col gap-1`}>
      <div className="flex items-center gap-2">
        {icon && <span className="text-lg">{icon}</span>}
        <span className="text-xs text-gray-500">{displayTitle}</span>
      </div>
      {children != null ? (
        <span className={`text-2xl font-bold ${color || 'text-gray-900'}`}>{children}</span>
      ) : (
        <span className={`text-2xl font-bold ${color || 'text-gray-900'}`}>{value ?? '-'}</span>
      )}
      {sub && <span className="text-xs text-gray-400">{sub}</span>}
    </div>
  )
}
