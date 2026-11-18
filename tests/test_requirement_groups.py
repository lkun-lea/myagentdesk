from concurrent.futures import ThreadPoolExecutor
import json
import os
from threading import Event, Thread
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest

from server.config import Settings
from server.main import create_app
from server.db import connect
from test_accounts import FakeMailAuth, ORIGIN, HEADERS, login
from test_project_context import ModelEndpoint, ThreadingHTTPServer


@pytest.fixture
def workflow(database, tmp_path):
    endpoint = ThreadingHTTPServer(('127.0.0.1', 0), ModelEndpoint)
    endpoint.requests = []
    endpoint.arrived, endpoint.release = Event(), Event()
    endpoint.release.set()
    endpoint.fail, endpoint.fail_on = False, -1
    endpoint.ask, endpoint.reject = False, False

    def plan(bundle, results, name, args):
        if name == 'read_context' and getattr(endpoint, 'invalid_read', False):
            endpoint.invalid_read = False
            return name, {**args, 'end': 5000}
        if name == 'submit_handoff' and bundle['role'] == 'development' and endpoint.ask and not bundle.get('decisions'):
            endpoint.ask = False
            return 'request_decision', {'question': '本阶段采用哪种范围？', 'options': ['保留首版范围', '扩大范围'], 'evidence': [results['read_context']['text']]}
        if name == 'submit_handoff' and bundle['role'] == 'testing' and endpoint.reject:
            endpoint.reject = False
            args = {**args, 'verdict': 'rejected', 'summary': '边界条件缺少明确证据，退回研发。'}
        return name, args

    endpoint.plan = plan
    thread = Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / 'model.env'
    config.write_text(f'ATLAS_API_KEY=fixture-key\nATLAS_BASE_URL=http://127.0.0.1:{endpoint.server_port}/v1\nATLAS_MODEL=context-fixture\n')
    fake = FakeMailAuth()
    addresses = ('product@example.com', 'developer@example.com', 'tester@example.com', 'replacement@example.com')
    fake.users = {address: str(uuid4()) for address in addresses}
    settings = Settings(database, ORIGIN, 'https://auth.test', 'publishable-test', Fernet.generate_key().decode(),
                        str(config), os.environ.get('MAD_NODE_BINARY', 'node'))
    app = create_app(settings, auth_transport=httpx.MockTransport(fake.reply))
    endpoint.settings, endpoint.fake = settings, fake
    clients = [TestClient(app, base_url=ORIGIN, headers=HEADERS) for _ in addresses]
    for client, address in zip(clients, addresses):
        login(client, address)
    product, developer, tester, replacement = clients
    identities = [client.get('/api/auth/session').json()['user']['id'] for client in clients]
    project = product.post('/api/projects', json={'name': '真实需求阶段验收'}).json()['id']
    for client, address in zip(clients[1:], addresses[1:]):
        invite = str(uuid4())
        product.post(f'/api/projects/{project}/invites', json={'id': invite, 'email': address})
        client.post(f'/api/invites/{invite}/accept')
    for member_id, role in zip(identities, ('product', 'development', 'testing', 'development')):
        product.put(f'/api/projects/{project}/roles/{member_id}', json={'role': role})
    document = {'id': str(uuid4()), 'expected_revision': 0, 'title': '需求约定', 'source': 'fixture://issue6',
                'body': '需求群应共享同一版本的方案，遇到歧义交给本人，决定后保留原运行。'}
    requirement = {'id': str(uuid4()), 'expected_revision': 0, 'title': '#6 需求群阶段交接',
                   'body': '实现需求群和本人决策。', 'criteria': ['群与需求一一对应', '决定后原检查点继续']}
    product.put(f'/api/projects/{project}/context', json=document)
    product.put(f'/api/projects/{project}/requirements', json=requirement)
    spec = {'requirement_id': requirement['id'], 'source': 'https://github.com/lkun-lea/myagentdesk/issues/6',
            'auto_run': False,
            'product_id': identities[0], 'development_id': identities[1], 'testing_id': identities[2], 'quality_id': identities[0]}
    try:
        yield clients, identities, project, document, requirement, spec, endpoint
    finally:
        endpoint.release.set()
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=2)


def create_group(product, project, spec):
    response = product.post(f'/api/projects/{project}/groups', json=spec)
    assert response.status_code == 201, response.text
    return response.json()


def stage_run(client, group, execute=True):
    response = client.post(f"/api/projects/{group['project_id']}/runs", json={
        'id': str(uuid4()), 'requirement_id': group['requirement_id'], 'group_id': group['id']})
    assert response.status_code == 201, response.text
    run = response.json()
    if execute:
        assert client.post(f"/api/runs/{run['id']}/execute").status_code == 202
    return client.get(f"/api/runs/{run['id']}").json()


def advance(client, group, note='按当前证据确认', action='approve'):
    response = client.post(f"/api/groups/{group['id']}/advance", json={'expected_revision': group['revision'], 'action': action, 'note': note})
    assert response.status_code == 200, response.text
    return client.get(f"/api/groups/{group['id']}").json()


def test_initiation_and_product_stage(workflow):
    (product, developer, tester, _), _, project, _, _, spec, _ = workflow
    with ThreadPoolExecutor(2) as pool:
        groups = list(pool.map(lambda _: create_group(product, project, spec), range(2)))
    assert groups[0]['id'] == groups[1]['id']
    group = groups[0]
    denied = tester.post(f'/api/projects/{project}/runs', json={'id': str(uuid4()), 'requirement_id': spec['requirement_id'], 'group_id': group['id']})
    assert denied.status_code == 409
    run = stage_run(product, group)
    assert run['status'] == 'completed'
    current = product.get(f"/api/groups/{group['id']}").json()
    assert current['stage'] == 'product' and current['state'] == 'awaiting_confirmation'
    data = {'expected_revision': current['revision'], 'action': 'approve', 'note': '本人确认产品范围'}
    assert developer.post(f"/api/groups/{group['id']}/advance", json=data).status_code == 403
    first = product.post(f"/api/groups/{group['id']}/advance", json=data)
    assert first.json() == product.post(f"/api/groups/{group['id']}/advance", json=data).json()
    assert product.get(f"/api/groups/{group['id']}").json()['stage'] == 'development'
    message = {'id': str(uuid4()), 'body': '同步已确认的边界'}
    for _ in range(2):
        assert developer.post(f"/api/groups/{group['id']}/messages", json=message).status_code == 200
    messages = tester.get(f"/api/groups/{group['id']}/messages").json()
    assert len([item for item in messages if item['id'] == message['id']]) == 1
    assert tester.get(f"/api/groups/{group['id']}/messages", params={'after': messages[-1]['sequence']}).json() == []


def test_development_decision_and_same_checkpoint_resume(workflow):
    (product, developer, tester, _), _, project, _, _, spec, endpoint = workflow
    group = create_group(product, project, spec)
    stage_run(product, group)
    group = advance(product, product.get(f"/api/groups/{group['id']}").json())
    endpoint.ask = True
    run = stage_run(developer, group)
    assert run['status'] == 'waiting_decision' and run['checkpoint'] == 3
    decision = developer.get('/api/decisions').json()[0]
    # Replace the application/AgentService after waiting; only database checkpoints survive.
    restarted = create_app(endpoint.settings, auth_transport=httpx.MockTransport(endpoint.fake.reply))
    developer = TestClient(restarted, base_url=ORIGIN, headers=HEADERS, cookies=developer.cookies)
    assert developer.get('/api/decisions').json()[0]['id'] == decision['id']
    answer = {'expected_revision': decision['revision'], 'answer': decision['options'][0], 'note': '按首版范围继续，保留原运行。'}
    assert tester.post(f"/api/decisions/{decision['id']}/resolve", json=answer).status_code == 404
    reply = developer.post(f"/api/decisions/{decision['id']}/resolve", json=answer)
    assert reply.status_code == 200 and reply.json()['run_id'] == run['id']
    restored = developer.get(f"/api/runs/{run['id']}").json()
    assert restored['status'] == 'completed' and restored['checkpoint'] == 4
    assert developer.post(f"/api/decisions/{decision['id']}/resolve", json=answer).json() == reply.json()
    assert developer.post(f"/api/decisions/{decision['id']}/resolve", json={**answer, 'note': '不同答复'}).status_code == 409
    assert developer.get('/api/decisions').json() == []
    messages = developer.get(f"/api/groups/{group['id']}/messages").json()
    decisions = [item for item in messages if item['kind'] in {'decision', 'decision_resolved'}]
    assert len(decisions) == 2
    assert all(item['data']['run_id'] == run['id'] and item['data']['checkpoint'] == 3 for item in decisions)
    assert developer.get(f"/api/groups/{group['id']}").json()['stage'] == 'testing'
    assert developer.post(f"/api/runs/{run['id']}/execute").status_code == 409


def test_testing_return_rework_and_quality_gate(workflow):
    (product, developer, tester, _), _, project, doc, _, spec, endpoint = workflow
    group = create_group(product, project, spec)
    stage_run(product, group)
    group = advance(product, product.get(f"/api/groups/{group['id']}").json())
    stage_run(developer, group)
    group = tester.get(f"/api/groups/{group['id']}").json()
    endpoint.reject = True
    rejected = stage_run(tester, group)
    assert rejected['status'] == 'completed'
    group = developer.get(f"/api/groups/{group['id']}").json()
    assert group['stage'] == 'development'
    second = stage_run(developer, group)
    assert second['group_revision'] == group['revision']
    group = tester.get(f"/api/groups/{group['id']}").json()
    tested = stage_run(tester, group)
    group = product.get(f"/api/groups/{group['id']}").json()
    assert group['stage'] == 'quality'
    assert tested['candidate_run'] == second['id']
    assert tester.post(f"/api/groups/{group['id']}/advance", json={'expected_revision': group['revision'], 'action': 'approve', 'note': '擅自放行'}).status_code == 403
    group = advance(product, group)
    assert group['stage'] == 'completed'
    product.put(f'/api/projects/{project}/context', json={**doc, 'expected_revision': 1, 'body': '约定更新，需要重新确认。'})
    assert product.get(f"/api/groups/{group['id']}").json()['stage'] == 'product'


def test_decision_takeover_revocation_and_revision_invalidation(workflow):
    (product, developer, tester, replacement), identities, project, _, req, spec, endpoint = workflow
    group = create_group(product, project, spec)
    stage_run(product, group)
    group = advance(product, product.get(f"/api/groups/{group['id']}").json())
    endpoint.ask = True
    run = stage_run(developer, group)
    decision = developer.get('/api/decisions').json()[0]
    product.delete(f'/api/projects/{project}/members/{identities[1]}')
    answer = {'expected_revision': decision['revision'], 'answer': decision['options'][0], 'note': '继续首版'}
    assert developer.post(f"/api/decisions/{decision['id']}/resolve", json=answer).status_code == 404
    reassigned = product.post(f"/api/decisions/{decision['id']}/reassign", json={'member_id': identities[3], 'expected_revision': decision['revision']})
    assert reassigned.status_code == 200, reassigned.text
    assert developer.get(f"/api/runs/{run['id']}").status_code == 404
    decision = replacement.get('/api/decisions').json()[0]
    assert replacement.post(f"/api/decisions/{decision['id']}/resolve", json={**answer, 'expected_revision': decision['revision']}).status_code == 200
    assert replacement.get(f"/api/runs/{run['id']}").json()['status'] == 'completed'
    product.put(f'/api/projects/{project}/requirements', json={**req, 'expected_revision': 1})
    current = tester.get(f"/api/groups/{group['id']}").json()
    assert current['stage'] == 'product' and current['requirement_revision'] == 2
    assert tester.post(f"/api/runs/{run['id']}/execute").status_code == 404


def test_automatic_agents_handoff_stops_for_human_and_quality(workflow):
    (product, developer, tester, _), _, project, _, _, spec, endpoint = workflow
    group = create_group(product, project, {**spec, 'auto_run': True})
    current = product.get(f"/api/groups/{group['id']}").json()
    assert current['stage'] == 'product' and current['state'] == 'awaiting_confirmation'
    endpoint.ask = True
    current = advance(product, current)
    assert current['state'] == 'waiting_decision'
    decision = developer.get('/api/decisions').json()[0]
    original = developer.get(f"/api/runs/{decision['run_id']}").json()
    assert developer.post(f"/api/decisions/{decision['id']}/resolve", json={
        'expected_revision': decision['revision'], 'answer': decision['options'][0], 'note': '已确认，保留首版范围。'}).status_code == 200
    current = tester.get(f"/api/groups/{group['id']}").json()
    assert current['stage'] == 'quality'
    restored = developer.get(f"/api/runs/{decision['run_id']}").json()
    assert restored['id'] == original['id'] and restored['checkpoint'] > original['checkpoint']
    assert len(current['runs']) == 3
    assert advance(product, current)['stage'] == 'completed'


def test_invalid_tool_range_can_be_corrected_without_success_receipt(workflow):
    (product, _, _, _), _, project, _, _, spec, endpoint = workflow
    endpoint.invalid_read = True
    group = create_group(product, project, spec)
    run = stage_run(product, group)
    assert run['status'] == 'completed' and run['checkpoint'] == 3
    refused = [e for e in run['events'] if e['kind'] == 'tool_refused']
    assert len(refused) == 1 and refused[0]['data']['reason'] == 'invalid_request'
    reads = [e for e in run['events'] if e['kind'] == 'tool_result' and e['data']['tool'] == 'read_context']
    assert len(reads) == 1
