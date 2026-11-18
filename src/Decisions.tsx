import { useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Select, Spin, Tag } from 'antd'
import { api, errorCode, messageFor } from './accounts-api'

type Decision = { id: string; run_id: string; owner_id: string; group_id?: string; project_id: string;
  question: string; options: string[]; evidence: string[]; checkpoint: number; revision: number;
  state: string; execution_state: string; answer?: string; note?: string; due_at: string;
  project_name?: string; requirement_title?: string; receipt?: { outcome: string; execution_state: string };
  outdated?: boolean;
  missing_resources?: { id: string; name: string; required_access: string }[];
  references?: { title: string; source: string; revision: number; excerpt: string }[];
  job?: { state: string; reason?: string; attempts: number }; escalations?: { reason: string; created_at: string }[] }
const executionLabels: Record<string,string> = { waiting_decision: '等待本人决定', waiting_authorization: '已确认，等待授权',
  queued: '等待续跑', paused: '等待接手或续跑', running: '正在续跑', completed: '已交接', stale: '需要核对新版本', failed: '执行失败，可核对后重试', cancelled: '已取消' }

export function DecisionInbox({ changed, initialDecision }: { changed?: () => void; initialDecision?: string }) {
  const [items,setItems]=useState<Decision[]>()
  const [history,setHistory]=useState(false)
  const [detail,setDetail]=useState<Decision>()
  const [answers,setAnswers]=useState<Record<string,{answer?:string;note?:string}>>({})
  const [busy,setBusy]=useState('')
  const [error,setError]=useState('')
  const [notice,setNotice]=useState('')
  const [reload,setReload]=useState(0)
  const [questions,setQuestions]=useState<Record<string,string>>({})
  const replacing=useRef<Record<string,{id:string;question:string}>>({})
  useEffect(()=>{
    let active=true
    const read=async()=>{
      try {
        const rows=await api<Decision[]>('/decisions?history='+history)
        const focused=initialDecision ? await api<Decision>('/decisions/'+initialDecision) : undefined
        if(active) {setItems(rows);if(focused)setDetail(focused)}
      } catch(failure) {if(active){setError(messageFor(errorCode(failure)));setDetail(undefined)}}
    }
    void read();const timer=window.setInterval(read,3000)
    return()=>{active=false;window.clearInterval(timer)}
  },[history,reload,initialDecision])
  async function act(id:string,action:()=>Promise<unknown>,saved=true){
    setBusy(id);setError('');setNotice('')
    try {await action();if(saved)setNotice('操作已保存。可在历史记录核对原决定和当前执行状态。');setReload(n=>n+1);changed?.()}
    catch(failure){setError(messageFor(errorCode(failure)))}finally{setBusy('')}
  }
  const visible=detail ? [detail] : items
  return <section className="mad-page"><div className="mad-page-heading"><h2>待我决策</h2>
    <Select aria-label="决定记录范围" value={history?'history':'active'} onChange={v=>{setHistory(v==='history');setDetail(undefined)}}
      options={[{value:'active',label:'待确认和待继续'},{value:'history',label:'历史记录'}]}/></div>
    {error&&<Alert type="error" showIcon message={error} action={<Button onClick={()=>{setError('');setReload(n=>n+1)}}>重新核对</Button>}/>}
    {notice&&<Alert type="success" showIcon message={notice}/>}
    {!items&&!error&&<Spin/>}
    {visible?.length===0&&<p className="account-muted">暂无需要处理的决定。</p>}
    {detail&&!initialDecision&&<Button onClick={()=>setDetail(undefined)}>返回决定列表</Button>}
    {initialDecision&&<a href="/?view=decisions">返回决定列表</a>}
    {visible?.map(item=><article className="mad-context-card" key={item.id}>
      <div className="mad-page-heading"><h3>{item.question}</h3><Tag>{item.state==='superseded'?'已被替换':item.outdated?'版本已变化，需重新核对':executionLabels[item.execution_state]??item.state}</Tag></div>
      <p>{item.project_name} {item.requirement_title}</p>
      <p className="account-muted">运行 {item.run_id} · 原检查点 {item.checkpoint}</p>
      {item.state==='pending'&&new Date(item.due_at)<new Date()&&<Tag color="warning">已逾期，请联系项目负责人</Tag>}
      <ul>{item.evidence.map((line,i)=><li key={i}>{line}</li>)}</ul>
      <div className="account-actions"><Button onClick={()=>void act(item.id,async()=>setDetail(await api<Decision>('/decisions/'+item.id)),false)}>查看原始证据与执行状态</Button>
        <a href={'/?view=decisions&decision='+item.id}>此决定链接</a>
        {item.group_id&&<a href={'/?view=chat&project='+item.project_id+'&group='+item.group_id}>返回原需求群</a>}</div>
      {item.references?.map((ref,i)=><details key={i}><summary>{ref.title} · v{ref.revision}</summary><p>{ref.source}</p><pre>{ref.excerpt}</pre></details>)}
      {item.missing_resources?.length ? <Alert type="warning" message={'等待目录写入授权：'+item.missing_resources.map(r=>r.name).join('、')}/>:null}
      {item.job?.reason&&<p role="status">续跑状态：{item.job.reason==='reassignment_review'?'请核对交接后继续原运行':messageFor(item.job.reason)}</p>}
      {item.project_id&&<a href={'/?project='+item.project_id+'&page=requirements'}>核对需求及资料版本</a>}
      {item.escalations?.map((entry,i)=><p key={i}>统筹记录：{entry.reason}</p>)}
      {item.state==='pending'?<>
        <label className="mad-group-field">选择方案<Select aria-label="决策方案" value={answers[item.id]?.answer}
          options={item.options.map(value=>({value,label:value}))} onChange={answer=>setAnswers({...answers,[item.id]:{...answers[item.id],answer}})}/></label>
        <label className="mad-group-field">决定说明<Input.TextArea aria-label="决定说明" maxLength={3000} value={answers[item.id]?.note??''}
          onChange={e=>setAnswers({...answers,[item.id]:{...answers[item.id],note:e.target.value}})}/></label>
        <Button type="primary" loading={busy===item.id} disabled={!!busy||item.outdated||!answers[item.id]?.answer||!answers[item.id]?.note?.trim()}
          onClick={()=>void act(item.id,()=>api('/decisions/'+item.id+'/resolve','POST',{expected_revision:item.revision,...answers[item.id]}))}>确认并继续原运行</Button>
        <details><summary>更正此问题并替换原请求</summary>
          <label className="mad-group-field">替换问题<Input.TextArea aria-label="替换问题" maxLength={3000} value={questions[item.id]??''}
            onChange={e=>setQuestions({...questions,[item.id]:e.target.value})}/></label>
          <Button disabled={!questions[item.id]?.trim()||!!busy} onClick={()=>void act(item.id,async()=>{
            const question=questions[item.id];const old=replacing.current[item.id]
            if(!old||old.question!==question)replacing.current[item.id]={id:crypto.randomUUID(),question}
            await api('/decisions/'+item.id+'/replace','POST',{...replacing.current[item.id],expected_revision:item.revision,options:item.options,evidence:item.evidence})
            setDetail(undefined)
          })}>替换并使旧入口失效</Button></details>
      </>:item.state==='superseded'?<p>此请求已替换，请从待确认列表打开当前问题。</p>:<><p>已保存决定：{item.answer}</p><p>{item.note}</p>
        {item.state==='resolved'&&!['running','completed','cancelled'].includes(item.execution_state)&&<Button loading={busy===item.id} disabled={!!item.outdated}
          onClick={()=>void act(item.id,()=>api('/decisions/'+item.id+'/resume','POST',{expected_revision:item.revision}))}>核对并续跑原运行</Button>}</>}
    </article>)}
  </section>
}

export function DecisionDelegation({ decision, changed }: {decision:{id:string;revision:number;question:string};changed:()=>void}){
  const [people,setPeople]=useState<{user_id:string;email:string}[]>()
  const [member,setMember]=useState<string>()
  const [reason,setReason]=useState('')
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  useEffect(()=>{let active=true;void api<{user_id:string;email:string}[]>('/decisions/'+decision.id+'/candidates')
    .then(rows=>{if(active)setPeople(rows)}).catch(e=>{if(active)setError(messageFor(errorCode(e)))})
    return()=>{active=false}},[decision.id,decision.revision])
  async function act(action:()=>Promise<unknown>){setBusy(true);setError('');try{await action();changed()}catch(e){setError(messageFor(errorCode(e)))}finally{setBusy(false)}}
  return <section className="mad-context-card"><h3>改派或升级：{decision.question}</h3>
    {error&&<Alert type="error" message={error}/>}
    {people?.length===0&&<p>暂无同时满足岗位、目录授权和独立验收约束的替补，原任务继续等待。</p>}
    <Select aria-label="决策替补" placeholder="选择已授权的同岗替补" value={member} options={people?.map(p=>({value:p.user_id,label:p.email}))} onChange={setMember}/>
    <Button disabled={!member||busy} onClick={()=>void act(()=>api('/decisions/'+decision.id+'/reassign','POST',{member_id:member,expected_revision:decision.revision}))}>转交决定与原运行</Button>
    <label className="mad-group-field">升级原因<Input.TextArea aria-label="升级原因" maxLength={3000} value={reason} onChange={e=>setReason(e.target.value)}/></label>
    <Button disabled={!reason.trim()||busy} onClick={()=>void act(()=>api('/decisions/'+decision.id+'/escalate','POST',{expected_revision:decision.revision,reason}))}>记录升级并保留等待</Button>
  </section>
}
