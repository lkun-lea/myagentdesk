import { StrictMode, useEffect, useRef, useState } from 'react'
import type { FormEvent, MouseEvent, ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import {
  App as AntApp, Avatar, Button, ConfigProvider, Input, Modal, Select, Steps, Tag, Timeline, theme,
} from 'antd'
import {
  ArrowLeftOutlined, ArrowRightOutlined,
  CheckOutlined, CodeOutlined, FileTextOutlined,
  FolderOpenOutlined, GithubOutlined, HistoryOutlined, LinkOutlined, MoonOutlined,
  ReloadOutlined, RocketOutlined, SafetyCertificateOutlined, SunOutlined, TeamOutlined,
} from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'
import {
  applyAction, approved, buildId, commitId, currentPlan, currentStep, digest, fresh,
  issue, loadDemo, minDate, nextAction, people, requiredRole, saveDemo, specId,
  suggestedDate, verified, version,
} from './demo'
import type { Action, DemoState, Role } from './demo'
import { IssueDetails, IssuesList, IssueTimeline } from './ProjectRoadmap'
import { issuesByNumber, roadmapIssues } from './roadmap'
import { AccountWorkspace } from './AccountWorkspace'
import './style.css'

const tabs = ['时间线', '需求', '规格', 'Issues', '构建与发布'] as const
type Tab = typeof tabs[number]
const stageNames = ['版本排期', '规格确认', '研发执行', '独立验收', '质量放行', '发布回执']
const actionLabels: Record<Action['kind'], string> = {
  schedule: '安排版本', spec: '确认规格与验收条件', dispatch: '派发给 tebb',
  disconnect: '模拟启动回执丢失', recover: '核对并恢复原运行', finish: '模拟本机完成',
  requestDecision: '请求产品决策', resolveDecision: '确认按原请求接续',
  upload: '补传候选与证据', replay: '重放完成回执', verify: '提交独立验收',
  rebuild: '模拟集成包变更', approve: '确认质量放行', release: '提交模拟发布', receipt: '接收模拟发布回执',
}
const actionHints: Record<Action['kind'], string> = {
  schedule: '把这条需求纳入版本，并设定目标发布日期。',
  spec: '确认重连与回执去重的两条必过条件。',
  dispatch: '将固定的需求和验收条件派给已配对的 tebb 设备。',
  disconnect: '', recover: '原设备的启动回执缺失。先核对原运行，保持同一派发。',
  requestDecision: '', resolveDecision: '启动结果未知时，确认先核对原请求，再从检查点 CP-02 接续。',
  finish: 'tebb 已领取。可模拟断网、请求产品决策，或生成本机执行结果。',
  upload: '本机已完成。接收候选、命令输出和完成回执后才能交给测试。',
  replay: '', verify: '按冻结用例复跑当前候选，记录同一构建的回归结果。',
  rebuild: '', approve: '核对当前集成提交、构建、产物摘要和独立验收结果。',
  release: '质量已放行。由发布负责人将对应构建提交到模拟渠道。',
  receipt: '发布请求已提交。收到同一构建和渠道的回执后更新时间线。',
}

function Person({ role }: { role: Role }) {
  const person = people[role]
  return <span className="person">
    <Avatar size={27} className={`avatar avatar-${person.color}`}>{person.name[0]}</Avatar>
    <span><strong>{person.name}</strong><small>{person.role}</small></span>
  </span>
}

function SectionTitle({ icon, title, extra }: { icon: ReactNode; title: string; extra?: ReactNode }) {
  return <div className="section-title"><h2>{icon}<span>{title}</span></h2>{extra}</div>
}

function Records({ state }: { state: DemoState }) {
  return state.events.length
    ? <Timeline className="events" items={[...state.events].reverse().map(entry => ({
      children: <><strong>{entry.label}</strong><p>{entry.detail}</p><small>{people[entry.actor].name} · {people[entry.actor].role}</small></>,
    }))} />
    : <div className="empty-line">确认排期后，版本决定与交接记录会显示在这里。</div>
}

function EvidencePanel({ state }: { state: DemoState }) {
  return <section className="panel evidence-panel">
    <SectionTitle icon={<SafetyCertificateOutlined />} title="验收证据" />
    {!state.evidence.length ? <div className="empty-line">候选回传后，由独立测试成员按固定用例复跑。</div> :
      <div className="evidence-list">
        {[...state.evidence].reverse().map(item => <div className="evidence-item" key={item.candidate}>
          <div className="evidence-head"><strong>候选 {item.candidate} · {buildId(item.candidate)}</strong>
            <Tag color={item.candidate === state.candidate ? 'green' : 'default'}>{item.candidate === state.candidate ? '通过' : '旧候选 · 已失效'}</Tag></div>
          <span>周宁 · 独立测试 · {specId}</span>
          <ul>{issue.criteria.map(criterion => <li key={criterion}><CheckOutlined />{criterion}</li>)}</ul>
          <code>test:reconnect · 2 passed · exit 0</code>
        </div>)}
      </div>}
  </section>
}

function BuildPanel({ state }: { state: DemoState }) {
  return <section className="panel build-panel">
    <SectionTitle icon={<RocketOutlined />} title="构建与发布" />
    {!state.candidate ? <div className="empty-line">等待 tebb 回传候选和证据。</div> : <>
      <dl className="details">
        <div><dt>构建</dt><dd>{buildId(state.candidate)}</dd></div>
        <div><dt>集成提交</dt><dd><code>{commitId(state.candidate)}</code></dd></div>
        <div><dt>产物摘要</dt><dd><code>sha256:{digest(state.candidate)}</code></dd></div>
        <div><dt>质量放行</dt><dd>{approved(state) ? '已放行' : '待确认'}</dd></div>
      </dl>
      <div className="receipt">
        <strong>{state.receipt ? state.receipt.id + ' · 模拟发布' : state.releasePending ? '等待渠道回执' : '尚未提交发布'}</strong>
        <span>{state.receipt ? `${state.receipt.channel} · ${state.receipt.date} · 计划修订 ${state.receipt.revision}` : 'Windows 稳定版 · 模拟渠道'}</span>
      </div>
    </>}
  </section>
}

function Workspace({ dark, toggleTheme, onAccounts }: { dark: boolean; toggleTheme: () => void; onAccounts: () => void }) {
  const { message } = AntApp.useApp()
  const [state, setState] = useState(loadDemo)
  const [role, setRole] = useState<Role>('owner')
  const [tab, setTab] = useState<Tab>('时间线')
  const [history, setHistory] = useState(false)
  const [selection, setSelection] = useState<{ number: number; pilot: boolean } | null>(null)
  const mainRef = useRef<HTMLDivElement>(null)
  const origin = useRef<HTMLElement | null>(null)
  const [editing, setEditing] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [date, setDate] = useState(suggestedDate)
  const [reason, setReason] = useState('')
  const [formError, setFormError] = useState('')
  const plan = currentPlan(state)
  const action = nextAction(state)
  const actor = action ? requiredRole[action] : null
  const canEdit = !state.releasePending && !state.receipt && role === 'owner'
  const blocker = state.dispatch?.status === 'unknown' ? '启动结果待核对 · 等待顾言确认原设备'
    : state.dispatch?.status === 'decision' ? '等待陈舟确认 · 检查点 CP-02' : null
  const stage = currentStep(state)

  useEffect(() => saveDemo(state), [state])
  useEffect(() => {
    if (!selection) return
    const close = mainRef.current?.querySelector<HTMLButtonElement>('[data-drawer-close]')
    close?.focus({ preventScroll: true })
    if (window.matchMedia('(max-width: 730px)').matches) close?.scrollIntoView({ block: 'nearest' })
  }, [selection])

  function navigate(page: Tab) {
    setSelection(null)
    origin.current = null
    setTab(page)
  }

  function openIssue(number: number, event: MouseEvent<HTMLElement>, pilot = false) {
    if (!event.currentTarget.closest('.issue-drawer')) origin.current = event.currentTarget
    setSelection({ number, pilot })
  }

  function closeIssue() {
    setSelection(null)
    const trigger = origin.current
    origin.current = null
    if (trigger?.isConnected) {
      trigger.focus({ preventScroll: true })
      if (window.matchMedia('(max-width: 730px)').matches) trigger.scrollIntoView({ block: 'nearest' })
    }
  }

  function execute(command: Action) {
    const result = applyAction(state, role, command)
    if (result.error) { message.error(result.error); return false }
    setState(result.state)
    if (result.info) message.info(result.info)
    return true
  }

  function editPlan() {
    setDate(plan?.date ?? suggestedDate)
    setReason('')
    setFormError('')
    setEditing(true)
  }

  function submitPlan(event: FormEvent) {
    event.preventDefault()
    if (!reason.trim() || date < minDate || date === plan?.date) {
      setFormError('请选择新的目标日期，并填写排期原因。')
      return
    }
    if (execute({ kind: 'schedule', date, reason })) setEditing(false)
  }

  function primary() {
    if (!action) return
    if (action === 'schedule') editPlan()
    else execute({ kind: action })
  }

  function reset() {
    setState(fresh())
    setRole('owner')
    navigate('时间线')
    setHistory(false)
    setResetting(false)
  }

  const secondaryActions = [
    action === 'finish' ? 'disconnect' : null,
    action === 'finish' && !state.dispatch?.decision ? 'requestDecision' : null,
    state.candidate && !state.releasePending && !state.receipt ? 'replay' : null,
    state.candidate === 1 && verified(state) && !state.releasePending && !state.receipt ? 'rebuild' : null,
  ].filter((kind): kind is 'disconnect' | 'requestDecision' | 'replay' | 'rebuild' => kind !== null)

  const pilotControls = <div className="pilot-controls">
    <div className="pilot-heading"><strong>{version} · 模拟试点</strong><a href={issue.url} target="_blank" rel="noreferrer">tebb #{issue.number} ↗</a></div>
    <section className="action-panel" aria-label="试点下一步">
      {actor && action ? <>
        <Person role={actor} />
        <h3>{actionLabels[action]}</h3>
        <p>{actionHints[action]}</p>
        {role === actor
          ? <Button type="primary" icon={<ArrowRightOutlined />} onClick={primary}>{actionLabels[action]}</Button>
          : <Button type="primary" icon={<TeamOutlined />} onClick={() => setRole(actor)}>切换到{people[actor].role}</Button>}
      </> : <div className="finished"><CheckOutlined /><div><strong>已收到模拟发布回执</strong><p>{state.receipt?.id} · {state.receipt?.date}</p></div></div>}
      {secondaryActions.length > 0 && <div className="scenario-actions">
        <span>可选场景</span>
        {secondaryActions.map(kind => <Button size="small" key={kind} disabled={role !== requiredRole[kind]} onClick={() => execute({ kind })}>{actionLabels[kind]}</Button>)}
        {role !== 'dev' && <Button size="small" type="link" onClick={() => setRole('dev')}>切换到研发</Button>}
      </div>}
    </section>
    <dl className="details version-details">
      <div><dt>目标发布日期</dt><dd>{plan?.date ?? '待排期'}</dd></div>
      <div><dt>计划修订</dt><dd>{plan ? `第 ${state.plans.length} 版` : '—'}</dd></div>
    </dl>
    <div className="pilot-links">
      {plan && canEdit && <Button size="small" onClick={editPlan}>调整目标日期</Button>}
      <Button size="small" type="link" onClick={() => navigate('需求')}>试点需求</Button>
      <Button size="small" type="link" onClick={() => navigate('构建与发布')}>构建与发布</Button>
      {state.events.length > 0 && <Button size="small" type="link" onClick={() => { navigate('时间线'); setHistory(true) }}>查看版本决定</Button>}
    </div>
  </div>

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand" aria-label="MyAgentDesk"><span className="brand-mark">m.</span></div>
      <button className="side-project" aria-current="page" aria-label="打开 MyAgentDesk 项目" onClick={() => { navigate('时间线'); setHistory(false) }}>
        <FolderOpenOutlined /><span>项目</span>
      </button>
    </aside>
    <div className="app-body">
      <header className="topbar">
        <div className="breadcrumb"><span>项目</span><span aria-hidden="true">/</span><strong>MyAgentDesk</strong></div>
        <div className="top-actions">
          <Button type="text" onClick={onAccounts}>账号工作区</Button>
          <Button type="text" onClick={event => openIssue(1, event, true)}>试点演示</Button>
          <label className="role-picker"><TeamOutlined /><span>演示身份</span><Select
            aria-label="演示身份" value={role} onChange={setRole} popupMatchSelectWidth={190}
            options={(Object.keys(people) as Role[]).map(key => ({ value: key, label: `${people[key].name} · ${people[key].role}` }))}
          /></label>
          <Button type="text" icon={dark ? <SunOutlined /> : <MoonOutlined />} aria-label={dark ? '切换浅色主题' : '切换深色主题'} onClick={toggleTheme} />
          <Button type="text" icon={<ReloadOutlined />} aria-label="重置演示" onClick={() => setResetting(true)} />
        </div>
      </header>
      <main className="workspace">
        <header className="project-head">
          <div className="project-name"><span className="project-icon"><FolderOpenOutlined /></span>
            <div className="title-row"><h1>MyAgentDesk</h1></div>
          </div>
          <a className="github-link" href={issuesByNumber.get(1)?.url} target="_blank" rel="noreferrer"><GithubOutlined /> 首版路线图 #1<LinkOutlined /></a>
        </header>
        <nav className="project-tabs" aria-label="项目页面">
          {tabs.map(item => <button key={item} type="button" aria-current={tab === item ? 'page' : undefined}
            onClick={() => { navigate(item); if (item === '时间线') setHistory(false) }}>{item}{item === 'Issues' && ` ${roadmapIssues.length}`}</button>)}
        </nav>
        <div ref={mainRef} className={`content-grid ${selection ? 'has-drawer' : ''}`} onKeyDown={event => {
          if (event.key === 'Escape' && selection && !editing && !resetting) closeIssue()
        }}>
          <div className="main-column">
            {tab === '时间线' && <>
              <section className="calendar-page" aria-label="项目时间线">
                <div className="calendar-toolbar">
                  <h2>{history ? '试点事件记录' : '首版交付'}</h2>
                  {!history && <span>11 月 16–27 日 · 排期示意</span>}
                  <div className="view-switch">
                    <Button size="small" type={history ? 'text' : 'default'} aria-pressed={!history} onClick={() => setHistory(false)}>版本</Button>
                    <Button size="small" type={history ? 'default' : 'text'} aria-pressed={history} icon={<HistoryOutlined />} onClick={() => setHistory(true)}>事件记录</Button>
                  </div>
                </div>
                {history ? <div className="history-body"><Records state={state} /></div>
                  : <IssueTimeline selected={selection?.number ?? null} open={openIssue} showAll={() => navigate('Issues')}
                    blocker={blocker ? `模拟阻塞 · ${blocker}` : null}
                    openBlocker={event => openIssue(14, event, true)} />}
              </section>
            </>}
            {tab === '需求' && <>
              <section className="panel">
                <SectionTitle icon={<FileTextOutlined />} title="需求详情" extra={<Tag color="blue">#{issue.number}</Tag>} />
                <h3 className="requirement-title">{issue.demoScope}</h3>
                <p className="muted">{issue.title}</p>
                <Steps size="small" current={state.receipt ? stageNames.length : stage} items={stageNames.map(title => ({ title }))} />
                <dl className="details">
                  <div><dt>当前阶段</dt><dd>{state.receipt ? '已收到模拟发布回执' : stageNames[stage]}</dd></div>
                  <div><dt>派发编号</dt><dd>{state.dispatch?.id ?? '待派发'}</dd></div>
                  <div><dt>设备与启动</dt><dd>{state.dispatch ? 'DEV-01 · 启动 1 次' : '—'}</dd></div>
                  <div><dt>完成通知</dt><dd>{state.candidate ? '1 条' : '0 条'}</dd></div>
                </dl>
                {state.candidate > 0 && <details className="artifact">
                  <summary><CodeOutlined /> reconnect-task.patch</summary>
                  <dl className="details">
                    <div><dt>本机运行</dt><dd>RUN-0313-01</dd></div>
                    <div><dt>回传来源</dt><dd>tebb · 研发自测 · 模拟</dd></div>
                    <div><dt>原始候选</dt><dd>{buildId(1)} · {commitId(1)}</dd></div>
                  </dl>
                  <pre>{'test:reconnect\nPASS  reconnect retains task ID\nPASS  replay starts no additional task\nexit 0'}</pre>
                </details>}
                <div className="inline-links">
                  <Button size="small" type="link" onClick={() => navigate('规格')}>查看规格 <ArrowRightOutlined /></Button>
                  <Button size="small" type="link" onClick={() => navigate('构建与发布')}>查看构建 <ArrowRightOutlined /></Button>
                </div>
              </section>
              <EvidencePanel state={state} />
            </>}
            {tab === '规格' && <section className="panel">
              <SectionTitle icon={<FileTextOutlined />} title="规格与验收条件" extra={<Tag color={state.spec ? 'green' : 'default'}>{state.spec ? '产品已确认' : '待确认'}</Tag>} />
              <p className="muted">OpenSpec 示例 · {specId}</p>
              <ol className="criteria">{issue.criteria.map(criterion => <li key={criterion}>{criterion}</li>)}</ol>
              <Button size="small" type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate('需求')}>返回需求</Button>
            </section>}
            {tab === 'Issues' && <IssuesList selected={selection?.number ?? null} open={openIssue} />}
            {tab === '构建与发布' && <><BuildPanel state={state} /><EvidencePanel state={state} /></>}
          </div>
          {selection && <IssueDetails key={`${selection.number}-${selection.pilot}`} number={selection.number}
            open={openIssue} close={closeIssue} showPilot={selection.pilot}
            pilot={[1, 6, 11, 14, 16].includes(selection.number) ? pilotControls : undefined} />}
        </div>
      </main>
    </div>
    <Modal open={editing} title={plan ? '调整目标日期' : `安排 ${version} 发布`} footer={null} onCancel={() => setEditing(false)} destroyOnHidden>
      <form className="plan-form" onSubmit={submitPlan}>
        <label>目标发布日期<Input autoFocus type="date" min={minDate} required value={date} onChange={event => setDate(event.target.value)} /></label>
        <label>排期原因<Input.TextArea rows={3} required value={reason} onChange={event => setReason(event.target.value)} placeholder="例如，为弱网复跑预留时间" /></label>
        <p>{plan ? `${plan.date} → ${date || '待选'} · 影响试点需求 tebb #${issue.number} 的发布日期` : `将试点需求 tebb #${issue.number} 纳入版本必过范围。`}</p>
        {formError && <p role="alert" className="form-error">{formError}</p>}
        <div className="modal-actions"><Button onClick={() => setEditing(false)}>取消</Button><Button type="primary" htmlType="submit">确认排期</Button></div>
      </form>
    </Modal>
    <Modal open={resetting} title="重置演示" okText="重置" cancelText="取消" onOk={reset} onCancel={() => setResetting(false)}>
      <p>清除这个浏览器中的演示记录，回到待排期。</p>
    </Modal>
  </div>
}

function Root() {
  const [dark, setDark] = useState(false)
  const [demo, setDemo] = useState(() => new URLSearchParams(window.location.search).get('demo') === '1')
  useEffect(() => {
    const readLocation = () => setDemo(new URLSearchParams(window.location.search).get('demo') === '1')
    window.addEventListener('popstate', readLocation)
    return () => window.removeEventListener('popstate', readLocation)
  }, [])
  function switchView(useDemo: boolean) {
    window.history.pushState(null, '', useDemo ? '/?demo=1' : '/')
    setDemo(useDemo)
  }
  return <ConfigProvider locale={zhCN} button={{ autoInsertSpace: false }} theme={{
    algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: { colorPrimary: dark ? '#86aeff' : '#315ed7', borderRadius: 8, fontFamily: 'Inter, "Microsoft YaHei UI", "PingFang SC", sans-serif' },
  }}>
    <AntApp className={dark ? 'theme-dark' : 'theme-light'}>
      {demo ? <Workspace dark={dark} toggleTheme={() => setDark(value => !value)} onAccounts={() => switchView(false)} />
        : <AccountWorkspace onDemo={() => switchView(true)} />}
    </AntApp>
  </ConfigProvider>
}

createRoot(document.getElementById('root')!).render(<StrictMode><Root /></StrictMode>)
