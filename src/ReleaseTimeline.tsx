import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, MouseEvent } from 'react'
import { Alert, Button, Spin, Tag } from 'antd'
import { CloseOutlined, FlagOutlined } from '@ant-design/icons'
import { api, errorCode, messageFor } from './accounts-api'
import { ProjectContent } from './Workbench'
import type { OpenRoute, Project } from './Workbench'
import { VersionPlanEditor } from './VersionPlanEditor'
import { acceptanceStates, blockers, planStates, requirementStages } from './release-plans'
import type { PlanRevision, PlanningData, ScheduledRequirement, VersionPlan } from './release-plans'
import './release-timeline.css'

type Selection = { kind: 'plan'; id: string; milestone?: number; revision?: number } | { kind: 'requirement'; id: string }
type History = { revisions: PlanRevision[]; next_before: number | null }

const day = 86400000
const dayNumber = (value: string) => Date.parse(`${value}T00:00:00Z`) / day
const axisLabel = (value: number) => new Date(value * day).toISOString().slice(5, 10).replace('-', '/')
const displayStage = (requirement: ScheduledRequirement) => requirementStages[requirement.stage ?? ''] ?? '待建需求群'

function makeAxis(plans: VersionPlan[]) {
  const dates = plans.flatMap(plan => [plan.target_date, ...plan.milestones.map(item => item.date)].filter((value): value is string => !!value)).map(dayNumber)
  const first = Math.min(...dates) - 1
  const span = Math.max(7, Math.max(...dates) - first + 2)
  const step = Math.ceil(span / 10)
  const columns = Math.ceil(span / step)
  return {
    columns, labels: Array.from({ length: columns }, (_, index) => axisLabel(first + index * step)),
    position: (date: string) => `${100 * (dayNumber(date) - first + 0.5) / (columns * step)}%`,
  }
}

function PlanHistory({ path, plan, revision, requirements, onUnavailable }: {
  path: string; plan: VersionPlan; revision?: number; requirements: Map<string, ScheduledRequirement>; onUnavailable: (code: string) => void
}) {
  const [history, setHistory] = useState<History>()
  const [focused, setFocused] = useState<PlanRevision>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [busy, setBusy] = useState(false)
  const endpoint = `${path}/${plan.plan_id}/history`
  function fail(failure: unknown) {
    const code = errorCode(failure)
    setError(messageFor(code))
    if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
  }
  useEffect(() => {
    let active = true
    setHistory(undefined)
    setFocused(undefined)
    setError('')
    Promise.all([api<History>(endpoint), revision ? api<PlanRevision>(`${endpoint}/${revision}`) : Promise.resolve(undefined)])
      .then(([value, item]) => { if (active) { setHistory(value); setFocused(item) } })
      .catch(failure => { if (active) fail(failure) })
    return () => { active = false }
  }, [endpoint, plan.revision, revision, attempt, onUnavailable])

  const rows = [...(history?.revisions ?? [])]
  if (focused && !rows.some(row => row.revision === focused.revision)) rows.push(focused)
  return <section aria-label="计划修订历史">
    <h3>计划修订历史</h3>
    {error && <Alert type="error" message={error} action={<Button size="small" onClick={() => setAttempt(value => value + 1)}>重试</Button>} />}
    {!history && !error && <Spin aria-label="正在读取计划修订" />}
    {rows.map(item => <details key={item.revision} open={item.revision === revision || undefined}>
      <summary>v{item.revision} · {item.target_date ?? '无目标日期'} · {item.requirement_ids.length} 条需求</summary>
      <p>{item.reason}</p><p className="account-muted">{item.actor} · {new Date(item.created_at).toLocaleString()}</p>
      <ul>{item.requirement_ids.map(id => <li key={id}>{requirements.get(id)?.title ?? id}</li>)}</ul>
      {item.milestones.length > 0 && <p>里程碑 {item.milestones.map(entry => `${entry.name} ${entry.date}`).join('、')}</p>}
      {item.required_checks.length > 0 && <p>发版必过项 {item.required_checks.join('、')}</p>}
    </details>)}
    {history?.next_before && <Button size="small" loading={busy} onClick={async () => {
      setBusy(true)
      try {
        const older = await api<History>(`${endpoint}?before=${history.next_before}`)
        setHistory(value => value && { revisions: [...value.revisions, ...older.revisions], next_before: older.next_before })
      } catch (failure) { fail(failure) }
      finally { setBusy(false) }
    }}>更早修订</Button>}
  </section>
}

export function ReleaseTimeline({ project, userId, planId, planRevision, navigate, onUnavailable }: {
  project: Project; userId: string; planId?: string; planRevision?: number;
  navigate: OpenRoute; onUnavailable: (code: string) => void
}) {
  const path = `/projects/${project.id}/versions`
  const [data, setData] = useState<PlanningData>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  const [showEvents, setShowEvents] = useState(false)
  const [selection, setSelection] = useState<Selection>()
  const [editor, setEditor] = useState<{ plan?: VersionPlan }>()
  const opener = useRef<HTMLElement | null>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const axisScroll = useRef<HTMLElement>(null)
  const timelinePosition = useRef({ page: 0, axis: 0 })
  const manager = project.role !== 'member'

  useEffect(() => {
    let active = true
    let sequence = 0
    const read = () => {
      const request = ++sequence
      void api<PlanningData>(path).then(value => {
        if (active && sequence === request) { setData(value); setError('') }
      }).catch(failure => {
        if (!active || sequence !== request) return
        const code = errorCode(failure)
        setError(messageFor(code))
        if (code === 'project_not_found' || code === 'login_required') {
          setData(undefined)
          setSelection(undefined)
          onUnavailable(code)
        }
      })
    }
    read()
    window.addEventListener('focus', read)
    return () => { active = false; window.removeEventListener('focus', read) }
  }, [path, attempt, onUnavailable])

  useEffect(() => {
    if (planId) {
      setSelection({ kind: 'plan', id: planId, revision: planRevision })
      setShowEvents(false)
    }
  }, [planId, planRevision])

  function open(next: Selection, event?: MouseEvent<HTMLElement>) {
    if (event) opener.current = event.currentTarget
    setSelection(next)
  }

  function showTimeline() {
    setShowEvents(false)
    requestAnimationFrame(() => {
      if (axisScroll.current) axisScroll.current.scrollLeft = timelinePosition.current.axis
      window.scrollTo({ top: timelinePosition.current.page, behavior: 'instant' })
    })
  }

  useEffect(() => {
    if (selection && !showEvents) closeButton.current?.focus({ preventScroll: window.innerWidth > 900 })
  }, [selection, showEvents, !!data])

  useEffect(() => {
    if (!selection || showEvents || editor) return
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeDetail()
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [selection, showEvents, editor, planId, project.id])

  function closeDetail() {
    setSelection(undefined)
    if (planId) navigate('projects', { project: project.id, page: 'timeline' }, true)
    requestAnimationFrame(() => (opener.current?.isConnected ? opener.current : document.getElementById('mad-release-timeline-tab'))?.focus())
  }

  const refresh = () => setAttempt(value => value + 1)
  const requirements = new Map(data?.requirements.map(item => [item.id, item]))
  const selectedPlan = selection?.kind === 'plan' ? data?.plans.find(plan => plan.plan_id === selection.id) : undefined
  const selectedRequirement = selection?.kind === 'requirement' ? requirements.get(selection.id) : undefined
  const dated = data?.plans.filter(plan => plan.target_date || plan.milestones.length) ?? []
  const undated = data?.plans.filter(plan => !plan.target_date) ?? []
  const axis = dated.length ? makeAxis(dated) : undefined
  const localToday = new Date()
  const today = `${localToday.getFullYear()}-${String(localToday.getMonth() + 1).padStart(2, '0')}-${String(localToday.getDate()).padStart(2, '0')}`
  const overdue = (plan: VersionPlan) => plan.status === 'committed' && !!plan.target_date && plan.target_date < today && !plan.actual_published_at

  function requirementButton(item: ScheduledRequirement) {
    return <button className="mad-release-row-button" aria-expanded={selectedRequirement?.id === item.id}
      onClick={event => open({ kind: 'requirement', id: item.id }, event)}>
      <strong>{item.title}</strong><small>{item.sources.length ? `GitHub #${item.sources.map(source => source.issue_number).join('、#')}` : `需求 v${item.revision}`}</small>
    </button>
  }

  return <section className="mad-page mad-release-page">
    <div className="mad-page-heading">
      <div className="mad-categories" aria-label="项目时间线视图">
        <Button type="text" id="mad-release-timeline-tab" aria-pressed={!showEvents} onClick={() => { if (showEvents) showTimeline() }}>版本时间线</Button>
        <Button type="text" aria-pressed={showEvents} onClick={() => {
          if (showEvents) return
          timelinePosition.current = { page: window.scrollY, axis: axisScroll.current?.scrollLeft ?? 0 }
          setShowEvents(true)
        }}>事件记录</Button>
      </div>
      <div className="account-actions">
        <Button onClick={() => navigate('projects', { project: project.id, page: 'issues' })}>查看 Issues</Button>
        {!showEvents && manager && data && <Button type="primary" onClick={event => { opener.current = event.currentTarget; setEditor({}) }}>新建版本计划</Button>}
      </div>
    </div>
    {showEvents && <ProjectContent project={project} page="timeline" onUnavailable={onUnavailable} navigate={(view, params) => {
      if (params?.plan && params.planRevision) {
        open({ kind: 'plan', id: params.plan, revision: Number(params.planRevision) })
        showTimeline()
      }
      navigate(view, params)
    }} />}
    <div hidden={showEvents}>
      {error && <Alert showIcon type="error" message={error} action={<Button size="small" onClick={refresh}>重新加载</Button>} />}
      {!data && !error && <Spin aria-label="正在加载版本计划" />}
      {data && <div className="mad-release-layout" data-detail={!!selection}>
        <div className="mad-release-main">
          {axis ? <section ref={axisScroll} className="mad-release-scroll" aria-label="版本日期刻度" tabIndex={0}>
            <div className="mad-release-calendar" style={{ '--release-columns': axis.columns } as CSSProperties}>
              <div className="calendar-row mad-release-axis"><span className="calendar-stub">版本 / 需求</span>
                <div>{axis.labels.map((value, index) => <time key={index}>{value}</time>)}</div></div>
              {dated.map(plan => <section key={plan.plan_id} aria-label={plan.name} className="mad-release-band">
                <div className="calendar-row mad-release-version" data-selected={selectedPlan?.plan_id === plan.plan_id}>
                  <div className="calendar-stub"><button className="mad-release-row-button" aria-expanded={selectedPlan?.plan_id === plan.plan_id}
                    onClick={event => open({ kind: 'plan', id: plan.plan_id }, event)}><strong>{plan.name}</strong><small>{planStates[plan.release_state]}</small></button></div>
                  <div className="mad-release-track">
                    {plan.target_date && <button className="mad-release-mark mad-release-target" style={{ '--release-position': axis.position(plan.target_date) } as CSSProperties}
                      data-overdue={overdue(plan)} onClick={event => open({ kind: 'plan', id: plan.plan_id }, event)} aria-label={`${plan.name} 目标 ${plan.target_date}`}>
                      <FlagOutlined /> {plan.target_date.slice(5)}{overdue(plan) ? ' · 逾期' : ''}</button>}
                  </div>
                </div>
                {plan.milestones.map((milestone, index) => <div className="calendar-row mad-release-milestone" key={index}>
                  <span className="calendar-stub">{milestone.name}</span>
                  <div className="mad-release-track"><button className="mad-release-mark" aria-label={`${milestone.name} ${milestone.date}`}
                    onClick={event => open({ kind: 'plan', id: plan.plan_id, milestone: index }, event)}
                    style={{ '--release-position': axis.position(milestone.date) } as CSSProperties}>{milestone.date.slice(5)}</button></div>
                </div>)}
                {plan.requirement_ids.map(id => requirements.get(id)).filter((item): item is ScheduledRequirement => !!item).map(item =>
                  <div className="calendar-row mad-release-req" key={item.id} data-selected={selectedRequirement?.id === item.id}>
                    <div className="calendar-stub">{requirementButton(item)}</div>
                    <div className="mad-release-track">{plan.target_date && <button className="mad-release-mark" data-state={item.acceptance}
                      style={{ '--release-position': axis.position(plan.target_date) } as CSSProperties}
                      aria-label={`${item.title} ${acceptanceStates[item.acceptance]}`} onClick={event => open({ kind: 'requirement', id: item.id }, event)}>
                      {item.blocker ? '阻塞' : displayStage(item)}</button>}</div>
                  </div>)}
              </section>)}
            </div>
          </section> : <p className="account-muted">有目标日期或里程碑的版本会显示在时间轴上。</p>}
          <section className="mad-context-card mad-release-queue" aria-label="待排期">
            <h3>待排期 <span className="account-muted">{undated.length} 个版本 · {data.unscheduled_ids.length} 条需求</span></h3>
            {undated.map(plan => <Button type="link" key={plan.plan_id} onClick={event => open({ kind: 'plan', id: plan.plan_id }, event)}>{plan.name} · 无目标日期</Button>)}
            {data.unscheduled_ids.map(id => requirements.get(id)).filter((item): item is ScheduledRequirement => !!item).map(item =>
              <div key={item.id}>{requirementButton(item)}</div>)}
            {!data.unscheduled_ids.length && !undated.length && <p className="account-muted">暂无待排期记录。</p>}
          </section>
        </div>
        {selection && <aside className="issue-drawer mad-release-detail" aria-labelledby="mad-release-detail-title">
          <header className="drawer-header"><h2 id="mad-release-detail-title">{selectedPlan?.name ?? selectedRequirement?.title ?? '计划详情'}</h2>
            <Button ref={closeButton} type="text" icon={<CloseOutlined />} aria-label="关闭详情" onClick={closeDetail} /></header>
          <div className="drawer-content">
          {selectedPlan && <>
            <p><Tag>{planStates[selectedPlan.release_state]}</Tag> v{selectedPlan.revision}</p>
            <dl className="mad-release-facts">
              <dt>计划负责人</dt><dd>{selectedPlan.owner ?? '未分配有效负责人'}</dd>
              <dt>目标日期</dt><dd>{selectedPlan.target_date ?? '待排期'}{overdue(selectedPlan) && ' · 已逾期'}</dd>
              <dt>实际发布</dt><dd>{selectedPlan.actual_published_at ?? '尚无发布回执'}</dd>
            </dl>
            {selection.kind === 'plan' && selection.milestone !== undefined && selectedPlan.milestones[selection.milestone] &&
              <p>里程碑 {selectedPlan.milestones[selection.milestone].name} · {selectedPlan.milestones[selection.milestone].date}</p>}
            <p>{Object.entries(acceptanceStates).map(([key, label]) => `${label} ${selectedPlan.counts[key as keyof VersionPlan['counts']]}`).join(' · ')}</p>
            <section><h3>版本范围</h3>
              {selectedPlan.requirement_ids.map(id => requirements.get(id)).filter((item): item is ScheduledRequirement => !!item).map(item =>
                <div key={item.id}>{requirementButton(item)}{item.blocker && <small>{blockers[item.blocker]?.label}</small>}</div>)}
              {!selectedPlan.requirement_ids.length && <p className="account-muted">尚未纳入需求。</p>}
            </section>
            <section><h3>发版必过项</h3>
              {selectedPlan.required_checks.length ? <ul>{selectedPlan.required_checks.map((check, index) => <li key={index}>{check}</li>)}</ul> : <p className="account-muted">未设置</p>}
            </section>
            <PlanHistory key={selectedPlan.plan_id} path={path} plan={selectedPlan} requirements={requirements}
              revision={selection.kind === 'plan' ? selection.revision : undefined} onUnavailable={onUnavailable} />
            {manager && <Button onClick={() => setEditor({ plan: selectedPlan })}>调整版本计划</Button>}
          </>}
          {selectedRequirement && <>
            <p><Tag color={selectedRequirement.blocker ? 'warning' : 'default'}>{acceptanceStates[selectedRequirement.acceptance]}</Tag> 需求 v{selectedRequirement.revision}</p>
            <dl className="mad-release-facts"><dt>当前阶段</dt><dd>{displayStage(selectedRequirement)}</dd>
              {selectedRequirement.stage !== 'completed' && <><dt>当前负责人</dt><dd>{selectedRequirement.owner || '未分配'}</dd></>}
              {selectedRequirement.blocker && <>
                <dt>阻塞</dt><dd>{blockers[selectedRequirement.blocker]?.label}</dd>
                <dt>下一步</dt><dd>{blockers[selectedRequirement.blocker]?.next}</dd>
                <dt>处理期限</dt><dd>{selectedRequirement.due_at ? new Date(selectedRequirement.due_at).toLocaleString() : '未设置'}</dd>
              </>}
            </dl>
            <section><h3>验收条件</h3><ul>{selectedRequirement.criteria.map((criterion, index) => <li key={index}>{criterion}</li>)}</ul></section>
            <Button onClick={() => navigate('projects', { project: project.id, page: 'requirements', requirement: selectedRequirement.id })}>打开需求与证据</Button>
            {selectedRequirement.group_id && <Button onClick={() => navigate('chat', { project: project.id, group: selectedRequirement.group_id! })}>打开需求群</Button>}
            {selectedRequirement.sources.map(source => <Button key={source.issue_number} onClick={() => navigate('projects', {
              project: project.id, page: 'issues', issue: String(source.issue_number), sourceRevision: String(source.connection_revision),
            })}>核对 GitHub Issue #{source.issue_number}</Button>)}
          </>}
          {!selectedPlan && !selectedRequirement && <p>当前项目找不到该记录。</p>}
          </div>
        </aside>}
      </div>}
    </div>
    {data && editor && <VersionPlanEditor key={editor.plan?.plan_id ?? 'new'} path={path} data={data} current={editor.plan} userId={userId}
      onUnavailable={onUnavailable} cancel={() => setEditor(undefined)} saved={id => { setEditor(undefined); open({ kind: 'plan', id }); refresh() }} />}
  </section>
}
