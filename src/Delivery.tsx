import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Modal, Select, Spin, Tag } from 'antd'
import { api, errorCode, messageFor } from './accounts-api'
import type { VersionPlan } from './release-plans'
import type { OpenRoute, Project } from './Workbench'

type Group = { id: string; requirement_id: string; revision: number; stage: string; state: string;
  auto_run: boolean; development_id: string; testing_id: string }
type Proof = { key: string; run: string; command: string; environment: string; exit_code: number; output: string }
type Dispatch = { id: string; group_id: string; group_revision: number; stage: 'development' | 'testing'; status: string; has_artifact: boolean;
  input: { commit: string; plan_id: string; plan_revision: number; candidate_id: string | null; criteria: string[]; checks: string[];
    requirement_revision: number; digest: string | null };
  receipt: { commit: string; digest: string | null; checks: Proof[] } | null }
type Build = { plan_id: string; plan_revision: number; candidate_id: string; verification_id: string; ci: { url: string; run_id: number } }
type Release = { plan_id: string; plan_revision: number; candidate_id: string; published_at: string;
  payload: { commit: string; digest: string; channel: string; filename: string } }
type Delivery = { dispatches: Dispatch[]; builds: Build[]; releases: Release[]; publishers: string[]; can_grant: boolean }
type Member = { id: string; email: string }
const statusNames: Record<string, string> = { queued: '待领取', running: '运行中', succeeded: '已回传', failed: '失败',
  cancelled: '已取消', needs_review: '待核对', historical: '历史回执' }

function Evidence({ attempt, project }: { attempt: Dispatch; project: Project }) {
  const data = attempt.receipt
  return <details>
    <summary>{attempt.stage === 'development' ? '实现与自测' : '独立验收'} · {attempt.id.slice(0, 8)} · {statusNames[attempt.status]}</summary>
    <p>需求 v{attempt.input.requirement_revision} · 计划 v{attempt.input.plan_revision} · 提交 <code>{attempt.input.commit}</code></p>
    {data?.digest && <p>产物 SHA-256 <code>{data.digest}</code></p>}
    {attempt.has_artifact &&
      <a href={'/api/projects/' + project.id + '/delivery/artifacts/' + attempt.id} download>下载候选补丁</a>}
    {data?.checks.map(item => <div className="mad-context-card" key={item.key}>
      <strong>{item.key === 'implementation' ? '候选自测' : (item.key.startsWith('criterion:') ? attempt.input.criteria : attempt.input.checks)[Number(item.key.split(':')[1]) - 1]}</strong>
      <p>{item.environment} · 命令运行 {item.run} · 退出码 {item.exit_code}</p>
      <pre className="mad-delivery-output">{item.command}{'\n'}{item.output}</pre>
    </div>)}
  </details>
}

export function GroupDelivery({ group, project, userId }: { group: Group; project: Project; userId: string }) {
  const [plans, setPlans] = useState<VersionPlan[]>([])
  const [attempts, setAttempts] = useState<Dispatch[]>([])
  const [plan, setPlan] = useState<string>()
  const [commit, setCommit] = useState('')
  const [shown, setShown] = useState<{ id: string; secret: string }>()
  const [busy, setBusy] = useState(false)
  const [fault, setFault] = useState('')
  const [refresh, setRefresh] = useState(0)
  const pending = useRef<{ id: string; secret: string; revision: number; plan_id: string; commit?: string }>(undefined)
  const path = '/projects/' + project.id
  const groupPath = '/groups/' + group.id + '/dispatches'
  const canDispatch = !group.auto_run && group.state === 'active' && (
    group.stage === 'development' && group.development_id === userId || group.stage === 'testing' && group.testing_id === userId)

  useEffect(() => {
    if (group.auto_run) return
    let active = true
    Promise.all([api<Pick<Delivery, 'dispatches'>>(path + '/delivery?group_id=' + group.id), api<{ plans: VersionPlan[] }>(path + '/versions')]).then(([data, versions]) => {
      if (!active) return
      setAttempts(data.dispatches)
      setPlans(versions.plans.filter(item => item.status === 'committed' && item.requirement_ids.length === 1 && item.requirement_ids[0] === group.requirement_id))
    }).catch(error => { if (active) setFault(messageFor(errorCode(error))) })
    return () => { active = false }
  }, [group.auto_run, group.id, group.requirement_id, group.revision, path, refresh])

  async function act(action: () => Promise<unknown>) {
    setBusy(true)
    setFault('')
    try { await action(); setRefresh(value => value + 1) }
    catch (error) { setFault(messageFor(errorCode(error))) }
    finally { setBusy(false) }
  }

  const planId = plan ?? plans[0]?.plan_id
  const openAttempt = attempts.find(item => item.group_revision === group.revision && ['queued', 'running', 'needs_review'].includes(item.status))
  if (!canDispatch && !attempts.length && !fault) return null
  return <section className="mad-context-card">
    <div className="mad-page-heading"><h3>tebb 执行与证据</h3><Button onClick={() => setRefresh(value => value + 1)}>刷新回执</Button></div>
    {fault && <Alert type="error" message={fault} showIcon />}
    {canDispatch && !openAttempt && (!planId ? <p>先在项目时间线确认仅包含本需求的版本计划，并固定 OpenSpec 规格。</p> :
      <form className="mad-context-form" onSubmit={event => {
        event.preventDefault()
        const sha = group.stage === 'development' ? commit.trim().toLowerCase() : undefined
        if (!pending.current || pending.current.revision !== group.revision || pending.current.plan_id !== planId || pending.current.commit !== sha) {
          const secret = Array.from(crypto.getRandomValues(new Uint8Array(32)), value => value.toString(16).padStart(2, '0')).join('')
          pending.current = { id: crypto.randomUUID(), secret, revision: group.revision, plan_id: planId, commit: sha }
        }
        const request = pending.current
        void act(async () => { await api(groupPath, 'POST', request); setShown(request); pending.current = undefined })
      }}>
        <label>版本计划<Select aria-label="版本计划" value={planId} onChange={setPlan}
          options={plans.map(item => ({ value: item.plan_id, label: item.name + ' v' + item.revision }))} /></label>
        {group.stage === 'development' && <label>候选完整提交 SHA<Input required value={commit} pattern="[a-fA-F0-9]{40}"
          onChange={event => setCommit(event.target.value)} /></label>}
        <Button type="primary" htmlType="submit" loading={busy}>{group.stage === 'testing' ? '派发独立验收' : '派发至 tebb'}</Button>
      </form>)}
    {shown && <div className="mad-context-form">
      <label>派发编号<Input readOnly value={shown.id} /></label>
      <label>本机领取令牌<Input.Password readOnly value={shown.secret} /></label>
      <p>将派发编号和领取令牌交给获授权的本机 tebb 适配器。同一次执行使用原编号补传。</p>
    </div>}
    {canDispatch && openAttempt && ['queued', 'needs_review'].includes(openAttempt.status) &&
      <Button disabled={busy} onClick={() => void act(() => api(groupPath + '/' + openAttempt.id + '/cancel', 'POST'))}>取消本次派发</Button>}
    {attempts.map(item => <Evidence attempt={item} project={project} key={item.id} />)}
  </section>
}

export function BuildRelease({ project, userId, planId, navigate, onUnavailable }: {
  project: Project; userId: string; planId?: string; navigate: OpenRoute; onUnavailable: (code: string) => void
}) {
  const path = '/projects/' + project.id
  const [data, setData] = useState<Delivery>()
  const [plans, setPlans] = useState<VersionPlan[]>([])
  const [members, setMembers] = useState<Member[]>([])
  const [publisher, setPublisher] = useState<string>()
  const [candidate, setCandidate] = useState<string>()
  const [ci, setCi] = useState('')
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [modal, holder] = Modal.useModal()
  const manager = project.role !== 'member'

  useEffect(() => {
    let active = true
    Promise.all([api<Delivery>(path + '/delivery'), api<{ plans: VersionPlan[] }>(path + '/versions'),
      manager ? api<Member[]>(path + '/members') : Promise.resolve([])]).then(([delivery, versions, people]) => {
      if (active) { setData(delivery); setPlans(versions.plans); setMembers(people) }
    }).catch(error => {
      if (!active) return
      const code = errorCode(error)
      setFault(messageFor(code))
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
    })
    return () => { active = false }
  }, [path, manager, refresh, onUnavailable])

  async function act(action: () => Promise<unknown>) {
    setFault('')
    setBusy(true)
    try { await action(); setRefresh(value => value + 1) }
    catch (error) { setFault(messageFor(errorCode(error))) }
    finally { setBusy(false) }
  }

  const candidates = data?.dispatches.filter(item => item.stage === 'development' && item.status === 'succeeded') ?? []
  const selected = candidates.find(item => item.id === candidate) ?? candidates.find(item => !planId || item.input.plan_id === planId) ?? candidates[0]
  const plan = plans.find(item => item.plan_id === selected?.input.plan_id)
  const build = data?.builds.find(item => item.candidate_id === selected?.id && item.plan_revision === plan?.revision)
  const released = data?.releases.some(item => item.plan_id === plan?.plan_id && item.plan_revision === plan?.revision)
  const current = selected && plan && selected.input.plan_revision === plan.revision && plan.status === 'committed'
  return <section className="mad-page">
    {holder}
    <div className="mad-page-heading"><h2>构建与发布</h2><Button onClick={() => setRefresh(value => value + 1)}>刷新</Button></div>
    {fault && <Alert type="error" showIcon message={fault} />}
    {!data && !fault && <Spin />}
    {data && !candidates.length && <p>暂无候选产物。研发阶段的 tebb 回执入档后会出现在这里。</p>}
    {!!candidates.length && <Select aria-label="候选产物" value={selected?.id} onChange={setCandidate}
      options={candidates.map(item => ({ value: item.id, label: item.input.commit.slice(0, 12) + ' · 计划 v' + item.input.plan_revision }))} />}
    {selected && <section className="mad-context-card">
      <div className="mad-page-heading"><h3>{plan?.name ?? '版本计划'} · v{selected.input.plan_revision}</h3>
        <Tag>{!current ? '历史候选' : released ? '已发布' : build ? '已放行' : '待核对证据'}</Tag></div>
      <Button onClick={() => navigate('projects', { project: project.id, page: 'timeline', plan: selected.input.plan_id })}>查看版本时间线</Button>
      <Evidence attempt={selected} project={project} />
      {data?.dispatches.filter(item => item.input.candidate_id === selected.id).map(item => <Evidence attempt={item} project={project} key={item.id} />)}
      {build && <p><a href={build.ci.url} rel="noreferrer" target="_blank">构建 CI #{build.ci.run_id}</a></p>}
      {current && manager && !build && <form className="mad-context-form" onSubmit={event => {
        event.preventDefault()
        void act(() => api(path + '/delivery/' + plan.plan_id + '/approve', 'POST', {
          revision: plan.revision, candidate_id: selected.id, ci_run_id: Number(ci),
        }))
      }}><label>同一提交的 GitHub Actions 运行 ID<Input required inputMode="numeric" pattern="[0-9]+" value={ci}
          onChange={event => setCi(event.target.value)} /></label>
        <Button htmlType="submit" loading={busy}>核对构建并放行</Button></form>}
      {current && build && !released && data?.publishers.includes(userId) && <Button type="primary" loading={busy} onClick={() => modal.confirm({
        title: '确认发布到本地试点渠道？', content: '渠道将保存当前候选文件，并按实际写入生成回执。',
        okText: '发布并登记回执', cancelText: '取消',
        onOk: () => act(() => api(path + '/delivery/' + plan.plan_id + '/publish', 'POST', {
          revision: plan.revision, candidate_id: selected.id,
        })),
      })}>发布到试点渠道</Button>}
    </section>}
    {!!data?.releases.length && <section className="mad-context-card"><h3>发布回执</h3>
      {data.releases.map(item => <details key={item.plan_id + ':' + item.plan_revision}>
        <summary>计划 v{item.plan_revision} · {new Date(item.published_at).toLocaleString()} · {item.payload.channel}</summary>
        <p>集成提交 <code>{item.payload.commit}</code></p><p>产物 SHA-256 <code>{item.payload.digest}</code></p>
        <a href={path.replace('/projects/', '/api/projects/') + '/delivery/artifacts/' + item.candidate_id} download>{item.payload.filename}</a>
      </details>)}
    </section>}
    {data?.can_grant && <section className="mad-context-card">
      <h3>试点发布权限</h3>
      <div className="account-actions"><Select aria-label="发布成员" value={publisher} onChange={setPublisher}
        options={members.map(item => ({ label: item.email, value: item.id }))} />
        {publisher && <Button loading={busy} onClick={() => void act(() => api(path + '/delivery/publishers/' + publisher +
          '?enabled=' + !data.publishers.includes(publisher), 'PUT'))}>
          {data.publishers.includes(publisher) ? '撤销发布权限' : '授予发布权限'}</Button>}
      </div>
    </section>}
  </section>
}
