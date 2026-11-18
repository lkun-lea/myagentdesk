import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tabs } from 'antd'
import { MoonOutlined, SunOutlined } from '@ant-design/icons'
import { api, errorCode, isUnauthorized, messageFor } from './accounts-api'
import { PersonalCalendar, PersonalTasks } from './PersonalPlanning'
import { MyAgent, ProjectContext } from './ProjectContext'
import { DecisionInbox, ProjectChats } from './RequirementGroups'
import { globalViews, ProjectContent, projectPages, Workbench } from './Workbench'
import type { OpenRoute, Project, ProjectPage, View } from './Workbench'
import './accounts.css'
import './workbench.css'

type User = { id: string; email: string }
type Member = User & { role: Project['role'] }
type Invite = { id: string; email: string; expires_at: string; state: 'pending' | 'accepted' | 'expired' | 'revoked' }
type Activity = { action: string; occurred_at: string }
type Outcome = { outcome: string }
type Configuration = { available: boolean; missing: string[]; invalid: string[] }

const roles = { owner: '所有者', admin: '管理员', member: '成员' }
const inviteStates = { pending: '待接受', accepted: '已接受', expired: '已过期', revoked: '已撤销' }
const activities: Record<string, string> = {
  project_created: '创建项目', invite_created: '创建邀请', invite_accepted: '接受邀请',
  invite_revoked: '撤销邀请', role_admin: '设为管理员', role_member: '设为成员', member_removed: '移除成员',
}
const validId = (value: string | null) => value && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value) ? value : null

function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [challenge, setChallenge] = useState<string | null>(null)
  const [pending, setPending] = useState<{ id: string; kind: 'send' | 'verify' } | null>(null)
  const [notice, setNotice] = useState('')
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const validCode = /^[0-9]{6,10}$/.test(code)

  async function apply(outcome: string, operation: { id: string; kind: 'send' | 'verify' }) {
    if (outcome === 'sent') {
      setChallenge(operation.id)
      setNotice('如果该邮箱已开通，验证码将发送到邮箱。')
    } else if (outcome === 'verified') {
      const result = await api<{ user: User | null }>('/auth/session')
      if (result.user) onLogin(result.user)
      else setFault(messageFor('login_required'))
    } else if (outcome === 'auth_result_unknown' || outcome === 'processing' || outcome === 'not_found') {
      setPending(operation)
      if (operation.kind === 'send' && outcome === 'auth_result_unknown') setChallenge(operation.id)
      setFault(outcome === 'processing' ? '原请求仍在处理，可以稍后核对。' : messageFor('auth_result_unknown'))
      return
    } else {
      setFault(messageFor(outcome))
    }
    setPending(null)
  }

  async function submit(kind: 'send' | 'verify') {
    const operation = { id: crypto.randomUUID(), kind }
    setBusy(true)
    setFault('')
    setNotice('')
    setPending(null)
    try {
      await api('/auth/flow', 'POST')
      const result = kind === 'send'
        ? await api<Outcome>('/auth/code', 'POST', { operation_id: operation.id, email })
        : await api<Outcome>('/auth/verify', 'POST', { operation_id: operation.id, challenge_id: challenge, email, code })
      await apply(result.outcome, operation)
    } catch (error) {
      const failure = errorCode(error)
      if (failure === 'network' || failure === 'database_unavailable' || failure === 'auth_result_unknown') setPending(operation)
      setFault(messageFor(failure))
    } finally {
      if (kind === 'verify') setCode('')
      setBusy(false)
    }
  }

  async function check() {
    if (!pending) return
    setBusy(true)
    setFault('')
    try {
      const result = await api<Outcome>('/auth/operations/' + pending.id)
      await apply(result.outcome, pending)
    } catch (error) {
      setFault(messageFor(errorCode(error)))
    } finally {
      setBusy(false)
    }
  }

  return <form className="login-form" onSubmit={event => {
    event.preventDefault()
    if (challenge && validCode) void submit('verify')
    else if (!challenge && !pending) void submit('send')
  }}>
    <h1>登录 MyAgentDesk</h1>
    <p className="account-muted">使用已开通的邮箱账号登录。</p>
    {fault && <Alert type="error" message={fault} showIcon />}
    {notice && <Alert type="info" message={notice} showIcon />}
    <label>邮箱<Input type="email" autoComplete="email" required value={email} disabled={busy || !!challenge || !!pending}
      onChange={event => setEmail(event.target.value)} /></label>
    {challenge && <label>验证码<Input inputMode="numeric" autoComplete="one-time-code" value={code}
      disabled={busy} onChange={event => setCode(event.target.value.replace(/\D/g, ''))} /></label>}
    <div className="account-actions">
      {!challenge && !pending && <Button type="primary" htmlType="submit" loading={busy}>获取验证码</Button>}
      {challenge && <Button type="primary" htmlType="submit" loading={busy} disabled={!validCode}>登录</Button>}
      {pending && <Button onClick={check} disabled={busy}>核对原请求</Button>}
      {(challenge || pending) && <Button type="link" disabled={busy} onClick={() => {
        setChallenge(null); setPending(null); setCode(''); setFault(''); setNotice('')
      }}>重新申请</Button>}
    </div>
  </form>
}

function ProjectPanel({ project, onExpired, onUnavailable }: {
  project: Project; onExpired: () => void; onUnavailable: (code: string) => void
}) {
  const path = '/projects/' + project.id
  const manager = project.role !== 'member'
  const [members, setMembers] = useState<Member[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [activity, setActivity] = useState<Activity | null>(null)
  const [email, setEmail] = useState('')
  const [link, setLink] = useState('')
  const [fault, setFault] = useState('')
  const [busy, setBusy] = useState(false)
  const [streamStatus, setStreamStatus] = useState('')
  const [assignments, setAssignments] = useState<{user_id: string; role: string}[]>([])
  const inviteRequest = useRef<{ id: string; email: string } | null>(null)
  const generation = useRef(0)
  const mounted = useRef(false)

  useEffect(() => {
    mounted.current = true
    let alive = true
    const load = () => {
      const request = ++generation.current
      Promise.all([api<Project>(path), api<Member[]>(path + '/members'), manager ? api<Invite[]>(path + '/invites') : Promise.resolve([]),
        api<{user_id: string; role: string}[]>(path + '/roles')])
        .then(([current, people, links, positions]) => {
          if (!alive || request !== generation.current) return
          if (current.role !== project.role) onUnavailable('role_changed')
          else { setMembers(people); setInvites(links); setAssignments(positions) }
        })
        .catch(error => {
          if (!alive || request !== generation.current) return
          if (isUnauthorized(error)) onExpired()
          else if (errorCode(error) === 'project_not_found') onUnavailable('project_not_found')
          else if (errorCode(error) === 'permission_denied') onUnavailable('role_changed')
          else { setMembers([]); setInvites([]); setActivity(null); setFault(messageFor(errorCode(error))) }
        })
    }
    load()
    const stream = manager ? new EventSource('/api' + path + '/activity') : null
    if (stream) {
      stream.onmessage = event => {
        if (!alive) return
        setActivity(JSON.parse(event.data))
        setStreamStatus('')
        load()
      }
      stream.onerror = () => {
        if (!alive) return
        setStreamStatus('正在核对活动连接')
        api<Project>(path).then(data => {
          if (alive && data.role !== project.role) onUnavailable('role_changed')
          else if (alive) setStreamStatus('')
        }).catch(error => {
          if (!alive) return
          if (isUnauthorized(error)) onExpired()
          else if (errorCode(error) === 'project_not_found') onUnavailable('project_not_found')
          else setStreamStatus(messageFor(errorCode(error)))
        })
      }
    }
    window.addEventListener('focus', load)
    const poll = window.setInterval(load, 5000)
    return () => {
      alive = false
      mounted.current = false
      generation.current++
      window.clearInterval(poll)
      stream?.close()
      window.removeEventListener('focus', load)
    }
  }, [path, manager, project.role, onExpired, onUnavailable])

  async function mutate(operation: () => Promise<unknown>) {
    setBusy(true)
    setFault('')
    try {
      await operation()
      if (!mounted.current) return
      const request = ++generation.current
      const [people, links] = await Promise.all([api<Member[]>(path + '/members'), manager ? api<Invite[]>(path + '/invites') : Promise.resolve([])])
      if (mounted.current && request === generation.current) { setMembers(people); setInvites(links) }
    } catch (error) {
      if (!mounted.current) return
      if (isUnauthorized(error)) onExpired()
      else if (errorCode(error) === 'project_not_found') onUnavailable('project_not_found')
      else setFault(messageFor(errorCode(error)))
    } finally {
      if (mounted.current) setBusy(false)
    }
  }

  const inviteLink = (id: string) => new URL('/?invite=' + id, window.location.origin).href

  return <section className="project-members">
    <div className="member-heading"><div><span className="account-muted">项目 · {roles[project.role]}</span><h1>{project.name}</h1></div>
      {manager && <a href={'/api' + path + '/activity.json'}>下载成员活动</a>}</div>
    {fault && <Alert type="error" message={fault} showIcon />}
    <h2>项目成员</h2>
    <ul className="account-list">{members.map(member => <li key={member.id}>
      <span>{member.email}</span>
      {project.role === 'owner' && member.role !== 'owner' ? <Select
        aria-label={member.email + ' 的角色'} value={member.role} disabled={busy}
        options={[{ value: 'admin', label: '管理员' }, { value: 'member', label: '成员' }]}
        onChange={role => mutate(() => api(path + '/members/' + member.id, 'PATCH', { role }))} />
        : <span>{roles[member.role]}</span>}
      {manager ? <Select aria-label={member.email + ' 的岗位'} placeholder="分配岗位" disabled={busy}
        value={assignments.find(item => item.user_id === member.id)?.role}
        options={[{ value: 'product', label: '产品' }, { value: 'development', label: '研发' }, { value: 'testing', label: '独立测试' }]}
        onChange={role => mutate(() => api(path + '/roles/' + member.id, 'PUT', { role }))} />
        : <span>{({ product: '产品', development: '研发', testing: '独立测试' } as Record<string, string>)[
          assignments.find(item => item.user_id === member.id)?.role ?? ''] ?? '未分配岗位'}</span>}
      {manager && member.role !== 'owner' && (project.role === 'owner' || member.role === 'member')
        && <Button danger type="text" disabled={busy} onClick={() => mutate(() => api(path + '/members/' + member.id, 'DELETE'))}>移除</Button>}
    </li>)}</ul>
    {manager && <>
      <h2>项目邀请</h2>
      <form className="account-actions" onSubmit={event => {
        event.preventDefault()
        const address = email.trim().toLowerCase()
        if (inviteRequest.current?.email !== address) inviteRequest.current = { id: crypto.randomUUID(), email: address }
        const request = inviteRequest.current
        void mutate(async () => {
          const invite = await api<Invite>(path + '/invites', 'POST', request)
          setLink(inviteLink(invite.id))
          setEmail('')
          inviteRequest.current = null
        })
      }}>
        <Input type="email" aria-label="邀请邮箱" required placeholder="邀请邮箱" value={email} disabled={busy}
          onChange={event => setEmail(event.target.value)} />
        <Button type="primary" htmlType="submit" loading={busy}>创建邀请链接</Button>
      </form>
      {link && <label className="invite-link">发送给受邀成员<Input aria-label="邀请链接" readOnly value={link} onFocus={event => event.target.select()} /></label>}
      <ul className="account-list">{invites.map(invite => <li key={invite.id}>
        <span>{invite.email}</span><span>{inviteStates[invite.state]}</span>
        {invite.state === 'pending' && <>
          <Button type="link" size="small" onClick={() => setLink(inviteLink(invite.id))}>查看链接</Button>
          <Button type="text" size="small" disabled={busy} onClick={() => mutate(() => api(path + '/invites/' + invite.id, 'DELETE'))}>撤销</Button>
        </>}
      </li>)}</ul>
      <p className="account-muted" role="status">{streamStatus || (activity ? '最近活动 · ' + (activities[activity.action] ?? activity.action) : '暂无成员活动')}</p>
    </>}
  </section>
}

export function AccountWorkspace({ dark, onDemo, toggleTheme }: {
  dark: boolean; onDemo: () => void; toggleTheme: () => void
}) {
  const [configuration, setConfiguration] = useState<Configuration | null>(null)
  const [user, setUser] = useState<User | null>(null)
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [location, setLocation] = useState(() => new URLSearchParams(window.location.search))
  const [name, setName] = useState('')
  const [fault, setFault] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [reload, setReload] = useState(0)
  const [sessionCheck, setSessionCheck] = useState(0)
  const [pendingDecisions, setPendingDecisions] = useState(0)
  const projectId = validId(location.get('project'))
  const inviteId = validId(location.get('invite'))
  const taskId = validId(location.get('task')) ?? undefined
  const view = globalViews.find(item => item.id === location.get('view'))?.id ?? (projectId ? 'projects' : 'workbench')
  const page = (Object.keys(projectPages) as ProjectPage[]).find(key => key === location.get('page')) ?? 'timeline'

  useEffect(() => {
    if (!user) { setPendingDecisions(0); return }
    let active = true
    const refresh = () => api<unknown[]>('/decisions').then(items => { if (active) setPendingDecisions(items.length) }).catch(() => {})
    void refresh()
    const timer = window.setInterval(refresh, 3000)
    return () => { active = false; window.clearInterval(timer) }
  }, [user])

  useEffect(() => {
    const onLocation = () => setLocation(new URLSearchParams(window.location.search))
    window.addEventListener('popstate', onLocation)
    return () => window.removeEventListener('popstate', onLocation)
  }, [])

  const navigate: OpenRoute = (target: View, params = {}, replace = false) => {
    const next = new URLSearchParams(params)
    if (target !== 'workbench' && target !== 'projects') next.set('view', target)
    const query = next.toString()
    if (window.location.search !== (query ? `?${query}` : '')) {
      const path = query ? `/?${query}` : '/'
      if (replace) window.history.replaceState(null, '', path)
      else window.history.pushState(null, '', path)
    }
    setLocation(next)
    setFault('')
  }

  useEffect(() => {
    let active = true
    setReady(false)
    api<Configuration>('/auth/configuration').then(async config => {
      if (!active) return
      setConfiguration(config)
      if (config.available) {
        const result = await api<{ user: User | null }>('/auth/session')
        if (active) setUser(result.user)
      }
    }).catch(error => { if (active) setFault(messageFor(errorCode(error))) })
      .finally(() => { if (active) setReady(true) })
    return () => { active = false }
  }, [sessionCheck])

  useEffect(() => {
    const refresh = () => setReload(value => value + 1)
    const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('mad-account') : null
    if (channel) channel.onmessage = () => {
      setUser(null); setProjects([]); setProject(null); setSessionCheck(value => value + 1)
    }
    window.addEventListener('focus', refresh)
    return () => { channel?.close(); window.removeEventListener('focus', refresh) }
  }, [])

  function signalChange() {
    if (typeof BroadcastChannel !== 'function') return
    const channel = new BroadcastChannel('mad-account')
    channel.postMessage('session-changed')
    channel.close()
  }

  const expired = useRef(() => {
    setUser(null); setProjects([]); setProject(null); setFault(messageFor('login_required'))
  }).current
  const unavailable = useRef((code: string) => {
    setProject(null)
    if (code !== 'role_changed') setFault(messageFor(code))
    setReload(value => value + 1)
  }).current
  const projectError = useCallback((code: string) => {
    if (code === 'login_required') expired()
    else unavailable(code)
  }, [expired, unavailable])

  useEffect(() => {
    if (!user) return
    let active = true
    api<Project[]>('/projects').then(items => { if (active) setProjects(items) }).catch(error => {
      if (!active) return
      if (isUnauthorized(error)) expired()
      else { setProjects([]); setFault(messageFor(errorCode(error))) }
    })
    if (projectId) api<Project>('/projects/' + projectId).then(item => {
      if (active) setProject(item)
    }).catch(error => {
      if (!active) return
      setProject(null)
      if (isUnauthorized(error)) expired()
      else setFault(messageFor(errorCode(error)))
    })
    else setProject(null)
    return () => { active = false }
  }, [user, projectId, page, reload, expired])

  async function act(task: () => Promise<void>) {
    setBusy(true); setFault(''); setNotice('')
    try { await task() }
    catch (error) {
      if (isUnauthorized(error)) expired()
      else setFault(messageFor(errorCode(error)))
    } finally { setBusy(false) }
  }

  const current = project?.id === projectId && projects.some(item => item.id === projectId) ? project : null
  const hasShell = ready && !!user && !inviteId
  return <div className={hasShell ? 'account-shell mad-shell' : 'account-shell'}>
    <header className="account-top">
      <strong>MyAgentDesk</strong>
      <div className="account-actions">
        {user && <><span>{user.email}</span><Button onClick={() => act(async () => {
          const result = await api<Outcome>('/auth/logout', 'POST')
          setUser(null); setProjects([]); setProject(null); signalChange()
          if (result.outcome === 'remote_unconfirmed') setNotice('已退出当前工作台。认证服务的远端撤销暂未确认。')
        })} loading={busy}>退出登录</Button></>}
        <Button type="text" icon={dark ? <SunOutlined /> : <MoonOutlined />}
          aria-label={dark ? '切换到浅色' : '切换到深色'} onClick={toggleTheme} />
        <Button type="link" onClick={onDemo}>打开 Demo</Button>
      </div>
    </header>
    {hasShell && <nav className="mad-rail" aria-label="全局导航">
      {globalViews.map(item => <Button key={item.id} type="text" icon={item.icon} aria-label={item.label} aria-current={view === item.id ? 'page' : undefined}
        onClick={() => navigate(item.id)}>{item.label}{item.id === 'decisions' && pendingDecisions > 0 ? ` (${pendingDecisions})` : ''}</Button>)}
    </nav>}
    <main className={hasShell ? 'account-main mad-main' : user ? 'account-main signed-in' : 'account-main'}>
      {fault && <Alert type="error" showIcon message={fault} />}
      {notice && <Alert type="warning" showIcon message={notice} />}
      {!ready && <Spin />}
      {ready && !user && (configuration?.available
        ? <Login onLogin={identity => { setUser(identity); setFault(''); signalChange() }} />
        : <section className="login-form"><h1>登录 MyAgentDesk</h1>
          <p>登录服务尚未就绪。</p>
          <Button onClick={() => { setFault(''); setSessionCheck(value => value + 1) }}>重新检查</Button>
        </section>)}
      {ready && user && <>
        {inviteId && <section className="invite-accept"><h1>接受项目邀请</h1><p>{user.email}</p>
          <Button type="primary" loading={busy} onClick={() => act(async () => {
            const joined = await api<Project>('/invites/' + inviteId + '/accept', 'POST')
            setProject(joined); navigate('projects', { project: joined.id }); setReload(value => value + 1)
          })}>接受邀请</Button>
        </section>}
        {!inviteId && view === 'workbench' && <Workbench key={user.id} projects={projects} refresh={reload} navigate={navigate} />}
        {!inviteId && view === 'chat' && <ProjectChats key={user.id} projects={projects} userId={user.id}
          selectedProject={projectId || undefined} initialGroup={location.get('group') ?? undefined} navigate={navigate} onUnavailable={projectError} />}
        {!inviteId && view === 'decisions' && <DecisionInbox key={user.id} />}
        {!inviteId && view === 'calendar' && <PersonalCalendar key={user.id} navigate={navigate} taskId={taskId} onExpired={expired} />}
        {!inviteId && view === 'tasks' && <PersonalTasks key={user.id} navigate={navigate} taskId={taskId} onExpired={expired} />}
        {!inviteId && view === 'agent' && <MyAgent key={user.id} />}
        {!inviteId && view === 'projects' && <section className="mad-page mad-projects-page">
          <nav className="account-projects" aria-label="已授权的项目">
            <Button type="link" onClick={() => navigate('projects')}>全部项目</Button>
            {projects.map(item => <Button key={item.id} type={projectId === item.id ? 'primary' : 'default'}
              aria-current={projectId === item.id ? 'true' : undefined}
              onClick={() => navigate('projects', { project: item.id })}>{item.name}</Button>)}
          </nav>
          {current && <>
            <div className="mad-page-heading"><h1>{current.name}</h1></div>
            <Tabs activeKey={page} onChange={key => navigate('projects', { project: current.id, page: key })}
              items={Object.entries(projectPages).map(([key, label]) => ({ key, label }))} />
            {page === 'members' ? <ProjectPanel key={user.id + ':' + current.id + ':' + current.role} project={current}
              onExpired={expired} onUnavailable={unavailable} />
              : page === 'requirements' || page === 'materials'
                ? <ProjectContext key={user.id + ':' + current.id + ':' + page} project={current} userId={user.id} page={page} onUnavailable={projectError} />
                : <ProjectContent key={user.id + ':' + current.id + ':' + page} project={current} page={page} onUnavailable={projectError} navigate={navigate} />}
          </>}
          {projectId && !current && !fault && <Spin aria-label="正在核验项目权限" />}
          {!projectId && <><h1>项目</h1>
          {!projects.length && <p className="account-muted">当前账号还没有项目。</p>}
          <form className="account-actions" onSubmit={event => {
            event.preventDefault()
            void act(async () => {
              const created = await api<Project>('/projects', 'POST', { name })
              setName(''); setProject(created); navigate('projects', { project: created.id }); setReload(value => value + 1)
            })
          }}>
            <Input aria-label="项目名称" required maxLength={100} placeholder="项目名称" value={name} onChange={event => setName(event.target.value)} />
            <Button type="primary" htmlType="submit" loading={busy}>创建项目</Button>
          </form></>}
        </section>}
      </>}
    </main>
  </div>
}
