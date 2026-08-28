/**
 * EchoServe 进化系统 — 共享常量映射
 */

export const degradeLevelMap = {
  normal: { label: '正常', color: 'bg-green-100 text-green-700 border-green-300' },
  level_1: { label: 'L1 降级', color: 'bg-yellow-100 text-yellow-700 border-yellow-300' },
  level_2: { label: 'L2 降级', color: 'bg-orange-100 text-orange-700 border-orange-300' },
  level_3: { label: 'L3 只读', color: 'bg-red-100 text-red-700 border-red-300' },
}

export const expStatusMap = {
  pending: { label: '待启动', color: 'bg-slate-100 text-slate-600' },
  running: { label: '运行中', color: 'bg-blue-100 text-blue-700' },
  converged: { label: '已收敛', color: 'bg-green-100 text-green-700' },
  failed: { label: '失败', color: 'bg-red-100 text-red-700' },
  paused: { label: '已暂停', color: 'bg-yellow-100 text-yellow-700' },
  approved: { label: '已批准', color: 'bg-emerald-100 text-emerald-700' },
  rejected: { label: '已拒绝', color: 'bg-red-100 text-red-700' },
}

export const tplStatusMap = {
  draft: { label: '草稿', color: 'bg-slate-100 text-slate-600' },
  pending_review: { label: '待审核', color: 'bg-yellow-100 text-yellow-700' },
  approved: { label: '已批准', color: 'bg-emerald-100 text-emerald-700' },
  canary: { label: '灰度中', color: 'bg-blue-100 text-blue-700' },
  active: { label: '已上线', color: 'bg-green-100 text-green-700' },
  disabled: { label: '已禁用', color: 'bg-slate-100 text-slate-500' },
  rolled_back: { label: '已回滚', color: 'bg-red-100 text-red-700' },
}
