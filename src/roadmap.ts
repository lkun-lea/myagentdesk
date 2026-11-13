import snapshot from './github-issues.json'

export const roadmapIssues = snapshot.issues
export const snapshotDate = snapshot.date
export const issuesByNumber = new Map(roadmapIssues.map(issue => [issue.number, issue]))
export const mainPath = [2, 3, 6, 16, 14, 11]
export const followUp = [8, 12, 13, 15]
export const phaseLabels = ['基础', '排期', '执行', '交付']

type RoadmapItem = { label: string; phase: number; prerequisites: number[]; integration: number[] }
export const roadmap: Partial<Record<number, RoadmapItem>> = {
  2: { label: '运行基座', phase: 0, prerequisites: [], integration: [] },
  3: { label: '账号与成员', phase: 0, prerequisites: [2], integration: [] },
  4: { label: '项目导航', phase: 0, prerequisites: [2], integration: [3] },
  5: { label: '个人 Agent', phase: 1, prerequisites: [2, 3], integration: [4] },
  6: { label: '需求阶段', phase: 1, prerequisites: [2, 3], integration: [4, 5] },
  7: { label: '人工决策续跑', phase: 2, prerequisites: [5, 6], integration: [] },
  8: { label: '跨项目统筹', phase: 4, prerequisites: [6, 16], integration: [7, 4] },
  9: { label: 'Issues 只读关联', phase: 1, prerequisites: [3], integration: [4, 6] },
  10: { label: 'OpenSpec 规格', phase: 1, prerequisites: [6], integration: [4, 5] },
  11: { label: '独立验收与发布', phase: 3, prerequisites: [5, 6, 7], integration: [9, 10, 14, 16] },
  12: { label: 'Skill 导入', phase: 4, prerequisites: [5], integration: [] },
  13: { label: 'Skill 设计', phase: 4, prerequisites: [12, 5], integration: [] },
  14: { label: 'tebb 本机派发', phase: 2, prerequisites: [3, 6], integration: [11, 16] },
  15: { label: 'Codex 编码执行器', phase: 4, prerequisites: [14], integration: [11] },
  16: { label: '版本计划', phase: 1, prerequisites: [3, 6], integration: [4, 11] },
}

export const issueLabel = (number: number) => roadmap[number]?.label ?? '首版路线图'
export const openPrerequisites = (number: number) =>
  roadmap[number]?.prerequisites.filter(id => issuesByNumber.get(id)?.state === 'OPEN') ?? []
export const downstreamIssues = (number: number) =>
  roadmapIssues.filter(issue => roadmap[issue.number]?.prerequisites.includes(number)).map(issue => issue.number)
