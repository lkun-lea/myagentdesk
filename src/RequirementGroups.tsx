import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Steps, Tag } from 'antd'
import { api, errorCode, messageFor } from './accounts-api'
import type { OpenRoute, Project } from './Workbench'

type Member = { id: string; email: string; role: string }
type Position = { user_id: string; role: string }
type Requirement = { id: string; title: string }
type Run = { id: string; role: string; status: string; user_id: string; checkpoint: number; group_revision: number }
type Decision = { id: string; run_id: string; owner_id: string; role?: string; question: string; options: string[];
  evidence: string[]; checkpoint: number; revision: number }
type Group = { id: string; requirement_id: string; project_id: string; requirement_revision: number; revision: number;
  stage: string; state: string; source: string; product_id: string; development_id: string; testing_id: string; quality_id: string;
  runs?: Run[]; pending_decisions?: Decision[]; requirement?: { title: string; body: string; criteria: string[] } }
type Message = { id: string; sequence: number; author: string; kind: string; body: string; created_at: string;
  data: { evidence?: string[]; role?: string; run_id?: string; owner_id?: string; source?: string; stage?: string;
    from_stage?: string; checkpoint?: number; candidate_run?: string; context_references?: { document_id: string; revision: number; start_offset: number; end_offset: number }[] } }
const stageNames: Record<string, string> = { product: '产品确认', development: '实现与自测', testing: '独立验收', quality: '质量复核', completed: '已完成' }
const stages = ['product', 'development', 'testing', 'quality']
const runNames: Record<string, string> = { queued: '待开始', running: '执行中', waiting_decision: '等待本人决策', paused: '已暂停', failed: '失败可重试', stale: '需要核对版本', completed: '已交接', cancelled: '已取消' }

export function DecisionInbox({ changed }: { changed?: () => void }) {
  const [items, setItems] = useState<Decision[]>()
  const [answers, setAnswers] = useState<Record<string, { answer?: string; note?: string }>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let active = true
    const read = () => api<Decision[]>('/decisions').then(value => { if (active) setItems(value) }).catch(failure => {
      if (active) setError(messageFor(errorCode(failure)))
    })
    void read()
    const timer = window.setInterval(read, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [reload])
  return <section className="mad-page"><h2>待我决策</h2>
    {error && <Alert type="error" showIcon message={error} />}
    {!items && !error && <Spin />}
    {items?.length === 0 && <p className="account-muted">暂无待处理的决定。</p>}
    {items?.map(item => <article className="mad-context-card" key={item.id}>
      <h3>{item.question}</h3><p className="account-muted">运行 {item.run_id.slice(0, 8)} · 检查点 {item.checkpoint}</p>
      <ul>{item.evidence.map((line, index) => <li key={index}>{line}</li>)}</ul>
      <label className="mad-group-field">选择方案<Select aria-label="决策方案" value={answers[item.id]?.answer}
        options={item.options.map(option => ({ value: option, label: option }))}
        onChange={answer => setAnswers({ ...answers, [item.id]: { ...answers[item.id], answer } })} /></label>
      <label className="mad-group-field">决定说明<Input.TextArea aria-label="决定说明" maxLength={3000} value={answers[item.id]?.note ?? ''}
        onChange={event => setAnswers({ ...answers, [item.id]: { ...answers[item.id], note: event.target.value } })} /></label>
      <Button type="primary" loading={busy === item.id} disabled={!!busy || !answers[item.id]?.answer || !answers[item.id]?.note?.trim()}
        onClick={async () => {
          setBusy(item.id); setError('')
          try {
            await api('/decisions/' + item.id + '/resolve', 'POST', { expected_revision: item.revision, ...answers[item.id] })
            setReload(value => value + 1); changed?.()
          } catch (failure) { setError(messageFor(errorCode(failure))) }
          finally { setBusy('') }
        }}>确认并继续原运行</Button>
    </article>)}
  </section>
}

export function RequirementGroups({ project, userId, requirementId, initialGroup, onGroup, onUnavailable }: {
  project: Project; userId: string; requirementId?: string; initialGroup?: string;
  onGroup?: (value: boolean) => void; onUnavailable: (code: string) => void
}) {
  const path = '/projects/' + project.id
  const manager = project.role !== 'member'
  const [groups, setGroups] = useState<Group[]>([])
  const [group, setGroup] = useState<Group>()
  const [selected, setSelected] = useState(initialGroup)
  const [members, setMembers] = useState<Member[]>([])
  const [positions, setPositions] = useState<Position[]>([])
  const [requirements, setRequirements] = useState<Requirement[]>([])
  const [draft, setDraft] = useState<Record<string, string>>({ requirement_id: requirementId ?? '', quality_id: userId, source: '' })
  const [messages, setMessages] = useState<Message[]>([])
  const [body, setBody] = useState('')
  const [note, setNote] = useState('')
  const [replacement, setReplacement] = useState<Record<string, string>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const [ready, setReady] = useState(false)
  const [runDetail, setRunDetail] = useState<{ id: string; status: string; checkpoint: number; events: { id: number; kind: string; data: unknown }[] }>()
  const [original, setOriginal] = useState<{ title: string; source: string; revision: number; body: string }>()
  const cursor = useRef(0)
  const posting = useRef<{ id: string; body: string } | undefined>(undefined)
  const visible = groups.filter(item => !requirementId || item.requirement_id === requirementId)
  const selectedId = visible.find(item => item.id === selected)?.id ?? visible[0]?.id
  const memberName = (id: string) => members.find(member => member.id === id)?.email ?? '已离开或未分配成员'

  useEffect(() => {
    let active = true
    Promise.all([api<Group[]>(path + '/groups'), api<Member[]>(path + '/members'), api<Position[]>(path + '/roles'),
      api<Requirement[]>(path + '/requirements')]).then(([value, people, roles, req]) => {
      if (!active) return
      setGroups(value); setMembers(people); setPositions(roles); setRequirements(req); setReady(true)
      onGroup?.(value.some(item => item.requirement_id === requirementId))
    }).catch(failure => {
      if (!active) return
      const code = errorCode(failure)
      setGroups([]); setMembers([]); setReady(true); setError(messageFor(code))
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
    })
    return () => { active = false }
  }, [path, reload, requirementId, onGroup, onUnavailable])

  useEffect(() => {
    setGroup(undefined); setMessages([]); setRunDetail(undefined); cursor.current = 0
    if (!selectedId) return
    let active = true
    const read = async () => {
      try {
        const [current, incoming] = await Promise.all([api<Group>('/groups/' + selectedId), api<Message[]>('/groups/' + selectedId + '/messages?after=' + cursor.current)])
        if (!active) return
        setGroup(current)
        if (incoming.length) {
          cursor.current = Math.max(cursor.current, incoming[incoming.length - 1].sequence)
          setMessages(previous => [...new Map([...previous, ...incoming].map(item => [item.id, item])).values()].sort((a, b) => a.sequence - b.sequence))
        }
        const own = current.runs?.filter(item => item.user_id === userId).at(-1)
        if (own) {
          const detail = await api<typeof runDetail>('/runs/' + own.id)
          if (active) setRunDetail(detail)
        }
      } catch (failure) {
        if (!active) return
        const code = errorCode(failure)
        setError(messageFor(code))
        if (code === 'project_not_found' || code === 'login_required') {
          setGroup(undefined); setMessages([]); setRunDetail(undefined); onUnavailable(code)
        }
      }
    }
    void read()
    const timer = window.setInterval(read, 1800)
    return () => { active = false; window.clearInterval(timer) }
  }, [selectedId, reload, userId, onUnavailable])

  async function act(action: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await action(); setReload(value => value + 1) }
    catch (failure) { setError(messageFor(errorCode(failure))) }
    finally { setBusy(false) }
  }

  return <section className="mad-page mad-requirement-group">
    <h2>需求群</h2>
    {error && <Alert type="error" showIcon message={error} action={<Button onClick={() => { setError(''); setReload(value => value + 1) }}>重新加载</Button>} />}
    {!ready && <Spin />}
    {visible.length > 1 && <Select aria-label="选择需求群" value={selectedId} onChange={setSelected}
      options={visible.map(item => ({ value: item.id, label: requirements.find(req => req.id === item.requirement_id)?.title ?? item.id }))} />}
    {ready && !visible.length && <>
      <p className="account-muted">这条需求尚未建立协作群。</p>
      {manager && <form className="mad-context-form" onSubmit={event => {
        event.preventDefault(); void act(async () => {
          const created = await api<Group>(path + '/groups', 'POST', draft)
          setSelected(created.id)
        })
      }}>
        {!requirementId && <label>需求<Select aria-label="建群需求" value={draft.requirement_id || undefined}
          options={requirements.map(req => ({ value: req.id, label: req.title }))}
          onChange={value => setDraft({ ...draft, requirement_id: value })} /></label>}
        <label>需求来源<Input aria-label="需求来源" required maxLength={500} value={draft.source} onChange={event => setDraft({ ...draft, source: event.target.value })} /></label>
        {stages.map(role => <label key={role}>{stageNames[role]}负责人<Select aria-label={stageNames[role] + '负责人'}
          value={draft[role + '_id']} options={members.filter(member => role === 'quality' ? member.role !== 'member'
            : positions.some(position => position.user_id === member.id && position.role === role)).map(member => ({ value: member.id, label: member.email }))}
          onChange={value => setDraft({ ...draft, [role + '_id']: value })} /></label>)}
        <Button htmlType="submit" type="primary" loading={busy} disabled={!draft.requirement_id || stages.some(role => !draft[role + '_id'])}>立项并创建需求群</Button>
      </form>}
    </>}
    {group && <>
      <div className="mad-page-heading"><h3>{group.requirement?.title}</h3><Tag>{stageNames[group.stage]} · 流程 v{group.revision}</Tag></div>
      <Steps size="small" current={group.stage === 'completed' ? 4 : stages.indexOf(group.stage)} items={stages.map(stage => ({ title: stageNames[stage] }))} />
      <p className="account-muted">需求 v{group.requirement_revision} · 当前负责人 {group.stage === 'completed' ? '已完成' : memberName(group[(group.stage + '_id') as keyof Group] as string)}</p>
      <p>{group.source.startsWith('https://') ? <a href={group.source} target="_blank" rel="noreferrer">需求来源</a> : group.source}</p>
      <div className="account-actions">
        {group.state === 'active' && group[group.stage + '_id' as keyof Group] === userId && <Button type="primary" loading={busy}
          onClick={() => void act(async () => {
            const run = await api<Run>(path + '/runs', 'POST', { id: crypto.randomUUID(), requirement_id: group.requirement_id, group_id: group.id })
            await api('/runs/' + run.id + '/execute', 'POST')
          })}>推进本阶段 Agent</Button>}
        <Button onClick={() => setReload(value => value + 1)}>刷新群记录</Button>
      </div>
      {group.state === 'awaiting_confirmation' && group[group.stage + '_id' as keyof Group] === userId && <section className="mad-context-card">
        <label className="mad-group-field">复核说明<Input.TextArea aria-label="复核说明" value={note} maxLength={3000} onChange={event => setNote(event.target.value)} /></label>
        <div className="account-actions">{(['approve', 'return'] as const).map(action => <Button key={action} disabled={busy || !note.trim()}
          type={action === 'approve' ? 'primary' : 'default'} onClick={() => void act(() => api('/groups/' + group.id + '/advance', 'POST', {
            expected_revision: group.revision, action, note,
          }))}>{action === 'approve' ? (group.stage === 'quality' ? '复核通过并完成需求' : '确认产品方案') : '退回并说明原因'}</Button>)}</div>
      </section>}
      {group.state === 'waiting_decision' && <Alert type="warning" showIcon message="Agent 正在等待本人决策，原运行和检查点已保留。" />}
      {group.pending_decisions?.some(item => item.owner_id === userId) && <DecisionInbox changed={() => setReload(value => value + 1)} />}
      {manager && group.pending_decisions?.filter(item => item.owner_id !== userId).map(item => <section className="mad-context-card" key={item.id}>
        <h3>{item.question}</h3><p>待处理成员 · {memberName(item.owner_id)}</p>
        <Select aria-label="决策替补" placeholder="选择同岗位替补" value={replacement[item.id]}
          options={members.filter(member => positions.some(position => position.user_id === member.id && position.role === item.role))
            .map(member => ({ value: member.id, label: member.email }))}
          onChange={value => setReplacement({ ...replacement, [item.id]: value })} />
        <Button disabled={!replacement[item.id] || busy} onClick={() => void act(() => api('/decisions/' + item.id + '/reassign', 'POST', {
          member_id: replacement[item.id], expected_revision: item.revision,
        }))}>转交决定与原运行</Button>
      </section>)}
      {runDetail && <section className="mad-context-card"><h3>我的原运行 {runDetail.id.slice(0, 8)}</h3>
        <p>{runNames[runDetail.status]} · 检查点 {runDetail.checkpoint}</p>
        <details><summary>阶段工具与证据</summary>{runDetail.events?.filter(item => item.kind !== 'text_delta').slice(-25)
          .map(item => <pre key={item.id}>{item.kind}{'\n'}{JSON.stringify(item.data, null, 2)}</pre>)}</details>
      </section>}
      <section aria-label="需求群消息" className="mad-group-messages">
        {messages.map(message => <article className="mad-context-card" key={message.id}>
          <div className="mad-page-heading"><strong>{memberName(message.author)}{message.kind === 'handoff' ? ' · Agent 交接' : ''}</strong>
            <time dateTime={message.created_at}>{new Date(message.created_at).toLocaleString()}</time></div>
          <p>{message.kind === 'transition' && message.body === `${message.data.from_stage} → ${message.data.stage}`
            ? message.data.from_stage === message.data.stage ? '本阶段已交付，等待本人确认。' : `已交接至${stageNames[message.data.stage ?? '']}。`
            : message.body}</p>
          {message.data.run_id && <small>运行 {message.data.run_id.slice(0, 8)}</small>}
          {message.data.checkpoint !== undefined && <small>原检查点 {message.data.checkpoint}</small>}
          {message.data.candidate_run && <small>对应研发候选 {message.data.candidate_run.slice(0, 8)}</small>}
          {message.data.context_references?.map((ref, index) => <Button key={index} type="link" onClick={() => void act(async () => {
            setOriginal(await api(path + `/context/${ref.document_id}/${ref.revision}`))
          })}>查看阶段依据 v{ref.revision} · {ref.start_offset}–{ref.end_offset}</Button>)}
          {message.data.evidence && <ul>{message.data.evidence.map((line, index) => <li key={index}>{line}</li>)}</ul>}
        </article>)}
      </section>
      {original && <section className="mad-context-card"><h3>{original.title} · v{original.revision}</h3>
        <p>{original.source}</p><pre>{original.body}</pre><Button onClick={() => setOriginal(undefined)}>关闭原文</Button></section>}
      <form className="mad-group-compose" onSubmit={event => {
        event.preventDefault(); void act(async () => {
          if (!posting.current || posting.current.body !== body) posting.current = { id: crypto.randomUUID(), body }
          await api('/groups/' + group.id + '/messages', 'POST', posting.current)
          posting.current = undefined; setBody('')
        })
      }}><label className="mad-group-field">共享到需求群<Input.TextArea aria-label="群消息" required maxLength={8000} rows={2}
        value={body} onChange={event => setBody(event.target.value)} /></label>
        <Button type="primary" htmlType="submit" loading={busy} disabled={!body.trim()}>发送到需求群</Button></form>
    </>}
  </section>
}

export function ProjectChats({ projects, userId, selectedProject, initialGroup, navigate, onUnavailable }: {
  projects: Project[]; userId: string; selectedProject?: string; initialGroup?: string; navigate: OpenRoute; onUnavailable: (code: string) => void
}) {
  const project = selectedProject ? projects.find(item => item.id === selectedProject) : projects[0]
  return <section className="mad-page"><h1>聊天</h1>
    <Select aria-label="聊天项目" value={project?.id} options={projects.map(item => ({ value: item.id, label: item.name }))}
      onChange={id => navigate('chat', { project: id })} />
    {project ? <RequirementGroups key={project.id} project={project} userId={userId} initialGroup={initialGroup} onUnavailable={onUnavailable} />
      : <p className="account-muted">暂无可访问的项目。</p>}
  </section>
}
