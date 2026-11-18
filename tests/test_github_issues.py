from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest

from server.config import Settings
from server.errors import Problem
from server.github_issues import GitHubIssues
from server.main import create_app
from test_accounts import FakeMailAuth, HEADERS, MEMBER, ORIGIN, OWNER, login
from test_requirement_groups import create_group, stage_run, workflow


class GitHubFixture:
    def __init__(self):
        self.requests = []
        self.private = False
        self.offline = False
        self.rate_limited = False
        self.closed = False
        self.arrived, self.release = Event(), Event()
        self.release.set()

    def reply(self, request):
        assert request.method == 'GET' and request.url.host == 'api.github.com'
        assert 'authorization' not in request.headers
        path = request.url.path
        self.requests.append(path)
        if self.offline:
            raise httpx.ConnectError('fixture connection lost', request=request)
        if self.rate_limited:
            return httpx.Response(403, headers={'X-RateLimit-Remaining': '0'}, json={'message': 'rate limited'})
        if path == '/repos/example/design' or path == '/repositories/41':
            return httpx.Response(200, json={'id': 41, 'full_name': 'example/design', 'private': self.private})
        if path == '/repos/example/other' or path == '/repositories/42':
            return httpx.Response(200, json={'id': 42, 'full_name': 'example/other', 'private': False})
        if path == '/repositories/41/issues/8':
            return httpx.Response(200, json={'number': 8, 'pull_request': {}})
        if path in {'/repositories/41/issues/9', '/repositories/42/issues/9'}:
            self.arrived.set()
            assert self.release.wait(10)
            return httpx.Response(200, json={'number': 9, 'title': 'Original source', 'body': 'GitHub body',
                                            'state': 'closed' if self.closed else 'open',
                                            'state_reason': 'not_planned' if self.closed else None})
        return httpx.Response(404)


@pytest.fixture
def github_project(database):
    auth, github = FakeMailAuth(), GitHubFixture()
    settings = Settings(database, ORIGIN, 'https://auth.test', 'publishable-test', Fernet.generate_key().decode())
    app = create_app(settings, auth_transport=httpx.MockTransport(auth.reply), github_transport=httpx.MockTransport(github.reply))
    owner, member = [TestClient(app, base_url=ORIGIN, headers=HEADERS) for _ in range(2)]
    login(owner, OWNER)
    login(member, MEMBER)
    project = owner.post('/api/projects', json={'name': 'Issue 来源验收'}).json()['id']
    path = f'/api/projects/{project}'
    invitation = str(uuid4())
    assert owner.post(path + '/invites', json={'id': invitation, 'email': MEMBER}).status_code == 200
    assert member.post(f'/api/invites/{invitation}/accept').status_code == 200
    return owner, member, path, github


def attach(owner, path, repository='example/design', revision=0):
    response = owner.put(path + '/github/connection', json={'repository': repository, 'expected_revision': revision})
    assert response.status_code == 200
    return response.json()['revision']


def test_concurrent_confirmation_creates_one_requirement_and_preserves_state(github_project):
    owner, member, path, github = github_project
    revision = attach(owner, path)
    issue_path = path + '/github/issues/9'
    issue = member.get(issue_path).json()
    assert issue['url'] == 'https://github.com/example/design/issues/9' and issue['body'] == 'GitHub body'
    assert member.get(path + '/github/issues/8').json()['error'] == 'github_issue_not_found'
    payload = {'connection_revision': revision, 'requirement': {'title': '试点需求', 'body': '内部确认的内容', 'criteria': ['只读关联']}}
    assert member.post(issue_path + '/link', json=payload).status_code == 403
    with ThreadPoolExecutor(3) as workers:
        submissions = list(workers.map(lambda _: owner.post(issue_path + '/link', json=payload), range(3)))
    assert all(item.status_code == 200 for item in submissions)
    assert len({item.json()['requirement_id'] for item in submissions}) == 1
    link = submissions[0].json()
    requirement = owner.get(path + '/requirements').json()[0]
    assert requirement['id'] == link['requirement_id'] and requirement['body'] == '内部确认的内容'
    assert len(owner.get(path + '/requirements').json()) == 1
    assert owner.get(path + f"/github/requirements/{requirement['id']}/links").json() == [
        {'issue_number': 9, 'linked_revision': 1, 'connection_revision': revision}]
    timeline = owner.get(path + '/timeline').json()
    assert [row['target'] for row in timeline if row['action'] == 'github_issue_linked'] == [requirement['id']]
    github.closed = True
    latest = owner.get(issue_path).json()
    assert (latest['state'], latest['state_reason'], latest['linked']) == ('closed', 'not_planned', link)
    assert owner.get(path + '/requirements').json() == [requirement]


def test_repository_identity_and_linked_existing_requirement(github_project):
    owner, _, path, _ = github_project
    rev = attach(owner, path)
    first, second = str(uuid4()), str(uuid4())
    for identifier in (first, second):
        response = owner.put(path + '/requirements', json={'id': identifier, 'expected_revision': 0, 'title': identifier,
                                                            'body': '内部描述', 'criteria': ['一条条件']})
        assert response.status_code == 200
    source = path + '/github/issues/9'
    assert owner.post(source + '/link', json={'connection_revision': rev, 'requirement_id': first}).json()['requirement_id'] == first
    replacement = attach(owner, path, 'example/other', rev)
    assert owner.get(source, params={'revision': rev}).status_code == 409
    assert owner.get(source).json()['linked'] is None
    assert owner.post(source + '/link', json={'connection_revision': replacement, 'requirement_id': second}).json()['requirement_id'] == second
    restored = attach(owner, path, revision=replacement)
    assert owner.get(source, params={'revision': restored}).json()['linked']['requirement_id'] == first


def test_upstream_errors_and_revoke_never_return_old_content(github_project):
    owner, member, path, github = github_project
    rev = attach(owner, path)
    issue_path = path + '/github/issues/9'
    assert member.get(issue_path).status_code == 200
    for flag, code, reason in [('private', 404, 'github_source_unavailable'), ('offline', 503, 'github_unavailable'),
                               ('rate_limited', 429, 'github_rate_limited')]:
        setattr(github, flag, True)
        response = member.get(issue_path)
        assert response.status_code == code and response.json() == {'error': reason}
        setattr(github, flag, False)
    assert member.post(path + '/github/disconnect', json={'expected_revision': rev}).status_code == 403
    github.arrived.clear()
    github.release.clear()
    with ThreadPoolExecutor() as workers:
        pending = workers.submit(member.get, issue_path)
        try:
            assert github.arrived.wait(5)
            member_id = member.get('/api/auth/session').json()['user']['id']
            assert owner.delete(path + '/members/' + member_id).status_code == 200
        finally:
            github.release.set()
        assert pending.result().json() == {'error': 'project_not_found'}
    assert owner.post(path + '/github/disconnect', json={'expected_revision': rev}).json()['connected'] is False
    assert owner.get(issue_path).json() == {'error': 'github_not_connected'}


def test_source_close_preserves_group_stage_and_evidence(workflow):
    (product, _, _, _), _, project, _, requirement, spec, endpoint = workflow
    group = create_group(product, project, spec)
    assert stage_run(product, group)['status'] == 'completed'
    github = GitHubFixture()
    app = create_app(endpoint.settings, auth_transport=httpx.MockTransport(endpoint.fake.reply),
                     github_transport=httpx.MockTransport(github.reply))
    client = TestClient(app, base_url=ORIGIN, headers=HEADERS, cookies=product.cookies)
    path = '/api/projects/' + project
    revision = attach(client, path)
    source = path + '/github/issues/9'
    result = client.post(source + '/link', json={'connection_revision': revision, 'requirement_id': requirement['id']})
    assert result.json()['stage'] == 'product'
    group_path = '/api/groups/' + group['id']
    before = client.get(group_path).json()
    evidence = client.get(path + '/handoffs').json()
    assert before['state'] == 'awaiting_confirmation' and evidence and not evidence[0]['outdated']
    github.closed = True
    assert client.get(source).json()['state'] == 'closed'
    assert client.get(group_path).json() == before
    assert client.get(path + '/handoffs').json() == evidence


def test_authenticated_transport_still_rejects_private_repositories():
    def reply(request):
        assert request.method == 'GET' and request.url.path == '/repositories/41'
        assert request.headers['authorization'] == 'Bearer fixture-github-reader'
        return httpx.Response(200, json={'id': 41, 'full_name': 'example/design', 'private': True})

    upstream = GitHubIssues('fixture-github-reader', httpx.MockTransport(reply))
    with pytest.raises(Problem) as error:
        upstream.issue(41, 9)
    assert error.value.code == 'github_source_unavailable'
