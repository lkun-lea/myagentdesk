export type Milestone = { name: string; date: string }
export type VersionFields = {
  name: string
  owner_id: string
  target_date: string | null
  status: 'draft' | 'committed' | 'cancelled'
  requirement_ids: string[]
  milestones: Milestone[]
  required_checks: string[]
  reason: string
}
export type PlanRevision = VersionFields & { plan_id: string; revision: number; created_at: string; actor: string }
export type VersionPlan = VersionFields & {
  plan_id: string
  revision: number
  owner: string | null
  counts: Record<'accepted' | 'pending' | 'blocked', number>
  release_state: 'draft' | 'awaiting_build' | 'awaiting_release' | 'awaiting_acceptance' | 'cancelled' | 'published'
  actual_published_at: string | null
}
export type ScheduledRequirement = {
  id: string; title: string; revision: number; criteria: string[]; group_id: string | null; stage: string | null
  acceptance: 'accepted' | 'pending' | 'blocked'; blocker: string | null; owner: string; due_at: string | null
  sources: { issue_number: number; connection_revision: number }[]
}
export type PlanningData = {
  plans: VersionPlan[]; requirements: ScheduledRequirement[]; managers: { id: string; email: string }[]; unscheduled_ids: string[]
}

export const planStates = { draft: '计划中', awaiting_build: '等待构建放行', awaiting_release: '等待发布回执', awaiting_acceptance: '等待阶段验收', cancelled: '已取消', published: '已发布' }
export const acceptanceStates = { accepted: '已验收', pending: '待验收', blocked: '阻塞' }
export const requirementStages: Record<string, string> = { product: '产品确认', development: '实现与自测', testing: '独立验收', quality: '质量复核', completed: '阶段完成' }
export const blockers: Record<string, { label: string; next: string }> = {
  waiting_decision: { label: '等待本人决策', next: '由阶段负责人在需求群核对并确认' },
  waiting_authorization: { label: '等待授权', next: '由项目管理员核对产物目录权限' },
  awaiting_takeover: { label: '等待替补接手', next: '由替补核对交接和原检查点' },
  owner_missing: { label: '未分配有效负责人', next: '由项目管理员核对当前岗位与成员' },
  failed: { label: '阶段运行失败', next: '查看运行结果并从检查点继续' },
  cancelled: { label: '阶段运行已取消', next: '由阶段负责人决定是否续跑' },
  stale: { label: '版本待核对', next: '核对当前需求和资料版本后续跑' },
  evidence_outdated: { label: '验收证据已失效', next: '按当前版本重新独立验收' },
}
