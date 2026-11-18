from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from server.agents import AgentService
from server.db import connect
from server.errors import Problem
from server.main import create_app
from server.project_context import authorized_run
from server.recovery import enqueue
from test_accounts import HEADERS, ORIGIN
from test_requirement_groups import workflow, create_group, stage_run, advance


@pytest.fixture
def decision_case(workflow,tmp_path):
    clients,identities,project,doc,req,spec,endpoint=workflow
    settings=replace(endpoint.settings,resource_root=str(tmp_path/'artifacts'))
    app=create_app(settings,auth_transport=httpx.MockTransport(endpoint.fake.reply))
    clients=[TestClient(app,base_url=ORIGIN,headers=HEADERS,cookies=c.cookies) for c in clients]
    product,developer,tester,replacement=clients
    resource=str(uuid4())
    assert product.post(f'/api/projects/{project}/resources',json={'id':resource,'name':'研发交付目录'}).status_code==201
    def grant(member,access,expected=0):
        response=product.put(f'/api/projects/{project}/resources/{resource}/grants/{member}',json={'expected_revision':expected,'access':access})
        assert response.status_code==200,response.text
        return response.json()
    grant(identities[1],'write')
    spec={**spec,'development_resources':[resource]}
    group=create_group(product,project,spec)
    stage_run(product,group)
    group=advance(product,product.get(f"/api/groups/{group['id']}").json())
    base_plan=endpoint.plan
    def plan(bundle,results,name,args):
        if bundle['role']=='development' and name=='submit_handoff':
            key='approved' if bundle.get('decisions') else 'draft'
            published=results.get('publish_artifact',{})
            if published.get('operation_key')!=key:
                return 'publish_artifact',{'resource_id':resource,'operation_key':key,'content':key+' content'}
            if not bundle.get('decisions'):
                return 'request_decision',{'question':'是否继续交付？','options':['继续首版','扩大范围'],'evidence':[results['read_context']['text']]}
        return base_plan(bundle,results,name,args)
    endpoint.plan=plan
    run=stage_run(developer,group)
    assert run['status']=='waiting_decision',run['events']
    decision=developer.get('/api/decisions').json()[0]
    answer={'expected_revision':decision['revision'],'answer':'继续首版','note':'本人已确认。'}
    return {'clients':clients,'ids':identities,'project':project,'group':group,'run':run,'decision':decision,'answer':answer,
            'resource':resource,'grant':grant,'settings':settings,'app':app,'endpoint':endpoint,'doc':doc,'requirement':req}


def test_directory_revocation_keeps_decision_then_restores_once(decision_case):
    c=decision_case
    product,developer,tester,_=c['clients']
    c['grant'](c['ids'][1],'none',1)
    before=len(c['endpoint'].requests)
    response=developer.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer'])
    assert response.status_code==200,response.text
    receipt=response.json()
    assert receipt['execution_state']=='waiting_authorization'
    detail=developer.get(f"/api/decisions/{c['decision']['id']}").json()
    assert detail['state']=='resolved' and detail['execution_state']=='waiting_authorization'
    assert detail['missing_resources'][0]['id']==c['resource'] and detail['references']
    assert len(c['endpoint'].requests)==before
    assert tester.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer']).status_code==404
    assert developer.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer']).json()==receipt
    c['grant'](c['ids'][1],'write',2)
    restored=developer.get(f"/api/runs/{c['run']['id']}").json()
    assert restored['status']=='completed' and restored['id']==c['run']['id']
    assert restored['checkpoint']>c['run']['checkpoint']
    assert developer.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer']).json()==receipt
    with connect(c['settings'].database_url) as db:
        assert db.execute('SELECT count(*) AS n FROM mad_artifacts WHERE run_id=%s',(c['run']['id'],)).fetchone()['n']==2
        assert db.execute('SELECT attempts FROM mad_resume_jobs WHERE run_id=%s',(c['run']['id'],)).fetchone()['attempts']==2
        artifact=db.execute("SELECT id FROM mad_artifacts WHERE run_id=%s AND operation_key='approved'",(c['run']['id'],)).fetchone()
    assert tester.get('/api/artifacts/'+str(artifact['id'])).status_code==403
    c['grant'](c['ids'][2],'read')
    assert tester.get('/api/artifacts/'+str(artifact['id'])).text=='approved content'


def test_reassignment_requires_current_grants_and_invalidates_original_entry(decision_case):
    c=decision_case
    product,developer,tester,replacement=c['clients']
    target=f"/api/decisions/{c['decision']['id']}"
    assert product.get(target+'/candidates').json()==[]
    deny=product.post(target+'/reassign',json={'member_id':c['ids'][3],'expected_revision':c['decision']['revision']})
    assert deny.status_code==409
    assert product.post(target+'/escalate',json={'expected_revision':c['decision']['revision'],'reason':'没有已获目录授权的替补，继续等待。'}).status_code==200
    assert developer.get(target).json()['escalations']
    c['grant'](c['ids'][3],'write')
    assert product.get(target+'/candidates').json()[0]['user_id']==c['ids'][3]
    with connect(c['settings'].database_url) as db:
        old=authorized_run(db,UUID(c['run']['id']),UUID(c['ids'][1]))
    product.delete(f"/api/projects/{c['project']}/members/{c['ids'][1]}")
    assert developer.get(target).status_code==404
    assert product.post(target+'/reassign',json={'member_id':c['ids'][3],'expected_revision':c['decision']['revision']}).status_code==200
    assert developer.post(target+'/resolve',json=c['answer']).status_code==404
    with connect(c['settings'].database_url) as db,pytest.raises(Problem):
        AgentService(c['settings']).check(db,old)
    fresh=replacement.get(target).json()
    assert fresh['checkpoint']==c['decision']['checkpoint'] and fresh['evidence']==c['decision']['evidence']
    assert replacement.post(target+'/resolve',json={**c['answer'],'expected_revision':fresh['revision']}).status_code==200
    assert replacement.get(f"/api/runs/{c['run']['id']}").json()['status']=='completed'


def test_resolved_blocked_run_can_be_taken_over_without_redeciding(decision_case):
    c=decision_case
    product,developer,_,replacement=c['clients']
    target=f"/api/decisions/{c['decision']['id']}"
    c['grant'](c['ids'][1],'none',1)
    assert developer.post(target+'/resolve',json=c['answer']).status_code==200
    c['grant'](c['ids'][3],'write')
    latest=developer.get(target).json()
    assert product.post(target+'/reassign',json={'member_id':c['ids'][3],'expected_revision':latest['revision']}).status_code==200
    fresh=replacement.get(target).json()
    assert fresh['answer']==c['answer']['answer'] and fresh['state']=='resolved'
    assert developer.post(target+'/resolve',json=c['answer']).status_code==404
    assert replacement.post(target+'/resume',json={'expected_revision':fresh['revision']}).status_code==200
    assert replacement.get(f"/api/runs/{c['run']['id']}").json()['status']=='completed'


def test_replaced_request_and_changed_version_reject_old_confirmation(decision_case):
    c=decision_case
    product,developer,_,_=c['clients']
    target=f"/api/decisions/{c['decision']['id']}"
    data={'id':str(uuid4()),'expected_revision':c['decision']['revision'],'question':'请确认新的边界问题',
          'options':c['decision']['options'],'evidence':c['decision']['evidence']}
    response=developer.post(target+'/replace',json=data)
    assert response.status_code==200,response.text
    assert developer.post(target+'/replace',json=data).json()['id']==data['id']
    assert developer.post(target+'/resolve',json=c['answer']).status_code==409
    product.put(f"/api/projects/{c['project']}/requirements",json={**c['requirement'],'expected_revision':1})
    assert developer.post('/api/decisions/'+data['id']+'/resolve',json={**c['answer'],'expected_revision':1}).status_code==409


def worker_environment(c):
    return {**os.environ,'MAD_DATABASE_URL':c['settings'].database_url,'MAD_MODEL_ENV_FILE':c['settings'].model_env_file,
            'MAD_NODE_BINARY':c['settings'].node_binary,'MAD_RESOURCE_ROOT':c['settings'].resource_root,'MAD_RESUME_WORKER':'0'}


def test_inflight_revocation_stops_before_next_protected_write(decision_case,monkeypatch):
    c=decision_case
    _,developer,_,_=c['clients']
    monkeypatch.setattr(c['app'].state.agent_service,'process_job',lambda *_:None)
    developer.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer'])
    endpoint=c['endpoint']
    endpoint.arrived.clear();endpoint.release.clear()
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(AgentService(c['settings']).drain_jobs)
        assert endpoint.arrived.wait(10)
        c['grant'](c['ids'][1],'none',1)
        endpoint.release.set()
        future.result(timeout=15)
    assert developer.get(f"/api/runs/{c['run']['id']}").json()['status']=='waiting_authorization'
    assert len(list(Path(c['settings'].resource_root).rglob('*.txt')))==1
    c['grant'](c['ids'][1],'write',2)
    assert developer.get(f"/api/runs/{c['run']['id']}").json()['status']=='completed'


def test_transfer_back_still_rejects_original_execution_revision(decision_case):
    c=decision_case
    product,developer,_,replacement=c['clients']
    c['grant'](c['ids'][3],'write')
    target=f"/api/decisions/{c['decision']['id']}"
    with connect(c['settings'].database_url) as db:
        original=authorized_run(db,UUID(c['run']['id']),UUID(c['ids'][1]))
    assert product.post(target+'/reassign',json={'member_id':c['ids'][3],'expected_revision':1}).status_code==200
    latest=replacement.get(target).json()
    assert product.post(target+'/reassign',json={'member_id':c['ids'][1],'expected_revision':latest['revision']}).status_code==200
    with connect(c['settings'].database_url) as db,pytest.raises(Problem) as denied:
        AgentService(c['settings']).check(db,original)
    assert denied.value.code=='execution_replaced'
    assert developer.post(target+'/resolve',json=c['answer']).status_code==409
    current=developer.get(target).json()
    assert current['checkpoint']==c['run']['checkpoint']


def test_restart_after_committed_confirmation_and_concurrent_workers(decision_case,monkeypatch):
    c=decision_case
    _,developer,_,_=c['clients']
    monkeypatch.setattr(c['app'].state.agent_service,'process_job',lambda *_:None)
    response=developer.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer'])
    assert response.status_code==200
    original_files=list(Path(c['settings'].resource_root).rglob('*.txt'))
    assert len(original_files)==1
    completed=subprocess.run([sys.executable,'-m','server.resume_worker','--once'],env=worker_environment(c),capture_output=True,
                             timeout=40,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    assert completed.returncode==0
    restored=developer.get(f"/api/runs/{c['run']['id']}").json()
    assert restored['status']=='completed' and restored['checkpoint']==c['run']['checkpoint']+2
    count=len(c['endpoint'].requests)
    with ThreadPoolExecutor(3) as pool:
        list(pool.map(lambda _:AgentService(c['settings']).drain_jobs(),range(3)))
    assert len(c['endpoint'].requests)==count
    assert len(list(Path(c['settings'].resource_root).rglob('*.txt')))==2


def test_application_startup_recovers_queue_without_manual_click(decision_case,monkeypatch):
    c=decision_case
    _,developer,_,_=c['clients']
    monkeypatch.setattr(c['app'].state.agent_service,'process_job',lambda *_:None)
    developer.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer'])
    app=create_app(replace(c['settings'],resume_worker=True),auth_transport=httpx.MockTransport(c['endpoint'].fake.reply))
    with TestClient(app,base_url=ORIGIN,headers=HEADERS,cookies=developer.cookies) as restarted:
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            run=restarted.get(f"/api/runs/{c['run']['id']}").json()
            if run['status']=='completed':
                break
            time.sleep(.1)
        assert run['status']=='completed' and run['id']==c['run']['id']
    assert len(list(Path(c['settings'].resource_root).rglob('*.txt')))==2


def test_worker_crash_after_file_publish_before_receipt_does_not_write_twice(decision_case,monkeypatch):
    c=decision_case
    _,developer,_,_=c['clients']
    monkeypatch.setattr(c['app'].state.agent_service,'process_job',lambda *_:None)
    developer.post(f"/api/decisions/{c['decision']['id']}/resolve",json=c['answer'])
    script='''
import os
import server.agents as agents
from server.config import Settings
original=agents.publish_artifact
def crash(db,run,args,settings):
    result=original(db,run,args,settings)
    if args['operation_key']=='approved':
        os._exit(37)
    return result
agents.publish_artifact=crash
agents.AgentService(Settings.from_env()).drain_jobs()
'''
    child=subprocess.run([sys.executable,'-c',script],env=worker_environment(c),capture_output=True,timeout=40,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    assert child.returncode==37
    files=list(Path(c['settings'].resource_root).rglob('*.txt'))
    assert len(files)==2
    published=next(path for path in files if path.read_text()=='approved content')
    stamp=published.stat().st_mtime_ns
    with connect(c['settings'].database_url) as db:
        assert db.execute("SELECT count(*) AS n FROM mad_artifacts WHERE operation_key='approved'").fetchone()['n']==0
        assert db.execute('SELECT state FROM mad_resume_jobs WHERE run_id=%s',(c['run']['id'],)).fetchone()['state']=='running'
    AgentService(c['settings']).drain_jobs()
    assert developer.get(f"/api/runs/{c['run']['id']}").json()['status']=='completed'
    assert published.stat().st_mtime_ns==stamp
    with connect(c['settings'].database_url) as db:
        artifact=db.execute("SELECT * FROM mad_artifacts WHERE operation_key='approved'").fetchone()
        assert artifact['digest']==hashlib.sha256(b'approved content').hexdigest()
    assert len(list(Path(c['settings'].resource_root).rglob('*.txt')))==2
