from base64 import b64encode
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from socket import socket
from subprocess import run
from threading import Thread
from time import monotonic, sleep
from uuid import uuid4
import json
import os
import sys

from fastapi.testclient import TestClient
import httpx
import uvicorn

from server.github_issues import GitHubIssues
from server.main import create_app
from test_accounts import HEADERS, ORIGIN
from test_requirement_groups import advance, create_group, stage_run, workflow
from test_specifications import DIRECTORY, source


ARTIFACT = b'commit patch bytes'
DIGEST = sha256(ARTIFACT).hexdigest()
RECIPE = Path(__file__).parent / 'fixtures' / 'delivery-pilot.json'


def evidence(key, code=0):
    return {'key': key, 'run': uuid4().hex, 'command': 'python3 verify.py', 'environment': 'test double',
            'exit_code': code, 'output': 'observed exit ' + str(code)}


def make_request(group, plan, commit=None):
    return {'id': str(uuid4()), 'secret': uuid4().hex + uuid4().hex,
            'revision': group['revision'], 'plan_id': plan, 'commit': commit}


def tebb_run(app, listener_origin, listener, request, tmp_path, stage, fail=False):
    workspace = Path(__file__).resolve().parents[1]
    recipe = RECIPE
    if fail:
        broken = json.loads(recipe.read_text(encoding='utf-8'))
        broken['criterion:1'] = ['/usr/bin/python3', '-c', "print('intentional first verification failure'); raise SystemExit(4)"]
        recipe = tmp_path / 'failing-recipe.json'
        recipe.write_text(json.dumps(broken), encoding='utf-8')
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = monotonic() + 10
        while not server.started and monotonic() < deadline:
            sleep(.04)
        assert server.started
        command = [sys.executable, str(workspace / 'scripts' / 'tebb_delivery.py'), '--server', listener_origin,
                   '--dispatch', request['id'], '--repo', str(workspace), '--tebb', os.environ['MAD_DELIVERY_TEBB'],
                   '--recipe', str(recipe), '--state', str(tmp_path / stage)]
        for _ in range(2):  # the second invocation only replays the local outbox
            result = run(command, env={**os.environ, 'MAD_DISPATCH_SECRET': request['secret']},
                         capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
            assert result.returncode == 0, result.stderr
    finally:
        server.should_exit = True
        thread.join(10)


def test_one_real_issue_to_matching_release(source, workflow, monkeypatch, tmp_path):
    (original_clients, users, project, _, _, group_input, endpoint) = workflow
    repository, base, candidate_commit, _ = source
    live = bool(os.environ.get('MAD_DELIVERY_TEBB'))
    ci_run = int(os.environ.get('MAD_DELIVERY_CI_RUN', '71'))
    assert not live or repository == 'lkun-lea/myagentdesk'
    release_root = tmp_path / 'published'
    settings = replace(endpoint.settings, pilot_release_root=str(release_root),
                       github_read_token=os.environ.get('MAD_DELIVERY_GITHUB_READ_TOKEN', '') if live else '')
    if not live:
        read = GitHubIssues.read

        def source_with_actions(client, path):
            if '/actions/runs/' in path:
                identifier = int(path.rsplit('/', 1)[-1])
                return {'id': identifier, 'head_sha': candidate_commit if identifier == ci_run else base, 'status': 'completed',
                        'conclusion': 'success', 'event': 'push', 'path': '.github/workflows/ci.yml',
                        'repository': {'id': 71}, 'head_repository': {'id': 71}}
            return read(client, path)

        monkeypatch.setattr(GitHubIssues, 'read', source_with_actions)

    app = create_app(settings, auth_transport=httpx.MockTransport(endpoint.fake.reply))
    product, developer, tester, publisher = [
        TestClient(app, base_url=ORIGIN, headers=HEADERS, cookies=client.cookies) for client in original_clients
    ]
    worker = TestClient(app, base_url=ORIGIN, headers=HEADERS)
    path = '/api/projects/' + project

    connection = product.put(path + '/github/connection', json={'repository': repository, 'expected_revision': 0})
    assert connection.status_code == 200, connection.text
    revision = connection.json()['revision']
    issue = product.get(path + '/github/issues/10')
    assert issue.status_code == 200, issue.text
    content = {'title': '#10 ' + issue.json()['title'][:105], 'body': '同一需求贯通固定规格、候选、独立验收和发布。',
               'criteria': ['规格链接固定在完整提交', '改写的验收条件重新待验收']}
    link = product.post(path + '/github/issues/10/link', json={'connection_revision': revision, 'requirement': content})
    assert link.status_code == 200, link.text
    requirement = link.json()['requirement_id']
    pin = product.put(path + f'/requirements/{requirement}/specification', json={
        **content, 'expected_revision': 1, 'connection_revision': revision, 'directory': DIRECTORY, 'commit': base,
    })
    assert pin.status_code == 200, pin.text
    group = create_group(product, project, {**group_input, 'requirement_id': requirement, 'source': issue.json()['url']})
    group_path = f"/api/groups/{group['id']}"
    plan = str(uuid4())
    plan_path = path + '/versions/' + plan
    draft = {'name': '固定规格发布试点', 'owner_id': users[0], 'status': 'committed', 'requirement_ids': [requirement],
             'milestones': [], 'required_checks': ['固定规格接口及历史回归'], 'target_date': '2026-11-25', 'reason': '确认试点版本'}
    assert product.put(plan_path, json={**draft, 'expected_revision': 0}).status_code == 200
    draft = {**draft, 'target_date': '2026-11-26', 'reason': '为预留一次独立验收改期'}
    assert product.put(plan_path, json={**draft, 'expected_revision': 1}).status_code == 200
    assert stage_run(product, group)['status'] == 'completed'
    group = advance(product, product.get(group_path).json())
    group = product.get(group_path).json()

    def dispatch(actor, current, commit=None):
        request = make_request(current, plan, commit)
        response = actor.post(group_path + '/dispatches', json=request)
        assert response.status_code == 200, response.text
        assert response.json()['input']['plan_revision'] == 2
        return request

    def headless(request, stage, fail=False):
        with socket() as listener:
            listener.bind(('127.0.0.1', 0))
            origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
            sandbox_app = create_app(replace(settings, origin=origin), auth_transport=httpx.MockTransport(endpoint.fake.reply))
            tebb_run(sandbox_app, origin, listener, request, tmp_path, stage, fail)

    def worker_paths(request):
        return '/api/executor/' + request['id'], {'Authorization': 'Bearer ' + request['secret']}

    dev_request = dispatch(developer, group, candidate_commit)
    assert developer.post(group_path + '/dispatches', json=dev_request).json()['id'] == dev_request['id']
    assert developer.post(path + '/runs', json={'id': str(uuid4()), 'requirement_id': requirement,
                                               'group_id': group['id']}).json()['error'] == 'external_execution_required'
    if live:
        headless(dev_request, 'development')
    else:
        uri, headers = worker_paths(dev_request)
        first_device, second_device = str(uuid4()), str(uuid4())
        assert worker.post(uri + '/claim', json={'device': first_device}).status_code == 404
        for _ in range(2):
            assert worker.post(uri + '/claim', headers=headers, json={'device': first_device}).status_code == 200
        assert worker.post(uri + '/claim', headers=headers, json={'device': second_device}).status_code == 409
        receipt = {'device': first_device, 'commit': candidate_commit, 'status': 'succeeded', 'digest': DIGEST,
                   'artifact': b64encode(ARTIFACT).decode(), 'checks': [evidence('implementation')]}
        assert worker.put(uri + '/receipt', headers=headers, json={**receipt, 'digest': '0'*64}).status_code == 422
        for _ in range(2):
            assert worker.put(uri + '/receipt', headers=headers, json=receipt).status_code == 200
        assert worker.put(uri + '/receipt', headers=headers, json={**receipt, 'commit': base}).status_code == 409

    dev_id = dev_request['id']
    group = tester.get(group_path).json()
    assert group['stage'] == 'testing'
    imported = tester.get(path + '/delivery/artifacts/' + dev_id)
    assert imported.status_code == 200
    digest = sha256(imported.content).hexdigest()
    build_path = path + '/delivery/' + plan
    build_input = {'revision': 2, 'candidate_id': dev_id, 'ci_run_id': ci_run}
    release_input = {'revision': 2, 'candidate_id': dev_id}
    assert product.post(build_path + '/approve', json=build_input).json()['error'] == 'delivery_evidence_required'
    assert developer.post(group_path + '/dispatches', json=make_request(group, plan)).status_code == 403

    failed = dispatch(tester, group)
    if live:
        headless(failed, 'testing', fail=True)
    else:
        uri, headers = worker_paths(failed)
        device = str(uuid4())
        assert worker.post(uri + '/claim', headers=headers, json={'device': device}).status_code == 200
        failed_check = evidence('criterion:1', 4)
        proof = {'device': device, 'commit': candidate_commit, 'digest': digest, 'checks': [failed_check]}
        assert worker.put(uri + '/receipt', headers=headers, json={**proof, 'status': 'succeeded'}).status_code == 422
        assert worker.put(uri + '/receipt', headers=headers, json={**proof, 'status': 'failed'}).status_code == 200
    assert tester.get(group_path).json()['stage'] == 'testing'

    good = dispatch(tester, group)
    if live:
        headless(good, 'testing')
    else:
        uri, headers = worker_paths(good)
        device = str(uuid4())
        assert worker.post(uri + '/claim', headers=headers, json={'device': device}).status_code == 200
        passing = [evidence(key) for key in ('criterion:1', 'criterion:2', 'check:1')]
        proof = {'device': device, 'commit': candidate_commit, 'status': 'succeeded', 'digest': digest, 'checks': passing}
        assert worker.put(uri + '/receipt', headers=headers, json={**proof, 'commit': base}).status_code == 409
        assert worker.put(uri + '/receipt', headers=headers, json={**proof, 'digest': '0'*64}).status_code == 409
        assert worker.put(uri + '/receipt', headers=headers, json={**proof, 'checks': passing[:1]}).status_code == 409
        done = worker.put(uri + '/receipt', headers=headers, json=proof)
        assert done.status_code == 200, done.text
    assert len([msg for msg in tester.get(group_path + '/messages').json() if msg['kind'] == 'executor']) == 3
    testing_done = product.get(group_path).json()
    assert testing_done['stage'] == 'quality'
    assert product.post(build_path + '/approve', json=build_input).status_code == 409
    assert advance(product, testing_done)['stage'] == 'completed'

    if not live:
        assert product.post(build_path + '/approve', json={**build_input, 'ci_run_id': ci_run + 1}).json()['error'] == 'delivery_ci_mismatch'
    approved = product.post(build_path + '/approve', json=build_input)
    assert approved.status_code == 200, approved.text
    assert tester.post(build_path + '/publish', json=release_input).status_code == 403
    assert product.post(build_path + '/publish', json=release_input).status_code == 403
    assert product.put(path + f'/delivery/publishers/{users[3]}?enabled=true').status_code == 200
    first = publisher.post(build_path + '/publish', json=release_input)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result['payload']['commit'] == candidate_commit and result['payload']['digest'] == digest
    assert result == publisher.post(build_path + '/publish', json=release_input).json()
    bundle, = release_root.rglob('*.patch')
    assert bundle.read_bytes() == imported.content
    assert tester.get(path + '/versions').json()['plans'][0]['actual_published_at'] == result['published_at']
    assert publisher.post(build_path + '/publish', json={**release_input, 'candidate_id': failed['id']}).status_code == 409

    detail = tester.get(path + '/delivery').json()
    assert [row['status'] for row in detail['dispatches']] == ['succeeded', 'failed', 'succeeded']
    assert {row['actor'] for row in detail['dispatches']} == {users[1], users[2]}
    assert len(detail['releases']) == 1
    if live and os.environ.get('MAD_DELIVERY_LOG'):
        Path(os.environ['MAD_DELIVERY_LOG']).write_text(json.dumps(detail, ensure_ascii=False, default=str, indent=2), encoding='utf-8')
    assert product.put(plan_path, json={**draft, 'expected_revision': 2, 'reason': '冻结旧计划发布记录'}).status_code == 200
    assert tester.get(path + '/versions').json()['plans'][0]['actual_published_at'] is None
    assert publisher.post(build_path + '/publish', json={**release_input, 'revision': 3}).status_code == 409
    assert len(tester.get(path + '/delivery').json()['releases']) == 1
    assert product.delete(path + '/members/' + users[2]).status_code == 200
    assert tester.get(path + '/delivery/artifacts/' + dev_id).status_code == 404
