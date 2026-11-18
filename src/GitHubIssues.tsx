import { useEffect, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tag } from 'antd'
import { api, ApiError, errorCode, messageFor } from './accounts-api'
import type { OpenRoute, Project } from './Workbench'

type Connection = { connected: boolean; revision: number }
type LinkedRequirement = { requirement_id: string; title: string; revision: number; linked_revision: number; stage: string | null }
type Issue = { repository: string; number: number; title: string; body: string; body_truncated: boolean; state: 'open' | 'closed';
  state_reason: string | null; url: string; checked_at: string; connection_revision: number; linked: LinkedRequirement | null }
type Requirement = { id: string; title: string; revision: number }

const stages: Record<string, string> = { product: '产品确认', development: '实现与自测', testing: '独立验收', quality: '质量复核', completed: '已完成' }
const reasons: Record<string, string> = { completed: '已完成', not_planned: '不计划处理', duplicate: '重复' }
const issueRoute = (project: string, number: string, revision: number) => ({ project, page: 'issues', issue: number, sourceRevision: String(revision) })

export function GitHubIssues({ project, number, sourceRevision, navigate, onUnavailable }: {
  project: Project; number: string | null; sourceRevision: string | null; navigate: OpenRoute; onUnavailable: (code: string) => void
}) {
  const path = `/projects/${project.id}`
  const manager = project.role === 'admin' || project.role === 'owner'
  const [connection, setConnection] = useState<Connection>()
  const [repository, setRepository] = useState('')
  const [issueInput, setIssueInput] = useState(number ?? '')
  const [requirements, setRequirements] = useState<Requirement[]>([])
  const [lookup, setLookup] = useState<{ key: string; issue: Issue }>()
  const [target, setTarget] = useState<string>()
  const [drafting, setDrafting] = useState(false)
  const [draft, setDraft] = useState({ title: '', body: '', criteria: '' })
  const [failure, setFailure] = useState('')
  const [reading, setReading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const key = `${number}:${sourceRevision}:${refresh}`
  const issue = lookup?.key === key ? lookup.issue : undefined
  const rootRoute = { project: project.id, page: 'issues' }

  useEffect(() => {
    const recheck = () => setRefresh(value => value + 1)
    window.addEventListener('focus', recheck)
    window.addEventListener('online', recheck)
    window.addEventListener('offline', recheck)
    return () => {
      window.removeEventListener('focus', recheck)
      window.removeEventListener('online', recheck)
      window.removeEventListener('offline', recheck)
    }
  }, [])

  useEffect(() => {
    setIssueInput(number ?? '')
    setTarget(undefined)
    setDrafting(false)
    setDraft({ title: '', body: '', criteria: '' })
  }, [number, sourceRevision])

  useEffect(() => {
    let active = true
    setLookup(undefined)
    setFailure('')
    setReading(true)
    async function load() {
      const current = await api<Connection>(path + '/github/connection')
      if (!active) return
      setConnection(current)
      if (!number) return
      if (!current.connected) throw new ApiError('github_not_connected', 404)
      if (!/^[1-9]\d{0,9}$/.test(number) || Number(number) > 2147483647) throw new ApiError('github_invalid_number', 422)
      const revision = sourceRevision ?? String(current.revision)
      if (revision !== String(current.revision)) throw new ApiError('version_conflict', 409)
      const [item, options] = await Promise.all([
        api<Issue>(`${path}/github/issues/${number}?revision=${revision}`),
        manager ? api<Requirement[]>(path + '/requirements') : Promise.resolve([]),
      ])
      if (active) { setLookup({ key, issue: item }); setRequirements(options) }
    }
    void load().catch(error => {
      if (!active) return
      const code = errorCode(error)
      setFailure(messageFor(code))
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
    }).finally(() => { if (active) setReading(false) })
    return () => { active = false }
  }, [path, number, sourceRevision, key, manager, onUnavailable])

  async function act(action: () => Promise<void>) {
    setBusy(true)
    setFailure('')
    try { await action() }
    catch (error) {
      const code = errorCode(error)
      setFailure(messageFor(code))
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
      if (code !== 'invalid_request') setLookup(undefined)
    } finally { setBusy(false) }
  }

  async function associate(payload: object) {
    if (!issue) return
    await api(`${path}/github/issues/${issue.number}/link`, 'POST', { ...payload, connection_revision: issue.connection_revision })
    setDrafting(false)
    setRefresh(value => value + 1)
  }

  return <section className="mad-page">
    <div className="mad-page-heading"><h2>GitHub Issues</h2>
      <Button onClick={() => setRefresh(value => value + 1)} disabled={reading || busy}>刷新 GitHub 来源</Button></div>
    {failure && <Alert type="error" showIcon message={failure} />}
    {reading && <Spin aria-label="正在核验 GitHub 来源" />}
    {connection && <>
      <p className="account-muted">{connection.connected ? '已连接公开仓库 · 按编号读取 · GitHub 只读' : '项目尚未连接公开 GitHub 仓库。'}</p>
      {manager && <details open={!connection.connected || undefined}>
        <summary>仓库连接设置</summary>
        <form className="account-actions" onSubmit={event => {
          event.preventDefault()
          void act(async () => {
            const updated = await api<Connection>(path + '/github/connection', 'PUT', { repository, expected_revision: connection.revision })
            setConnection(updated)
            setRepository('')
            navigate('projects', rootRoute)
            setRefresh(value => value + 1)
          })
        }}>
          <Input aria-label="GitHub 仓库" placeholder="组织或用户 / 仓库" maxLength={140} required value={repository} onChange={event => setRepository(event.target.value)} />
          <Button htmlType="submit" disabled={busy || reading || !repository.trim()}>{connection.connected ? '更换仓库' : '连接公开仓库'}</Button>
          {connection.connected && <Button disabled={busy || reading} onClick={() => void act(async () => {
            const updated = await api<Connection>(path + '/github/disconnect', 'POST', { expected_revision: connection.revision })
            setConnection(updated)
            navigate('projects', rootRoute)
            setRefresh(value => value + 1)
          })}>断开连接</Button>}
        </form>
      </details>}
      {connection.connected && <form className="account-actions" onSubmit={event => {
        event.preventDefault()
        const value = issueInput.trim().replace(/^#/, '')
        if (!/^[1-9]\d{0,9}$/.test(value) || Number(value) > 2147483647) { setFailure(messageFor('github_invalid_number')); return }
        navigate('projects', issueRoute(project.id, value, connection.revision))
        setRefresh(previous => previous + 1)
      }}>
        <Input aria-label="Issue 编号" placeholder="Issue 编号，例如 9" value={issueInput} maxLength={11} onChange={event => setIssueInput(event.target.value)} />
        <Button type="primary" htmlType="submit" disabled={busy || reading || !issueInput.trim()}>查看 Issue</Button>
      </form>}
    </>}
    {issue && <article className="mad-context-card mad-github-issue" aria-label="Issue 详情">
      <div className="mad-page-heading"><span className="account-muted">{issue.repository} #{issue.number}</span>
        <Tag color={issue.state === 'open' ? 'green' : 'default'}>{issue.state === 'open' ? '开放' : '已关闭'}</Tag></div>
      <h3>{issue.title}</h3>
      <p className="account-muted">{issue.state === 'closed' && <>关闭原因 {reasons[issue.state_reason ?? ''] ?? '未提供'} · </>}
        最近核对 <time dateTime={issue.checked_at}>{new Date(issue.checked_at).toLocaleString()}</time></p>
      <a href={issue.url} target="_blank" rel="noreferrer">在 GitHub 查看或修改</a>
      <details><summary>Issue 正文</summary><pre>{issue.body || '无正文。'}</pre>{issue.body_truncated && <p>长正文请在 GitHub 查看。</p>}</details>
      {issue.linked ? <div>
        <p>已关联 {issue.linked.title} · 关联时 v{issue.linked.linked_revision} · 当前 v{issue.linked.revision}</p>
        <p>内部阶段 {stages[issue.linked.stage ?? ''] ?? '待建需求群'}</p>
        <Button onClick={() => navigate('projects', { project: project.id, page: 'requirements', requirement: issue.linked!.requirement_id })}>打开关联需求</Button>
      </div> : <>
        <p className="account-muted">尚未关联内部需求。</p>
        {manager && <div className="account-actions">
          <Select aria-label="关联已有需求" placeholder="选择已有需求" value={target} onChange={setTarget}
            options={requirements.map(item => ({ value: item.id, label: `${item.title} · v${item.revision}` }))} />
          <Button disabled={busy || !target} onClick={() => void act(() => associate({ requirement_id: target }))}>关联已有需求</Button>
          <Button disabled={busy} onClick={() => setDrafting(value => !value)}>{drafting ? '收起新需求' : '创建并关联需求'}</Button>
        </div>}
        {manager && drafting && <form className="mad-context-form" onSubmit={event => {
          event.preventDefault()
          void act(() => associate({ requirement: { title: draft.title.trim(), body: draft.body.trim(), criteria: draft.criteria.split('\n').map(line => line.trim()).filter(Boolean) } }))
        }}>
          <label>需求标题<Input required maxLength={120} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
          <label>需求说明<Input.TextArea required maxLength={12000} value={draft.body} onChange={event => setDraft({ ...draft, body: event.target.value })} /></label>
          <label>验收条件（每行一条）<Input.TextArea required value={draft.criteria} onChange={event => setDraft({ ...draft, criteria: event.target.value })} /></label>
          <Button type="primary" htmlType="submit" disabled={busy || !draft.title.trim() || !draft.body.trim() || !draft.criteria.trim()}>确认创建并关联</Button>
        </form>}
      </>}
    </article>}
  </section>
}
