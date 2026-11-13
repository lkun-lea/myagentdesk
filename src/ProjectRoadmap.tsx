import { useState } from 'react'
import type { CSSProperties, MouseEvent, ReactNode } from 'react'
import { Button } from 'antd'
import { CloseOutlined } from '@ant-design/icons'
import {
  downstreamIssues, followUp, issueLabel, issuesByNumber, mainPath, openPrerequisites,
  phaseLabels, roadmap, roadmapIssues, snapshotDate,
} from './roadmap'

export type OpenIssue = (number: number, event: MouseEvent<HTMLButtonElement>) => void

const milestones = [
  { issue: 2, label: '基座与账号', day: 17 },
  { issue: 16, label: '版本排期', day: 20 },
  { issue: 14, label: 'tebb 执行', day: 23 },
  { issue: 11, label: '验收与发布', day: 26 },
]

export function IssueTimeline({ selected, open, showAll, blocker, openBlocker }: {
  selected: number | null; open: OpenIssue; showAll: () => void; blocker: string | null
  openBlocker: (event: MouseEvent<HTMLButtonElement>) => void
}) {
  return <>
    <div className="calendar-row calendar-axis">
      <span className="calendar-stub">版本 / Issue</span>
      <div>
        <div className="calendar-weeks"><span>11 月 16–22 日</span><span>11 月 23–27 日</span></div>
        <div className="calendar-days" aria-label="示意日期">
          {Array.from({ length: 12 }, (_, index) => <span key={index}>{index + 16}</span>)}
        </div>
      </div>
    </div>
    <div className="calendar-row calendar-overview">
      <div className="calendar-stub calendar-version">
        <strong>首版试点</strong>
        <button className="inline-button" onClick={event => open(1, event)}>路线图 #1</button>
        <small>目标日期待定</small>
      </div>
      <div className="calendar-grid calendar-milestones">
        <div className="calendar-axis-line" />
        {milestones.map((item, index) => <div key={item.issue}
          className={`calendar-pin ${index % 2 ? 'pin-high' : ''} ${index === 3 ? 'pin-target' : ''}`}
          style={{ '--position': `${(item.day - 15.5) / 12 * 100}%` } as CSSProperties}>
          <button className="inline-button" onClick={event => open(item.issue, event)}>{item.label}</button>
          <span aria-hidden="true" />
        </div>)}
        <div className="calendar-phases">{phaseLabels.map(label => <span key={label}>{label}</span>)}</div>
      </div>
    </div>
    {mainPath.map(number => {
      const blocked = openPrerequisites(number).length > 0
      return <div className="calendar-row calendar-task" key={number} data-selected={selected === number || undefined}>
        <button className="calendar-stub issue-button" aria-expanded={selected === number} onClick={event => open(number, event)}>
          <span className="issue-id">#{number}</span><strong>{issueLabel(number)}</strong>
        </button>
        <div className="calendar-grid calendar-track">
          <button className={`calendar-slot ${blocked ? 'slot-wait' : ''}`}
            style={{ '--phase': roadmap[number]?.phase ?? 0 } as CSSProperties}
            aria-label={`#${number} ${blocked ? '等待前置' : '可安排'}，查看详情`}
            aria-expanded={selected === number} onClick={event => open(number, event)}>
            <span className="status-dot" />{blocked ? '等待前置' : '可安排'}
          </button>
        </div>
      </div>
    })}
    <div className="calendar-footer">
      <span><i className="status-dot status-plan" />计划顺序</span>
      <span><i className="status-dot status-wait" />等待前置</span>
      {blocker && <button className="inline-button pilot-blocker" onClick={openBlocker}>{blocker}</button>}
      <button className="inline-button all-issues-link" onClick={showAll}>查看全部 Issues →</button>
    </div>
  </>
}

export function IssuesList({ selected, open }: { selected: number | null; open: OpenIssue }) {
  const groups = [
    { title: '路线图', items: roadmapIssues.filter(issue => issue.number === 1) },
    { title: '首版试点', items: roadmapIssues.filter(issue => issue.number !== 1 && !followUp.includes(issue.number)) },
    { title: '试点后交付', items: roadmapIssues.filter(issue => followUp.includes(issue.number)) },
  ]
  return <div className="github-issues">
    <div className="calendar-toolbar"><h2>GitHub Issues</h2><span>{snapshotDate} · 只读快照</span></div>
    {groups.map(group => <section key={group.title}>
      <h3 className="issue-group">{group.title} · {group.items.length}</h3>
      {group.items.map(issue => <div className="issue-list-row" key={issue.number} data-selected={selected === issue.number || undefined}>
        <button className="issue-button" aria-expanded={selected === issue.number} onClick={event => open(issue.number, event)}>
          <span className="issue-id">#{issue.number}</span><span>{issue.title}</span>
        </button>
        <span>{issue.state === 'OPEN' ? '开放' : '已关闭'}</span>
      </div>)}
    </section>)}
  </div>
}

export function IssueDetails({ number, open, close, pilot, showPilot }: {
  number: number; open: OpenIssue; close: () => void; pilot?: ReactNode; showPilot: boolean
}) {
  const [tab, setTab] = useState<'flow' | 'criteria' | 'pilot'>(showPilot ? 'pilot' : 'flow')
  const issue = issuesByNumber.get(number)
  if (!issue) return null
  const item = roadmap[number]
  const waiting = openPrerequisites(number)
  const related = [
    { title: item ? '开发前置' : '首版交付顺序', items: item?.prerequisites ?? mainPath },
    { title: '产品联调', items: item?.integration ?? [] },
    { title: '后续依赖本项', items: downstreamIssues(number) },
  ]
  return <aside className="issue-drawer" aria-labelledby="drawer-title">
    <header className="drawer-header">
      <div><span className="issue-id">Issue #{number}</span><h2 id="drawer-title">{issue.title}</h2></div>
      <Button type="text" size="small" icon={<CloseOutlined />} aria-label="关闭详情" data-drawer-close onClick={close} />
    </header>
    <div className="drawer-meta">
      <span>{issue.state === 'OPEN' ? 'GitHub 开放' : 'GitHub 已关闭'}</span>
      <span>{issue.assignees.join('、') || '未分配'}</span>
      <span>{issue.milestone || '无 GitHub 里程碑'}</span>
    </div>
    <div className="drawer-tabs" aria-label="详情视图">
      <button aria-pressed={tab === 'flow'} onClick={() => setTab('flow')}>流程与依赖</button>
      <button aria-pressed={tab === 'criteria'} onClick={() => setTab('criteria')}>验收标准</button>
      {pilot && <button aria-pressed={tab === 'pilot'} onClick={() => setTab('pilot')}>试点演示</button>}
    </div>
    <div className="drawer-content">
      {tab === 'flow' && <>
        {waiting.length > 0 && <div className="drawer-blocked"><strong>等待开发前置</strong><span>{waiting.map(id => `#${id}`).join('、')} 在 GitHub 仍开放</span></div>}
        {followUp.includes(number) && <p className="muted">首版验收后交付。</p>}
        {related.filter(group => group.items.length).map(group => <section className="related-group" key={group.title}>
          <h3>{group.title}</h3>
          <div>{group.items.map(id => <button key={id} onClick={event => open(id, event)}>#{id} {issueLabel(id)}</button>)}</div>
        </section>)}
      </>}
      {tab === 'criteria' && (issue.criteria.length ? <ul className="drawer-criteria">
        {issue.criteria.map(criterion => <li key={criterion}>{criterion}</li>)}
      </ul> : <p className="muted">在 GitHub 原文查看验收说明。</p>)}
      {tab === 'pilot' && pilot}
      <a className="drawer-source" href={issue.url} target="_blank" rel="noreferrer">在 GitHub 查看 #{number} ↗</a>
    </div>
  </aside>
}
