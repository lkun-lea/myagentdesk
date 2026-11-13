export const issue = {
  number: 313,
  title: '移动端接入：手机 Web/PWA 与 Windows 云任务接续',
  url: 'https://github.com/lkun-lea/tebb/issues/313',
  demoScope: '重连后回到同一个任务，并且不重复启动。',
  criteria: ['断开连接并重连后，打开原任务 ID。', '重复提交同一回执，原任务只启动一次。'],
}

export const people = {
  owner: { name: '林安', role: '项目负责人', color: 'blue' },
  product: { name: '陈舟', role: '产品', color: 'purple' },
  dev: { name: '顾言', role: '研发', color: 'geekblue' },
  qa: { name: '周宁', role: '独立测试', color: 'cyan' },
  quality: { name: '许然', role: '质量负责人', color: 'gold' },
  release: { name: '沈曦', role: '发布负责人', color: 'green' },
} as const

export type Role = keyof typeof people
type DispatchStatus = 'running' | 'unknown' | 'decision' | 'upload'
type PlanRevision = { date: string; reason: string; previous?: string }
type Dispatch = { id: string; status: DispatchStatus; decision?: 'resume-original' }
type Evidence = { candidate: number; reviewer: 'qa' }
type ReleaseReceipt = { id: string; candidate: number; revision: number; date: string; channel: string }
export type Entry = { label: string; detail: string; actor: Role }

export type DemoState = {
  plans: PlanRevision[]
  spec: boolean
  dispatch: Dispatch | null
  candidate: number
  evidence: Evidence[]
  approvedCandidate: number | null
  releasePending: boolean
  receipt: ReleaseReceipt | null
  events: Entry[]
}

export const fresh = (): DemoState => ({
  plans: [], spec: false, dispatch: null, candidate: 0, evidence: [],
  approvedCandidate: null, releasePending: false, receipt: null, events: [],
})

export type Action =
  | { kind: 'schedule'; date: string; reason: string }
  | { kind: 'spec' | 'dispatch' | 'disconnect' | 'recover' | 'requestDecision' | 'resolveDecision' | 'finish' | 'upload' | 'replay' | 'verify' | 'rebuild' | 'approve' | 'release' | 'receipt' }

export const requiredRole = {
  schedule: 'owner', spec: 'product', dispatch: 'dev', disconnect: 'dev', recover: 'dev',
  requestDecision: 'dev', resolveDecision: 'product',
  finish: 'dev', upload: 'dev', replay: 'dev', verify: 'qa', rebuild: 'dev',
  approve: 'quality', release: 'release', receipt: 'release',
} as const satisfies Record<Action['kind'], Role>

export const version = 'v0.1.0'
export const minDate = '2026-11-18'
export const suggestedDate = '2026-11-20'
export const dispatchId = 'DSP-0313-01'
export const specId = 'reconnect-task · 验收 v1'
export const currentPlan = (state: DemoState) => state.plans.at(-1)
export const verified = (state: DemoState) => state.evidence.some(item => item.candidate === state.candidate)
export const approved = (state: DemoState) => state.candidate > 0 && state.approvedCandidate === state.candidate
export const buildId = (candidate: number) => `CI-${460 + candidate}`
export const commitId = (candidate: number) => candidate === 1 ? 'e81c742' : 'a4f295d'
export const digest = (candidate: number) => candidate === 1 ? '7af0e51f…d1038c' : 'ae44182d…e62950'

export function currentStep(state: DemoState): number {
  if (!currentPlan(state)) return 0
  if (!state.spec) return 1
  if (!state.candidate) return 2
  if (!verified(state)) return 3
  if (!approved(state)) return 4
  return 5
}

export function nextAction(state: DemoState): Action['kind'] | null {
  if (state.receipt) return null
  if (!currentPlan(state)) return 'schedule'
  if (!state.spec) return 'spec'
  if (!state.candidate) {
    if (!state.dispatch) return 'dispatch'
    if (state.dispatch.status === 'unknown') return 'recover'
    if (state.dispatch.status === 'decision') return 'resolveDecision'
    return state.dispatch.status === 'upload' ? 'upload' : 'finish'
  }
  if (!verified(state)) return 'verify'
  if (!approved(state)) return 'approve'
  return state.releasePending ? 'receipt' : 'release'
}

function success(state: DemoState, patch: Partial<DemoState>, actor: Role, label: string, detail: string) {
  return { state: { ...state, ...patch, events: [...state.events, { actor, label, detail }] } }
}

export function applyAction(state: DemoState, actor: Role, action: Action): { state: DemoState; error?: string; info?: string } {
  if (requiredRole[action.kind] !== actor) return { state, error: '当前成员没有执行此操作的权限。' }
  if (action.kind === 'replay' && state.candidate) return { state, info: '已返回原回执。启动、候选和完成记录仍各一条。' }
  if (action.kind === 'receipt' && state.receipt) return { state, info: '已返回原发布回执。' }
  if (state.receipt) return { state, error: '此版本的演示已发布。' }
  const plan = currentPlan(state)
  switch (action.kind) {
    case 'schedule': {
      const reason = action.reason.trim()
      const parsed = Date.parse(action.date)
      const validDate = Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === action.date
      if (state.releasePending || !validDate || action.date < minDate || !reason || plan?.date === action.date) break
      const revision = { date: action.date, previous: plan?.date, reason }
      const detail = plan ? `${plan.date} → ${action.date} · ${reason}` : `${action.date} · ${reason}`
      return success(state, { plans: [...state.plans, revision] }, actor, plan ? '调整版本排期' : '纳入版本计划', detail)
    }
    case 'spec':
      if (!plan || state.spec) break
      return success(state, { spec: true }, actor, '确认验收条件', specId)
    case 'dispatch':
      if (!state.spec || state.dispatch) break
      return success(state, { dispatch: { id: dispatchId, status: 'running' } }, actor, 'tebb 已领取', `${dispatchId} · 设备 DEV-01 · 启动 1 次`)
    case 'disconnect':
      if (state.dispatch?.status !== 'running' || state.candidate) break
      return success(state, { dispatch: { ...state.dispatch, status: 'unknown' } }, actor, '启动结果待核对', `${dispatchId} · 回执缺失 · 等待原设备`)
    case 'recover':
      if (state.dispatch?.status !== 'unknown') break
      return success(state, { dispatch: { ...state.dispatch, status: 'running' } }, actor, '找回原运行', `${dispatchId} · 设备 DEV-01 · 启动 1 次`)
    case 'requestDecision':
      if (state.dispatch?.status !== 'running' || state.dispatch.decision || state.candidate) break
      return success(state, { dispatch: { ...state.dispatch, status: 'decision' } }, actor,
        '暂停等待产品确认', `${dispatchId} · 检查点 CP-02 · 确认连接结果未知时的处理规则`)
    case 'resolveDecision':
      if (state.dispatch?.status !== 'decision') break
      return success(state, { dispatch: { ...state.dispatch, status: 'running', decision: 'resume-original' } }, actor,
        '产品确认并续跑原任务', `${dispatchId} · 检查点 CP-02 · 核对原请求，保持原任务 ID`)
    case 'finish':
      if (state.dispatch?.status !== 'running' || state.candidate) break
      return success(state, { dispatch: { ...state.dispatch, status: 'upload' } }, actor, '本机完成，等待回传', `${dispatchId} · 产物和命令记录待补`)
    case 'upload':
      if (state.dispatch?.status !== 'upload' || state.candidate) break
      return success(state, { candidate: 1 }, actor, '候选与回执已入档', `${dispatchId} · 候选 1 · 完成通知 1 条`)
    case 'verify':
      if (!state.candidate || verified(state)) break
      return success(state, { evidence: [...state.evidence, { candidate: state.candidate, reviewer: 'qa' }] }, actor,
        '独立验收通过', `候选 ${state.candidate} · ${buildId(state.candidate)} · 2 项必过条件通过`)
    case 'rebuild':
      if (state.candidate !== 1 || !verified(state) || state.releasePending) break
      return success(state, { candidate: 2, approvedCandidate: null }, actor, '集成包变更，等待重新验收', `${buildId(1)} → ${buildId(2)} · 旧证据进入历史`)
    case 'approve':
      if (!verified(state) || approved(state)) break
      return success(state, { approvedCandidate: state.candidate }, actor, '质量放行', `${buildId(state.candidate)} · ${commitId(state.candidate)} · ${digest(state.candidate)}`)
    case 'release':
      if (!approved(state) || state.releasePending) break
      return success(state, { releasePending: true }, actor, '提交模拟发布', `${buildId(state.candidate)} · Windows 稳定版 · 等待渠道回执`)
    case 'receipt': {
      if (!plan || !state.releasePending || !approved(state)) break
      const date = new Date(Date.parse(plan.date) + 86400000).toISOString().slice(0, 10)
      const receipt = { id: 'REL-010', candidate: state.candidate, revision: state.plans.length, date, channel: 'Windows 稳定版' }
      return success(state, { receipt, releasePending: false }, actor, '收到模拟发布回执', `${receipt.id} · ${receipt.channel} · ${date}`)
    }
    case 'replay':
      break
  }
  return { state, error: '当前记录尚未满足操作条件。' }
}

const storageKey = 'myagentdesk-first-demo-v1'

export function loadDemo(): DemoState {
  try {
    const raw = localStorage.getItem(storageKey)
    return raw ? JSON.parse(raw) as DemoState : fresh()
  } catch {
    return fresh()
  }
}

export function saveDemo(state: DemoState) {
  try {
    localStorage.setItem(storageKey, JSON.stringify(state))
  } catch {
    // The session still works when the browser disables local storage.
  }
}
