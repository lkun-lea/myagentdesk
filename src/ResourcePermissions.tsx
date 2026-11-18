import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select } from 'antd'
import { api, errorCode, messageFor } from './accounts-api'

export type Resource = { id:string; name:string; grants:{user_id:string;access:string;revision:number}[] }
export function ResourcePermissions({projectId,members,manager}:{projectId:string;members:{id:string;email:string}[];manager:boolean}){
  const [value,setValue]=useState<{available:boolean;resources:Resource[]}>()
  const [name,setName]=useState('')
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [reload,setReload]=useState(0)
  const pending=useRef<{id:string;name:string}|undefined>(undefined)
  const memberKey=members.map(m=>m.id).join(',')
  useEffect(()=>{let active=true;void api<typeof value>('/projects/'+projectId+'/resources')
    .then(rows=>{if(active)setValue(rows)}).catch(e=>{if(active){setValue(undefined);setError(messageFor(errorCode(e)))}})
    return()=>{active=false}},[projectId,reload,memberKey])
  async function act(action:()=>Promise<unknown>){setBusy(true);setError('');try{await action();setReload(n=>n+1)}catch(e){setError(messageFor(errorCode(e)))}finally{setBusy(false)}}
  return <section className="mad-page"><h2>产物目录授权</h2>
    <p className="account-muted">工具由 Agent 共用。目录读取、写入和续跑分别核对当前成员的授权。</p>
    {error&&<Alert type="error" message={error}/>}
    {value&&!value.available&&<p>服务尚未配置项目产物目录。</p>}
    {manager&&value?.available&&<form className="account-actions" onSubmit={e=>{e.preventDefault();void act(async()=>{
      if(!pending.current||pending.current.name!==name)pending.current={id:crypto.randomUUID(),name}
      await api('/projects/'+projectId+'/resources','POST',pending.current);pending.current=undefined;setName('')
    })}}><Input aria-label="产物目录名称" required maxLength={80} value={name} onChange={e=>setName(e.target.value)}/>
      <Button htmlType="submit" loading={busy}>创建产物目录</Button></form>}
    {value?.resources.map(resource=><article className="mad-context-card" key={resource.id}><h3>{resource.name}</h3>
      {members.map(member=>{const grant=resource.grants.find(g=>g.user_id===member.id);return <div className="account-actions" key={member.id}>
        <span>{member.email}</span><Select aria-label={resource.name+' · '+member.email+' 的目录权限'} value={grant?.access??'none'} disabled={!manager||busy}
          options={[{value:'none',label:'未授权'},{value:'read',label:'读取产物'},{value:'write',label:'读取和发布产物'}]}
          onChange={access=>void act(()=>api('/projects/'+projectId+'/resources/'+resource.id+'/grants/'+member.id,'PUT',{
            expected_revision:grant?.revision??0,access}))}/></div>})}
    </article>)}
  </section>
}
