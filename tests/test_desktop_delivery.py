"""Exercise the same public Issue through authenticated desktop claims and recovery."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4
import json
import os
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

from server.main import create_app
from test_accounts import HEADERS, ORIGIN
from test_delivery import evidence, ARTIFACT, DIGEST
from test_requirement_groups import advance, create_group, stage_run, workflow
from test_specifications import DIRECTORY, source


class SimulatedExit(BaseException):
    pass


class Pipe:
    def __init__(self, client):
        self.client = client
        self.offline = False
        self.lose = ''
        self.lost = 0

    def __call__(self, connection, method, path, body=None):
        if self.offline:
            raise httpx.ConnectError('simulated lost desktop link')
        response = self.client.request(method, path, json=body,
                                       headers={'Authorization': 'Bearer ' + connection['credential']})
        response.raise_for_status()
        if self.lose and path.endswith(self.lose):
            self.lose = ''
            self.lost += 1
            raise httpx.ReadError('response lost after service commit')
        return response.json()


class CountingTerminal:
    def __init__(self, actual, mode='ack'):
        self.actual = actual
        self.mode = mode
        self.starts = 0
        self.original = None

    def __getattr__(self, key):
        return getattr(self.actual, key)

    def start(self, *args, **kwargs):
        self.starts += 1
        if self.mode == 'unknown':
            raise SimulatedExit()
        self.original = self.actual.start(*args, **kwargs)['job']['id']
        if self.mode == 'ack':
            raise SimulatedExit()
        return {'job': self.actual.get(self.original)}


def pairing(actor, workbench, label):
    package = {'id': str(uuid4()), 'label': label, 'secret': uuid4().hex + uuid4().hex}
    assert actor.post('/api/delivery/devices', json=package).status_code == 200
    return {'id': package['id'], 'origin': ORIGIN, 'code': package['secret']}, workbench


def request_attempt(actor, group, plan, commit=None):
    payload = {'id': str(uuid4()), 'revision': group['revision'], 'plan_id': plan, 'desktop': True, 'commit': commit}
    response = actor.post(f"/api/groups/{group['id']}/dispatches", json=payload)
    assert response.status_code == 200, response.text
    return payload['id']


def environments(workflow, source):
    original, users, project, _, _, group_input, endpoint = workflow
    repository, base, head, _ = source
    settings = replace(endpoint.settings, github_read_token=os.environ.get('MAD_DELIVERY_GITHUB_READ_TOKEN', ''))
    app = create_app(settings, auth_transport=httpx.MockTransport(endpoint.fake.reply))
    product, developer, tester, _ = [
        TestClient(app, base_url=ORIGIN, headers=HEADERS, cookies=client.cookies) for client in original
    ]
    worker = TestClient(app, base_url=ORIGIN, headers=HEADERS)
    prefix = '/api/projects/' + project
    connection = product.put(prefix + '/github/connection', json={'repository': repository, 'expected_revision': 0})
    assert connection.status_code == 200, connection.text
    revision = connection.json()['revision']
    upstream = product.get(prefix + '/github/issues/10')
    assert upstream.status_code == 200, upstream.text
    issue = upstream.json()
    spec = {'title': '#10 ' + issue['title'][:105], 'body': '验证桌面设备领取、确认与原运行恢复。',
            'criteria': ['完整提交定位规则', '历史证据可回查']}
    link = product.post(prefix + '/github/issues/10/link', json={'connection_revision': revision, 'requirement': spec})
    assert link.status_code == 200, link.text
    requirement = link.json()['requirement_id']
    response = product.put(prefix + f'/requirements/{requirement}/specification', json={
        **spec, 'expected_revision': 1, 'connection_revision': revision, 'directory': DIRECTORY, 'commit': base,
    })
    assert response.status_code == 200, response.text
    group = create_group(product, project, {**group_input, 'requirement_id': requirement, 'source': issue['url']})
    plan = str(uuid4())
    response = product.put(prefix + '/versions/' + plan, json={'expected_revision': 0, 'name': '桌面派发试点',
        'owner_id': users[0], 'status': 'committed', 'requirement_ids': [requirement],
        'milestones': [], 'required_checks': [], 'reason': '同一真实需求验收'})
    assert response.status_code == 200, response.text
    group_path = f"/api/groups/{group['id']}"
    assert stage_run(product, group)['status'] == 'completed'
    assert advance(product, product.get(group_path).json())['stage'] == 'development'
    return product, developer, tester, worker, prefix, group_path, plan, head


def test_desktop_device_permissions_and_idempotent_receipts(source, workflow):
    product, developer, tester, worker, prefix, group_path, plan, head = environments(workflow, source)
    identifier = request_attempt(developer, product.get(group_path).json(), plan, head)
    devices = [pairing(developer, worker, name)[0] for name in ('desktop-1', 'desktop-2')]
    unrelated = pairing(tester, worker, 'testing-desktop')[0]

    def activate(device):
        credential = uuid4().hex + uuid4().hex
        path = f"/api/executor/devices/{device['id']}/activate"
        payload = {'code': device['code'], 'credential': credential}
        assert worker.post(path, json=payload).status_code == 200
        assert worker.post(path, json=payload).status_code == 200
        assert worker.post(path, json={**payload, 'credential': '0'*64}).status_code == 409
        return {'Authorization': 'Bearer ' + credential}

    tokens = [activate(device) for device in devices]
    other = activate(unrelated)
    claim = f'/api/executor/{identifier}/claim'
    assert worker.post(claim, headers=other, json={'device': unrelated['id']}).status_code == 404
    for device, auth in zip(devices, tokens):
        queue = worker.get(f"/api/executor/devices/{device['id']}/dispatches", headers=auth)
        assert [item['id'] for item in queue.json()['dispatches']] == [identifier]
        assert 'secret_digest' not in queue.text

    def compete(index):
        return worker.post(claim, headers=tokens[index], json={'device': devices[index]['id']}).status_code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(compete, range(2)))
    assert sorted(outcomes) == [200, 409]
    winner = outcomes.index(200)
    assert compete(winner) == 200
    receipt = {'device': devices[winner]['id'], 'commit': head, 'status': 'succeeded', 'digest': DIGEST,
               'artifact': b64_artifact(), 'checks': [evidence('implementation')]}
    uri = f'/api/executor/{identifier}/receipt'
    auth = tokens[winner]
    assert worker.put(uri, headers=auth, json=receipt).status_code == 200
    assert worker.put(uri, headers=auth, json=receipt).status_code == 200
    assert sum(row['kind'] == 'executor' for row in product.get(group_path + '/messages').json()) == 1
    assert tester.get(prefix + '/delivery/artifacts/' + identifier).content == ARTIFACT
    assert developer.delete('/api/delivery/devices/' + devices[winner]['id']).status_code == 200
    assert worker.put(uri, headers=auth, json=receipt).status_code == 401


def b64_artifact():
    from base64 import b64encode
    return b64encode(ARTIFACT).decode()


@pytest.mark.skipif(not os.environ.get('MAD_DESKTOP_TEBB'), reason='Enable the Windows tebb desktop sandbox acceptance explicitly')
def test_live_desktop_recovery_on_issue_10(source, workflow, monkeypatch, tmp_path):
    assert source[0] == 'lkun-lea/myagentdesk'
    workbench = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(os.environ['MAD_DESKTOP_TEBB'])
    monkeypatch.setenv('DESK_COMPANION_DATA_DIR', str(tmp_path / 'tebb-data'))
    from desk_companion.integrations.myagentdesk_delivery import DesktopDelivery
    from desk_companion.projects.terminal import TerminalManager, root, workspace

    def available_terminal():
        terminal = TerminalManager()
        for _ in range(3):
            sandbox = terminal.status(refresh=True)
            if sandbox['ready']:
                return terminal
        pytest.fail(sandbox['error'])

    product, developer, tester, worker, prefix, group_path, plan, head = environments(workflow, source)
    identifier = request_attempt(developer, product.get(group_path).json(), plan, head)
    recipe = json.loads((workbench / 'tests/fixtures/delivery-pilot.json').read_text(encoding='utf-8'))
    development = json.dumps({'implementation': recipe['implementation']})
    qa = json.dumps({key: recipe[key] for key in ('criterion:1', 'criterion:2')})
    pipes = [Pipe(worker), Pipe(worker)]
    wrappers = [CountingTerminal(available_terminal()), CountingTerminal(available_terminal())]

    def boot(slot, terminal, pipe):
        return DesktopDelivery(tmp_path / slot, terminal, workspace, transport=pipe)

    desktops = [boot('d' + str(index), wrappers[index], pipes[index]) for index in range(2)]
    connections = [pairing(developer, worker, 'tebb ' + str(index))[0] for index in range(2)]
    for desktop, package in zip(desktops, connections):
        desktop.operation(action='pair', package=json.dumps(package))
        desktop.sync_once()
        assert [item['id'] for item in desktop.status()['queue']] == [identifier]
        desktop.operation(action='approve', identifier=identifier, directory=str(workbench), recipe=development)
    for pipe in pipes:
        pipe.lose = '/claim'

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda desktop: desktop.sync_once(), desktops))
    winner = [pipe.lost for pipe in pipes].index(1)
    loser = 1 - winner
    assert desktops[loser].status()['runs'][0]['phase'] == 'blocked'
    device = desktops[winner]
    device.sync_once()
    device.sync_once()
    with pytest.raises(SimulatedExit):
        device.sync_once()
    terminal = wrappers[winner]
    assert terminal.original and terminal.starts == 1 and wrappers[loser].starts == 0
    timeout = monotonic() + 45
    while terminal.actual.get(terminal.original)['state'] in {'starting', 'running', 'stopping'} and monotonic() < timeout:
        sleep(.15)
    assert terminal.actual.get(terminal.original)['state'] == 'succeeded'
    original_request = terminal.actual.get(terminal.original)['request_id']
    (root() / 'requests' / original_request).write_text('', encoding='utf-8')

    pipe = pipes[winner]
    pipe.offline = True
    restarted_terminal = CountingTerminal(TerminalManager(), 'normal')
    slot = 'd' + str(winner)
    device = boot(slot, restarted_terminal, pipe)
    device.sync_once()
    recovered = device.status()['runs'][0]
    assert recovered['phase'] == 'outbox' and recovered['runs'] == [terminal.original]
    assert restarted_terminal.starts == 0
    assert sum(row['kind'] == 'executor' for row in product.get(group_path + '/messages').json()) == 0

    pipe.offline = False
    pipe.lose = '/receipt'
    device.sync_once()
    assert device.status()['runs'][0]['phase'] == 'outbox'
    device = boot(slot, restarted_terminal, pipe)
    device.sync_once()
    assert device.status()['runs'][0]['phase'] == 'uploaded'
    assert sum(row['kind'] == 'executor' for row in product.get(group_path + '/messages').json()) == 1
    assert restarted_terminal.starts == 0 and pipe.lost == 2
    assert tester.get(prefix + '/delivery/artifacts/' + identifier).status_code == 200

    unknown = request_attempt(tester, product.get(group_path).json(), plan)
    qa_pipe = Pipe(worker)
    unknown_terminal = CountingTerminal(available_terminal(), 'unknown')
    observer = boot('tester', unknown_terminal, qa_pipe)
    observer.operation(action='pair', package=json.dumps(pairing(tester, worker, 'tebb QA')[0]))
    observer.sync_once()
    observer.operation(action='approve', identifier=unknown, directory=str(workbench), recipe=qa)
    observer.sync_once()
    observer.sync_once()
    with pytest.raises(SimulatedExit):
        observer.sync_once()
    second_terminal = CountingTerminal(TerminalManager(), 'normal')
    observer = boot('tester', second_terminal, qa_pipe)
    for _ in range(3):
        observer.sync_once()
    assert observer.status()['runs'][0]['receipt_status'] == 'needs_review'
    assert second_terminal.starts == 0
    messages = product.get(group_path + '/messages').json()
    assert sum(row['kind'] == 'executor' for row in messages) == 1
    assert sum(row['kind'] == 'executor_review' for row in messages) == 1
    assert product.get(group_path).json()['stage'] == 'testing'
    if os.environ.get('MAD_DESKTOP_LOG'):
        record = {'issue': 'https://github.com/lkun-lea/myagentdesk/issues/10', 'candidate': head,
                  'winner': connections[winner]['id'], 'loser': connections[loser]['id'],
                  'claim_and_receipt_acks_dropped': pipe.lost, 'recovered': recovered,
                  'unknown': observer.status()['runs'][0], 'dispatches': product.get(prefix + '/delivery').json()['dispatches'],
                  'notifications': [msg for msg in messages if msg['kind'] in {'executor', 'executor_review'}]}
        Path(os.environ['MAD_DESKTOP_LOG']).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
