import { useState } from 'react'
import { Alert, Button, Input, Modal, Select } from 'antd'
import type { PlanningData, VersionFields, VersionPlan } from './release-plans'
import { api, errorCode, messageFor } from './accounts-api'

const planStatus = { draft: '草拟', committed: '已承诺', cancelled: '取消版本' }

export function VersionPlanEditor({ path, data, current, userId, cancel, saved, onUnavailable }: {
  path: string; data: PlanningData; current?: VersionPlan; userId: string; cancel: () => void
  saved: (id: string) => void; onUnavailable: (code: string) => void
}) {
  const [id] = useState(() => current?.plan_id ?? crypto.randomUUID())
  const [draft, setDraft] = useState<VersionFields>(() => current ? {
    name: current.name, owner_id: current.owner_id, target_date: current.target_date, status: current.status,
    requirement_ids: current.requirement_ids, milestones: current.milestones, required_checks: current.required_checks, reason: '',
  } : { name: '', owner_id: userId, target_date: null, status: 'draft', requirement_ids: [], milestones: [], required_checks: [], reason: '' })
  const [checks, setChecks] = useState(draft.required_checks.join('\n'))
  const [review, setReview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const occupied = new Set(data.plans.filter(plan => plan.plan_id !== id && plan.status !== 'cancelled').flatMap(plan => plan.requirement_ids))
  const labels = new Map(data.requirements.map(item => [item.id, item.title]))
  const added = draft.requirement_ids.filter(item => !current?.requirement_ids.includes(item))
  const removed = current?.requirement_ids.filter(item => !draft.requirement_ids.includes(item)) ?? []
  const items = checks.split('\n').map(item => item.trim()).filter(Boolean)
  const valid = draft.name.trim() && draft.reason.trim() && data.managers.some(person => person.id === draft.owner_id)
    && draft.milestones.every(item => item.name.trim() && item.date) && items.length <= 20 && items.every(item => item.length <= 300)

  function update<Key extends keyof VersionFields>(key: Key, value: VersionFields[Key]) {
    setDraft(old => ({ ...old, [key]: value }))
  }

  async function submit() {
    setBusy(true)
    setError('')
    try {
      await api(`${path}/${id}`, 'PUT', { ...draft, required_checks: items, expected_revision: current?.revision ?? 0 })
      saved(id)
    } catch (failure) {
      const code = errorCode(failure)
      setError(messageFor(code))
      if (code === 'login_required' || code === 'project_not_found') onUnavailable(code)
    } finally { setBusy(false) }
  }

  return <Modal open title={review ? '确认计划变更' : current ? '调整版本计划' : '新建版本计划'}
    onCancel={busy ? undefined : cancel} closable={!busy} keyboard={!busy} maskClosable={false}
    footer={<div className="account-actions">
      <Button disabled={busy} onClick={review ? () => { setReview(false); setError('') } : cancel}>{review ? '返回编辑' : '取消'}</Button>
      <Button type="primary" disabled={!valid} loading={busy} onClick={review ? () => void submit() : () => setReview(true)}>
        {review ? '确认并保存修订' : '预览变更'}</Button>
    </div>}>
    {error && <Alert showIcon type="error" message={error} />}
    {review ? <div className="mad-release-review" aria-label="计划变更影响">
      <p><strong>{draft.name}</strong> · {planStatus[draft.status]}</p>
      <p>目标日期 <strong>{current?.target_date ?? '未设置'} → {draft.target_date ?? '未设置'}</strong></p>
      <p>版本范围 {current?.requirement_ids.length ?? 0} → {draft.requirement_ids.length} 条</p>
      {added.length > 0 && <p>纳入 {added.map(item => labels.get(item)).join('、')}</p>}
      {removed.length > 0 && <p>移出 {removed.map(item => labels.get(item)).join('、')}</p>}
      {draft.status === 'cancelled' && <p>本版本将退出排期，原范围保留在修订记录。</p>}
      <p>负责人 {data.managers.find(person => person.id === draft.owner_id)?.email}</p>
      <p>里程碑 {draft.milestones.map(item => `${item.name} ${item.date}`).join('、') || '未设置'}</p>
      <p>发版必过项 {items.join('、') || '未设置'}</p>
      <p>变更原因 {draft.reason}</p>
    </div> : <div className="mad-context-form mad-release-editor">
      <label>版本名称<Input aria-label="版本名称" maxLength={100} value={draft.name} onChange={event => update('name', event.target.value)} /></label>
      <label>计划负责人<Select aria-label="计划负责人" value={draft.owner_id} onChange={value => update('owner_id', value)}
        options={data.managers.map(person => ({ value: person.id, label: person.email }))} /></label>
      <label>目标日期<Input aria-label="目标日期" type="date" value={draft.target_date ?? ''} onChange={event => update('target_date', event.target.value || null)} /></label>
      <label>计划状态<Select aria-label="计划状态" value={draft.status} onChange={value => update('status', value)}
        options={Object.entries(planStatus).map(([value, label]) => ({ value, label }))} /></label>
      <label>纳入需求<Select aria-label="纳入需求" mode="multiple" maxCount={100} value={draft.requirement_ids} onChange={value => update('requirement_ids', value)}
        options={data.requirements.map(item => ({ value: item.id, label: item.title + (occupied.has(item.id) ? '（已在其他版本）' : ''), disabled: occupied.has(item.id) && !draft.requirement_ids.includes(item.id) }))} /></label>
      <section className="mad-context-form" aria-label="计划里程碑">
        {draft.milestones.map((item, index) => <div className="account-actions" key={index}>
          <Input aria-label={`里程碑 ${index + 1} 名称`} placeholder="里程碑名称" maxLength={100} value={item.name}
            onChange={event => update('milestones', draft.milestones.map((entry, i) => i === index ? { ...entry, name: event.target.value } : entry))} />
          <Input aria-label={`里程碑 ${index + 1} 日期`} type="date" value={item.date}
            onChange={event => update('milestones', draft.milestones.map((entry, i) => i === index ? { ...entry, date: event.target.value } : entry))} />
          <Button aria-label={`移除里程碑 ${index + 1}`} onClick={() => update('milestones', draft.milestones.filter((_, i) => i !== index))}>移除</Button>
        </div>)}
        <Button disabled={draft.milestones.length >= 12} onClick={() => update('milestones', [...draft.milestones, { name: '', date: '' }])}>添加里程碑</Button>
      </section>
      <label>发版必过项（每行一条）<Input.TextArea aria-label="发版必过项" rows={2} maxLength={6020} value={checks} onChange={event => setChecks(event.target.value)} /></label>
      <label>变更原因<Input.TextArea aria-label="变更原因" required rows={2} maxLength={1000} value={draft.reason} onChange={event => update('reason', event.target.value)} /></label>
    </div>}
  </Modal>
}
