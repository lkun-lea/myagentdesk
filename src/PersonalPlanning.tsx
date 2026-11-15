import { useEffect, useState } from 'react'
import { Alert, Button, Input, Modal, Popconfirm, Segmented, Spin, Tag } from 'antd'
import { CalendarOutlined, LeftOutlined, PlusOutlined, RightOutlined } from '@ant-design/icons'
import { api, errorCode, isUnauthorized, messageFor } from './accounts-api'
import type { OpenRoute } from './Workbench'

type Task = { id: string; title: string; completed: boolean }
type Appointment = { id: string; title: string; starts_at: string; ends_at: string; task_id: string | null }
type Draft = { id: string; title: string; start: string; end: string; taskId?: string; existing?: boolean }
type Mode = '日' | '周' | '月'
type PlanningProps = { navigate: OpenRoute; taskId?: string; onExpired: () => void }

const dateLabel = new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', weekday: 'short' })
const timeLabel = new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
const monthLabel = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long' })
const localDay = (day = new Date()) => new Date(day.getFullYear(), day.getMonth(), day.getDate())
const addDays = (day: Date, count: number) => new Date(day.getFullYear(), day.getMonth(), day.getDate() + count)
const monday = (day: Date) => addDays(day, -((day.getDay() + 6) % 7))
const iso = (date: Date) => date.toISOString().replace('.000Z', 'Z')
const localInput = (value: Date) => new Date(value.getTime() - value.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)

function bounds(day: Date, mode: Mode): [Date, Date] {
  if (mode === '日') return [day, addDays(day, 1)]
  if (mode === '周') return [monday(day), addDays(monday(day), 7)]
  const first = new Date(day.getFullYear(), day.getMonth(), 1)
  const last = new Date(day.getFullYear(), day.getMonth() + 1, 0)
  return [monday(first), addDays(monday(last), 7)]
}

function newDraft(day: Date, task?: Task): Draft {
  const start = new Date(day)
  start.setHours(9)
  const end = new Date(start.getTime() + 60 * 60 * 1000)
  return { id: crypto.randomUUID(), title: task?.title ?? '', start: localInput(start), end: localInput(end), taskId: task?.id }
}

export function PersonalTasks({ navigate, taskId, onExpired }: PlanningProps) {
  const [tasks, setTasks] = useState<Task[]>()
  const [title, setTitle] = useState('')
  const [operation, setOperation] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  function failure(value: unknown) {
    if (isUnauthorized(value)) onExpired()
    else setError(messageFor(errorCode(value)))
  }
  useEffect(() => {
    let current = true
    api<Task[]>('/tasks').then(items => { if (current) setTasks(items) }).catch(value => {
      if (!current) return
      if (isUnauthorized(value)) onExpired()
      else setError(messageFor(errorCode(value)))
    })
    return () => { current = false }
  }, [reload, onExpired])

  async function change(path: string, method: string, body: object) {
    setBusy(true); setError('')
    try {
      await api(path, method, body)
      if (method === 'POST') { setTitle(''); setOperation('') }
      setReload(value => value + 1)
    } catch (value) { failure(value) }
    finally { setBusy(false) }
  }

  return <section className="mad-page">
    <h1>事项</h1>
    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => {
      setError(''); setReload(value => value + 1)
    }}>重试</Button>} />}
    <form className="account-actions" onSubmit={event => {
      event.preventDefault()
      if (!title.trim() || busy) return
      const id = operation || crypto.randomUUID()
      setOperation(id)
      void change('/tasks', 'POST', { id, title: title.trim() })
    }}>
      <Input aria-label="事项名称" placeholder="事项名称" required maxLength={100} value={title} disabled={busy}
        onChange={event => { setTitle(event.target.value); setOperation('') }} />
      <Button type="primary" htmlType="submit" loading={busy} icon={<PlusOutlined />}>添加事项</Button>
    </form>
    {taskId && tasks && !tasks.some(task => task.id === taskId) && <Alert type="warning" showIcon message="当前账号无法访问该事项。" />}
    {!tasks && !error ? <Spin /> : tasks?.length === 0 ? <p className="account-muted">暂无事项。</p> :
      <ul className="account-list mad-personal-list">{tasks?.map(task => <li key={task.id} aria-current={task.id === taskId ? 'true' : undefined}>
        <span>{task.title}</span>
        <Tag>{task.completed ? '已完成' : '待处理'}</Tag>
        <Button size="small" onClick={() => navigate('calendar', { task: task.id })}>安排时间</Button>
        <Button size="small" disabled={busy} onClick={() => void change('/tasks/' + task.id, 'PATCH', { completed: !task.completed })}>
          {task.completed ? '重新打开' : '完成'}
        </Button>
      </li>)}</ul>}
  </section>
}

export function PersonalCalendar({ navigate, taskId, onExpired }: PlanningProps) {
  const [day, setDay] = useState(localDay)
  const [mode, setMode] = useState<Mode>('周')
  const [appointments, setAppointments] = useState<Appointment[]>()
  const [draft, setDraft] = useState<Draft>()
  const [error, setError] = useState('')
  const [formError, setFormError] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const [first, last] = bounds(day, mode)
  const query = new URLSearchParams({ start: iso(first), end: iso(last) }).toString()
  const days: Date[] = []
  for (let cell = first; cell < last; cell = addDays(cell, 1)) days.push(cell)

  useEffect(() => {
    let active = true
    api<Appointment[]>('/calendar?' + query).then(items => {
      if (active) { setAppointments(items); setError('') }
    }).catch(value => {
      if (!active) return
      if (isUnauthorized(value)) onExpired()
      else { setAppointments(undefined); setError(messageFor(errorCode(value))) }
    })
    return () => { active = false }
  }, [query, reload, onExpired])

  useEffect(() => {
    if (!taskId) return
    let active = true
    api<Task[]>('/tasks').then(items => {
      if (!active) return
      const task = items.find(item => item.id === taskId)
      if (task) { setFormError(''); setDraft(newDraft(localDay(), task)) }
      else setError('当前账号无法访问该事项。')
    }).catch(value => {
      if (!active) return
      if (isUnauthorized(value)) onExpired()
      else setError(messageFor(errorCode(value)))
    })
    return () => { active = false }
  }, [taskId, onExpired])

  function move(amount: number) {
    setAppointments(undefined); setError('')
    setDay(value => mode === '月' ? new Date(value.getFullYear(), value.getMonth() + amount, 1)
      : addDays(value, amount * (mode === '周' ? 7 : 1)))
  }

  function closeDraft() {
    setDraft(undefined); setFormError('')
    if (taskId) navigate('calendar', {}, true)
  }

  async function save(item: Draft, remove = false) {
    setBusy(true); setFormError('')
    try {
      if (remove) await api('/calendar/' + item.id, 'DELETE')
      else {
        const start = new Date(item.start)
        const end = new Date(item.end)
        if (!item.title.trim() || !(start < end) || localInput(start) !== item.start || localInput(end) !== item.end) {
          setFormError('请填写名称，并确认开始时间早于结束时间。'); return
        }
        await api('/calendar/' + item.id, 'PUT', {
          title: item.title.trim(), starts_at: iso(start), ends_at: iso(end), task_id: item.taskId ?? null,
        })
        setDay(localDay(start))
      }
      closeDraft(); setReload(value => value + 1)
    } catch (value) {
      if (isUnauthorized(value)) onExpired()
      else setFormError(messageFor(errorCode(value)))
    } finally { setBusy(false) }
  }

  return <section className="mad-page mad-calendar">
    <div className="mad-page-heading"><h1>日程</h1><Button type="primary" icon={<PlusOutlined />}
      onClick={() => { setFormError(''); setDraft(newDraft(day)) }}>创建日程</Button></div>
    <div className="mad-calendar-toolbar">
      <Button onClick={() => { setError(''); setDay(localDay()) }}>今天</Button>
      <Button icon={<LeftOutlined />} aria-label="上一段时间" onClick={() => move(-1)} />
      <Button icon={<RightOutlined />} aria-label="下一段时间" onClick={() => move(1)} />
      <strong>{mode === '月' ? monthLabel.format(day) : mode === '日' ? dateLabel.format(day)
        : `${dateLabel.format(first)} - ${dateLabel.format(addDays(last, -1))}`}</strong>
      <Segmented<Mode> options={['日', '周', '月']} value={mode} onChange={setMode} aria-label="日程视图" />
    </div>
    {error && <Alert type="error" showIcon message={error} action={<Button size="small" onClick={() => {
      setError(''); setReload(value => value + 1)
    }}>重试</Button>} />}
    {!appointments && !error && <Spin aria-label="正在加载日程" />}
    <div className="mad-calendar-grid" data-mode={mode}>
      {days.map(date => <section key={date.getTime()} className="mad-calendar-cell" aria-label={dateLabel.format(date)}>
        <div className="mad-calendar-date"><time dateTime={localInput(date).slice(0, 10)}>{dateLabel.format(date)}</time>
          <Button type="text" size="small" icon={<PlusOutlined />} aria-label={`${dateLabel.format(date)}添加日程`}
            onClick={() => { setFormError(''); setDraft(newDraft(date)) }} /></div>
        {appointments?.filter(item => new Date(item.starts_at) < addDays(date, 1) && new Date(item.ends_at) > date).map(item =>
          <div key={item.id} className="mad-appointment">
            <button type="button" onClick={() => {
              setFormError(''); setDraft({ id: item.id, title: item.title, start: localInput(new Date(item.starts_at)),
                end: localInput(new Date(item.ends_at)), taskId: item.task_id ?? undefined, existing: true })
            }}><CalendarOutlined /> <time dateTime={item.starts_at}>{timeLabel.format(new Date(item.starts_at))}</time> {item.title}</button>
            {item.task_id && <Button type="link" size="small" onClick={() => navigate('tasks', { task: item.task_id! })}>查看关联事项</Button>}
          </div>)}
      </section>)}
    </div>
    <Modal title={draft?.existing ? '编辑日程' : '创建日程'} open={!!draft} onCancel={closeDraft} maskClosable={!busy}
      footer={draft && <>
        {draft.existing && <Popconfirm title="删除这个日程？" onConfirm={() => save(draft, true)}>
          <Button danger disabled={busy}>删除</Button></Popconfirm>}
        <Button disabled={busy} onClick={closeDraft}>取消</Button>
        <Button type="primary" loading={busy} onClick={() => void save(draft)}>保存日程</Button>
      </>}>
      {formError && <Alert type="error" showIcon message={formError} />}
      {draft && <div className="mad-calendar-fields">
        <label>日程名称<Input value={draft.title} disabled={busy} maxLength={100}
          onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
        <label>开始时间<Input type="datetime-local" value={draft.start} disabled={busy}
          onChange={event => setDraft({ ...draft, start: event.target.value })} /></label>
        <label>结束时间<Input type="datetime-local" value={draft.end} disabled={busy}
          onChange={event => setDraft({ ...draft, end: event.target.value })} /></label>
        {draft.taskId && <p className="account-muted">已关联事项</p>}
      </div>}
    </Modal>
  </section>
}
