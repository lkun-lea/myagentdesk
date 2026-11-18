import { useEffect, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tag } from 'antd'
import { api, errorCode, messageFor } from './accounts-api'
import type { OpenRoute, Project } from './Workbench'

type Reference = {
  repository: string; commit: string; directory: string; change_id: string; tree: string; criteria_revision: number;
}
type Requirement = { id: string; title: string; body: string; revision: number; criteria: string[]; specification: Reference | null }
type CaseResult = { criterion: number; result: 'passed' | 'rejected'; evidence: string }
type Baseline = {
  requirement: Requirement; current_revision: number;
  history: { revision: number; specification: Reference | null; created_at: string }[];
  connection: { connected: boolean; revision: number }; can_confirm: boolean;
  cases: { criterion: number; text: string; affected: boolean; status: keyof typeof caseLabels }[];
  evidence: { run_id: string; candidate_run: string | null; verdict: string; summary: string; evidence: string[];
    case_results: CaseResult[]; current: boolean }[];
}

const caseLabels = {
  pending: '待验收', pending_reverification: '待重新验收', historical: '有历史通过记录，当前候选待验收',
  passed: '当前候选已通过', rejected: '未通过',
}

export function Specifications({ project, requirementId, revision, navigate, onUnavailable }: {
  project: Project; requirementId?: string; revision?: number; navigate: OpenRoute; onUnavailable: (code: string) => void
}) {
  const path = '/projects/' + project.id
  const [requirements, setRequirements] = useState<Requirement[]>()
  const [baseline, setBaseline] = useState<Baseline>()
  const [fault, setFault] = useState('')
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({ directory: '', commit: '', criteria: '' })
  const selected = requirementId ?? requirements?.[0]?.id
  const endpoint = selected ? path + '/requirements/' + selected + '/specification' : undefined

  useEffect(() => {
    let active = true
    void api<Requirement[]>(path + '/requirements').then(value => {
      if (active) setRequirements(value)
    }).catch(error => {
      if (!active) return
      const code = errorCode(error)
      setRequirements([])
      setFault(messageFor(code))
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
    })
    return () => { active = false }
  }, [path, reload, onUnavailable])

  useEffect(() => {
    setBaseline(undefined)
    setEditing(false)
    if (!endpoint) return
    let active = true
    void api<Baseline>(endpoint + (revision ? '?revision=' + revision : '')).then(value => {
      if (active) setBaseline(value)
    }).catch(error => {
      if (!active) return
      const code = errorCode(error)
      setFault(messageFor(code))
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
    })
    return () => { active = false }
  }, [endpoint, revision, reload, onUnavailable])

  function open(id: string, nextRevision?: number) {
    setFault('')
    navigate('projects', { project: project.id, page: 'specs', requirement: id,
      ...(nextRevision ? { specRevision: String(nextRevision) } : {}) })
  }

  async function confirm(value: Baseline) {
    setBusy(true); setFault('')
    try {
      const { title, body, id } = value.requirement
      await api(endpoint!, 'PUT', {
        title, body, expected_revision: value.current_revision, connection_revision: value.connection.revision,
        directory: draft.directory.trim(), commit: draft.commit.trim().toLowerCase(), criteria: draft.criteria.split('\n').map(line => line.trim()),
      })
      open(id)
      setReload(count => count + 1)
    } catch (error) {
      const code = errorCode(error)
      setFault(messageFor(code))
      if (code === 'project_not_found' || code === 'login_required') onUnavailable(code)
    } finally {
      setBusy(false)
    }
  }

  const requirement = baseline?.requirement
  const reference = requirement?.specification
  const historical = baseline && baseline.requirement.revision !== baseline.current_revision
  const treeUrl = reference && `https://github.com/${reference.repository}/tree/${reference.commit}/${reference.directory}`

  return <section className="mad-page" aria-label="固定规格">
    <div className="mad-page-heading"><h2>项目规格</h2>
      <Button onClick={() => { setFault(''); setReload(value => value + 1) }}>刷新规格</Button></div>
    {fault && <Alert type="error" showIcon message={fault} />}
    {(!requirements || (endpoint && !baseline && !fault)) && <Spin />}
    {requirements?.length === 0 && !fault && <p className="account-muted">先在需求页确认一条需求。</p>}
    {!!requirements?.length && <div className="account-actions">
      <Select aria-label="规格所属需求" value={selected} disabled={busy}
        options={requirements.map(item => ({ value: item.id, label: item.title }))} onChange={id => open(id)} />
      {baseline && <Select<number> aria-label="规格历史版本" value={baseline.requirement.revision} disabled={busy}
        options={baseline.history.map(item => ({ value: item.revision,
          label: `需求 v${item.revision}${item.revision === baseline.current_revision ? ' · 当前' : ' · 历史'}` }))}
        onChange={value => open(baseline.requirement.id, value === baseline.current_revision ? undefined : value)} />}
    </div>}
    {baseline && requirement && <>
      <section className="mad-context-card">
        <div className="mad-page-heading"><h3>{requirement.title} · 需求 v{requirement.revision}</h3>
          <Tag color={historical ? 'default' : 'blue'}>{historical ? '历史版本' : '当前版本'}</Tag></div>
        {reference && treeUrl ? <>
          <p>{reference.repository} · {reference.change_id} · 验收条件 v{reference.criteria_revision}</p>
          <p className="account-muted">固定提交 <code>{reference.commit}</code></p>
          <div className="account-actions">
            <a href={treeUrl} target="_blank" rel="noreferrer">查看固定变更目录</a>
            <a href={`https://github.com/${reference.repository}/blob/${reference.commit}/${reference.directory}/proposal.md`} target="_blank" rel="noreferrer">查看固定提案</a>
          </div>
        </> : <p className="account-muted">该需求版本暂无固定 OpenSpec 规格。</p>}
        <div className="account-actions">
          <Button onClick={() => navigate('projects', { project: project.id, page: 'requirements', requirement: requirement.id })}>返回需求与交接</Button>
          {!historical && baseline.can_confirm && baseline.connection.connected && !editing && <Button type="primary" onClick={() => {
            setDraft({ directory: reference?.directory ?? 'openspec/changes/', commit: reference?.commit ?? '', criteria: requirement.criteria.join('\n') })
            setEditing(true)
          }}>{reference ? '更换规格与确认条件' : '固定 OpenSpec 规格'}</Button>}
          {!historical && baseline.can_confirm && !baseline.connection.connected && <Button
            onClick={() => navigate('projects', { project: project.id, page: 'issues' })}>先连接公开 GitHub 仓库</Button>}
        </div>
      </section>
      {editing && <form className="mad-context-form" onSubmit={event => { event.preventDefault(); void confirm(baseline) }}>
        <h3>确认新的规格基线</h3>
        <label>OpenSpec 变更目录<Input required value={draft.directory} maxLength={140}
          onChange={event => setDraft({ ...draft, directory: event.target.value })} /></label>
        <label>完整提交 SHA<Input required value={draft.commit} pattern="[a-fA-F0-9]{40}"
          onChange={event => setDraft({ ...draft, commit: event.target.value })} /></label>
        <label>验收条件，每行一条<Input.TextArea required rows={4} value={draft.criteria}
          onChange={event => setDraft({ ...draft, criteria: event.target.value })} /></label>
        <p className="account-muted">保存后创建需求新版本，已有原始验收记录可以按历史版本查看。</p>
        <div className="account-actions"><Button type="primary" htmlType="submit" loading={busy}>确认规格与验收条件</Button>
          <Button disabled={busy} onClick={() => setEditing(false)}>取消</Button></div>
      </form>}
      <section className="mad-context-card"><h3>验收条件</h3>
        <ol>{requirement.criteria.map((text, index) => {
          const item = baseline.cases[index]
          return <li key={index}>{text} {item && <Tag color={item.status === 'passed' ? 'green' : item.affected ? 'orange' : 'default'}>{caseLabels[item.status]}</Tag>}</li>
        })}</ol>
      </section>
      <section className="mad-context-card"><h3>该版本的验收证据</h3>
        {!baseline.evidence.length && <p className="account-muted">该版本暂无独立验收记录。</p>}
        {baseline.evidence.map(item => <details key={item.run_id}>
          <summary>{item.verdict === 'passed' ? '通过' : '退回'} · {item.current ? '当前有效' : '历史证据'} · {item.run_id.slice(0, 8)}</summary>
          <p>{item.summary}</p><p>候选运行 <code>{item.candidate_run ?? '—'}</code></p>
          <ul>{item.evidence.map((proof, i) => <li key={i}>{proof}</li>)}</ul>
          {item.case_results.length > 0 && <ul>{item.case_results.map(result => <li key={result.criterion}>
            条件 {result.criterion} · {result.result === 'passed' ? '通过' : '未通过'} · {result.evidence}
          </li>)}</ul>}
        </details>)}
      </section>
    </>}
  </section>
}
