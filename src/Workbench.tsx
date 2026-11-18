import { useEffect, useState } from 'react'
import { Alert, Button, Modal, Select, Spin, Tag, Timeline } from 'antd'
import {
  AppstoreOutlined, CalendarOutlined, CheckSquareOutlined, ClockCircleOutlined,
  FileTextOutlined, FolderOpenOutlined, GithubOutlined, MessageOutlined, RobotOutlined, QuestionCircleOutlined,
  RocketOutlined, SafetyCertificateOutlined, StarFilled, StarOutlined,
} from '@ant-design/icons'
import { api, errorCode, messageFor } from './accounts-api'

export const projectPages = {
  timeline: '时间线', requirements: '需求', specs: '规格', materials: '资料',
  issues: 'Issues', build: '构建与发布', members: '成员',
} as const
export type ProjectPage = keyof typeof projectPages
export type View = 'workbench' | 'chat' | 'projects' | 'calendar' | 'tasks' | 'agent' | 'decisions'
export type OpenRoute = (view: View, params?: Record<string, string>, replace?: boolean) => void

export type Project = {
  id: string; workspace_id: string; workspace_name?: string; name: string; role: 'owner' | 'admin' | 'member'
}

export const globalViews = [
  { id: 'chat', label: '聊天', icon: <MessageOutlined /> },
  { id: 'projects', label: '项目', icon: <FolderOpenOutlined /> },
  { id: 'calendar', label: '日程', icon: <CalendarOutlined /> },
  { id: 'tasks', label: '事项', icon: <CheckSquareOutlined /> },
  { id: 'workbench', label: '工作台', icon: <AppstoreOutlined /> },
  { id: 'agent', label: '我的 Agent', icon: <RobotOutlined /> },
  { id: 'decisions', label: '待我决策', icon: <QuestionCircleOutlined /> },
] as const

const icons: Record<string, React.ComponentType> = {
  calendar: CalendarOutlined, check: CheckSquareOutlined, requirements: MessageOutlined,
  specs: FileTextOutlined, build: RocketOutlined, evidence: SafetyCertificateOutlined,
  materials: FolderOpenOutlined, timeline: ClockCircleOutlined, github: GithubOutlined,
}

type Tool = {
  id: string; name: string; category: string; icon: string; description: string; version: string; enabled: boolean
} & ({ scope: 'account'; target: View } | { scope: 'project'; target: ProjectPage })
type Catalog = { tools: Tool[]; favorites: string[] }

export function Workbench({ projects, refresh, navigate }: {
  projects: Project[]; refresh: number; navigate: OpenRoute
}) {
  const [catalog, setCatalog] = useState<Catalog>()
  const [section, setSection] = useState('全部工具')
  const [error, setError] = useState('')
  const [request, setRequest] = useState(0)
  const [saving, setSaving] = useState('')
  const [chooser, setChooser] = useState<Tool>()
  const [selected, setSelected] = useState<string>()
  const visible = catalog?.tools.filter(tool => tool.scope === 'account' || projects.length > 0) ?? []
  const groups = ['全部工具', ...new Set(visible.map(tool => tool.category))]
  const selectedGroup = groups.includes(section) ? section : groups[0]

  useEffect(() => {
    let current = true
    api<Catalog>('/workbench').then(value => {
      if (current) setCatalog(value)
    }).catch(failure => {
      if (current) { setCatalog(undefined); setError(messageFor(errorCode(failure))) }
    })
    return () => { current = false }
  }, [request, refresh])

  async function toggle(tool: Tool, saved: boolean) {
    setSaving(tool.id)
    setError('')
    try {
      await api('/workbench/favorites/' + tool.id, 'PUT', { saved })
      setCatalog(current => current && ({
        ...current,
        favorites: saved ? [...new Set([...current.favorites, tool.id])] : current.favorites.filter(id => id !== tool.id),
      }))
    } catch (failure) {
      setError(messageFor(errorCode(failure)))
    } finally {
      setSaving('')
    }
  }

  function open(tool: Tool, project?: string) {
    if (!tool.enabled) return
    if (tool.scope === 'account') { navigate(tool.target); return }
    const target = project ?? (projects.length === 1 ? projects[0].id : undefined)
    if (target && projects.some(item => item.id === target)) {
      setChooser(undefined)
      navigate('projects', { project: target, page: tool.target })
    } else {
      setSelected(undefined)
      setChooser(tool)
    }
  }

  function card(tool: Tool) {
    const Icon = icons[tool.icon] ?? AppstoreOutlined
    const saved = catalog?.favorites.includes(tool.id) ?? false
    const starLabel = `${saved ? '取消收藏' : '收藏'}${tool.name}`
    return <article className="mad-tool" key={tool.id}>
      <button type="button" className="mad-tool-launch" title={`${tool.name} · 版本 ${tool.version}`}
        disabled={!tool.enabled} onClick={() => open(tool)}>
        <span className="mad-tool-icon" aria-hidden="true"><Icon /></span>
        <span className="mad-tool-copy"><strong>{tool.name}</strong>
          <small>{tool.enabled ? tool.description : '工具已停用'}</small></span>
      </button>
      <Button type="text" size="small" icon={saved ? <StarFilled /> : <StarOutlined />} aria-label={starLabel}
        title={starLabel} aria-pressed={saved} loading={saving === tool.id}
        disabled={!!saving || (!tool.enabled && !saved)} onClick={() => void toggle(tool, !saved)} />
    </article>
  }

  const favorites = visible.filter(tool => catalog?.favorites.includes(tool.id))
  return <section className="mad-page mad-workbench">
    <h1>工作台</h1>
    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => {
      setError(''); setRequest(value => value + 1)
    }}>重试</Button>} />}
    {!catalog && !error && <Spin aria-label="正在加载工具" />}
    {catalog && <>
      <section aria-label="常用工具">
        <h2>常用工具</h2>
        {favorites.length ? <div className="mad-tool-grid">{favorites.map(card)}</div>
          : <p className="account-muted">点工具旁的星标，将它加入常用。</p>}
      </section>
      <section aria-label="工具目录">
        <h2>工具目录</h2>
        <nav className="mad-categories" aria-label="工具分类">
          {groups.map(group => <Button key={group} type="text" aria-pressed={group === selectedGroup}
            onClick={() => setSection(group)}>{group}</Button>)}
        </nav>
        <div className="mad-tool-grid">
          {visible.filter(tool => selectedGroup === '全部工具' || tool.category === selectedGroup).map(card)}
        </div>
      </section>
    </>}
    <Modal title={chooser?.name} open={!!chooser} onCancel={() => setChooser(undefined)} okText="打开项目工具"
      okButtonProps={{ disabled: !projects.some(project => project.id === selected) }}
      onOk={() => { if (chooser && selected) open(chooser, selected) }}>
      <label htmlFor="workbench-project-picker">选择项目</label>
      <Select id="workbench-project-picker" className="mad-project-picker" aria-label="选择项目" value={selected}
        onChange={setSelected} options={projects.map(project => ({ value: project.id, label: project.name }))} />
    </Modal>
  </section>
}

const waitingPages: Partial<Record<ProjectPage, [number, string]>> = {
  requirements: [6, '需求与阶段记录尚未接入。'], specs: [10, 'OpenSpec 变更和校验尚未接入。'],
  materials: [6, '项目资料目录尚未接入。'],
  build: [11, '验收证据、构建和发布回执尚未接入。'],
}

export function ProjectContent({ project, page, onUnavailable, navigate }: {
  project: Project; page: ProjectPage; onUnavailable: (code: string) => void; navigate: OpenRoute
}) {
  const [events, setEvents] = useState<{ id: number; occurred_at: string; action: string; target: string }[]>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (page !== 'timeline') return
    let active = true
    api<{ id: number; occurred_at: string; action: string; target: string }[]>(`/projects/${project.id}/timeline`).then(items => {
      if (active) setEvents(items)
    }).catch(failure => {
      if (!active) return
      const code = errorCode(failure)
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
      else setError(messageFor(code))
    })
    return () => { active = false }
  }, [project.id, page, attempt, onUnavailable])

  if (page === 'timeline') return <section className="mad-project-content">
    <h2>时间线</h2>
    {error ? <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => {
      setError(''); setAttempt(value => value + 1)
    }}>重试</Button>} /> : !events ? <Spin aria-label="正在加载时间线" /> : events.length
      ? <Timeline items={events.map(event => ({ key: event.id, children: <><strong>{({ project_created: '创建项目',
        requirement_group_created: '需求已立项建群', requirement_stage_changed: '需求阶段已流转', requirement_confirmed: '需求版本已确认',
        context_published: '项目资料已发布', github_issue_linked: '已关联 GitHub Issue',
        version_planned: '创建版本计划', version_revised: '修改版本计划' } as Record<string,string>)[event.action] ?? event.action}</strong>
        {['requirement_group_created', 'requirement_stage_changed'].includes(event.action) && <Button type="link"
          onClick={() => navigate('chat', { project: project.id, group: event.target })}>查看需求群</Button>}
        {event.action === 'github_issue_linked' && <Button type="link"
          onClick={() => navigate('projects', { project: project.id, page: 'requirements', requirement: event.target })}>查看关联需求</Button>}
        {['version_planned', 'version_revised'].includes(event.action) && <Button type="link" onClick={() => {
          const [plan, planRevision] = event.target.split(':')
          navigate('projects', { project: project.id, page: 'timeline', plan, planRevision })
        }}>查看计划修订</Button>}
        <p className="account-muted"><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString()}</time></p></> }))} />
      : <p className="account-muted">暂无项目事件。</p>}
  </section>

  const pending = waitingPages[page]
  return pending && <section className="mad-project-content">
    <h2>{projectPages[page]}</h2><p className="account-muted">{pending[1]}</p>
    <Tag><a href={`https://github.com/lkun-lea/myagentdesk/issues/${pending[0]}`} target="_blank" rel="noreferrer">
      查看实施进度 #{pending[0]}</a></Tag>
  </section>
}
