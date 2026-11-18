import '@douyinfe/semi-ui/react19-adapter'
import '@douyinfe/semi-ui/lib/es/_base/base.css'
import { useId, useLayoutEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import Button from '@douyinfe/semi-ui/lib/es/button'
import Table from '@douyinfe/semi-ui/lib/es/table'
import Tag from '@douyinfe/semi-ui/lib/es/tag'
import {
  IconAlertCircle, IconArrowLeft, IconClose, IconCode, IconExternalOpen, IconTickCircle,
} from '@douyinfe/semi-icons'
import { issuesByNumber, roadmapIssues } from './roadmap'

const gmailIssue = issuesByNumber.get(18)!
const repositoryIssuesUrl = new URL('.', gmailIssue.url).href
const loginPullRequest = 'https://github.com/lkun-lea/myagentdesk/pull/17'
type RoadmapIssue = (typeof roadmapIssues)[number]
const displayedIssues = [18, 16, 14, 11, 9, 7, 6, 3].map(number => issuesByNumber.get(number)!)
const owner = (issue: RoadmapIssue) => issue.assignees.join('、') || '未分配'
const updatedDate = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
})

const stages = {
  triage: {
    name: '问题确认', status: '已记录', tone: 'complete', heading: '复现记录',
    reason: '申请 Gmail 验证码后，Brevo 显示 Delivered，Gmail 收件箱未找到同一封邮件。',
    next: '保留发码回执，交由研发与独立验收核对。',
    evidence: [{ label: '环境与复现步骤', status: '有记录', color: 'green', url: gmailIssue.url }],
  },
  fix: {
    name: '兼容修复', status: '有记录', tone: 'complete', heading: '修复内容',
    reason: '登录支持 6 至 10 位验证码。Gmail 实际投递仍待核对。',
    next: '分别验证 QQ 与 Gmail 实际收件。',
    evidence: [{ label: '验证码长度回归', status: '有记录', color: 'green', url: loginPullRequest }],
  },
  qq: {
    name: 'QQ 实收登录', status: '有通过记录', tone: 'complete', heading: '历史实测',
    reason: 'QQ 收件箱实际收到验证码，随后成功登录。',
    next: '修复 Gmail 投递后，用同一配置复跑 QQ。',
    evidence: [{ label: 'QQ 实收并登录', status: '已记录', color: 'green', url: loginPullRequest }],
  },
  gmail: {
    name: 'Gmail 实收登录', status: '阻塞', tone: 'blocked', heading: '阻塞原因',
    reason: 'Brevo 显示已投递，Gmail 收件箱未找到邮件。测试人员从 Brevo 预览取码后成功登录。',
    next: '关联同一次发码、投递与收件记录，用 Gmail 实收验证码完成登录。',
    evidence: [
      { label: 'Brevo 投递回执', status: 'Delivered', color: 'green', url: gmailIssue.url },
      { label: 'Gmail 收件箱实收', status: '待验证', color: 'red', url: gmailIssue.url },
      { label: 'Brevo 预览取码登录', status: '替代验证', color: 'grey', url: loginPullRequest },
    ],
  },
  quality: {
    name: '质量复核', status: '等待验证', tone: 'waiting', heading: '进入条件',
    reason: 'Gmail 实际收件并登录，同配置下 QQ 复跑通过，证据关联到对应测试。',
    next: '证据齐全后再做质量复核。',
    evidence: [{ label: '独立验收标准', status: '待核对', color: 'grey', url: gmailIssue.url }],
  },
} as const
type Stage = keyof typeof stages
const stageOrder = ['triage', 'fix', 'qq', 'gmail', 'quality'] as const
const connections: ReadonlyArray<readonly [Stage, Stage]> = [
  ['triage', 'fix'], ['fix', 'qq'], ['fix', 'gmail'], ['qq', 'quality'], ['gmail', 'quality'],
]
const stageColor = { complete: 'green', blocked: 'red', waiting: 'grey' } as const

function GitHubStatus({ issue }: { issue: RoadmapIssue }) {
  const open = issue.state === 'OPEN'
  return <Tag size="small" color={open ? 'green' : 'grey'}>{open ? '开放' : '已关闭'}</Tag>
}

function Connectors({ graph }: { graph: HTMLDivElement }) {
  const path = useRef<SVGPathElement>(null)
  useLayoutEffect(() => {
    const buttons = Array.from(graph.querySelectorAll<HTMLButtonElement>('[data-stage]'))
    const draw = () => {
      const frame = graph.getBoundingClientRect()
      const nodes = Object.fromEntries(buttons.map(button => [button.dataset.stage, button.getBoundingClientRect()]))
      const vertical = nodes.fix.top > nodes.triage.bottom
      const edges = connections.map(([from, to]) => {
        const start = nodes[from]
        const end = nodes[to]
        if (vertical) {
          const x1 = start.left - frame.left + start.width / 2
          const x2 = end.left - frame.left + end.width / 2
          const y1 = start.bottom - frame.top
          const y2 = end.top - frame.top
          const mid = (y1 + y2) / 2
          return `M${x1} ${y1} C${x1} ${mid} ${x2} ${mid} ${x2} ${y2}`
        }
        const x1 = start.right - frame.left
        const x2 = end.left - frame.left
        const y1 = start.top - frame.top + start.height / 2
        const y2 = end.top - frame.top + end.height / 2
        const mid = (x1 + x2) / 2
        return `M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`
      })
      path.current?.setAttribute('d', edges.join(' '))
    }
    draw()
    const observer = new ResizeObserver(draw)
    observer.observe(graph)
    return () => observer.disconnect()
  }, [graph])
  return <svg className="demo-flow-edges" aria-hidden="true"><path ref={path} /></svg>
}

type View = { page: 'list' } | { page: 'flow'; stage: Stage | null }

export default function DemoRequirements() {
  const [view, setView] = useState<View>({ page: 'list' })
  const [graph, setGraph] = useState<HTMLDivElement | null>(null)
  const region = useRef<HTMLElement>(null)
  const focusTarget = useRef<string | null>(null)
  const listOrigin = useRef('issue-title')
  const panelId = useId()
  const selected = view.page === 'flow' ? view.stage : null
  const detail = selected ? stages[selected] : null

  useLayoutEffect(() => {
    if (!focusTarget.current) return
    const target = region.current?.querySelector<HTMLElement>(`[data-focus="${focusTarget.current}"]`)
    focusTarget.current = null
    target?.focus({ preventScroll: true })
    target?.scrollIntoView({ block: 'nearest' })
  }, [view])

  function openFlow(stage: Stage | null, origin: string) {
    listOrigin.current = origin
    focusTarget.current = stage ?? 'back'
    setView({ page: 'flow', stage })
  }

  function openStage(stage: Stage) {
    setView({ page: 'flow', stage })
  }

  function closeStage() {
    focusTarget.current = selected
    setView({ page: 'flow', stage: null })
  }

  function backToList() {
    focusTarget.current = listOrigin.current
    setView({ page: 'list' })
  }

  function handleEscape(event: KeyboardEvent) {
    if (event.key !== 'Escape' || view.page !== 'flow' || event.defaultPrevented) return
    event.stopPropagation()
    if (selected) closeStage()
    else backToList()
  }

  return <section ref={region} className="demo-requirements" aria-label="项目需求" onKeyDown={handleEscape}>
    {view.page === 'list' ? <>
      <header className="demo-requirements-toolbar">
        <h2>需求列表</h2>
        <div><span>{displayedIssues.length} 条 Issue 摘录</span>
          <a href={repositoryIssuesUrl} target="_blank" rel="noreferrer">GitHub <IconExternalOpen aria-hidden="true" /></a>
        </div>
      </header>
      <Table<RoadmapIssue> className="demo-requirements-table" size="small" pagination={false}
        rowKey="number" dataSource={displayedIssues}
        onRow={item => ({ className: item?.number === gmailIssue.number ? 'demo-requirements-highlight' : undefined })}
        columns={[
          { title: '标题', dataIndex: 'title', render: (title: string, row: RoadmapIssue) => {
            const label = <><span className="demo-issue-number">#{row.number}</span><span>{title}</span></>
            return row.number === gmailIssue.number
              ? <button className="demo-requirement-title" type="button" data-focus="issue-title"
                  onClick={() => openFlow(null, 'issue-title')}>{label}</button>
              : <a className="demo-requirement-title" href={row.url} target="_blank" rel="noreferrer">{label}</a>
          } },
          { title: '负责人', dataIndex: 'assignees', className: 'demo-requirement-owner-column', width: 78,
            render: (_: unknown, row: RoadmapIssue) => owner(row) },
          { title: 'GitHub 状态', dataIndex: 'state', className: 'demo-requirement-state-column', width: 92,
            render: (_: unknown, row: RoadmapIssue) => <GitHubStatus issue={row} /> },
          { title: '当前阶段', key: 'stage', className: 'demo-requirement-stage-column', width: 104,
            render: (_: unknown, row: RoadmapIssue) => row.number === gmailIssue.number
              ? <Button className="demo-requirement-blocked" size="small" theme="light" type="danger" icon={<IconAlertCircle aria-hidden="true" />}
                  data-focus="issue-blocked" onClick={() => openFlow('gmail', 'issue-blocked')}>验收阻塞</Button> : '—' },
          { title: '更新', dataIndex: 'updatedAt', className: 'demo-requirement-date-column', width: 74,
            render: (timestamp?: string) => timestamp
              ? <time dateTime={timestamp}>{updatedDate.format(new Date(timestamp)).slice(5)}</time> : '—' },
        ]} />
      <footer className="demo-requirements-footer">#18 的当前阶段为流程示意</footer>
    </> : <>
      <header className="demo-requirement-header">
        <div className="demo-requirement-actions">
          <Button size="small" theme="borderless" type="tertiary" icon={<IconArrowLeft aria-hidden="true" />}
            data-focus="back" onClick={backToList}>需求列表</Button>
          <a href={gmailIssue.url} target="_blank" rel="noreferrer">GitHub #{gmailIssue.number} <IconExternalOpen aria-hidden="true" /></a>
        </div>
        <div className="demo-requirement-heading">
          <span className="demo-requirement-symbol" aria-hidden="true"><IconAlertCircle /></span>
          <h2>{gmailIssue.title}</h2><GitHubStatus issue={gmailIssue} /><Tag size="small" color="red">验收阻塞</Tag>
        </div>
        <div className="demo-requirement-roles"><span>流程角色 · 示意</span>
          <Tag size="small" shape="circle" color="blue" prefixIcon={<IconCode aria-hidden="true" />}>研发</Tag>
          <Tag size="small" shape="circle" color="green" prefixIcon={<IconTickCircle aria-hidden="true" />}>独立验收</Tag>
          <Tag size="small" shape="circle" color="violet">质量负责人</Tag>
        </div>
      </header>
      <div className="demo-flow-layout" data-panel-open={detail ? '' : undefined}>
        <section className="demo-flow" aria-label="需求流程">
          <header><h3>需求流程</h3><span>依据 Issue 和 PR 记录编排</span></header>
          <div ref={setGraph} className="demo-flow-graph">
            {graph && <Connectors graph={graph} />}
            {stageOrder.map(key => {
              const stage = stages[key]
              return <button key={key} type="button" className="demo-flow-node" data-stage={key} data-tone={stage.tone}
                data-focus={key} aria-label={`${stage.name}，${stage.status}`}
                aria-expanded={selected === key} aria-controls={detail ? panelId : undefined}
                onClick={() => openStage(key)}>
                <i aria-hidden="true" />{stage.name}
                {stage.tone === 'blocked' && <span aria-hidden="true"><IconAlertCircle size="small" /></span>}
              </button>
            })}
          </div>
          <p>两项收件验收通过后进入质量复核</p>
        </section>
        {detail && <aside id={panelId} className="demo-stage-panel" aria-label="阶段详情">
          <header><span>阶段详情</span><Button size="small" theme="borderless" type="tertiary"
            icon={<IconClose aria-hidden="true" />} aria-label="关闭阶段详情" onClick={closeStage} /></header>
          <div className="demo-stage-content" aria-live="polite">
            <div className="demo-stage-heading"><h3>{detail.name}</h3><Tag size="small" color={stageColor[detail.tone]}>{detail.status}</Tag></div>
            <dl className="demo-stage-owner"><dt>负责人</dt><dd>{owner(gmailIssue)} <small>· GitHub</small></dd></dl>
            <div className="demo-stage-reason" data-blocked={detail.tone === 'blocked' ? '' : undefined}>
              <h4>{detail.heading}</h4><p>{detail.reason}</p>
            </div>
            <h4 className="demo-stage-evidence-heading">验收证据</h4>
            <ul className="demo-stage-evidence">{detail.evidence.map(item => <li key={item.label}>
              <a href={item.url} target="_blank" rel="noreferrer">{item.label} <IconExternalOpen aria-hidden="true" /></a>
              <Tag size="small" color={item.color}>{item.status}</Tag>
            </li>)}</ul>
            <p className="demo-stage-next"><strong>下一步</strong>{detail.next}</p>
          </div>
        </aside>}
      </div>
    </>}
  </section>
}
