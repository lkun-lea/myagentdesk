import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tag } from 'antd'
import { api, errorCode, messageFor } from './accounts-api'
import type { OpenRoute, Project } from './Workbench'
import { RequirementGroups } from './RequirementGroups'

type Agent = { id: string; revision: number; model: string; skills: string[] }
type Configuration = { agent: Agent; models: string[]; skills: string[]; available: boolean }
type Requirement = { id: string; revision: number; title: string; body: string; criteria: string[] }
type Document = { id: string; revision: number; title: string; source: string; summary: string }
type Run = { id: string; requirement_id: string; requirement_revision: number; status: string; checkpoint: number;
  config?: { model: string; revision: number; skills: string[] };
  context_changed?: boolean; events?: { id: number; kind: string; data: unknown }[];
  references?: { document_id: string; revision: number; start_offset: number; end_offset: number; active: boolean }[] }
type Handoff = { run_id: string; role: string; summary: string; evidence: string[]; outdated: boolean; verdict: string }

const statuses: Record<string, string> = {
  queued: '待开始', running: '执行中', paused: '已暂停', stale: '上下文已变化', completed: '已交接', failed: '执行失败', cancelled: '已取消',
  waiting_decision: '等待本人决定', waiting_authorization: '已确认，等待授权',
}

export function MyAgent() {
  const [value, setValue] = useState<Configuration>()
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    api<Configuration>('/agent').then(item => { if (active) setValue(item) }).catch(error => {
      if (active) setFault(messageFor(errorCode(error)))
    })
    return () => { active = false }
  }, [reload])
  return <section className="mad-page"><h1>我的 Agent</h1>
    {fault && <Alert type="error" showIcon message={fault} action={<Button onClick={() => {
      setFault(''); setReload(number => number + 1)
    }}>重试</Button>} />}
    {!value && !fault && <Spin />}
    {value && <form className="mad-context-form" onSubmit={event => {
      event.preventDefault(); setBusy(true); setFault('')
      void api<Agent>('/agent', 'PUT', { ...value.agent, expected_revision: value.agent.revision })
        .then(agent => setValue({ ...value, agent })).catch(error => setFault(messageFor(errorCode(error))))
        .finally(() => setBusy(false))
    }}><p className="account-muted">Agent {value.agent.id} · 配置版本 {value.agent.revision}</p>
      {!value.available && <Alert type="warning" message="模型服务尚未配置。" />}
      <label>模型<Select aria-label="模型" value={value.agent.model || undefined} disabled={busy}
        options={value.models.map(model => ({ value: model, label: model }))}
        onChange={model => setValue({ ...value, agent: { ...value.agent, model } })} /></label>
      <label>Skill<Select mode="multiple" aria-label="Skill" value={value.agent.skills} disabled={busy}
        options={value.skills.map(skill => ({ value: skill, label: skill }))}
        onChange={skills => setValue({ ...value, agent: { ...value.agent, skills } })} /></label>
      <Button htmlType="submit" type="primary" loading={busy} disabled={!value.available}>保存配置</Button>
    </form>}
  </section>
}

export function ProjectContext({ project, page, userId, initialRequirement, navigate, onUnavailable }: {
  project: Project; page: 'requirements' | 'materials'; userId: string; initialRequirement?: string;
  navigate: OpenRoute; onUnavailable: (code: string) => void
}) {
  const path = '/projects/' + project.id
  const manager = project.role !== 'member'
  const [requirements, setRequirements] = useState<Requirement[]>([])
  const [documents, setDocuments] = useState<Document[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [handoffs, setHandoffs] = useState<Handoff[]>([])
  const [selected, setSelected] = useState<Requirement>()
  const [details, setDetails] = useState<Run>()
  const [original, setOriginal] = useState<{ title: string; source: string; revision: number; body: string }>()
  const [editing, setEditing] = useState<Requirement>({ id: crypto.randomUUID(), revision: 0, title: '', body: '', criteria: [''] })
  const [editingOpen, setEditingOpen] = useState(false)
  const [doc, setDoc] = useState<{ id: string; expected_revision: number; title: string; source: string; body: string }>({
    id: crypto.randomUUID(), expected_revision: 0, title: '', source: '', body: '' })
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const [ready, setReady] = useState(false)
  const [grouped, setGrouped] = useState(false)
  const [sources, setSources] = useState<{ requirement: string; links: { issue_number: number; connection_revision: number }[] }>()
  const pendingRun = useRef<{ id: string; requirement: string } | undefined>(undefined)

  useEffect(() => {
    let active = true
    Promise.all([api<Requirement[]>(path + '/requirements'), api<Document[]>(path + '/context'),
      api<Run[]>(path + '/runs'), api<Handoff[]>(path + '/handoffs')]).then(([req, docs, running, shared]) => {
      if (!active) return
      setRequirements(req); setDocuments(docs); setRuns(running); setHandoffs(shared); setReady(true)
      setSelected(previous => req.find(item => item.id === (previous?.id ?? initialRequirement)) ?? req[0])
    }).catch(error => {
      if (!active) return
      const code = errorCode(error)
      setRequirements([]); setDocuments([]); setRuns([]); setHandoffs([]); setDetails(undefined)
      setFault(messageFor(code)); setReady(true)
      if (code === 'login_required' || code === 'project_not_found') onUnavailable(code)
    })
    return () => { active = false }
  }, [path, reload, initialRequirement, onUnavailable])

  useEffect(() => {
    const requirement = selected?.id
    if (page !== 'requirements' || !requirement) return
    let active = true
    const read = () => {
      setSources(undefined)
      void api<{ issue_number: number; connection_revision: number }[]>(`${path}/github/requirements/${requirement}/links`)
        .then(links => { if (active) setSources({ requirement, links }) })
        .catch(error => {
          if (!active) return
          const code = errorCode(error)
          if (code === 'login_required' || code === 'project_not_found') onUnavailable(code)
        })
    }
    read()
    window.addEventListener('focus', read)
    return () => { active = false; window.removeEventListener('focus', read) }
  }, [path, page, selected?.id, reload, onUnavailable])

  useEffect(() => {
    if (!details) return
    let active = true
    const read = () => api<Run>('/runs/' + details.id).then(value => {
      if (active) setDetails(value)
    }).catch(error => {
      if (!active) return
      const code = errorCode(error)
      setDetails(undefined); setFault(messageFor(code))
      if (code === 'login_required' || code === 'project_not_found') onUnavailable(code)
    })
    void read()
    const timer = window.setInterval(read, 1500)
    return () => { active = false; window.clearInterval(timer) }
  }, [details?.id, onUnavailable])

  async function act(action: () => Promise<unknown>) {
    setBusy(true); setFault('')
    try { await action(); setReload(value => value + 1) }
    catch (error) { setFault(messageFor(errorCode(error))) }
    finally { setBusy(false) }
  }

  const latest = details && requirements.find(item => item.id === details.requirement_id)
  return <section className="mad-page">
    <div className="mad-page-heading"><h2>{page === 'materials' ? '项目资料' : '需求与阶段交接'}</h2>
      <Button onClick={() => setReload(value => value + 1)}>刷新记录</Button></div>
    {fault && <Alert type="error" showIcon message={fault} action={<Button onClick={() => {
      setFault(''); setReload(value => value + 1)
    }}>重新加载</Button>} />}
    {!ready && <Spin />}
    {page === 'materials' ? <>
      <ul className="account-list">{documents.map(item => <li key={item.id}>
        <span><strong>{item.title}</strong> · v{item.revision}<p>{item.summary}</p><small>{item.source}</small></span>
        <Button onClick={() => void act(async () => setOriginal(await api(path + `/context/${item.id}/${item.revision}`)))}>查看原文</Button>
        {manager && <Button onClick={() => setDoc({ id: item.id, expected_revision: item.revision,
          title: item.title, source: item.source, body: '' })}>发布新版本</Button>}
      </li>)}</ul>
      {!documents.length && ready && <p className="account-muted">暂无项目资料。</p>}
      {manager && <form className="mad-context-form" onSubmit={event => {
        event.preventDefault(); void act(async () => {
          await api(path + '/context', 'PUT', doc)
          setDoc({ id: crypto.randomUUID(), expected_revision: 0, title: '', source: '', body: '' })
        })
      }}><h3>{doc.expected_revision ? `发布资料新版本 · 当前 v${doc.expected_revision}` : '添加项目资料'}</h3>
        <label>资料标题<Input required value={doc.title} maxLength={120} onChange={event => setDoc({ ...doc, title: event.target.value })} /></label>
        <label>来源<Input required value={doc.source} maxLength={500} onChange={event => setDoc({ ...doc, source: event.target.value })} /></label>
        <label>原文<Input.TextArea required rows={6} value={doc.body} maxLength={50000} onChange={event => setDoc({ ...doc, body: event.target.value })} /></label>
        <Button type="primary" htmlType="submit" loading={busy}>发布资料版本</Button>
      </form>}
    </> : <>
      <div className="account-actions"><Select aria-label="选择需求" value={selected?.id}
        options={requirements.map(item => ({ value: item.id, label: `${item.title} · v${item.revision}` }))}
        onChange={id => setSelected(requirements.find(item => item.id === id))} />
        {manager && <Button onClick={() => {
          setEditing({ id: crypto.randomUUID(), revision: 0, title: '', body: '', criteria: [''] }); setEditingOpen(true)
        }}>确认新需求</Button>}
      </div>
      {selected && <section className="mad-context-card"><h3>{selected.title} · v{selected.revision}</h3><p>{selected.body}</p>
        <ul>{selected.criteria.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul>
        {sources?.requirement === selected.id && sources.links.length > 0 && <div className="account-actions" aria-label="GitHub 来源">
          {sources.links.map(source => <Button key={source.issue_number} onClick={() => navigate('projects', {
            project: project.id, page: 'issues', issue: String(source.issue_number), sourceRevision: String(source.connection_revision),
          })}>核对 GitHub Issue #{source.issue_number}</Button>)}
        </div>}
        <div className="account-actions">
          {!grouped && <Button type="primary" loading={busy} onClick={() => void act(async () => {
            if (pendingRun.current?.requirement !== selected.id) pendingRun.current = { id: crypto.randomUUID(), requirement: selected.id }
            const run = await api<Run>(path + '/runs', 'POST', { id: pendingRun.current.id, requirement_id: selected.id })
            pendingRun.current = undefined
            setDetails(run)
          })}>创建本阶段运行</Button>}
          {manager && <Button onClick={() => { setEditing(selected); setEditingOpen(true) }}>修改需求版本</Button>}
        </div>
      </section>}
      {selected && <RequirementGroups key={selected.id} project={project} userId={userId} requirementId={selected.id}
        onGroup={setGrouped} onUnavailable={onUnavailable} />}
      {manager && editingOpen && <form className="mad-context-form" onSubmit={event => {
        event.preventDefault(); void act(async () => {
          await api(path + '/requirements', 'PUT', { ...editing, expected_revision: editing.revision })
          setEditing({ id: crypto.randomUUID(), revision: 0, title: '', body: '', criteria: [''] })
          setEditingOpen(false)
        })
      }}><h3>{editing.revision ? `确认需求新版本 · 当前 v${editing.revision}` : '确认新需求'}</h3>
        <label>需求标题<Input required maxLength={120} value={editing.title} onChange={event => setEditing({ ...editing, title: event.target.value })} /></label>
        <label>需求内容<Input.TextArea required rows={3} maxLength={12000} value={editing.body} onChange={event => setEditing({ ...editing, body: event.target.value })} /></label>
        <label>验收条件，每行一条<Input.TextArea required rows={3} value={editing.criteria.join('\n')}
          onChange={event => setEditing({ ...editing, criteria: event.target.value.split('\n') })} /></label>
        <div className="account-actions"><Button htmlType="submit" loading={busy}>确认需求版本</Button>
          <Button disabled={busy} onClick={() => setEditingOpen(false)}>取消编辑</Button></div>
      </form>}
      <h3>我的运行</h3>
      <ul className="account-list">{runs.map(item => <li key={item.id}><span>{item.id.slice(0, 8)} · {statuses[item.status]}</span>
        <Button onClick={() => setDetails(item)}>查看运行</Button></li>)}</ul>
      {details && <section className="mad-context-card"><h3>运行 {details.id.slice(0, 8)}</h3>
        <p>{statuses[details.status]} · 需求 v{details.requirement_revision} · 检查点 {details.checkpoint}</p>
        {details.config && <p className="account-muted">{details.config.model} · 配置 v{details.config.revision} · {details.config.skills.join('、')}</p>}
        {details.context_changed && <Alert type="warning" message="需求或已读取资料有更新，请核对新版本后确认继续。" />}
        <div className="account-actions">
          <Button disabled={busy || details.status === 'running' || details.context_changed || details.status === 'completed'} onClick={() => void act(() => api('/runs/' + details.id + '/execute', 'POST'))}>执行 / 从检查点继续</Button>
          <Button disabled={busy || details.status !== 'running'} onClick={() => void act(() => api('/runs/' + details.id + '/cancel', 'POST'))}>取消</Button>
          {details.context_changed && latest && <Button disabled={busy || details.status === 'running'} onClick={() => void act(() =>
            api('/runs/' + details.id + '/confirm', 'POST', { expected_revision: latest.revision,
              documents: Object.fromEntries(documents.map(item => [item.id, item.revision])) }))}>确认上下文新版本</Button>}
        </div>
        <h4>实际读取的资料</h4>
        <ul>{details.references?.map((ref, index) => <li key={index}>{documents.find(item => item.id === ref.document_id)?.title ?? ref.document_id}
          · v{ref.revision} · {ref.start_offset}–{ref.end_offset} {ref.active ? '' : '· 历史引用'}
          <Button type="link" onClick={() => void act(async () => setOriginal(await api(path + `/context/${ref.document_id}/${ref.revision}`)))}>查看引用原文</Button></li>)}</ul>
        <details><summary>工具回执与事件</summary>{details.events?.map(event => <pre key={event.id}>{event.kind}{'\n'}{JSON.stringify(event.data, null, 2)}</pre>)}</details>
      </section>}
      <h3>共享交接</h3>
      {handoffs.map(handoff => <section className="mad-context-card" key={handoff.run_id}>
        <Tag>{handoff.role === 'testing' ? '独立验收' : '研发交付'}</Tag><Tag>{handoff.outdated ? '已过期' : handoff.verdict}</Tag>
        <p>{handoff.summary}</p><ul>{handoff.evidence.map((item, index) => <li key={index}>{item}</li>)}</ul>
      </section>)}
    </>}
    {original && <section className="mad-context-card"><h3>{original.title} · v{original.revision}</h3>
      <p>{original.source}</p><pre>{original.body}</pre><Button onClick={() => setOriginal(undefined)}>关闭原文</Button></section>}
  </section>
}
